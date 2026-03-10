"""
calibration.py — Calibración automática del modelo SLO·BET
============================================================

Flujo:
1. Descarga partidos FINALIZADOS de las últimas N semanas de Sofascore
2. Para cada partido, reconstruye el análisis con datos PRE-PARTIDO
   (forma de ambos equipos ANTES de ese partido, tabla de ese momento)
3. Compara predicción del modelo vs resultado real
4. Guarda en Turso tabla `model_calibration`
5. Calcula métricas de calibración por mercado

Se puede llamar:
  - Manualmente via GET /calibration/run
  - Automáticamente via background task en startup
"""

import asyncio
import json
import time
from typing import Optional

import httpx

from app.database import _run, _build_stmt, _rows
from app.football_api import (
    TOURNAMENT_IDS, SF_HEADERS, _get_season,
    _parse_form_events,
    fetch_standings,
)
from app.analysis_engine import analyze_match, _over_probability, _btts_probability

# ── Turso table init ──────────────────────────────────────────────────────────

INIT_SQL = """
CREATE TABLE IF NOT EXISTS model_calibration (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sampled_at TEXT DEFAULT (datetime('now')),
    match_id TEXT UNIQUE,
    league TEXT,
    home_team TEXT,
    away_team TEXT,
    match_date TEXT,

    -- Predicciones del modelo (pre-partido)
    pred_home_win REAL,
    pred_draw REAL,
    pred_away_win REAL,
    pred_over15 REAL,
    pred_over25 REAL,
    pred_over35 REAL,
    pred_btts REAL,
    home_xg REAL,
    away_xg REAL,
    overall_score REAL,

    -- Resultado real
    real_home_goals INTEGER,
    real_away_goals INTEGER,
    real_result TEXT,        -- 'H', 'D', 'A'
    real_total_goals INTEGER,
    real_btts INTEGER,       -- 0 o 1

    -- Correctness flags
    correct_1x2 INTEGER,     -- 1 si el modelo acertó
    correct_over15 INTEGER,
    correct_over25 INTEGER,
    correct_over35 INTEGER,
    correct_btts INTEGER
);

CREATE INDEX IF NOT EXISTS idx_cal_league ON model_calibration(league);
CREATE INDEX IF NOT EXISTS idx_cal_date   ON model_calibration(match_date);
"""


def init_calibration_table():
    for stmt in INIT_SQL.strip().split(";"):
        s = stmt.strip()
        if s:
            try:
                _run(_build_stmt(s))
            except Exception as e:
                if "already exists" not in str(e):
                    print(f"[cal] init warning: {e}")


# ── Sofascore: fetch finished matches ─────────────────────────────────────────

async def _fetch_finished_matches(league: str, pages: int = 3) -> list[dict]:
    """
    Fetch recently finished matches for a league using /events/last/{page}.
    Returns list of raw Sofascore event dicts.
    """
    tid = TOURNAMENT_IDS.get(league)
    if not tid:
        return []
    sid = await _get_season(tid)
    if not sid:
        return []

    finished = []
    async with httpx.AsyncClient(headers=SF_HEADERS, timeout=15) as client:
        for page in range(pages):
            try:
                url = f"https://api.sofascore.com/api/v1/unique-tournament/{tid}/season/{sid}/events/last/{page}"
                r = await client.get(url)
                if r.status_code != 200:
                    break
                events = r.json().get("events", [])
                if not events:
                    break
                for ev in events:
                    status = ev.get("status", {}).get("type", "")
                    if status == "finished":
                        finished.append(ev)
                await asyncio.sleep(0.4)
            except Exception as e:
                print(f"[cal] fetch finished {league} page {page}: {e}")
                break

    return finished


async def _fetch_team_events_before(team_id: int, before_timestamp: int, last_n: int = 7) -> list[dict]:
    """
    Fetch team's last N finished events BEFORE a given timestamp.
    Used to reconstruct pre-match form.
    """
    async with httpx.AsyncClient(headers=SF_HEADERS, timeout=12) as client:
        try:
            url = f"https://api.sofascore.com/api/v1/team/{team_id}/events/last/0"
            r = await client.get(url)
            if r.status_code != 200:
                return []
            events = r.json().get("events", [])
            # Filter: only finished, only before the match timestamp
            before = [
                e for e in events
                if e.get("status", {}).get("type") == "finished"
                and e.get("startTimestamp", 0) < before_timestamp
            ]
            # Take last_n most recent
            return before[-last_n:] if len(before) >= 2 else []
        except Exception as e:
            print(f"[cal] pre-form team {team_id}: {e}")
            return []


def _parse_real_result(event: dict) -> Optional[dict]:
    """Extract real result from a finished Sofascore event."""
    try:
        hs = event.get("homeScore", {})
        as_ = event.get("awayScore", {})
        hg = hs.get("current", hs.get("display", hs.get("normaltime")))
        ag = as_.get("current", as_.get("display", as_.get("normaltime")))
        if hg is None or ag is None:
            return None
        hg, ag = int(hg), int(ag)
        result = "H" if hg > ag else ("D" if hg == ag else "A")
        return {
            "home_goals": hg,
            "away_goals": ag,
            "result": result,
            "total_goals": hg + ag,
            "btts": 1 if hg > 0 and ag > 0 else 0,
        }
    except Exception:
        return None


def _already_sampled(match_id: str) -> bool:
    """Check if this match_id is already in calibration table."""
    try:
        res = _run(_build_stmt(
            "SELECT id FROM model_calibration WHERE match_id = ?", [match_id]
        ))
        return len(_rows(res[0])) > 0
    except Exception:
        return False


def _save_calibration_row(row: dict):
    """Insert one calibration record into Turso."""
    sql = """
    INSERT OR IGNORE INTO model_calibration (
        match_id, league, home_team, away_team, match_date,
        pred_home_win, pred_draw, pred_away_win,
        pred_over15, pred_over25, pred_over35, pred_btts,
        home_xg, away_xg, overall_score,
        real_home_goals, real_away_goals, real_result,
        real_total_goals, real_btts,
        correct_1x2, correct_over15, correct_over25, correct_over35, correct_btts
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """
    params = [
        row["match_id"], row["league"], row["home_team"], row["away_team"], row["match_date"],
        row["pred_home_win"], row["pred_draw"], row["pred_away_win"],
        row["pred_over15"], row["pred_over25"], row["pred_over35"], row["pred_btts"],
        row["home_xg"], row["away_xg"], row["overall_score"],
        row["real_home_goals"], row["real_away_goals"], row["real_result"],
        row["real_total_goals"], row["real_btts"],
        row["correct_1x2"], row["correct_over15"], row["correct_over25"],
        row["correct_over35"], row["correct_btts"],
    ]
    try:
        _run(_build_stmt(sql, params))
    except Exception as e:
        print(f"[cal] save error: {e}")


# ── Core calibration runner ───────────────────────────────────────────────────

async def run_calibration(leagues: list[str] = None, pages_per_league: int = 3) -> dict:
    """
    Main calibration function. Fetches finished matches, runs predictions,
    compares with real results, saves to Turso.

    Returns summary dict with counts and accuracy metrics.
    """
    init_calibration_table()

    target_leagues = leagues or list(TOURNAMENT_IDS.keys())
    total_processed = 0
    total_skipped = 0
    errors = 0

    print(f"[cal] Starting calibration for {len(target_leagues)} leagues")

    for league in target_leagues:
        print(f"[cal] Processing {league}...")
        finished = await _fetch_finished_matches(league, pages=pages_per_league)
        print(f"[cal]   {len(finished)} finished matches found")

        for event in finished:
            match_id = str(event.get("id", ""))
            if not match_id:
                continue

            if _already_sampled(match_id):
                total_skipped += 1
                continue

            # Parse real result
            real = _parse_real_result(event)
            if not real:
                continue

            # Extract team IDs and match info
            ht = event.get("homeTeam", {})
            at = event.get("awayTeam", {})
            home_id = ht.get("id")
            away_id = at.get("id")
            if not home_id or not away_id:
                continue

            ts = event.get("startTimestamp", 0)
            match_date = event.get("startTimestamp", "")

            try:
                # Fetch PRE-MATCH form (events before this match's timestamp)
                home_events, away_events = await asyncio.gather(
                    _fetch_team_events_before(home_id, ts, last_n=7),
                    _fetch_team_events_before(away_id, ts, last_n=7),
                )
                await asyncio.sleep(0.3)

                # Parse form exactly as the live model does
                home_form = _parse_form_events(home_events, home_id)
                away_form = _parse_form_events(away_events, away_id)

                # Fetch standings (current — approximation for historical)
                standings = await fetch_standings(league)

                # Build match dict
                match_dict = {
                    "match_id": match_id,
                    "home_team": ht.get("name", ""),
                    "away_team": at.get("name", ""),
                    "home_team_id": home_id,
                    "away_team_id": away_id,
                    "league": league,
                    "match_date": str(ts),
                }

                # Run model (no odds needed for calibration)
                result = analyze_match(match_dict, home_form, away_form, {}, standings)

                probs = result.get("probabilities", {})
                sb = result.get("score_breakdown", {})
                goals_d = sb.get("goals", {})

                pred_hw = probs.get("home_win", 33.3) / 100
                pred_d  = probs.get("draw", 33.3) / 100
                pred_aw = probs.get("away_win", 33.3) / 100
                home_xg = goals_d.get("home_xg", 1.3)
                away_xg = goals_d.get("away_xg", 1.1)

                pred_over15 = _over_probability(home_xg, away_xg, 1.5)
                pred_over25 = _over_probability(home_xg, away_xg, 2.5)
                pred_over35 = _over_probability(home_xg, away_xg, 3.5)
                pred_btts   = _btts_probability(home_xg, away_xg)

                # Model prediction (what it would have bet)
                pred_result = "H" if pred_hw > pred_d and pred_hw > pred_aw else (
                    "D" if pred_d >= pred_hw and pred_d >= pred_aw else "A"
                )

                # Correctness
                correct_1x2   = 1 if pred_result == real["result"] else 0
                correct_over15 = 1 if (pred_over15 > 0.5) == (real["total_goals"] > 1) else 0
                correct_over25 = 1 if (pred_over25 > 0.5) == (real["total_goals"] > 2) else 0
                correct_over35 = 1 if (pred_over35 > 0.5) == (real["total_goals"] > 3) else 0
                correct_btts   = 1 if (pred_btts > 0.5) == (real["btts"] == 1) else 0

                import datetime
                md = datetime.datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else ""

                _save_calibration_row({
                    "match_id":      match_id,
                    "league":        league,
                    "home_team":     ht.get("name", ""),
                    "away_team":     at.get("name", ""),
                    "match_date":    md,
                    "pred_home_win": round(pred_hw, 4),
                    "pred_draw":     round(pred_d, 4),
                    "pred_away_win": round(pred_aw, 4),
                    "pred_over15":   round(pred_over15, 4),
                    "pred_over25":   round(pred_over25, 4),
                    "pred_over35":   round(pred_over35, 4),
                    "pred_btts":     round(pred_btts, 4),
                    "home_xg":       round(home_xg, 3),
                    "away_xg":       round(away_xg, 3),
                    "overall_score": result.get("overall_confidence", 50),
                    "real_home_goals": real["home_goals"],
                    "real_away_goals": real["away_goals"],
                    "real_result":     real["result"],
                    "real_total_goals": real["total_goals"],
                    "real_btts":       real["btts"],
                    "correct_1x2":     correct_1x2,
                    "correct_over15":  correct_over15,
                    "correct_over25":  correct_over25,
                    "correct_over35":  correct_over35,
                    "correct_btts":    correct_btts,
                })
                total_processed += 1

            except Exception as e:
                print(f"[cal] error processing {match_id}: {e}")
                errors += 1
                continue

        await asyncio.sleep(1.0)  # be polite between leagues

    print(f"[cal] Done: {total_processed} new, {total_skipped} skipped, {errors} errors")
    return {"processed": total_processed, "skipped": total_skipped, "errors": errors}


# ── Metrics calculator ────────────────────────────────────────────────────────

def get_calibration_metrics(league: str = None) -> dict:
    """
    Calculate calibration metrics from stored data.
    Returns accuracy per market, calibration buckets, and Brier scores.
    """
    where = f"WHERE league = '{league}'" if league else ""
    try:
        res = _run(_build_stmt(f"""
            SELECT
                COUNT(*) as total,
                SUM(correct_1x2)   as ok_1x2,
                SUM(correct_over15) as ok_o15,
                SUM(correct_over25) as ok_o25,
                SUM(correct_over35) as ok_o35,
                SUM(correct_btts)   as ok_btts,
                AVG(pred_home_win)  as avg_pred_hw,
                AVG(pred_draw)      as avg_pred_d,
                AVG(pred_away_win)  as avg_pred_aw,
                AVG(pred_over25)    as avg_pred_o25,
                AVG(pred_btts)      as avg_pred_btts,
                -- Real rates
                AVG(CASE WHEN real_result='H' THEN 1.0 ELSE 0.0 END) as real_hw_rate,
                AVG(CASE WHEN real_result='D' THEN 1.0 ELSE 0.0 END) as real_d_rate,
                AVG(CASE WHEN real_result='A' THEN 1.0 ELSE 0.0 END) as real_aw_rate,
                AVG(CASE WHEN real_total_goals > 2 THEN 1.0 ELSE 0.0 END) as real_o25_rate,
                AVG(CASE WHEN real_btts = 1 THEN 1.0 ELSE 0.0 END) as real_btts_rate,
                AVG(real_total_goals) as avg_goals,
                AVG(home_xg + away_xg) as avg_xg_total
            FROM model_calibration {where}
        """))
        row = _rows(res[0])
        if not row or not row[0]["total"]:
            return {"total": 0, "message": "Sin datos aún. Ejecutá /calibration/run"}

        r = row[0]
        total = int(r["total"] or 0)
        if total == 0:
            return {"total": 0}

        def pct(v): return round(float(v or 0) * 100 / total, 1)
        def rate(v): return round(float(v or 0) * 100, 1)

        # Brier score: mean((pred - outcome)^2), lower = better (0=perfect, 0.25=random)
        brier_res = _run(_build_stmt(f"""
            SELECT
                AVG((pred_home_win - CASE WHEN real_result='H' THEN 1.0 ELSE 0.0 END) *
                    (pred_home_win - CASE WHEN real_result='H' THEN 1.0 ELSE 0.0 END)) as bs_hw,
                AVG((pred_over25 - CASE WHEN real_total_goals > 2 THEN 1.0 ELSE 0.0 END) *
                    (pred_over25 - CASE WHEN real_total_goals > 2 THEN 1.0 ELSE 0.0 END)) as bs_o25,
                AVG((pred_btts - CAST(real_btts AS REAL)) *
                    (pred_btts - CAST(real_btts AS REAL))) as bs_btts
            FROM model_calibration {where}
        """))
        br = _rows(brier_res[0])
        brier = br[0] if br else {}

        # Calibration buckets for Over 2.5 (how often does model 70-80% actually win 70-80%?)
        bucket_res = _run(_build_stmt(f"""
            SELECT
                ROUND(pred_over25 * 10) / 10 as bucket,
                COUNT(*) as cnt,
                AVG(CASE WHEN real_total_goals > 2 THEN 1.0 ELSE 0.0 END) as actual_rate
            FROM model_calibration {where}
            GROUP BY bucket ORDER BY bucket
        """))
        buckets = _rows(bucket_res[0])

        # Per-league breakdown
        league_res = _run(_build_stmt(f"""
            SELECT league,
                COUNT(*) as total,
                AVG(correct_1x2) * 100 as acc_1x2,
                AVG(correct_over25) * 100 as acc_o25,
                AVG(correct_btts) * 100 as acc_btts,
                AVG(real_total_goals) as avg_goals
            FROM model_calibration
            GROUP BY league ORDER BY total DESC
        """))
        by_league = _rows(league_res[0])

        return {
            "total": total,
            "accuracy": {
                "1x2":     pct(r["ok_1x2"]),
                "over_15": pct(r["ok_o15"]),
                "over_25": pct(r["ok_o25"]),
                "over_35": pct(r["ok_o35"]),
                "btts":    pct(r["ok_btts"]),
            },
            "model_avg_predictions": {
                "home_win": rate(r["avg_pred_hw"]),
                "draw":     rate(r["avg_pred_d"]),
                "away_win": rate(r["avg_pred_aw"]),
                "over_25":  rate(r["avg_pred_o25"]),
                "btts":     rate(r["avg_pred_btts"]),
            },
            "real_rates": {
                "home_win": rate(r["real_hw_rate"]),
                "draw":     rate(r["real_d_rate"]),
                "away_win": rate(r["real_aw_rate"]),
                "over_25":  rate(r["real_o25_rate"]),
                "btts":     rate(r["real_btts_rate"]),
            },
            "avg_goals": {
                "real":  round(float(r["avg_goals"] or 2.5), 2),
                "model_xg": round(float(r["avg_xg_total"] or 2.4), 2),
            },
            "brier_scores": {
                "home_win": round(float(brier.get("bs_hw") or 0.25), 4),
                "over_25":  round(float(brier.get("bs_o25") or 0.25), 4),
                "btts":     round(float(brier.get("bs_btts") or 0.25), 4),
                "note": "0=perfecto, 0.25=azar, <0.20=bueno"
            },
            "calibration_buckets_over25": [
                {
                    "pred_range": f"{round(float(b['bucket'])*100-5)}-{round(float(b['bucket'])*100+5)}%",
                    "count": int(b["cnt"]),
                    "model_pred": round(float(b["bucket"]) * 100, 1),
                    "actual_pct": round(float(b["actual_rate"] or 0) * 100, 1),
                    "gap": round((float(b["bucket"]) - float(b["actual_rate"] or 0)) * 100, 1),
                }
                for b in buckets if b["bucket"] is not None
            ],
            "by_league": [
                {
                    "league": b["league"],
                    "total": int(b["total"]),
                    "acc_1x2": round(float(b["acc_1x2"] or 0), 1),
                    "acc_over25": round(float(b["acc_o25"] or 0), 1),
                    "acc_btts": round(float(b["acc_btts"] or 0), 1),
                    "avg_goals": round(float(b["avg_goals"] or 0), 2),
                }
                for b in by_league
            ],
        }

    except Exception as e:
        print(f"[cal] metrics error: {e}")
        return {"error": str(e)}
