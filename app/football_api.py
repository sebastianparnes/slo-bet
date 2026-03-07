"""
Football data — Sofascore directo desde Railway
================================================
IDs de Sofascore:
  PrvaLiga         → tournament 212
  2SNL             → tournament 532   ← CORRECTO (era 394, error anterior)
  Liga Profesional → tournament 155
  Primera Nacional → tournament 703
"""

import httpx
import os
import re
from datetime import datetime, date, timedelta
from typing import Optional

WORKER_URL  = os.getenv("XBET_WORKER_URL", "").rstrip("/")
SS_BASE_URL = "https://www.sofascore.com/api/v1"

SS_HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer":         "https://www.sofascore.com/",
    "Origin":          "https://www.sofascore.com",
    "Cache-Control":   "no-cache",
    "Pragma":          "no-cache",
}

TOURNAMENT_IDS = {
    "PrvaLiga":        212,
    "2SNL":            532,
    "PrimeraDivision": 155,
    "PrimeraNacional": 703,
}

_season_cache: dict[int, int] = {}

_cache:        dict = {}
_cache_expiry: dict = {}

def _cget(k):
    if k in _cache and datetime.now() < _cache_expiry.get(k, datetime.min):
        return _cache[k]
    _cache.pop(k, None); _cache_expiry.pop(k, None)
    return None

def _cset(k, v, ttl=300):
    _cache[k] = v
    _cache_expiry[k] = datetime.now() + timedelta(seconds=ttl)

async def _ss(path: str, ttl: int = 300) -> Optional[dict]:
    """Llama directamente a Sofascore API desde Railway."""
    cached = _cget(path)
    if cached is not None:
        return cached
    url = f"{SS_BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=15, headers=SS_HEADERS, follow_redirects=True) as c:
            r = await c.get(url)
        if r.status_code != 200:
            print(f"[SS] {r.status_code} para {path}")
            return None
        data = r.json()
        _cset(path, data, ttl)
        return data
    except Exception as e:
        print(f"[SS] Error {path}: {e}")
        return None

async def _current_season(tournament_id: int) -> Optional[int]:
    if tournament_id in _season_cache:
        return _season_cache[tournament_id]
    data = await _ss(f"/unique-tournament/{tournament_id}/seasons", ttl=3600)
    if not data:
        return None
    seasons = data.get("seasons", [])
    if not seasons:
        return None
    season_id = seasons[0]["id"]
    _season_cache[tournament_id] = season_id
    return season_id

def _norm(s: str) -> str:
    return re.sub(r"[^a-záéíóúüñ]", "", (s or "").lower())

def _same(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)

async def fetch_upcoming_matches(league_names: list[str], days_ahead: int = 10) -> list[dict]:
    all_matches = []
    today  = date.today()
    cutoff = today + timedelta(days=days_ahead)

    for league in league_names:
        tid = TOURNAMENT_IDS.get(league)
        if not tid:
            continue
        sid = await _current_season(tid)
        if not sid:
            print(f"[SS] No season para {league}, usando mock")
            all_matches.extend(_mock_matches(league))
            continue

        matches_found = []
        for page in range(0, 3):
            data = await _ss(f"/unique-tournament/{tid}/season/{sid}/events/next/{page}", ttl=600)
            if not data:
                break
            events = data.get("events", [])
            if not events:
                break
            for ev in events:
                dt = _parse_event_date(ev)
                if not dt or not (today <= dt.date() <= cutoff):
                    continue
                matches_found.append(_event_to_match(ev, league))
            if events and _parse_event_date(events[0]) and _parse_event_date(events[0]).date() > cutoff:
                break

        if matches_found:
            print(f"[SS] {league}: {len(matches_found)} fixtures")
            all_matches.extend(matches_found)
        else:
            print(f"[SS] {league}: sin fixtures, usando mock")
            all_matches.extend(_mock_matches(league))

    return sorted(all_matches, key=lambda x: x["date"])


async def fetch_team_form(team_id: int, league_name: str, last_n: int = 7) -> dict:
    """
    Forma real desde Sofascore. Usa G/E/P (español).
    Incluye 'recent_matches' con detalle de cada partido para la UI.
    """
    cache_key = f"form_{team_id}_{league_name}"
    cached = _cget(cache_key)
    if cached is not None:
        return cached

    data = await _ss(f"/team/{team_id}/events/last/0", ttl=600)
    if not data:
        result = _mock_form_for(team_id, league_name)
        _cset(cache_key, result, 300)
        return result

    events = data.get("events", [])
    if not events:
        result = _mock_form_for(team_id, league_name)
        _cset(cache_key, result, 300)
        return result

    form_list, scored, conceded, recent_matches = [], [], [], []
    for ev in events:
        if ev.get("status", {}).get("type") != "finished":
            continue
        ht  = ev.get("homeTeam", {})
        at  = ev.get("awayTeam", {})
        hs  = ev.get("homeScore", {}).get("current")
        as_ = ev.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        is_home = ht.get("id") == team_id
        tg = hs if is_home else as_
        og = as_ if is_home else hs
        scored.append(tg)
        conceded.append(og)
        res = "G" if tg > og else ("E" if tg == og else "P")
        form_list.append(res)
        dt = _parse_event_date(ev)
        recent_matches.append({
            "result":   res,
            "home":     ht.get("name", ""),
            "away":     at.get("name", ""),
            "score":    f"{hs}\u2013{as_}",
            "date":     dt.strftime("%d/%m") if dt else "",
            "was_home": is_home,
        })
        if len(form_list) >= last_n:
            break

    if not form_list:
        result = _mock_form_for(team_id, league_name)
        _cset(cache_key, result, 300)
        return result

    n = len(form_list)
    result = {
        "form":           form_list,
        "form_string":    "".join(form_list[-5:]),
        "avg_scored":     round(sum(scored) / n, 2),
        "avg_conceded":   round(sum(conceded) / n, 2),
        "clean_sheets":   sum(1 for g in conceded if g == 0),
        "btts_count":     sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed": n,
        "recent_matches": recent_matches,
        "source":         "sofascore",
    }
    _cset(cache_key, result, 300)
    return result


async def fetch_team_form_for_event(event_id: int, is_home: bool) -> Optional[dict]:
    """Pregame-form: forma justo antes del partido. Convierte W/D/L → G/E/P."""
    data = await _ss(f"/event/{event_id}/pregame-form", ttl=3600)
    if not data:
        return None
    key       = "homeTeam" if is_home else "awayTeam"
    team_data = data.get(key, {})
    form_raw  = team_data.get("form", [])
    if not form_raw:
        return None
    _es = {"W": "G", "D": "E", "L": "P"}
    form_es = [_es.get(c, c) for c in form_raw]
    return {
        "form":           form_es,
        "form_string":    "".join(form_es[-5:]),
        "avg_rating":     team_data.get("avgRating"),
        "table_position": team_data.get("position"),
        "recent_matches": [],
        "source":         "sofascore_pregame",
    }


async def fetch_h2h(
    event_id:      Optional[int],
    home_team_name: str,
    away_team_name: str,
    home_team_id:   int = 0,
    away_team_id:   int = 0,
) -> dict:
    """
    H2H con fallback: event_id → historial del equipo local.
    Devuelve 'recent' con lista de enfrentamientos para la UI.
    """
    data = None

    if event_id:
        data = await _ss(f"/event/{event_id}/h2h/events", ttl=3600)

    if not data and home_team_id:
        team_data = await _ss(f"/team/{home_team_id}/events/last/0", ttl=600)
        if team_data:
            h2h_events = [
                ev for ev in team_data.get("events", [])
                if ev.get("status", {}).get("type") == "finished"
                and (
                    _same(ev.get("homeTeam", {}).get("name", ""), away_team_name) or
                    _same(ev.get("awayTeam", {}).get("name", ""), away_team_name)
                )
            ]
            if h2h_events:
                data = {"lastEvents": h2h_events}

    if not data:
        return _mock_h2h()

    all_events = (
        data.get("previousPrimaryUniqueTournamentEvents",   []) +
        data.get("previousSecondaryUniqueTournamentEvents", []) +
        data.get("lastEvents", [])
    )

    if not all_events:
        return _mock_h2h()

    hw = aw = draws = btts = 0
    goals    = []
    h2h_list = []

    for ev in all_events:
        if ev.get("status", {}).get("type") != "finished":
            continue
        ht_name = ev.get("homeTeam", {}).get("name", "")
        at_name = ev.get("awayTeam", {}).get("name", "")
        hs  = ev.get("homeScore", {}).get("current")
        as_ = ev.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        is_std = _same(ht_name, home_team_name)
        hg = hs if is_std else as_
        ag = as_ if is_std else hs
        goals.append(hg + ag)
        if hg > ag:   hw += 1
        elif ag > hg: aw += 1
        else:          draws += 1
        if hg > 0 and ag > 0:
            btts += 1
        dt = _parse_event_date(ev)
        h2h_list.append({
            "home":  ht_name,
            "away":  at_name,
            "score": f"{hs}\u2013{as_}",
            "date":  dt.strftime("%d/%m/%y") if dt else "",
        })

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
        "recent":        h2h_list[:8],
        "source":        "sofascore",
    }


async def fetch_standings(league_name: str) -> list[dict]:
    tid = TOURNAMENT_IDS.get(league_name)
    if not tid:
        return []
    sid = await _current_season(tid)
    if not sid:
        return _mock_standings(league_name)
    data = await _ss(f"/unique-tournament/{tid}/season/{sid}/standings/total", ttl=1800)
    if not data:
        return _mock_standings(league_name)
    rows = data.get("standings", [{}])[0].get("rows", [])
    if not rows:
        return _mock_standings(league_name)
    result = []
    for row in rows:
        team = row.get("team", {})
        result.append({
            "rank":          row.get("position", 0),
            "team_id":       team.get("id", 0),
            "team_name":     team.get("name", ""),
            "team_short":    team.get("shortName", ""),
            "points":        row.get("points", 0),
            "played":        row.get("matches", 0),
            "won":           row.get("wins", 0),
            "drawn":         row.get("draws", 0),
            "lost":          row.get("losses", 0),
            "goals_for":     row.get("scoresFor", 0),
            "goals_against": row.get("scoresAgainst", 0),
            "goal_diff":     row.get("scoresFor", 0) - row.get("scoresAgainst", 0),
            "form":          row.get("forms", ""),
        })
    return result

# ─── Parsers ──────────────────────────────────────────────────────────────────

def _parse_event_date(ev: dict) -> Optional[datetime]:
    ts = ev.get("startTimestamp")
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts))
    except:
        return None

def _event_to_match(ev: dict, league_name: str) -> dict:
    dt   = _parse_event_date(ev)
    lid  = TOURNAMENT_IDS.get(league_name, 0)
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    return {
        "id":           str(ev.get("id", "")),
        "sofascore_id": ev.get("id"),
        "date":         dt.isoformat() if dt else "",
        "status":       "NS",
        "league":       league_name,
        "league_id":    lid,
        "round":        ev.get("roundInfo", {}).get("nameEn", str(ev.get("roundInfo", {}).get("round", ""))),
        "home_team":    home.get("name", ""),
        "home_team_id": home.get("id", 0),
        "away_team":    away.get("name", ""),
        "away_team_id": away.get("id", 0),
        "venue":        ev.get("venue", {}).get("name", "") if ev.get("venue") else "",
    }

# ─── Mock fallbacks ───────────────────────────────────────────────────────────

def _mock_h2h() -> dict:
    """0 partidos → la UI no muestra sección H2H."""
    return {
        "total_matches": 0, "home_wins": 0, "draws": 0, "away_wins": 0,
        "avg_goals_h2h": 0, "btts_pct": 0.0, "over25_pct": 0.0,
        "recent": [], "source": "mock",
    }

def _mock_form_for(team_id: int, league: str) -> dict:
    return {
        "form": ["E", "G", "P", "E", "G"], "form_string": "EGPEG",
        "avg_scored": 1.2, "avg_conceded": 1.2,
        "clean_sheets": 1, "btts_count": 2, "games_analyzed": 5,
        "recent_matches": [], "source": "mock",
    }

def _mock_matches(league_name: str) -> list[dict]:
    today = date.today()
    lid   = TOURNAMENT_IDS.get(league_name, 0)
    fixtures = {
        "PrvaLiga":        [("NS Mura",1600,"NK Olimpija Ljubljana",1598),("NK Maribor",1601,"NK Celje",1594),("FC Koper",2279,"NK Bravo",10203)],
        "2SNL":            [("NK Nafta 1903",14372,"NK Krka",88008),("NK Triglav",88004,"ND Slovan Ljubljana",99996)],
        "PrimeraDivision": [("River Plate",3196,"Boca Juniors",3197),("Racing Club",3198,"Independiente",3199)],
        "PrimeraNacional": [("San Martín Tucumán",4001,"Quilmes",4003)],
    }
    result = []
    for i, (h,hid,a,aid) in enumerate(fixtures.get(league_name,[])):
        d = today + timedelta(days=(i%5)+1)
        result.append({
            "id":f"mock_{league_name[:3]}_{i}","sofascore_id":None,
            "date":f"{d.isoformat()}T20:00:00","status":"NS",
            "league":league_name,"league_id":lid,"round":"—",
            "home_team":h,"home_team_id":hid,"away_team":a,"away_team_id":aid,"venue":"",
        })
    return result

_MOCK_STANDINGS = {
    "PrvaLiga":        [("NK Olimpija Ljubljana",1598,52),("NK Celje",1594,45),("NK Maribor",1601,42),("FC Koper",2279,38),("NK Bravo",10203,30),("NK Aluminij",10576,27),("NS Mura",1600,24),("NK Radomlje",14370,18)],
    "2SNL":            [("NK Nafta 1903",14372,48),("NK Krka",88008,44),("NK Triglav",88004,40),("ND Slovan Ljubljana",99996,37)],
    "PrimeraDivision": [("River Plate",3196,55),("Racing Club",3198,48),("Boca Juniors",3197,45),("Vélez Sársfield",3202,42),("Talleres",3205,40),("Estudiantes LP",3203,37),("San Lorenzo",3200,35),("Defensa y Justicia",3208,33)],
    "PrimeraNacional": [("San Martín Tucumán",4001,50),("Almirante Brown",4005,44),("Chacarita",4009,40),("Deportivo Morón",4006,37)],
}

def _mock_standings(league_name: str) -> list[dict]:
    teams = _MOCK_STANDINGS.get(league_name, [])
    played = 25
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":played,"won":t[2]//3,"drawn":t[2]%3,"lost":played-t[2]//3-t[2]%3,"goals_for":40-i*2,"goals_against":12+i*2,"goal_diff":28-i*4,"form":""} for i,t in enumerate(teams)]
