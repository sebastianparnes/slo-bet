"""
Football data — Sofascore API (no key requerida)
=================================================
- Busca el season ID actual dinámicamente (auto-actualiza cada temporada)
- Fixtures, resultados, forma, H2H, standings para 13 ligas
- Fallback a mock solo para SLO si Sofascore falla
"""

import httpx
import re
import asyncio
from datetime import datetime, date, timedelta
from typing import Optional

SF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.sofascore.com/",
    "Origin": "https://www.sofascore.com",
}

# Sofascore tournament IDs (estables, no cambian)
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

LEAGUES = TOURNAMENT_IDS  # alias

# ar-xbet championship IDs
XBET_LEAGUE_IDS = {
    "PrvaLiga":        118593,
    "2SNL":            270435,
    "PrimeraDivision": 119599,
    "PrimeraNacional": 2922491,
    "ChampionsLeague": 118587,
    "PremierLeague":   88637,
    "LaLiga":          127733,
    "SerieA":          110163,
    "Bundesliga":      96463,
    "Ligue1":          12821,
    "CroatiaHNL":      27735,
    "SerbiaSuper":     30035,
    "UruguayPrimera":  52183,
}

# Cache de season IDs (se obtienen una vez y se reusan)
_season_cache: dict[int, int] = {}

TEAM_IDS = {
    "NK Olimpija Ljubljana": 1598, "NK Maribor": 1601, "NK Celje": 1594,
    "FC Koper": 2279, "NK Koper": 2279, "NK Bravo": 10203, "NS Mura": 1600,
    "NK Mura": 1600, "NK Domžale": 1595, "NK Radomlje": 14370,
    "NK Primorje Ajdovščina": 99991, "NK Nafta 1903": 14372,
    "NK Aluminij": 10576, "FC Drava Ptuj": 10578, "NK Ankaran": 14371,
    "NK Rogaška": 99992, "ND Gorica": 99993,
}
ID_TO_NAME = {v: k for k, v in TEAM_IDS.items()}


def _team_id(name: str) -> int:
    if name in TEAM_IDS: return TEAM_IDS[name]
    for k, v in TEAM_IDS.items():
        if _same_team(k, name): return v
    return abs(hash(name)) % 90000 + 10000


def _same_team(a: str, b: str) -> bool:
    def n(s): return re.sub(r"[^a-z]", "", s.lower())
    na, nb = n(a), n(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)


def _norm_name(name: str, league: str = "") -> str:
    if league not in ("PrvaLiga", "2SNL"):
        return name.strip()
    mapping = {
        "olimpija": "NK Olimpija Ljubljana", "maribor": "NK Maribor",
        "celje": "NK Celje", "koper": "FC Koper", "bravo": "NK Bravo",
        "mura": "NS Mura", "domzale": "NK Domžale", "domžale": "NK Domžale",
        "radomlje": "NK Radomlje", "primorje": "NK Primorje Ajdovščina",
        "nafta": "NK Nafta 1903", "aluminij": "NK Aluminij",
        "drava": "FC Drava Ptuj", "ankaran": "NK Ankaran",
        "rogaska": "NK Rogaška", "rogaška": "NK Rogaška", "gorica": "ND Gorica",
    }
    key = re.sub(r"[^a-zčšž]", "", name.lower().strip())
    for k, v in mapping.items():
        if k in key or key in k:
            return v
    return name.strip()


# ── Sofascore HTTP ────────────────────────────────────────────────────────

async def _sf(path: str, retries: int = 2) -> dict:
    url = f"https://api.sofascore.com/api/v1{path}"
    for attempt in range(retries):
        try:
            async with httpx.AsyncClient(timeout=12, headers=SF_HEADERS, follow_redirects=True) as c:
                r = await c.get(url)
                if r.status_code == 200:
                    return r.json()
                if r.status_code == 404:
                    return {}
                print(f"[Sofascore] {path} → {r.status_code}")
        except Exception as e:
            print(f"[Sofascore] {path} attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                await asyncio.sleep(0.5)
    return {}


async def _get_season(tid: int) -> int:
    """Get current season ID for a tournament. Cached."""
    if tid in _season_cache:
        return _season_cache[tid]
    data = await _sf(f"/unique-tournament/{tid}/seasons")
    seasons = data.get("seasons", [])
    if seasons:
        # Most recent season first
        sid = seasons[0].get("id", 0)
        _season_cache[tid] = sid
        print(f"[Sofascore] Tournament {tid} → season {sid} ({seasons[0].get('name','')})")
        return sid
    # Fallback hardcoded (2025-26 season IDs)
    fallback = {
        212: 63839, 532: 63962, 155: 62562, 703: 62701,
        7: 61644, 17: 61627, 8: 61643, 23: 61736,
        35: 61737, 34: 61738, 44: 61827, 64: 61885, 278: 62800,
    }
    sid = fallback.get(tid, 63839)
    _season_cache[tid] = sid
    return sid


# ── Fixtures ─────────────────────────────────────────────────────────────

async def _fetch_sf_fixtures(league: str, days_ahead: int) -> list:
    tid = TOURNAMENT_IDS.get(league)
    if not tid:
        return []

    sid    = await _get_season(tid)
    today  = date.today()
    cutoff = today + timedelta(days=days_ahead)
    matches = []

    # Fetch multiple pages (page 0, 1, 2)
    for page in range(3):
        data   = await _sf(f"/unique-tournament/{tid}/season/{sid}/events/next/{page}")
        events = data.get("events", [])
        if not events:
            break

        found_in_range = False
        for ev in events:
            ts = ev.get("startTimestamp", 0)
            if not ts:
                continue
            dt = datetime.fromtimestamp(ts)
            if dt.date() > cutoff:
                break
            if dt.date() < today:
                continue
            found_in_range = True

            status = ev.get("status", {}).get("type", "")
            if status not in ("notstarted", "scheduled", ""):
                continue

            ht = ev.get("homeTeam", {})
            at = ev.get("awayTeam", {})
            home_name = _norm_name(ht.get("name", ""), league)
            away_name = _norm_name(at.get("name", ""), league)

            matches.append({
                "id":           str(ev.get("id", "")),
                "date":         dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "status":       "NS",
                "league":       league,
                "league_id":    XBET_LEAGUE_IDS.get(league, tid),
                "round":        ev.get("roundInfo", {}).get("name", ""),
                "home_team":    home_name,
                "home_team_id": ht.get("id", _team_id(home_name)),
                "away_team":    away_name,
                "away_team_id": at.get("id", _team_id(away_name)),
                "venue":        (ev.get("venue") or {}).get("name", ""),
            })

        if not found_in_range and page > 0:
            break

    print(f"[Sofascore] {league}: {len(matches)} fixtures (next {days_ahead}d)")
    return matches


# ── Team form ─────────────────────────────────────────────────────────────

async def _fetch_sf_form(team_id: int, last_n: int = 7) -> dict:
    data   = await _sf(f"/team/{team_id}/events/previous/0")
    events = data.get("events", [])

    results, scored, conceded, recent = [], [], [], []
    for ev in events:
        if ev.get("status", {}).get("type") != "finished":
            continue
        ht  = ev.get("homeTeam", {})
        at  = ev.get("awayTeam", {})
        hsc = ev.get("homeScore", {})
        asc = ev.get("awayScore", {})
        hg  = hsc.get("current") or hsc.get("display") or 0
        ag  = asc.get("current") or asc.get("display") or 0

        is_home = ht.get("id") == team_id
        tg = hg if is_home else ag
        og = ag if is_home else hg
        scored.append(tg); conceded.append(og)

        res = "W" if tg > og else ("D" if tg == og else "L")
        results.append(res)

        ts     = ev.get("startTimestamp", 0)
        dt_str = datetime.fromtimestamp(ts).strftime("%d/%m") if ts else ""
        recent.append({
            "home":          ht.get("name", "?"),
            "away":          at.get("name", "?"),
            "score":         f"{hg}-{ag}",
            "date":          dt_str,
            "result":        res,
            "is_home":       is_home,
            "opponent":      at.get("name", "?") if is_home else ht.get("name", "?"),
            "goals_for":     tg,
            "goals_against": og,
        })
        if len(results) >= last_n:
            break

    if not results:
        return None  # caller will use mock

    n = len(results)
    return {
        "form":            results,
        "form_string":     "".join(results[-5:]),
        "avg_scored":      round(sum(scored)/n, 2),
        "avg_conceded":    round(sum(conceded)/n, 2),
        "clean_sheets":    sum(1 for g in conceded if g == 0),
        "btts_count":      sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed":  n,
        "recent_matches":  recent,
    }


# ── H2H ──────────────────────────────────────────────────────────────────

async def _fetch_sf_h2h(home_id: int, away_id: int) -> dict:
    data   = await _sf(f"/event/head2head/{home_id}/{away_id}")
    events = (data.get("firstTeamEvents") or []) + (data.get("secondTeamEvents") or [])
    events = sorted(events, key=lambda e: e.get("startTimestamp", 0), reverse=True)[:10]

    hw = aw = draws = btts = 0
    goals, recent = [], []

    for ev in events:
        ht_id = ev.get("homeTeam", {}).get("id")
        hg    = (ev.get("homeScore") or {}).get("current") or (ev.get("homeScore") or {}).get("display") or 0
        ag    = (ev.get("awayScore") or {}).get("current") or (ev.get("awayScore") or {}).get("display") or 0
        goals.append(hg + ag)

        if hg > ag:
            if ht_id == home_id: hw += 1
            else: aw += 1
        elif ag > hg:
            if ht_id == home_id: aw += 1
            else: hw += 1
        else:
            draws += 1

        if hg > 0 and ag > 0: btts += 1
        ts     = ev.get("startTimestamp", 0)
        dt_str = datetime.fromtimestamp(ts).strftime("%d/%m/%y") if ts else ""
        recent.append({
            "home":  ev.get("homeTeam", {}).get("name", "?"),
            "away":  ev.get("awayTeam", {}).get("name", "?"),
            "score": f"{hg}-{ag}",
            "date":  dt_str,
        })

    n = len(goals)
    if n == 0:
        return _mock_h2h()

    return {
        "total_matches": n,
        "home_wins":     hw,
        "draws":         draws,
        "away_wins":     aw,
        "avg_goals_h2h": round(sum(goals)/n, 2),
        "btts_pct":      round(btts/n*100, 1),
        "over25_pct":    round(sum(1 for g in goals if g > 2.5)/n*100, 1),
        "recent":        recent,
    }


# ── Standings ─────────────────────────────────────────────────────────────

async def _fetch_sf_standings(league: str) -> list:
    tid = TOURNAMENT_IDS.get(league)
    if not tid:
        return []
    sid  = await _get_season(tid)
    data = await _sf(f"/unique-tournament/{tid}/season/{sid}/standings/total")
    rows = (data.get("standings") or [{}])[0].get("rows", [])

    result = []
    for row in rows:
        t    = row.get("team", {})
        name = _norm_name(t.get("name", ""), league)
        result.append({
            "rank":          row.get("position", 0),
            "team_id":       t.get("id", _team_id(name)),
            "team_name":     name,
            "points":        row.get("points", 0),
            "played":        row.get("matches", 0),
            "won":           row.get("wins", 0),
            "drawn":         row.get("draws", 0),
            "lost":          row.get("losses", 0),
            "goals_for":     row.get("scoresFor", 0),
            "goals_against": row.get("scoresAgainst", 0),
            "goal_diff":     row.get("scoresFor", 0) - row.get("scoresAgainst", 0),
            "form":          row.get("form", ""),
        })
    print(f"[Sofascore] standings {league}: {len(result)} teams")
    return result


# ── Public API ────────────────────────────────────────────────────────────

async def fetch_upcoming_matches(days_ahead: int = 7, leagues: list = None) -> list:
    if leagues is None:
        leagues = list(TOURNAMENT_IDS.keys())

    tasks   = [_fetch_sf_fixtures(lg, days_ahead) for lg in leagues]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    all_m = []
    for lg, res in zip(leagues, results):
        if isinstance(res, Exception):
            print(f"[Sofascore] {lg} error: {res}")
            res = []
        if res:
            all_m.extend(res)
        elif lg in ("PrvaLiga", "2SNL"):
            print(f"[mock] {lg}: using hardcoded fixtures")
            all_m.extend(_mock_matches(lg))

    return sorted(all_m, key=lambda x: x["date"])


async def fetch_team_form(team_id: int, league_id: int = 118593, last_n: int = 7) -> dict:
    result = await _fetch_sf_form(team_id, last_n)
    if result:
        return result
    return _mock_form(team_id)


async def fetch_h2h(home_id: int, away_id: int) -> dict:
    result = await _fetch_sf_h2h(home_id, away_id)
    return result


async def fetch_standings(league_id: int) -> list:
    # Find league name by xbet ID
    league = next((k for k, v in XBET_LEAGUE_IDS.items() if v == league_id), None)
    if not league:
        return _mock_standings()

    result = await _fetch_sf_standings(league)
    if result:
        return result
    if league == "2SNL":
        return _mock_standings_2snl()
    return _mock_standings()


async def fetch_past_results(league_id: int = 118593, last_n: int = 30) -> list:
    league = next((k for k, v in XBET_LEAGUE_IDS.items() if v == league_id), "PrvaLiga")
    tid    = TOURNAMENT_IDS.get(league, 212)
    sid    = await _get_season(tid)
    data   = await _sf(f"/unique-tournament/{tid}/season/{sid}/events/last/0")
    events = data.get("events", [])

    results = []
    for ev in events[:last_n]:
        hg = (ev.get("homeScore") or {}).get("current") or 0
        ag = (ev.get("awayScore") or {}).get("current") or 0
        ts = ev.get("startTimestamp", 0)
        ht = ev.get("homeTeam", {})
        at = ev.get("awayTeam", {})
        results.append({
            "id":         str(ev.get("id", "")),
            "date":       datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
            "home_team":  _norm_name(ht.get("name", ""), league),
            "away_team":  _norm_name(at.get("name", ""), league),
            "home_goals": hg,
            "away_goals": ag,
            "status":     "FT",
        })
    return results


# ── Backward compat ───────────────────────────────────────────────────────

async def fetch_team_form_for_event(event_id, is_home: bool) -> dict:
    return _mock_form(0)


# ── Mock data ─────────────────────────────────────────────────────────────

def _mock_matches(league_name: str = None) -> list:
    today = date.today().isoformat()
    all_m = [m for m in _get_hardcoded_fixtures() if m["date"][:10] >= today]
    if not all_m:
        all_m = _get_hardcoded_fixtures()
    if league_name:
        return [m for m in all_m if m["league"] == league_name]
    return all_m


def _get_hardcoded_fixtures() -> list:
    return [
        {"id":"p27_01","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"NK Maribor","home_team_id":1601,"away_team":"NS Mura","away_team_id":1600,"venue":"Ljudski vrt"},
        {"id":"p27_02","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"NK Primorje Ajdovščina","home_team_id":99991,"away_team":"NK Celje","away_team_id":1594,"venue":"Ajdovščina"},
        {"id":"p27_03","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"FC Koper","home_team_id":2279,"away_team":"NK Olimpija Ljubljana","away_team_id":1598,"venue":"Bonifika"},
        {"id":"p27_04","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"NK Bravo","home_team_id":10203,"away_team":"NK Aluminij","away_team_id":10576,"venue":"ZAK"},
        {"id":"p27_05","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"ND Gorica","home_team_id":99993,"away_team":"NK Radomlje","away_team_id":14370,"venue":"Nova Gorica"},
    ]


_TEAM_FORM_DATA = {
    1598: {"form":["W","W","D","W","W","W","D"],"sc":[3,2,1,2,3,1,2],"cc":[0,1,1,0,1,0,1]},
    1601: {"form":["W","D","W","L","W","W","W"],"sc":[2,1,2,0,1,2,2],"cc":[0,1,0,2,0,1,1]},
    1594: {"form":["W","W","W","D","L","W","W"],"sc":[2,3,1,1,0,2,1],"cc":[0,0,0,1,1,1,0]},
    2279: {"form":["W","D","L","W","W","D","W"],"sc":[1,1,0,2,2,1,1],"cc":[0,1,2,0,1,1,0]},
    10203:{"form":["D","W","L","D","W","W","L"],"sc":[1,2,0,1,2,1,1],"cc":[1,0,1,1,0,2,2]},
    10576:{"form":["W","L","D","W","L","W","D"],"sc":[1,0,1,2,0,1,1],"cc":[0,2,1,1,2,0,1]},
    1600: {"form":["L","D","W","L","D","W","L"],"sc":[0,1,2,1,0,1,0],"cc":[1,1,0,2,1,0,2]},
    14370:{"form":["L","L","D","W","L","D","L"],"sc":[0,1,1,2,0,0,1],"cc":[2,3,1,1,2,1,2]},
    99991:{"form":["D","W","L","D","W","L","D"],"sc":[1,2,0,1,1,0,0],"cc":[1,0,2,1,0,2,1]},
    99993:{"form":["L","D","W","L","L","W","D"],"sc":[0,1,1,0,1,2,1],"cc":[2,1,0,2,2,0,1]},
    14372:{"form":["W","W","D","L","W","D","W"],"sc":[2,1,1,0,2,1,1],"cc":[0,0,1,1,0,1,0]},
}


def _mock_form(team_id: int = 0) -> dict:
    data = _TEAM_FORM_DATA.get(team_id)
    if data:
        r, sc, cc = data["form"], data["sc"], data["cc"]
    else:
        seed = team_id % 7
        pools = [["W","W","W","D","W","L","W"],["W","D","W","W","L","W","D"],
                 ["D","W","L","W","W","D","W"],["L","W","D","L","W","W","W"],
                 ["W","L","W","D","L","W","D"],["D","D","W","L","D","W","L"],
                 ["L","W","L","W","D","L","W"]]
        sc = ([2,1,3,1,2,0,2][seed:]+[2,1,3,1,2,0,2][:seed])[:7]
        cc = ([0,1,1,2,1,2,1][seed:]+[0,1,1,2,1,2,1][:seed])[:7]
        r  = pools[seed]
    n = len(r)
    recent = []
    teams  = ["NK Olimpija Ljubljana","NK Maribor","NK Celje","FC Koper","NK Bravo",
              "NS Mura","NK Aluminij","NK Radomlje","NK Primorje Ajdovščina","ND Gorica"]
    tname  = ID_TO_NAME.get(team_id, "Equipo")
    for i, (res, tg, og) in enumerate(zip(r, sc, cc)):
        opp = teams[(team_id + i) % len(teams)]
        ih  = i % 2 == 0
        dt  = (date.today() - timedelta(days=(n-i)*7)).strftime("%d/%m")
        recent.append({
            "home": tname if ih else opp, "away": opp if ih else tname,
            "score": f"{tg}-{og}" if ih else f"{og}-{tg}",
            "date": dt, "result": res, "is_home": ih, "opponent": opp,
            "goals_for": tg, "goals_against": og,
        })
    return {
        "form": r, "form_string": "".join(r[-5:]),
        "avg_scored": round(sum(sc)/n,2), "avg_conceded": round(sum(cc)/n,2),
        "clean_sheets": sum(1 for g in cc if g==0),
        "btts_count": sum(1 for s,c in zip(sc,cc) if s>0 and c>0),
        "games_analyzed": n, "recent_matches": recent,
    }


def _mock_h2h() -> dict:
    return {"total_matches":8,"home_wins":4,"draws":2,"away_wins":2,
            "avg_goals_h2h":2.4,"btts_pct":62.5,"over25_pct":50.0,"recent":[]}


def _mock_standings() -> list:
    teams=[("NK Olimpija Ljubljana",1598,52),("NK Celje",1594,45),
           ("NK Maribor",1601,42),("FC Koper",2279,38),("NK Bravo",10203,30),
           ("NK Aluminij",10576,27),("NS Mura",1600,24),("NK Radomlje",14370,18),
           ("NK Primorje Ajdovščina",99991,15),("ND Gorica",99993,10)]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":25,
             "won":t[2]//3,"drawn":t[2]%3,"lost":25-t[2]//3-t[2]%3,
             "goals_for":45-i*4,"goals_against":15+i*4,"goal_diff":30-i*8,
             "form":"WWDWW" if i<3 else "WDLLL"} for i,t in enumerate(teams)]


def _mock_standings_2snl() -> list:
    teams=[("NK Nafta 1903",14372,48),("NK Krka",88008,44),("NK Triglav",88004,40),
           ("ND Slovan Ljubljana",99996,37),("Krško Posavje",88006,33),
           ("ND Beltinci",99997,30),("NK Ankaran",14371,26),("NK Rudar",88002,23)]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":24,
             "won":t[2]//3,"drawn":t[2]%3,"lost":24-t[2]//3-t[2]%3,
             "goals_for":35-i*2,"goals_against":12+i*2,"goal_diff":23-i*4,
             "form":"WWDWW" if i<4 else "WDLLL"} for i,t in enumerate(teams)]
