"""
Football data via Sofascore API (works from Railway)
=====================================================
SLO: PrvaLiga (tid=212), 2SNL (tid=532)
ARG: Primera División (tid=155), Primera Nacional (tid=703)

Flashscore bloqueaba desde Railway. Sofascore funciona directo.
"""

import asyncio
import httpx
import re
from datetime import datetime, date, timedelta

# ── Sofascore config ───────────────────────────────────────────────────────
SF_BASE = "https://www.sofascore.com/api/v1"
SF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
}

# Tournament IDs en Sofascore
TOURNAMENT_IDS = {
    # Slovenia
    "PrvaLiga":        212,
    "2SNL":            532,
    # Argentina
    "PrimeraDivision": 155,
    "PrimeraNacional": 703,
    # Europe
    "LaLiga":          8,
    "PremierLeague":   17,
    "SerieA":          23,
    "Bundesliga":      35,
    "Ligue1":          34,
    "ChampionsLeague": 7,
    # Balkans
    "CroatiaHNL":      44,
    "SerbiaSuper":     64,
    # South America extra
    "UruguayPrimera":  278,
}

LEAGUES = {
    # Slovenia
    "PrvaLiga":        {"tid": 212, "country": "SVN", "home_rate": 0.44},
    "2SNL":            {"tid": 532, "country": "SVN", "home_rate": 0.41},
    # Argentina
    "PrimeraDivision": {"tid": 155, "country": "ARG", "home_rate": 0.46},
    "PrimeraNacional": {"tid": 703, "country": "ARG", "home_rate": 0.43},
    # Europe — same Sofascore API, just different tournament IDs
    "LaLiga":          {"tid": 8,   "country": "ESP", "home_rate": 0.46},
    "PremierLeague":   {"tid": 17,  "country": "ENG", "home_rate": 0.46},
    "SerieA":          {"tid": 23,  "country": "ITA", "home_rate": 0.44},
    "Bundesliga":      {"tid": 35,  "country": "GER", "home_rate": 0.45},
    "Ligue1":          {"tid": 34,  "country": "FRA", "home_rate": 0.44},
    "ChampionsLeague": {"tid": 7,   "country": "EUR", "home_rate": 0.42},
    # Balkans
    "CroatiaHNL":      {"tid": 44,  "country": "CRO", "home_rate": 0.46},
    "SerbiaSuper":     {"tid": 64,  "country": "SRB", "home_rate": 0.45},
    # South America extra
    "UruguayPrimera":  {"tid": 278, "country": "URU", "home_rate": 0.47},
}

# ── Team ID maps ───────────────────────────────────────────────────────────
TEAM_IDS = {
    # PrvaLiga
    "NK Olimpija Ljubljana": 1598, "NK Maribor": 1601, "NK Celje": 1594,
    "FC Koper": 2279, "NK Koper": 2279, "NK Bravo": 10203, "NS Mura": 1600,
    "NK Mura": 1600, "NK Domžale": 1595, "NK Radomlje": 14370,
    "NK Primorje Ajdovščina": 99991, "NK Nafta 1903": 14372,
    "NK Aluminij": 10576, "FC Drava Ptuj": 10578, "NK Rogaška": 99992,
    "ND Gorica": 99993,
    # 2SNL
    "NK Krka": 88008, "NK Triglav": 88004, "ND Slovan Ljubljana": 99996,
    "Krško Posavje": 88006, "ND Beltinci": 99997, "NK Ankaran": 14371,
    "NK Rudar": 88002, "NK Jadran Dekani": 88007, "NK Grosuplje": 88005,
    "Tabor Sežana": 88001, "NK Bilje": 88003, "NK Dravinja": 99998,
    "NK Bistrica": 99999, "NK Ilirija": 99994, "NK Jesenice": 99995,
    "NK Žalec": 88009,
    # ARG Primera División
    "River Plate": 2678, "Boca Juniors": 2675, "Racing Club": 2682,
    "Independiente": 2681, "San Lorenzo": 2679, "Huracán": 2691,
    "Vélez Sársfield": 2680, "Lanús": 2688, "Banfield": 2692,
    "Estudiantes": 2683, "Gimnasia LP": 2685, "Colón": 2696,
    "Unión": 2697, "Talleres": 2693, "Belgrano": 2694,
    "Instituto": 2695, "Godoy Cruz": 2686, "Defensa y Justicia": 2698,
    "Platense": 2699, "Tigre": 2687, "Rosario Central": 2684,
    "Newell's Old Boys": 2690, "Atlético Tucumán": 2700, "Central Córdoba": 2701,
    "Sarmiento": 2702, "Argentinos Juniors": 2689,
}
ARG_TEAM_IDS = {k: v for k, v in TEAM_IDS.items() if v >= 2675 and v <= 2702}
ID_TO_NAME = {v: k for k, v in TEAM_IDS.items()}

def _team_id(name: str) -> int:
    if name in TEAM_IDS: return TEAM_IDS[name]
    for k, v in TEAM_IDS.items():
        if _same_team(k, name): return v
    return abs(hash(name)) % 90000 + 10000

def _team_name_from_id(tid: int) -> str:
    return ID_TO_NAME.get(tid, "")

def _same_team(a: str, b: str) -> bool:
    def n(s): return re.sub(r"[^a-z]", "", s.lower())
    na, nb = n(a), n(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)

def _norm_name(name: str) -> str:
    """Only normalize known SLO/ARG team names. European teams pass through as-is."""
    SLO_MAP = {
        "olimpija": "NK Olimpija Ljubljana", "maribor": "NK Maribor",
        "celje": "NK Celje", "koper": "FC Koper", "bravo": "NK Bravo",
        "mura": "NS Mura", "domzale": "NK Domžale", "domžale": "NK Domžale",
        "radomlje": "NK Radomlje", "primorje": "NK Primorje Ajdovščina",
        "nafta": "NK Nafta 1903", "aluminij": "NK Aluminij",
        "gorica": "ND Gorica", "rogaska": "NK Rogaška", "rogaška": "NK Rogaška",
        "triglav": "NK Triglav Kranj", "krsko": "NK Krško Posavje",
        "grosuplje": "NK Grosuplje", "jadran": "NK Jadran Dekani",
    }
    key = re.sub(r"[^a-záéíóúüñ]", "", name.lower().strip())
    for k, v in SLO_MAP.items():
        if k in key:
            return v
    return name.strip()


# ── Sofascore helpers ──────────────────────────────────────────────────────

async def _sf_get(client: httpx.AsyncClient, path: str) -> dict:
    """GET from Sofascore, return parsed JSON or {}."""
    try:
        r = await client.get(f"{SF_BASE}{path}")
        if r.status_code == 200:
            return r.json()
    except Exception as e:
        print(f"[Sofascore] {path} error: {e}")
    return {}


async def _get_current_season(client: httpx.AsyncClient, tid: int) -> int | None:
    """Return the current season ID for a tournament."""
    data = await _sf_get(client, f"/unique-tournament/{tid}/seasons")
    seasons = data.get("seasons", [])
    if not seasons:
        return None
    # First season is always the most recent
    return seasons[0]["id"]


# ── Public: upcoming fixtures ──────────────────────────────────────────────

async def fetch_upcoming_matches(leagues=None, days_ahead: int = 14) -> list[dict]:
    league_keys = leagues if leagues else ["PrvaLiga", "2SNL"]
    today   = date.today()
    cutoff  = today + timedelta(days=days_ahead)
    all_matches = []

    async with httpx.AsyncClient(timeout=20, headers=SF_HEADERS) as client:
        for league_name in league_keys:
            cfg = LEAGUES.get(league_name)
            if not cfg:
                continue
            tid = cfg["tid"]
            sid = await _get_current_season(client, tid)
            if not sid:
                print(f"[Sofascore] {league_name}: no season found, using mock")
                all_matches.extend(_mock_matches(league_name))
                continue

            found = []
            for page in range(0, 4):
                data = await _sf_get(client, f"/unique-tournament/{tid}/season/{sid}/events/next/{page}")
                events = data.get("events", [])
                if not events:
                    break
                for ev in events:
                    try:
                        ts = ev.get("startTimestamp", 0)
                        dt = datetime.fromtimestamp(ts)
                        if dt.date() > cutoff:
                            break
                        if dt.date() < today:
                            continue
                        home = ev["homeTeam"]["name"]
                        away = ev["awayTeam"]["name"]
                        home_n = _norm_name(home)
                        away_n = _norm_name(away)
                        found.append({
                            "id":           str(ev["id"]),
                            "sofascore_id": str(ev["id"]),
                            "date":         dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                            "status":       "NS",
                            "league":       league_name,
                            "league_id":    tid,
                            "round":        ev.get("roundInfo", {}).get("round", ""),
                            "home_team":    home_n,
                            "home_team_id": ev["homeTeam"]["id"],
                            "away_team":    away_n,
                            "away_team_id": ev["awayTeam"]["id"],
                            "venue":        ev.get("venue", {}).get("name", "") if ev.get("venue") else "",
                        })
                    except Exception as e:
                        continue

            if found:
                print(f"[Sofascore] {league_name}: {len(found)} fixtures")
                all_matches.extend(found)
            else:
                # Only fall back to hardcoded SLO fixtures if it's a SLO league
                cfg2 = LEAGUES.get(league_name, {})
                if cfg2.get("country") == "SVN":
                    print(f"[Sofascore] {league_name}: no upcoming, using SLO hardcoded")
                    from datetime import date as _d
                    today_s = _d.today().isoformat()
                    hc = [m for m in _get_hardcoded_fixtures()
                          if m["league"] == league_name and m["date"][:10] >= today_s]
                    all_matches.extend(hc)
                else:
                    print(f"[Sofascore] {league_name}: no upcoming fixtures (non-SLO, no fallback)")

    return sorted(all_matches, key=lambda x: x["date"])


# ── Public: past results ───────────────────────────────────────────────────

async def fetch_past_results(league_id=218, last_n: int = 40) -> list[dict]:
    """Fetch recent completed matches from Sofascore."""
    # Map int league_id to name
    id_to_name = {v["tid"]: k for k, v in LEAGUES.items()}
    league_name = id_to_name.get(league_id, "PrvaLiga")
    if isinstance(league_id, str):
        league_name = league_id
    cfg = LEAGUES.get(league_name, LEAGUES["PrvaLiga"])
    tid = cfg["tid"]
    SEASON_START = "2025-07-01"
    results = []

    async with httpx.AsyncClient(timeout=20, headers=SF_HEADERS) as client:
        sid = await _get_current_season(client, tid)
        if not sid:
            return []
        for page in range(0, 6):
            data = await _sf_get(client, f"/unique-tournament/{tid}/season/{sid}/events/last/{page}")
            events = data.get("events", [])
            if not events:
                break
            for ev in reversed(events):  # Sofascore returns newest first, reverse per page
                try:
                    ts = ev.get("startTimestamp", 0)
                    dt = datetime.fromtimestamp(ts)
                    match_date = dt.strftime("%Y-%m-%d")
                    if match_date < SEASON_START:
                        continue
                    hs = ev.get("homeScore", {})
                    as_ = ev.get("awayScore", {})
                    hg = hs.get("current", hs.get("normaltime"))
                    ag = as_.get("current", as_.get("normaltime"))
                    if hg is None or ag is None:
                        continue
                    home = _norm_name(ev["homeTeam"]["name"])
                    away = _norm_name(ev["awayTeam"]["name"])
                    results.append({
                        "id":         str(ev["id"]),
                        "date":       match_date,
                        "home_team":  home,
                        "away_team":  away,
                        "home_team_id": ev["homeTeam"]["id"],
                        "away_team_id": ev["awayTeam"]["id"],
                        "home_goals": int(hg),
                        "away_goals": int(ag),
                        "status":     "FT",
                    })
                except:
                    continue
            if len(results) >= last_n:
                break

    # Sort by date descending (most recent first) for form calculation
    results.sort(key=lambda x: x["date"], reverse=True)
    return results[:last_n]


# ── Public: team form ──────────────────────────────────────────────────────

async def fetch_team_form(team_id: int, league_id=218, last_n: int = 7) -> dict:
    if isinstance(league_id, str):
        league_id = TOURNAMENT_IDS.get(league_id, 218)

    past = await fetch_past_results(league_id, last_n=60)
    team_name = _team_name_from_id(team_id) or ""

    results, scored, conceded, recent_matches = [], [], [], []
    for m in past:
        is_home = (m.get("home_team_id") == team_id) or (team_name and _same_team(m["home_team"], team_name))
        is_away = (m.get("away_team_id") == team_id) or (team_name and _same_team(m["away_team"], team_name))
        if not is_home and not is_away:
            continue
        tg = m["home_goals"] if is_home else m["away_goals"]
        og = m["away_goals"] if is_home else m["home_goals"]
        scored.append(tg)
        conceded.append(og)
        res = "G" if tg > og else ("E" if tg == og else "P")
        results.append(res)
        recent_matches.append({
            "result":   res,
            "home":     m["home_team"],
            "away":     m["away_team"],
            "score":    f"{m['home_goals']}-{m['away_goals']}",
            "date":     m["date"][5:],
            "was_home": is_home,
        })
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
        "recent_matches": recent_matches,
        "source":         "Sofascore",
    }


# ── Public: H2H ───────────────────────────────────────────────────────────

async def fetch_h2h(event_id=None, home_team=None, away_team=None,
                    home_team_id: int = 0, away_team_id: int = 0) -> dict:
    """Flexible H2H — try Sofascore event H2H first, fallback to past results scan."""
    # Resolve IDs
    if isinstance(event_id, int) and isinstance(home_team, int):
        # Old-style call: fetch_h2h(home_id, away_id)
        home_id, away_id = event_id, home_team
        home_name = _team_name_from_id(home_id)
        away_name = _team_name_from_id(away_id)
        sf_event_id = None
    else:
        home_id   = home_team_id or _team_id(str(home_team or ""))
        away_id   = away_team_id or _team_id(str(away_team or ""))
        home_name = str(home_team or _team_name_from_id(home_id))
        away_name = str(away_team or _team_name_from_id(away_id))
        sf_event_id = str(event_id) if event_id else None

    # Try Sofascore event H2H endpoint
    if sf_event_id:
        async with httpx.AsyncClient(timeout=15, headers=SF_HEADERS) as client:
            data = await _sf_get(client, f"/event/{sf_event_id}/h2h/events")
            events = data.get("events", [])
            if events:
                return _parse_h2h_events(events, home_name, away_name)

    # Fallback: scan past results
    past = await fetch_past_results(218, last_n=100)
    hw = aw = draws = btts = over25 = 0
    goals, recent = [], []
    for m in past:
        ih = _same_team(m["home_team"], home_name) and _same_team(m["away_team"], away_name)
        ia = _same_team(m["home_team"], away_name) and _same_team(m["away_team"], home_name)
        if not ih and not ia:
            continue
        hg, ag = m["home_goals"], m["away_goals"]
        total = hg + ag
        goals.append(total)
        if total > 2: over25 += 1
        if hg > 0 and ag > 0: btts += 1
        if hg > ag:
            hw += (1 if ih else 0); aw += (1 if ia else 0)
        elif ag > hg:
            aw += (1 if ih else 0); hw += (1 if ia else 0)
        else:
            draws += 1
        recent.append({"home": m["home_team"], "away": m["away_team"],
                       "score": f"{hg}-{ag}", "date": m["date"][5:]})

    if not goals:
        return _mock_h2h()
    n = len(goals)
    return {
        "total_matches": n, "home_wins": hw, "draws": draws, "away_wins": aw,
        "avg_goals_h2h": round(sum(goals) / n, 2),
        "btts_pct": round(btts / n * 100, 1),
        "over25_pct": round(over25 / n * 100, 1),
        "recent": recent[:5], "source": "Sofascore/scan",
    }


def _parse_h2h_events(events: list, home_name: str, away_name: str) -> dict:
    hw = aw = draws = btts = over25 = 0
    goals, recent = [], []
    SEASON_START = "2024-07-01"  # H2H can include previous seasons
    for ev in events:
        try:
            ts = ev.get("startTimestamp", 0)
            match_date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if match_date < SEASON_START:
                continue
            hs = ev.get("homeScore", {}); as_ = ev.get("awayScore", {})
            hg = hs.get("current", hs.get("normaltime"))
            ag = as_.get("current", as_.get("normaltime"))
            if hg is None or ag is None: continue
            hg, ag = int(hg), int(ag)
            total = hg + ag
            goals.append(total)
            if total > 2: over25 += 1
            if hg > 0 and ag > 0: btts += 1
            h = ev["homeTeam"]["name"]; a = ev["awayTeam"]["name"]
            ih = _same_team(h, home_name)
            if hg > ag: hw += (1 if ih else 0); aw += (0 if ih else 1)
            elif ag > hg: aw += (1 if ih else 0); hw += (0 if ih else 1)
            else: draws += 1
            recent.append({"home": h, "away": a, "score": f"{hg}-{ag}",
                           "date": match_date[5:]})
        except: continue
    if not goals: return _mock_h2h()
    n = len(goals)
    return {
        "total_matches": n, "home_wins": hw, "draws": draws, "away_wins": aw,
        "avg_goals_h2h": round(sum(goals) / n, 2),
        "btts_pct": round(btts / n * 100, 1),
        "over25_pct": round(over25 / n * 100, 1),
        "recent": recent[:5], "source": "Sofascore",
    }


# ── Public: standings ──────────────────────────────────────────────────────

async def fetch_standings(league_id=218) -> list[dict]:
    if isinstance(league_id, str):
        league_id = TOURNAMENT_IDS.get(league_id, 218)

    id_to_name = {v["tid"]: k for k, v in LEAGUES.items()}
    league_name = id_to_name.get(league_id, "PrvaLiga")
    cfg = LEAGUES.get(league_name, LEAGUES["PrvaLiga"])
    tid = cfg["tid"]

    async with httpx.AsyncClient(timeout=15, headers=SF_HEADERS) as client:
        sid = await _get_current_season(client, tid)
        if not sid:
            return _mock_standings() if "SLO" in cfg.get("country","") else _mock_standings_arg()
        data = await _sf_get(client, f"/unique-tournament/{tid}/season/{sid}/standings/total")
        rows = data.get("standings", [{}])[0].get("rows", []) if data.get("standings") else []
        if not rows:
            return _mock_standings() if league_id in (212, 532) else _mock_standings_arg()
        result = []
        for i, row in enumerate(rows):
            team = row.get("team", {})
            name = _norm_name(team.get("name", ""))
            result.append({
                "rank":          row.get("position", i + 1),
                "team_id":       team.get("id", _team_id(name)),
                "team_name":     name,
                "points":        row.get("points", 0),
                "played":        row.get("matches", 0),
                "won":           row.get("wins", 0),
                "drawn":         row.get("draws", 0),
                "lost":          row.get("losses", 0),
                "goals_for":     row.get("scoresFor", 0),
                "goals_against": row.get("scoresAgainst", 0),
                "goal_diff":     row.get("scoresFor", 0) - row.get("scoresAgainst", 0),
                "form":          "",
            })
        return result


# ── Compatibility: fetch_team_form_for_event ───────────────────────────────

async def fetch_team_form_for_event(event_id: str, is_home: bool = True) -> dict:
    """Returns None so caller falls back to fetch_team_form(team_id, league)."""
    return None


# ── Hardcoded fixtures (fallback when Sofascore returns nothing) ────────────

def _get_hardcoded_fixtures() -> list[dict]:
    return [
        {"id":"p26_01","date":"2026-03-07T20:00:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"26","home_team":"NS Mura","home_team_id":1600,"away_team":"NK Primorje Ajdovščina","away_team_id":99991,"venue":"Fazanerija"},
        {"id":"p26_02","date":"2026-03-08T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"26","home_team":"NK Olimpija Ljubljana","home_team_id":1598,"away_team":"NK Maribor","away_team_id":1601,"venue":"Stožice"},
        {"id":"p26_03","date":"2026-03-08T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"26","home_team":"NK Aluminij","home_team_id":10576,"away_team":"FC Koper","away_team_id":2279,"venue":"Aluminij Stadium"},
        {"id":"p26_04","date":"2026-03-09T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"26","home_team":"NK Radomlje","home_team_id":14370,"away_team":"NK Bravo","away_team_id":10203,"venue":"Radomlje"},
        {"id":"p26_05","date":"2026-03-09T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"26","home_team":"NK Celje","home_team_id":1594,"away_team":"ND Gorica","away_team_id":99993,"venue":"Arena Z'dežele"},
        {"id":"p27_01","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"27","home_team":"NK Maribor","home_team_id":1601,"away_team":"NS Mura","away_team_id":1600,"venue":"Ljudski vrt"},
        {"id":"p27_02","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"27","home_team":"NK Primorje Ajdovščina","home_team_id":99991,"away_team":"NK Celje","away_team_id":1594,"venue":"Ajdovščina"},
        {"id":"p27_03","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"27","home_team":"FC Koper","home_team_id":2279,"away_team":"NK Olimpija Ljubljana","away_team_id":1598,"venue":"Bonifika"},
        {"id":"p27_04","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"27","home_team":"NK Bravo","home_team_id":10203,"away_team":"NK Aluminij","away_team_id":10576,"venue":"ZAK"},
        {"id":"p27_05","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"27","home_team":"ND Gorica","home_team_id":99993,"away_team":"NK Radomlje","away_team_id":14370,"venue":"Nova Gorica"},
    ]

def _mock_matches(league_name: str = None) -> list[dict]:
    from datetime import date as _date
    today = _date.today().isoformat()
    all_m = [m for m in _get_hardcoded_fixtures() if m["date"][:10] >= today]
    if not all_m:
        all_m = _get_hardcoded_fixtures()
    if league_name:
        filtered = [m for m in all_m if m["league"] == league_name]
        return filtered if filtered else all_m
    return all_m


# ── Mock data ──────────────────────────────────────────────────────────────

_TEAM_FORM_DATA = {
    1598: {"form":["G","G","E","G","G","G","E"],"sc":[3,2,1,2,3,1,2],"cc":[0,1,1,0,1,0,1]},
    1601: {"form":["G","E","G","P","G","G","G"],"sc":[2,1,2,0,1,2,2],"cc":[0,1,0,2,0,1,1]},
    1594: {"form":["G","G","G","E","P","G","G"],"sc":[2,3,1,1,0,2,1],"cc":[0,0,0,1,1,1,0]},
    2279: {"form":["G","E","P","G","G","E","G"],"sc":[1,1,0,2,2,1,1],"cc":[0,1,2,0,1,1,0]},
    10203:{"form":["E","G","P","E","G","G","P"],"sc":[1,2,0,1,2,1,1],"cc":[1,0,1,1,0,2,2]},
    10576:{"form":["G","P","E","G","P","G","E"],"sc":[1,0,1,2,0,1,1],"cc":[0,2,1,1,2,0,1]},
    1600: {"form":["P","E","G","P","E","G","P"],"sc":[0,1,2,1,0,1,0],"cc":[1,1,0,2,1,0,2]},
    14370:{"form":["P","P","E","G","P","E","P"],"sc":[0,1,1,2,0,0,1],"cc":[2,3,1,1,2,1,2]},
    99991:{"form":["E","G","P","E","G","P","E"],"sc":[1,2,0,1,1,0,0],"cc":[1,0,2,1,0,2,1]},
    99993:{"form":["P","E","G","P","P","G","E"],"sc":[0,1,1,0,1,2,1],"cc":[2,1,0,2,2,0,1]},
    14372:{"form":["G","G","E","P","G","E","G"],"sc":[2,1,1,0,2,1,1],"cc":[0,0,1,1,0,1,0]},
    1595: {"form":["G","G","P","G","E","G","P"],"sc":[2,3,0,1,1,2,0],"cc":[0,1,2,0,1,0,2]},
}

def _mock_form(team_id: int = 0) -> dict:
    data = _TEAM_FORM_DATA.get(team_id)
    if data:
        r, sc, cc = data["form"], data["sc"], data["cc"]
    else:
        seed = team_id % 7
        pools = [
            ["G","G","G","E","G","P","G"],["G","E","G","G","P","G","E"],
            ["E","G","P","G","G","E","G"],["P","G","E","P","G","G","G"],
            ["G","P","G","E","P","G","E"],["E","E","G","P","E","G","P"],
            ["P","G","P","G","E","P","G"],
        ]
        sc = [2,1,3,1,2,0,2][seed:] + [2,1,3,1,2,0,2][:seed]
        cc = [0,1,1,2,1,2,1][seed:] + [0,1,1,2,1,2,1][:seed]
        r  = pools[seed]
    n = len(r)
    recent = [{"result": res, "home": "Equipo A", "away": "Equipo B",
               "score": f"{s}-{c}", "date": "01-01", "was_home": i % 2 == 0}
              for i, (res, s, c) in enumerate(zip(r, sc, cc))]
    return {
        "form": r, "form_string": "".join(r[-5:]),
        "avg_scored": round(sum(sc)/n, 2), "avg_conceded": round(sum(cc)/n, 2),
        "clean_sheets": sum(1 for g in cc if g == 0),
        "btts_count": sum(1 for s, c in zip(sc, cc) if s > 0 and c > 0),
        "games_analyzed": n, "recent_matches": recent, "source": "mock",
    }

def _mock_h2h() -> dict:
    return {"total_matches": 0, "home_wins": 0, "draws": 0, "away_wins": 0,
            "avg_goals_h2h": 0, "btts_pct": 0, "over25_pct": 0, "recent": []}

def _mock_standings() -> list[dict]:
    teams = [
        ("NK Olimpija Ljubljana",1598,52),("NK Celje",1594,45),("NK Maribor",1601,42),
        ("FC Koper",2279,38),("NK Bravo",10203,30),("NK Aluminij",10576,27),
        ("NS Mura",1600,24),("NK Radomlje",14370,18),
        ("NK Primorje Ajdovščina",99991,15),("ND Gorica",99993,10),
    ]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":25,
             "won":t[2]//3,"drawn":t[2]%3,"lost":25-t[2]//3-t[2]%3,
             "goals_for":45-i*4,"goals_against":15+i*4,"goal_diff":30-i*8,"form":""}
            for i,t in enumerate(teams)]

def _mock_standings_arg() -> list[dict]:
    teams = [
        ("River Plate",2678,45),("Racing Club",2682,42),("Boca Juniors",2675,40),
        ("Independiente",2681,37),("Vélez Sársfield",2680,34),("San Lorenzo",2679,31),
        ("Estudiantes",2683,29),("Talleres",2693,27),("Godoy Cruz",2686,24),
        ("Belgrano",2694,22),
    ]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":20,
             "won":t[2]//3,"drawn":t[2]%3,"lost":20-t[2]//3-t[2]%3,
             "goals_for":30-i*3,"goals_against":10+i*3,"goal_diff":20-i*6,"form":""}
            for i,t in enumerate(teams)]

def _mock_standings_2snl() -> list[dict]:
    teams = [
        ("NK Nafta 1903",14372,48),("NK Krka",88008,44),("NK Triglav",88004,40),
        ("ND Slovan Ljubljana",99996,37),("Krško Posavje",88006,33),
        ("ND Beltinci",99997,30),("NK Ankaran",14371,26),("NK Rudar",88002,23),
    ]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":24,
             "won":t[2]//3,"drawn":t[2]%3,"lost":24-t[2]//3-t[2]%3,
             "goals_for":35-i*2,"goals_against":12+i*2,"goal_diff":23-i*4,"form":""}
            for i,t in enumerate(teams)]
