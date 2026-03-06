"""
Football data — TheSportsDB (gratuito, sin límite) + scraping Flashscore
========================================================================
TheSportsDB API v1 (free):
  - Fixtures próximos: /api/v1/json/3/eventsnextleague.php?id=LEAGUE_ID
  - Últimos resultados: /api/v1/json/3/eventspastleague.php?id=LEAGUE_ID
  - Tabla: /api/v1/json/3/lookuptable.php?l=LEAGUE_ID&s=SEASON

IDs en TheSportsDB:
  PrvaLiga → 4966
  2.SNL    → no disponible (usamos mock)

Sin API key requerida para v1.
"""

import httpx
import re
from datetime import datetime, date, timedelta
from typing import Optional

BASE = "https://www.thesportsdb.com/api/v1/json/3"

LEAGUES = {
    "PrvaLiga": {"tsdb_id": "4966", "fs_id": "slovenian-prvaliga"},
    "2SNL":     {"tsdb_id": None,   "fs_id": None},
}

CURRENT_SEASON = "2025-2026"

# Team name normalizations (TheSportsDB uses slightly different names)
NAME_MAP = {
    "Olimpija Ljubljana": "NK Olimpija Ljubljana",
    "NK Olimpija":        "NK Olimpija Ljubljana",
    "Maribor":            "NK Maribor",
    "Celje":              "NK Celje",
    "Koper":              "FC Koper",
    "NK Koper":           "FC Koper",
    "Bravo":              "NK Bravo",
    "Mura":               "NS Mura",
    "NS Mura":            "NS Mura",
    "NK Mura":            "NS Mura",
    "Domzale":            "NK Domžale",
    "NK Domzale":         "NK Domžale",
    "Radomlje":           "NK Radomlje",
    "Primorje":           "NK Primorje Ajdovščina",
    "Nafta 1903":         "NK Nafta 1903",
    "Aluminij":           "NK Aluminij",
    "Drava Ptuj":         "FC Drava Ptuj",
    "Ankaran":            "NK Ankaran",
}

def _norm_name(name: str) -> str:
    return NAME_MAP.get(name, name)


# ── Upcoming fixtures ──────────────────────────────────────────────────────

async def fetch_upcoming_matches(days_ahead: int = 14) -> list[dict]:
    all_matches = []

    async with httpx.AsyncClient(timeout=20) as client:
        for league_name, cfg in LEAGUES.items():
            if not cfg["tsdb_id"]:
                all_matches.extend(_mock_matches(league_name))
                continue
            try:
                resp = await client.get(
                    f"{BASE}/eventsnextleague.php",
                    params={"id": cfg["tsdb_id"]}
                )
                events = resp.json().get("events") or []
                today = date.today()
                cutoff = today + timedelta(days=days_ahead)

                for e in events:
                    match_date = e.get("dateEvent", "")
                    if not match_date:
                        continue
                    try:
                        d = date.fromisoformat(match_date)
                        if d < today or d > cutoff:
                            continue
                    except:
                        continue

                    home = _norm_name(e.get("strHomeTeam", ""))
                    away = _norm_name(e.get("strAwayTeam", ""))
                    time_str = e.get("strTime", "17:00:00") or "17:00:00"
                    dt_str = f"{match_date}T{time_str[:5]}:00+01:00"

                    all_matches.append({
                        "id":           str(e.get("idEvent", "")),
                        "date":         dt_str,
                        "status":       "NS",
                        "league":       league_name,
                        "league_id":    int(cfg["tsdb_id"]),
                        "round":        e.get("intRound", ""),
                        "home_team":    home,
                        "home_team_id": _team_id(home),
                        "away_team":    away,
                        "away_team_id": _team_id(away),
                        "venue":        e.get("strVenue", ""),
                        "tsdb_id":      e.get("idEvent", ""),
                    })
            except Exception as ex:
                print(f"[TheSportsDB] Error {league_name}: {ex}")
                all_matches.extend(_mock_matches(league_name))

    if not all_matches:
        return _mock_matches()

    return sorted(all_matches, key=lambda x: x["date"])


# ── Past results (for form + auto-result polling) ─────────────────────────

async def fetch_past_results(league_id: str, last_n: int = 20) -> list[dict]:
    async with httpx.AsyncClient(timeout=20) as client:
        try:
            resp = await client.get(
                f"{BASE}/eventspastleague.php",
                params={"id": league_id}
            )
            events = resp.json().get("events") or []
            results = []
            for e in events[-last_n:]:
                hg = e.get("intHomeScore")
                ag = e.get("intAwayScore")
                if hg is None or ag is None:
                    continue
                results.append({
                    "id":         str(e.get("idEvent", "")),
                    "date":       e.get("dateEvent", ""),
                    "home_team":  _norm_name(e.get("strHomeTeam", "")),
                    "away_team":  _norm_name(e.get("strAwayTeam", "")),
                    "home_goals": int(hg),
                    "away_goals": int(ag),
                    "status":     "FT",
                })
            return results
        except Exception as ex:
            print(f"[TheSportsDB] past results error: {ex}")
            return []


# ── Team form (built from past results) ───────────────────────────────────

async def fetch_team_form(team_id: int, league_id: int, last_n: int = 7) -> dict:
    league_tsdb = _tsdb_id_from_int(league_id)
    if not league_tsdb:
        return _mock_form(team_id)

    past = await fetch_past_results(league_tsdb, last_n=40)
    team_name = _team_name_from_id(team_id)

    results, scored, conceded = [], [], []
    for m in past:
        is_home = _same_team(m["home_team"], team_name)
        is_away = _same_team(m["away_team"], team_name)
        if not is_home and not is_away:
            continue
        tg = m["home_goals"] if is_home else m["away_goals"]
        og = m["away_goals"] if is_home else m["home_goals"]
        scored.append(tg); conceded.append(og)
        results.append("W" if tg > og else ("D" if tg == og else "L"))
        if len(results) >= last_n:
            break

    if not results:
        return _mock_form(team_id)

    n = len(results)
    return {
        "form":           results,
        "form_string":    "".join(results[-5:]),
        "avg_scored":     round(sum(scored) / n, 2),
        "avg_conceded":   round(sum(conceded) / n, 2),
        "clean_sheets":   sum(1 for g in conceded if g == 0),
        "btts_count":     sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed": n,
    }


# ── H2H ───────────────────────────────────────────────────────────────────

async def fetch_h2h(home_id: int, away_id: int) -> dict:
    home_name = _team_name_from_id(home_id)
    away_name = _team_name_from_id(away_id)

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{BASE}/searchevents.php",
                params={"e": f"{home_name} vs {away_name}"}
            )
            events = resp.json().get("event") or []
        except:
            events = []

    hw = aw = draws = btts = 0
    goals = []
    for e in events[:10]:
        hg = e.get("intHomeScore")
        ag = e.get("intAwayScore")
        if hg is None or ag is None:
            continue
        hg, ag = int(hg), int(ag)
        goals.append(hg + ag)
        eh = _norm_name(e.get("strHomeTeam",""))
        if hg > ag:   hw += (1 if _same_team(eh, home_name) else 0); aw += (0 if _same_team(eh, home_name) else 1)
        elif ag > hg: aw += (1 if _same_team(eh, away_name) else 0); hw += (0 if _same_team(eh, away_name) else 1)
        else: draws += 1
        if hg > 0 and ag > 0: btts += 1

    if not goals:
        return _mock_h2h()

    n = len(goals)
    return {
        "total_matches": n,
        "home_wins":     hw,
        "draws":         draws,
        "away_wins":     aw,
        "avg_goals_h2h": round(sum(goals) / n, 2),
        "btts_pct":      round(btts / n * 100, 1),
        "over25_pct":    round(sum(1 for g in goals if g > 2.5) / n * 100, 1),
    }


# ── Standings ──────────────────────────────────────────────────────────────

async def fetch_standings(league_id: int) -> list[dict]:
    tsdb_id = _tsdb_id_from_int(league_id)
    if not tsdb_id:
        return _mock_standings()

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                f"{BASE}/lookuptable.php",
                params={"l": tsdb_id, "s": CURRENT_SEASON}
            )
            rows = resp.json().get("table") or []
            if not rows:
                # try previous season format
                resp2 = await client.get(
                    f"{BASE}/lookuptable.php",
                    params={"l": tsdb_id, "s": "2024-2025"}
                )
                rows = resp2.json().get("table") or []
        except Exception as e:
            print(f"[TheSportsDB] standings error: {e}")
            return _mock_standings()

    if not rows:
        return _mock_standings()

    return [
        {
            "rank":          int(r.get("intRank", i + 1)),
            "team_id":       _team_id(_norm_name(r.get("strTeam", ""))),
            "team_name":     _norm_name(r.get("strTeam", "")),
            "points":        int(r.get("intPoints", 0)),
            "played":        int(r.get("intPlayed", 0)),
            "won":           int(r.get("intWin", 0)),
            "drawn":         int(r.get("intDraw", 0)),
            "lost":          int(r.get("intLoss", 0)),
            "goals_for":     int(r.get("intGoalsFor", 0)),
            "goals_against": int(r.get("intGoalsAgainst", 0)),
            "goal_diff":     int(r.get("intGoalDifference", 0)),
            "form":          r.get("strForm", ""),
        }
        for i, r in enumerate(rows)
    ]


# ── Helpers ────────────────────────────────────────────────────────────────

TEAM_IDS = {
    "NK Olimpija Ljubljana": 1598,
    "NK Maribor":            1601,
    "NK Celje":              1594,
    "FC Koper":              2279,
    "NK Bravo":              10203,
    "NS Mura":               1600,
    "NK Domžale":            1595,
    "NK Radomlje":           14370,
    "NK Primorje Ajdovščina":99991,
    "NK Nafta 1903":         14372,
    "NK Aluminij":           10576,
    "FC Drava Ptuj":         10578,
    "NK Ankaran":            14371,
}
ID_TO_NAME = {v: k for k, v in TEAM_IDS.items()}

def _team_id(name: str) -> int:
    return TEAM_IDS.get(name, abs(hash(name)) % 90000 + 10000)

def _team_name_from_id(team_id: int) -> str:
    return ID_TO_NAME.get(team_id, "")

def _tsdb_id_from_int(league_id: int) -> Optional[str]:
    for cfg in LEAGUES.values():
        if cfg["tsdb_id"] and int(cfg["tsdb_id"]) == league_id:
            return cfg["tsdb_id"]
    if league_id == 4966: return "4966"
    return None

def _same_team(a: str, b: str) -> bool:
    def norm(s): return re.sub(r"[^a-z]","", s.lower())
    na, nb = norm(a), norm(b)
    return na == nb or na in nb or nb in na


# ── Mock data (2SNL + fallback) ───────────────────────────────────────────

def _mock_matches(league_name: str = None) -> list[dict]:
    today = date.today()
    add = lambda d: (today + timedelta(days=d)).isoformat() + "T17:00:00+01:00"
    all_m = [
        {"id":"mock_01","date":add(1),"status":"NS","league":"PrvaLiga","league_id":4966,"round":"Demo","home_team":"NK Maribor","home_team_id":1601,"away_team":"FC Koper","away_team_id":2279,"venue":"Ljudski vrt"},
        {"id":"mock_02","date":add(2),"status":"NS","league":"PrvaLiga","league_id":4966,"round":"Demo","home_team":"NK Olimpija Ljubljana","home_team_id":1598,"away_team":"NK Celje","away_team_id":1594,"venue":"Stožice"},
        {"id":"mock_03","date":add(3),"status":"NS","league":"2SNL","league_id":219,"round":"Demo","home_team":"NK Nafta 1903","home_team_id":14372,"away_team":"NK Aluminij","away_team_id":10576,"venue":"Lendava"},
    ]
    if league_name: return [m for m in all_m if m["league"] == league_name]
    return all_m

def _mock_form(team_id: int = 0) -> dict:
    seed = team_id % 7
    pools = [["W","W","W","D","W","L","W"],["W","D","W","W","L","W","D"],["D","W","L","W","W","D","W"],["L","W","D","L","W","W","W"],["W","L","W","D","L","W","D"],["D","D","W","L","D","W","L"],["L","W","L","W","D","L","W"]]
    scored   = [2,1,3,1,2,0,2][seed:] + [2,1,3,1,2,0,2][:seed]
    conceded = [0,1,1,2,1,2,1][seed:] + [0,1,1,2,1,2,1][:seed]
    r = pools[seed]; n = 7
    return {"form":r,"form_string":"".join(r[-5:]),"avg_scored":round(sum(scored)/n,2),"avg_conceded":round(sum(conceded)/n,2),"clean_sheets":sum(1 for g in conceded if g==0),"btts_count":sum(1 for s,c in zip(scored,conceded) if s>0 and c>0),"games_analyzed":n}

def _mock_h2h() -> dict:
    return {"total_matches":8,"home_wins":4,"draws":2,"away_wins":2,"avg_goals_h2h":2.4,"btts_pct":62.5,"over25_pct":50.0}

def _mock_standings() -> list[dict]:
    teams = [("NK Olimpija Ljubljana",1598,58),("NK Maribor",1601,54),("NK Celje",1594,48),("FC Koper",2279,42),("NK Bravo",10203,36),("NS Mura",1600,30),("NK Domžale",1595,28),("NK Radomlje",14370,20)]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":27,"won":t[2]//3,"drawn":t[2]%3,"lost":27-t[2]//3-t[2]%3,"goals_for":55-i*4,"goals_against":18+i*4,"goal_diff":37-i*8,"form":"WWDWW" if i<3 else "WDLLL"} for i,t in enumerate(teams)]
