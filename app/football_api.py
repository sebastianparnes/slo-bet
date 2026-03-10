"""
Football data — Sofascore (no key required)
============================================
Sofascore expone endpoints JSON públicos para fixtures, forma y tabla.
Soporta las 13 ligas configuradas en el sistema.

Fallback a datos mock cuando Sofascore no responde o bloquea desde Railway.
"""

import httpx
import re
import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

# ── Sofascore tournament IDs ───────────────────────────────────────────────
TOURNAMENT_IDS = {
    "PrvaLiga":        212,
    "2SNL":            532,
    "PrimeraDivision": 155,
    "PrimeraNacional": 703,
    "ChampionsLeague": 7,
    "PremierLeague":   17,
    "LaLiga":          8,
    "SerieA":          23,
    "Bundesliga":      35,
    "Ligue1":          34,
    "CroatiaHNL":      44,
    "SerbiaSuper":     64,
    "UruguayPrimera":  278,
}

SF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

# In-memory caches
_season_cache: dict[int, int] = {}
_fixture_cache: dict[str, tuple[float, list]] = {}
_h2h_event_cache: dict[tuple, int] = {}  # (home_id, away_id) -> sf_event_id
_h2h_duel_cache: dict[tuple, dict] = {}  # (home_id, away_id) -> teamDuel stats
FIXTURE_CACHE_TTL = 300  # 5 min


# ── Season discovery ───────────────────────────────────────────────────────

async def _get_season(tid: int) -> Optional[int]:
    """Get current season ID for a tournament from Sofascore."""
    if tid in _season_cache:
        return _season_cache[tid]
    async with httpx.AsyncClient(timeout=10, headers=SF_HEADERS) as client:
        try:
            r = await client.get(
                f"https://api.sofascore.com/api/v1/unique-tournament/{tid}/seasons"
            )
            if r.status_code != 200:
                return None
            seasons = r.json().get("seasons", [])
            if not seasons:
                return None
            sid = seasons[0]["id"]
            _season_cache[tid] = sid
            return sid
        except Exception as e:
            print(f"[SF] season tid={tid}: {e}")
            return None


# ── Fixtures ───────────────────────────────────────────────────────────────

async def _fetch_sf_fixtures(league: str, days_ahead: int = 7) -> list[dict]:
    """Fetch upcoming fixtures from Sofascore for a league."""
    cached = _fixture_cache.get(league)
    if cached and (time.time() - cached[0]) < FIXTURE_CACHE_TTL:
        return cached[1]

    tid = TOURNAMENT_IDS.get(league)
    if not tid:
        return []

    sid = await _get_season(tid)
    if not sid:
        print(f"[SF] no season for {league} tid={tid}")
        return []

    now_ts = datetime.now(timezone.utc).timestamp()
    cutoff_ts = now_ts + days_ahead * 86400
    results = []

    async with httpx.AsyncClient(timeout=12, headers=SF_HEADERS) as client:
        for page in range(3):
            try:
                url = (f"https://api.sofascore.com/api/v1/unique-tournament/{tid}"
                       f"/season/{sid}/events/next/{page}")
                r = await client.get(url)
                if r.status_code != 200:
                    break
                events = r.json().get("events", [])
                if not events:
                    break
                for e in events:
                    ts = e.get("startTimestamp", 0)
                    if ts > cutoff_ts:
                        break
                    if ts >= now_ts - 3600:  # include matches starting within last hour
                        results.append(e)
            except Exception as ex:
                print(f"[SF] fixtures {league} p{page}: {ex}")
                break

    if results:
        _fixture_cache[league] = (time.time(), results)
    return results


# ── Team form ─────────────────────────────────────────────────────────────

async def _fetch_sf_form(team_id: int, last_n: int = 7) -> list[dict]:
    """Fetch last N results for a team."""
    async with httpx.AsyncClient(timeout=10, headers=SF_HEADERS) as client:
        try:
            r = await client.get(
                f"https://api.sofascore.com/api/v1/team/{team_id}/events/last/0"
            )
            if r.status_code != 200:
                return []
            events = r.json().get("events", [])
            # /events/last/0 returns oldest first — take last last_n for most recent
            return events[-last_n:]
        except Exception as e:
            print(f"[SF] form team={team_id}: {e}")
            return []


# ── H2H ───────────────────────────────────────────────────────────────────

async def _fetch_sf_h2h(home_id: int, away_id: int) -> list[dict]:
    """H2H via /event/{event_id}/h2h — event_id comes from fixture cache."""
    async with httpx.AsyncClient(timeout=10, headers=SF_HEADERS) as client:
        try:
            event_id = (_h2h_event_cache.get((home_id, away_id))
                        or _h2h_event_cache.get((away_id, home_id)))
            if not event_id:
                # Search fixture cache
                for _ts, fixtures in _fixture_cache.values():
                    for e in fixtures:
                        ht = e.get("homeTeam") or {}
                        at = e.get("awayTeam") or {}
                        if {ht.get("id"), at.get("id")} == {home_id, away_id}:
                            event_id = e.get("id")
                            _h2h_event_cache[(home_id, away_id)] = event_id
                            break
                    if event_id:
                        break
            if not event_id:
                print(f"[SF] h2h: no event_id found for {home_id} vs {away_id}")
                return []
            r = await client.get(f"https://api.sofascore.com/api/v1/event/{event_id}/h2h")
            if r.status_code != 200:
                return []
            data = r.json()
            events = data.get("previousEvents", data.get("events", []))
            if events:
                return events
            # Sofascore sometimes returns teamDuel stats instead of event list
            # Store teamDuel in a side-cache for the analysis engine
            td = data.get("teamDuel")
            if td:
                _h2h_duel_cache[(home_id, away_id)] = td
            return []
        except Exception as e:
            print(f"[SF] h2h {home_id} vs {away_id}: {e}")
            return []


# ── Standings ──────────────────────────────────────────────────────────────

async def _fetch_sf_standings(league: str) -> list[dict]:
    tid = TOURNAMENT_IDS.get(league)
    if not tid:
        return []
    sid = await _get_season(tid)
    if not sid:
        return []
    async with httpx.AsyncClient(timeout=10, headers=SF_HEADERS) as client:
        try:
            r = await client.get(
                f"https://api.sofascore.com/api/v1/unique-tournament/{tid}/season/{sid}/standings/total"
            )
            if r.status_code != 200:
                return []
            standings = r.json().get("standings", [])
            return standings[0].get("rows", []) if standings else []
        except Exception as e:
            print(f"[SF] standings {league}: {e}")
            return []


# ── Parsers ────────────────────────────────────────────────────────────────

def _same_team(a: str, b: str) -> bool:
    def n(s): return re.sub(r"[^a-z]", "", s.lower())
    na, nb = n(a), n(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)


def _parse_fixture(e: dict, league: str) -> dict:
    ts = e.get("startTimestamp", 0)
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00") if ts else ""
    ht = e.get("homeTeam", {})
    at = e.get("awayTeam", {})
    # Store event_id so H2H lookup can find it
    eid = e.get("id")
    if eid and ht.get("id") and at.get("id"):
        _h2h_event_cache[(ht["id"], at["id"])] = eid
    return {
        "id":            str(e.get("id", "")),
        "match_id":      str(e.get("id", "")),
        "date":          dt,
        "match_date":    dt,
        "status":        e.get("status", {}).get("type", "notstarted"),
        "league":        league,
        "home_team":     ht.get("name", ""),
        "home_team_id":  ht.get("id", 0),
        "away_team":     at.get("name", ""),
        "away_team_id":  at.get("id", 0),
        "venue":         (e.get("venue") or {}).get("name", ""),
        "round":         (e.get("roundInfo") or {}).get("name", ""),
        "_sf_home_id":   ht.get("id", 0),
        "_sf_away_id":   at.get("id", 0),
    }


def _parse_form_events(events: list[dict], team_sf_id: int) -> Optional[dict]:
    results, scored, conceded, recent_matches = [], [], [], []

    def _score_val(s: dict):
        """Try all known Sofascore score fields."""
        for k in ("current", "display", "normaltime", "period2", "extra"):
            v = s.get(k)
            if v is not None:
                try: return int(v)
                except: pass
        return None

    def _team_name(t: dict) -> str:
        """Try all known name fields."""
        return (t.get("name") or t.get("shortName") or t.get("nameCode") or "").strip()

    for e in events:
        status = (e.get("status") or {}).get("type", "")
        if status in ("canceled", "postponed", "notstarted", "inprogress"):
            continue
        if status != "finished":
            continue

        ht = e.get("homeTeam", {})
        at = e.get("awayTeam", {})
        home_id = ht.get("id")
        hs = e.get("homeScore", {})
        as_ = e.get("awayScore", {})
        hg = _score_val(hs)
        ag = _score_val(as_)
        if hg is None or ag is None:
            continue

        is_home = (home_id == team_sf_id)
        tg = hg if is_home else ag
        og = ag if is_home else hg

        if tg > og:    res = "W"
        elif tg == og: res = "D"
        else:          res = "L"

        results.append(res)
        scored.append(tg)
        conceded.append(og)

        ts = e.get("startTimestamp", 0)
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m") if ts else ""
        recent_matches.append({
            "home":   _team_name(ht),
            "away":   _team_name(at),
            "score":  f"{hg}-{ag}",
            "date":   date_str,
            "result": res,
        })

    if not results:
        return None

    n = len(results)
    return {
        "form":           results,
        "form_string":    "".join(results),  # oldest→newest, last 5 are most recent
        "avg_scored":     round(sum(scored) / n, 2),
        "avg_conceded":   round(sum(conceded) / n, 2),
        "clean_sheets":   sum(1 for g in conceded if g == 0),
        "btts_count":     sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed": n,
        "recent_matches": list(reversed(recent_matches)),  # newest first for display
    }


def _parse_h2h_events(events: list[dict], home_name: str) -> dict:
    if not events:
        return _mock_h2h()
    hw = aw = draws = btts = 0
    goals_list, recent = [], []
    for e in events[-10:]:
        ht = e.get("homeTeam", {})
        at = e.get("awayTeam", {})
        hs = e.get("homeScore", {})
        as_ = e.get("awayScore", {})
        hg = hs.get("current", hs.get("display"))
        ag = as_.get("current", as_.get("display"))
        if hg is None or ag is None:
            continue
        hg, ag = int(hg), int(ag)
        total = hg + ag
        goals_list.append(total)
        hn = ht.get("name", "")
        is_home = _same_team(hn, home_name) if home_name else True
        if hg > ag:
            if is_home: hw += 1
            else: aw += 1
        elif ag > hg:
            if is_home: aw += 1
            else: hw += 1
        else:
            draws += 1
        if hg > 0 and ag > 0:
            btts += 1
        ts = e.get("startTimestamp", 0)
        date_str = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%d/%m/%y") if ts else ""
        recent.append({"home": hn, "away": at.get("name",""), "score": f"{hg}-{ag}", "date": date_str})

    n = len(goals_list)
    if n == 0:
        return _mock_h2h()
    return {
        "total_matches":  n,
        "home_wins":      hw,
        "draws":          draws,
        "away_wins":      aw,
        "avg_goals_h2h":  round(sum(goals_list) / n, 2),
        "btts_pct":       round(btts / n * 100, 1),
        "over25_pct":     round(sum(1 for g in goals_list if g > 2) / n * 100, 1),
        "recent":         list(reversed(recent)),
    }


def _parse_standings_rows(rows: list[dict]) -> list[dict]:
    result = []
    for i, row in enumerate(rows):
        team = row.get("team", {})
        form_str = ""
        for r in (row.get("lastXGames") or {}).get("form", []):
            if r == "win":    form_str += "W"
            elif r == "draw": form_str += "D"
            elif r == "loss": form_str += "L"
        result.append({
            "rank":          row.get("position", i + 1),
            "team_id":       team.get("id", 0),
            "team_name":     team.get("name", ""),
            "points":        row.get("points", 0),
            "played":        row.get("matches", 0),
            "won":           row.get("wins", 0),
            "drawn":         row.get("draws", 0),
            "lost":          row.get("losses", 0),
            "goals_for":     row.get("scoresFor", 0),
            "goals_against": row.get("scoresAgainst", 0),
            "goal_diff":     row.get("scoresFor", 0) - row.get("scoresAgainst", 0),
            "form":          form_str,
        })
    return result


# ── Public API ─────────────────────────────────────────────────────────────

async def fetch_upcoming_matches(days_ahead: int = 7, leagues: list = None) -> list[dict]:
    target = leagues or list(TOURNAMENT_IDS.keys())
    tasks = [_fetch_sf_fixtures(lg, days_ahead) for lg in target]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_matches = []
    for lg, res in zip(target, results):
        if isinstance(res, Exception) or not res:
            print(f"[SF] {lg}: mock fallback")
            all_matches.extend(_mock_matches(lg))
        else:
            print(f"[SF] {lg}: {len(res)} fixtures")
            all_matches.extend([_parse_fixture(e, lg) for e in res])

    return sorted(all_matches, key=lambda x: x.get("date", ""))


async def fetch_team_form(team_id: int, league_id: int = 0, last_n: int = 7) -> dict:
    """Fetch team form using Sofascore team ID."""
    if not team_id:
        return _mock_form(0)
    events = await _fetch_sf_form(team_id, last_n=last_n)
    parsed = _parse_form_events(events, team_id)
    if parsed:
        return parsed
    return _mock_form(team_id)


async def fetch_team_form_for_event(team_id: int, league: str = "", last_n: int = 7) -> dict:
    """Alias for backward compatibility."""
    return await fetch_team_form(team_id, last_n=last_n)


async def fetch_h2h(home_id: int, away_id: int) -> dict:
    if not home_id or not away_id:
        return _mock_h2h()
    events = await _fetch_sf_h2h(home_id, away_id)
    if events:
        return _parse_h2h_events(events, "")
    # Fallback: use teamDuel stats if available
    td = (_h2h_duel_cache.get((home_id, away_id))
          or _h2h_duel_cache.get((away_id, home_id)))
    if td:
        return _parse_team_duel(td)
    return _mock_h2h()


async def fetch_standings(league_id_or_name) -> list[dict]:
    # Resolve league name
    if isinstance(league_id_or_name, str):
        league = league_id_or_name
    else:
        _id_map = {218: "PrvaLiga", 219: "2SNL", 212: "PrvaLiga"}
        league = _id_map.get(league_id_or_name, "PrvaLiga")

    rows = await _fetch_sf_standings(league)
    if rows:
        return _parse_standings_rows(rows)
    return _mock_standings(league)


# ── Mock data ──────────────────────────────────────────────────────────────

_MOCK_FIXTURES: dict[str, list] = {
    "PrvaLiga": [
        {"id":"p1","home_team":"NK Maribor","home_team_id":1601,"away_team":"NS Mura","away_team_id":1600},
        {"id":"p2","home_team":"NK Olimpija Ljubljana","home_team_id":1598,"away_team":"NK Celje","away_team_id":1594},
        {"id":"p3","home_team":"FC Koper","home_team_id":2279,"away_team":"NK Bravo","away_team_id":10203},
        {"id":"p4","home_team":"NK Aluminij","home_team_id":10576,"away_team":"NK Radomlje","away_team_id":14370},
    ],
    "2SNL": [
        {"id":"s1","home_team":"NK Nafta 1903","home_team_id":14372,"away_team":"NK Krka","away_team_id":88008},
        {"id":"s2","home_team":"NK Triglav","home_team_id":88004,"away_team":"NK Rudar","away_team_id":88002},
    ],
    "PrimeraDivision": [
        {"id":"arg1","home_team":"Boca Juniors","home_team_id":26124,"away_team":"River Plate","away_team_id":26195},
        {"id":"arg2","home_team":"Racing Club","home_team_id":26185,"away_team":"Independiente","away_team_id":26153},
    ],
    "PrimeraNacional": [
        {"id":"nac1","home_team":"Almirante Brown","home_team_id":26200,"away_team":"San Telmo","away_team_id":26201},
    ],
    "ChampionsLeague": [
        {"id":"cl1","home_team":"Real Madrid","home_team_id":2829,"away_team":"Bayern Munich","away_team_id":2672},
        {"id":"cl2","home_team":"Manchester City","home_team_id":17586,"away_team":"PSG","away_team_id":1644},
    ],
    "PremierLeague": [
        {"id":"pl1","home_team":"Arsenal","home_team_id":19,"away_team":"Liverpool","away_team_id":44},
        {"id":"pl2","home_team":"Manchester City","home_team_id":17586,"away_team":"Chelsea","away_team_id":38},
    ],
    "LaLiga": [
        {"id":"ll1","home_team":"Real Madrid","home_team_id":2829,"away_team":"FC Barcelona","away_team_id":2817},
        {"id":"ll2","home_team":"Atletico Madrid","home_team_id":2836,"away_team":"Sevilla","away_team_id":2833},
    ],
    "SerieA": [
        {"id":"sa1","home_team":"Inter Milan","home_team_id":2697,"away_team":"AC Milan","away_team_id":2692},
        {"id":"sa2","home_team":"Juventus","home_team_id":2699,"away_team":"Napoli","away_team_id":2714},
    ],
    "Bundesliga": [
        {"id":"bl1","home_team":"Bayern Munich","home_team_id":2672,"away_team":"Borussia Dortmund","away_team_id":2673},
        {"id":"bl2","home_team":"Bayer Leverkusen","home_team_id":2681,"away_team":"RB Leipzig","away_team_id":35975},
    ],
    "Ligue1": [
        {"id":"l1","home_team":"PSG","home_team_id":1644,"away_team":"Olympique Marseille","away_team_id":1641},
        {"id":"l2","home_team":"AS Monaco","home_team_id":1638,"away_team":"Lyon","away_team_id":1643},
    ],
    "CroatiaHNL": [
        {"id":"cr1","home_team":"Dinamo Zagreb","home_team_id":1674,"away_team":"Hajduk Split","away_team_id":1681},
    ],
    "SerbiaSuper": [
        {"id":"sr1","home_team":"Red Star Belgrade","home_team_id":2482,"away_team":"Partizan","away_team_id":2483},
    ],
    "UruguayPrimera": [
        {"id":"uy1","home_team":"Peñarol","home_team_id":2595,"away_team":"Nacional","away_team_id":2596},
    ],
}

_MOCK_FORM_DATA: dict[int, dict] = {
    1598:  {"form":["W","W","D","W","W","W","D"],"sc":[3,2,1,2,3,1,2],"cc":[0,1,1,0,1,0,1]},
    1601:  {"form":["W","D","W","L","W","W","W"],"sc":[2,1,2,0,1,2,2],"cc":[0,1,0,2,0,1,1]},
    1594:  {"form":["W","W","W","D","L","W","W"],"sc":[2,3,1,1,0,2,1],"cc":[0,0,0,1,1,1,0]},
    2279:  {"form":["W","D","L","W","W","D","W"],"sc":[1,1,0,2,2,1,1],"cc":[0,1,2,0,1,1,0]},
    10203: {"form":["D","W","L","D","W","W","L"],"sc":[1,2,0,1,2,1,1],"cc":[1,0,1,1,0,2,2]},
    10576: {"form":["W","L","D","W","L","W","D"],"sc":[1,0,1,2,0,1,1],"cc":[0,2,1,1,2,0,1]},
    1600:  {"form":["L","D","W","L","D","W","L"],"sc":[0,1,2,1,0,1,0],"cc":[1,1,0,2,1,0,2]},
    14370: {"form":["L","L","D","W","L","D","L"],"sc":[0,1,1,2,0,0,1],"cc":[2,3,1,1,2,1,2]},
    14372: {"form":["W","W","D","L","W","D","W"],"sc":[2,1,1,0,2,1,1],"cc":[0,0,1,1,0,1,0]},
    88008: {"form":["W","W","W","D","W","L","W"],"sc":[2,2,3,1,2,0,1],"cc":[0,1,0,1,0,2,0]},
    2829:  {"form":["W","W","W","D","W","W","L"],"sc":[3,2,3,1,2,2,1],"cc":[0,1,0,1,0,1,2]},
    2817:  {"form":["W","D","W","W","L","W","W"],"sc":[3,1,2,2,1,3,2],"cc":[0,1,0,1,2,0,1]},
    2697:  {"form":["W","W","W","W","D","W","W"],"sc":[2,3,2,3,1,2,2],"cc":[0,0,1,0,1,0,1]},
    2699:  {"form":["W","D","W","L","W","W","D"],"sc":[2,1,2,0,2,1,1],"cc":[0,1,0,2,0,1,1]},
    2672:  {"form":["W","W","D","W","W","L","W"],"sc":[4,3,1,2,3,1,2],"cc":[0,0,1,0,0,2,1]},
    2673:  {"form":["D","W","L","W","W","D","L"],"sc":[1,2,0,2,1,1,0],"cc":[1,1,2,0,1,1,2]},
    19:    {"form":["W","W","D","W","W","L","W"],"sc":[2,3,1,2,2,0,2],"cc":[0,0,1,0,1,2,1]},
    44:    {"form":["W","W","W","D","W","W","D"],"sc":[3,2,2,1,3,2,1],"cc":[0,0,1,1,0,0,1]},
    1644:  {"form":["W","W","W","D","W","W","W"],"sc":[3,4,2,1,3,2,3],"cc":[0,1,0,1,0,0,1]},
    1674:  {"form":["W","W","D","W","L","W","W"],"sc":[2,3,1,2,0,3,2],"cc":[0,0,1,0,2,1,0]},
    2482:  {"form":["W","W","W","D","W","L","W"],"sc":[3,2,2,1,2,0,2],"cc":[0,1,0,1,0,2,1]},
    2595:  {"form":["W","D","W","W","L","W","D"],"sc":[2,1,2,3,0,2,1],"cc":[0,1,0,0,2,0,1]},
    26124: {"form":["W","W","D","L","W","W","D"],"sc":[2,3,1,0,2,1,1],"cc":[0,0,1,2,1,0,1]},
    26195: {"form":["W","W","W","D","W","L","W"],"sc":[3,2,2,1,3,0,2],"cc":[0,1,0,1,0,2,1]},
}


def _mock_form(team_id: int = 0) -> dict:
    data = _MOCK_FORM_DATA.get(team_id)
    if not data:
        seed = team_id % 7
        pools = [
            ["W","W","W","D","W","L","W"],["W","D","W","W","L","W","D"],
            ["D","W","L","W","W","D","W"],["L","W","D","L","W","W","W"],
            ["W","L","W","D","L","W","D"],["D","D","W","L","D","W","L"],
            ["L","W","L","W","D","L","W"],
        ]
        r  = pools[seed]
        sc = ([2,1,3,1,2,0,2][seed:] + [2,1,3,1,2,0,2][:seed])[:7]
        cc = ([0,1,1,2,1,2,1][seed:] + [0,1,1,2,1,2,1][:seed])[:7]
        data = {"form": r, "sc": sc, "cc": cc}

    r, sc, cc = data["form"], data.get("sc", [1]*7), data.get("cc", [1]*7)
    n = len(r)
    recent = [
        {"home": "—", "away": "—",
         "score": f"{sc[i] if i<len(sc) else 1}-{cc[i] if i<len(cc) else 1}",
         "date": "—", "result": res}
        for i, res in enumerate(r)
    ]
    return {
        "form":           r,
        "form_string":    "".join(r[:5]),
        "avg_scored":     round(sum(sc) / n, 2),
        "avg_conceded":   round(sum(cc) / n, 2),
        "clean_sheets":   sum(1 for g in cc if g == 0),
        "btts_count":     sum(1 for s, c in zip(sc, cc) if s > 0 and c > 0),
        "games_analyzed": n,
        "recent_matches": recent,
    }


def _parse_team_duel(td: dict) -> dict:
    """Parse Sofascore teamDuel stats into our H2H format."""
    # teamDuel has: homeWins, awayWins, draws, homeGoals, awayGoals etc.
    hw    = td.get("homeWins", 0)
    aw    = td.get("awayWins", 0)
    draws = td.get("draws", 0)
    n     = hw + aw + draws
    if n == 0:
        return _mock_h2h()
    hg = td.get("homeGoals", 0)
    ag = td.get("awayGoals", 0)
    total_goals = hg + ag
    avg_goals = round(total_goals / n, 2) if n else 0.0
    # btts and over25 not directly available — estimate from avg goals
    btts_pct  = round(min(avg_goals / 4 * 100, 75), 1)
    over25_pct = round(min(avg_goals / 3.5 * 100, 80), 1)
    return {
        "total_matches":  n,
        "home_wins":      hw,
        "draws":          draws,
        "away_wins":      aw,
        "avg_goals_h2h":  avg_goals,
        "btts_pct":       btts_pct,
        "over25_pct":     over25_pct,
        "recent":         [],  # no individual events available via teamDuel
    }


def _mock_h2h() -> dict:
    return {
        "total_matches": 0, "home_wins": 0, "draws": 0, "away_wins": 0,
        "avg_goals_h2h": 0.0, "btts_pct": 0.0, "over25_pct": 0.0, "recent": [],
    }


def _mock_standings(league: str = "PrvaLiga") -> list[dict]:
    _data = {
        "PrvaLiga": [
            ("NK Olimpija Ljubljana",1598,52),("NK Celje",1594,45),("NK Maribor",1601,42),
            ("FC Koper",2279,38),("NK Bravo",10203,30),("NK Aluminij",10576,27),
            ("NS Mura",1600,24),("NK Radomlje",14370,18),
        ],
        "2SNL": [
            ("NK Nafta 1903",14372,48),("NK Krka",88008,44),
            ("NK Triglav",88004,40),("ND Slovan Ljubljana",99996,37),
        ],
        "PrimeraDivision": [
            ("Boca Juniors",26124,55),("River Plate",26195,52),
            ("Racing Club",26185,48),("Independiente",26153,44),
        ],
        "ChampionsLeague": [
            ("Real Madrid",2829,18),("Bayern Munich",2672,15),
            ("Manchester City",17586,15),("PSG",1644,13),
        ],
        "PremierLeague": [
            ("Liverpool",44,65),("Arsenal",19,58),
            ("Chelsea",38,52),("Manchester City",17586,50),
        ],
        "LaLiga": [
            ("FC Barcelona",2817,60),("Real Madrid",2829,57),
            ("Atletico Madrid",2836,52),("Athletic Bilbao",2825,48),
        ],
        "SerieA": [
            ("Napoli",2714,55),("Inter Milan",2697,54),
            ("Juventus",2699,50),("AC Milan",2692,47),
        ],
        "Bundesliga": [
            ("Bayern Munich",2672,58),("Bayer Leverkusen",2681,52),
            ("RB Leipzig",35975,46),("Borussia Dortmund",2673,43),
        ],
        "Ligue1": [
            ("PSG",1644,62),("AS Monaco",1638,52),
            ("Lyon",1643,46),("Olympique Marseille",1641,44),
        ],
        "CroatiaHNL": [
            ("Dinamo Zagreb",1674,55),("Hajduk Split",1681,48),
        ],
        "SerbiaSuper": [
            ("Red Star Belgrade",2482,58),("Partizan",2483,48),
        ],
        "UruguayPrimera": [
            ("Peñarol",2595,52),("Nacional",2596,48),
        ],
    }
    teams = _data.get(league, _data["PrvaLiga"])
    return [
        {
            "rank": i+1, "team_id": t[1], "team_name": t[0],
            "points": t[2], "played": 25,
            "won": t[2]//3, "drawn": t[2]%3, "lost": 25-t[2]//3-t[2]%3,
            "goals_for": 45-i*4, "goals_against": 15+i*4, "goal_diff": 30-i*8,
            "form": "WWDWW" if i<3 else ("WDLWL" if i<6 else "LDLLL"),
        }
        for i, t in enumerate(teams)
    ]


def _mock_matches(league: str = None) -> list[dict]:
    from datetime import date, timedelta
    leagues = [league] if league else list(_MOCK_FIXTURES.keys())
    result = []
    for lg in leagues:
        for i, m in enumerate(_MOCK_FIXTURES.get(lg, [])):
            base = date.today() + timedelta(days=i+1)
            result.append({
                **m,
                "match_id":    m["id"],
                "date":        f"{base.isoformat()}T18:00:00+00:00",
                "match_date":  f"{base.isoformat()}T18:00:00+00:00",
                "status":      "notstarted",
                "league":      lg,
                "round":       "—",
                "venue":       "",
                "_sf_home_id": m["home_team_id"],
                "_sf_away_id": m["away_team_id"],
            })
    return result
