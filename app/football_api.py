"""
Football data — Sofascore via Cloudflare Worker
================================================
Fuente única para SLO y ARG. Datos reales de forma, H2H, fixtures y tabla.

IDs de Sofascore (de la URL del sitio):
  PrvaLiga         → tournament 212
  2SNL             → tournament 394
  Liga Profesional → tournament 155
  Primera Nacional → tournament 703

Variable de entorno requerida (la misma que ya usás):
  XBET_WORKER_URL  → https://slo-bet-proxy.sebastianparnes26.workers.dev
"""

import httpx
import os
import re
from datetime import datetime, date, timedelta
from typing import Optional

WORKER_URL = os.getenv("XBET_WORKER_URL", "").rstrip("/")

# ─── IDs de Sofascore por liga ────────────────────────────────────────────────

TOURNAMENT_IDS = {
    "PrvaLiga":         212,
    "2SNL":             394,
    "PrimeraDivision":  155,
    "PrimeraNacional":  703,
}

# Se populan en runtime la primera vez que se consulta
_season_cache: dict[int, int] = {}   # tournament_id → current season_id

# ─── Headers para el worker ──────────────────────────────────────────────────

HEADERS = {
    "User-Agent": "slobet-api/3.0",
    "Accept":     "application/json",
}

# ─── Caché simple en memoria ─────────────────────────────────────────────────

from datetime import datetime, timedelta
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

# ─── HTTP helper ──────────────────────────────────────────────────────────────

async def _ss(path: str, ttl: int = 300) -> Optional[dict]:
    """Llama a /sofascore/<path> en el worker."""
    if not WORKER_URL:
        return None
    cached = _cget(path)
    if cached is not None:
        return cached
    url = f"{WORKER_URL}/sofascore{path}"
    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as c:
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

# ─── Season resolver ──────────────────────────────────────────────────────────

async def _current_season(tournament_id: int) -> Optional[int]:
    if tournament_id in _season_cache:
        return _season_cache[tournament_id]
    data = await _ss(f"/unique-tournament/{tournament_id}/seasons", ttl=3600)
    if not data:
        return None
    seasons = data.get("seasons", [])
    if not seasons:
        return None
    # El primer elemento siempre es la temporada más reciente en Sofascore
    season_id = seasons[0]["id"]
    _season_cache[tournament_id] = season_id
    return season_id

# ─── Normalización de nombres ─────────────────────────────────────────────────

def _norm(s: str) -> str:
    return re.sub(r"[^a-záéíóúüñ]", "", (s or "").lower())

def _same(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)

# ─── API pública ──────────────────────────────────────────────────────────────

async def fetch_upcoming_matches(league_names: list[str], days_ahead: int = 10) -> list[dict]:
    """
    Trae los próximos partidos de las ligas dadas.
    league_names: ["PrvaLiga", "2SNL"] o ["PrimeraDivision", "PrimeraNacional"]
    """
    all_matches = []
    today = date.today()
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

        # Sofascore: /unique-tournament/{tid}/season/{sid}/events/next/0
        # Devuelve los próximos partidos en pages de 10
        matches_found = []
        for page in range(0, 3):  # máximo 30 partidos hacia adelante
            data = await _ss(f"/unique-tournament/{tid}/season/{sid}/events/next/{page}", ttl=600)
            if not data:
                break
            events = data.get("events", [])
            if not events:
                break
            for ev in events:
                dt = _parse_event_date(ev)
                if not dt:
                    continue
                if not (today <= dt.date() <= cutoff):
                    continue
                matches_found.append(_event_to_match(ev, league))
            # Si el primer evento de la página ya está fuera del rango, parar
            if events and _parse_event_date(events[0]) and _parse_event_date(events[0]).date() > cutoff:
                break

        if matches_found:
            print(f"[SS] {league}: {len(matches_found)} fixtures")
            all_matches.extend(matches_found)
        else:
            print(f"[SS] {league}: sin fixtures, usando mock")
            all_matches.extend(_mock_matches(league))

    return sorted(all_matches, key=lambda x: x["date"])


async def fetch_past_results(league_name: str, last_n: int = 30) -> list[dict]:
    """Últimos N resultados de una liga."""
    tid = TOURNAMENT_IDS.get(league_name)
    if not tid:
        return []
    sid = await _current_season(tid)
    if not sid:
        return []

    results = []
    for page in range(0, 4):
        data = await _ss(f"/unique-tournament/{tid}/season/{sid}/events/last/{page}", ttl=600)
        if not data:
            break
        events = data.get("events", [])
        if not events:
            break
        for ev in events:
            if ev.get("status", {}).get("type") != "finished":
                continue
            results.append(_event_to_result(ev))
        if len(results) >= last_n:
            break

    return results[-last_n:]


async def fetch_team_form(team_id: int, league_name: str, last_n: int = 7) -> dict:
    """
    Forma real de un equipo desde Sofascore.
    Usa /team/{id}/events/last/0 que devuelve todos los partidos recientes
    del equipo, no solo de una liga.
    """
    cache_key = f"form_{team_id}_{league_name}"
    cached = _cget(cache_key)
    if cached is not None:
        return cached

    # Primero intentar con pregame-form de Sofascore si tenemos un event_id
    # (se llama desde analyze_match que ya tiene el evento)
    # Como fallback, scrapear partidos del equipo
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

    form_list, scored, conceded = [], [], []
    for ev in events:
        if ev.get("status", {}).get("type") != "finished":
            continue
        ht = ev.get("homeTeam", {})
        at = ev.get("awayTeam", {})
        hs = ev.get("homeScore", {}).get("current")
        as_ = ev.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue
        is_home = ht.get("id") == team_id
        tg = hs if is_home else as_
        og = as_ if is_home else hs
        scored.append(tg)
        conceded.append(og)
        form_list.append("W" if tg > og else ("D" if tg == og else "L"))
        if len(form_list) >= last_n:
            break

    if not form_list:
        result = _mock_form_for(team_id, league_name)
        _cset(cache_key, result, 300)
        return result

    n = len(form_list)
    result = {
        "form":          form_list,
        "form_string":   "".join(form_list[-5:]),
        "avg_scored":    round(sum(scored) / n, 2),
        "avg_conceded":  round(sum(conceded) / n, 2),
        "clean_sheets":  sum(1 for g in conceded if g == 0),
        "btts_count":    sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed": n,
        "source":        "sofascore",
    }
    _cset(cache_key, result, 300)
    return result


async def fetch_team_form_for_event(event_id: int, is_home: bool) -> Optional[dict]:
    """
    Usa el endpoint de pregame-form de Sofascore, que es el más preciso.
    Devuelve la forma justo antes de ese partido específico.
    """
    data = await _ss(f"/event/{event_id}/pregame-form", ttl=3600)
    if not data:
        return None
    key = "homeTeam" if is_home else "awayTeam"
    team_data = data.get(key, {})
    form_str = team_data.get("form", [])  # lista de "W","D","L"
    avg_rating = team_data.get("avgRating")
    position = team_data.get("position")

    if not form_str:
        return None
    return {
        "form":           list(form_str),
        "form_string":    "".join(form_str[-5:]),
        "avg_rating":     avg_rating,
        "table_position": position,
        "source":         "sofascore_pregame",
    }


async def fetch_h2h(event_id: int, home_team_name: str, away_team_name: str) -> dict:
    """
    H2H real desde Sofascore. Necesita el event_id del partido.
    """
    data = await _ss(f"/event/{event_id}/h2h/events", ttl=3600)
    if not data:
        return _mock_h2h()

    all_events = (
        data.get("previousPrimaryUniqueTournamentEvents", []) +
        data.get("previousSecondaryUniqueTournamentEvents", []) +
        data.get("lastEvents", [])
    )

    if not all_events:
        return _mock_h2h()

    hw = aw = draws = btts = 0
    goals = []
    for ev in all_events:
        if ev.get("status", {}).get("type") != "finished":
            continue
        ht = ev.get("homeTeam", {}).get("name", "")
        at = ev.get("awayTeam", {}).get("name", "")
        hs = ev.get("homeScore", {}).get("current")
        as_ = ev.get("awayScore", {}).get("current")
        if hs is None or as_ is None:
            continue

        # Determinar quién es home/away en el contexto del partido analizado
        is_standard = _same(ht, home_team_name)
        hg = hs if is_standard else as_
        ag = as_ if is_standard else hs

        goals.append(hg + ag)
        if hg > ag:   hw += 1
        elif ag > hg: aw += 1
        else:          draws += 1
        if hg > 0 and ag > 0:
            btts += 1

    if not goals:
        return _mock_h2h()

    n = len(goals)
    return {
        "total_matches":  n,
        "home_wins":      hw,
        "draws":          draws,
        "away_wins":      aw,
        "avg_goals_h2h":  round(sum(goals) / n, 2),
        "btts_pct":       round(btts / n * 100, 1),
        "over25_pct":     round(sum(1 for g in goals if g > 2.5) / n * 100, 1),
        "source":         "sofascore",
    }


async def fetch_standings(league_name: str) -> list[dict]:
    """Tabla de posiciones desde Sofascore."""
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
            "rank":           row.get("position", 0),
            "team_id":        team.get("id", 0),
            "team_name":      team.get("name", ""),
            "team_short":     team.get("shortName", ""),
            "points":         row.get("points", 0),
            "played":         row.get("matches", 0),
            "won":            row.get("wins", 0),
            "drawn":          row.get("draws", 0),
            "lost":           row.get("losses", 0),
            "goals_for":      row.get("scoresFor", 0),
            "goals_against":  row.get("scoresAgainst", 0),
            "goal_diff":      row.get("scoresFor", 0) - row.get("scoresAgainst", 0),
            "form":           row.get("forms", ""),
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
    dt = _parse_event_date(ev)
    lid = TOURNAMENT_IDS.get(league_name, 0)
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    tournament = ev.get("tournament", {})
    return {
        "id":            str(ev.get("id", "")),
        "sofascore_id":  ev.get("id"),
        "date":          dt.isoformat() if dt else "",
        "status":        "NS",
        "league":        league_name,
        "league_id":     lid,
        "round":         ev.get("roundInfo", {}).get("nameEn", ev.get("roundInfo", {}).get("round", "")),
        "home_team":     home.get("name", ""),
        "home_team_id":  home.get("id", 0),
        "away_team":     away.get("name", ""),
        "away_team_id":  away.get("id", 0),
        "venue":         ev.get("venue", {}).get("name", "") if ev.get("venue") else "",
    }

def _event_to_result(ev: dict) -> dict:
    dt = _parse_event_date(ev)
    home = ev.get("homeTeam", {})
    away = ev.get("awayTeam", {})
    hs = ev.get("homeScore", {})
    as_ = ev.get("awayScore", {})
    return {
        "id":          str(ev.get("id", "")),
        "date":        dt.strftime("%Y-%m-%d") if dt else "",
        "home_team":   home.get("name", ""),
        "home_team_id":home.get("id", 0),
        "away_team":   away.get("name", ""),
        "away_team_id":away.get("id", 0),
        "home_goals":  hs.get("current", 0),
        "away_goals":  as_.get("current", 0),
        "status":      "FT",
    }

# ─── Mock fallbacks (por si Sofascore falla) ──────────────────────────────────

def _mock_h2h() -> dict:
    return {
        "total_matches": 0, "home_wins": 0, "draws": 0, "away_wins": 0,
        "avg_goals_h2h": 2.3, "btts_pct": 50.0, "over25_pct": 45.0,
        "source": "mock",
    }

def _mock_form_for(team_id: int, league: str) -> dict:
    # Devuelve forma neutral — el motor de análisis lo marcará como datos parciales
    return {
        "form": ["D", "W", "L", "D", "W"],
        "form_string": "DWLDW",
        "avg_scored": 1.2,
        "avg_conceded": 1.2,
        "clean_sheets": 1,
        "btts_count": 2,
        "games_analyzed": 5,
        "source": "mock",
    }

def _mock_matches(league_name: str) -> list[dict]:
    """Fixtures hardcodeados mínimos como último recurso."""
    today = date.today()
    lid = TOURNAMENT_IDS.get(league_name, 0)

    slo_fixtures = [
        ("NS Mura", 1600, "NK Olimpija Ljubljana", 1598, "PrvaLiga"),
        ("NK Maribor", 1601, "NK Celje", 1594, "PrvaLiga"),
        ("FC Koper", 2279, "NK Bravo", 10203, "PrvaLiga"),
        ("NK Nafta 1903", 14372, "NK Krka", 88008, "2SNL"),
        ("NK Triglav", 88004, "ND Slovan Ljubljana", 99996, "2SNL"),
    ]
    arg_fixtures = [
        ("River Plate", 3196, "Boca Juniors", 3197, "PrimeraDivision"),
        ("Racing Club", 3198, "Independiente", 3199, "PrimeraDivision"),
        ("San Lorenzo", 3200, "Huracán", 3201, "PrimeraDivision"),
        ("San Martín Tucumán", 4001, "Quilmes", 4003, "PrimeraNacional"),
    ]

    all_f = slo_fixtures + arg_fixtures
    result = []
    for i, (h, hid, a, aid, lg) in enumerate(all_f):
        if lg != league_name:
            continue
        d = today + timedelta(days=(i % 5) + 1)
        result.append({
            "id": f"mock_{lg[:3]}_{i}",
            "sofascore_id": None,
            "date": f"{d.isoformat()}T20:00:00",
            "status": "NS", "league": lg, "league_id": lid,
            "round": "Fecha mock", "home_team": h, "home_team_id": hid,
            "away_team": a, "away_team_id": aid, "venue": "",
        })
    return result

# Mock standings mínimo
_MOCK_STANDINGS = {
    "PrvaLiga": [
        ("NK Olimpija Ljubljana", 1598, 52), ("NK Celje", 1594, 45), ("NK Maribor", 1601, 42),
        ("FC Koper", 2279, 38), ("NK Bravo", 10203, 30), ("NK Aluminij", 10576, 27),
        ("NS Mura", 1600, 24), ("NK Radomlje", 14370, 18), ("NK Primorje Ajdovščina", 99991, 15),
        ("ND Gorica", 99993, 10),
    ],
    "2SNL": [
        ("NK Nafta 1903", 14372, 48), ("NK Krka", 88008, 44), ("NK Triglav", 88004, 40),
        ("ND Slovan Ljubljana", 99996, 37),
    ],
    "PrimeraDivision": [
        ("River Plate", 3196, 55), ("Racing Club", 3198, 48), ("Boca Juniors", 3197, 45),
        ("Vélez Sársfield", 3202, 42), ("Talleres", 3205, 40), ("Estudiantes LP", 3203, 37),
        ("San Lorenzo", 3200, 35), ("Defensa y Justicia", 3208, 33), ("Lanús", 3204, 30),
        ("Godoy Cruz", 3207, 28), ("Atlético Tucumán", 3214, 26), ("Rosario Central", 3216, 25),
    ],
    "PrimeraNacional": [
        ("San Martín Tucumán", 4001, 50), ("Almirante Brown", 4005, 44), ("Chacarita", 4009, 40),
        ("Deportivo Morón", 4006, 37),
    ],
}

def _mock_standings(league_name: str) -> list[dict]:
    teams = _MOCK_STANDINGS.get(league_name, [])
    played = 25
    return [{
        "rank": i+1, "team_id": t[1], "team_name": t[0], "points": t[2],
        "played": played, "won": t[2]//3, "drawn": t[2]%3,
        "lost": played - t[2]//3 - t[2]%3,
        "goals_for": 40-i*2, "goals_against": 12+i*2, "goal_diff": 28-i*4,
        "form": "",
    } for i, t in enumerate(teams)]
