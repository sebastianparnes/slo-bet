"""
Football data — Sofascore (no key) + mock fallback
====================================================
Sofascore tiene datos reales para todas las ligas sin API key.
"""

import httpx
import re
from datetime import datetime, date, timedelta
from typing import Optional

SF_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, */*",
    "Referer": "https://www.sofascore.com/",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
}

# Sofascore tournament IDs
TOURNAMENT_IDS = {
    "PrvaLiga":        {"id": 212,  "season": 63839},
    "2SNL":            {"id": 532,  "season": 63962},
    "PrimeraDivision": {"id": 155,  "season": 62562},
    "PrimeraNacional": {"id": 703,  "season": 62701},
    "ChampionsLeague": {"id": 7,    "season": 61644},
    "PremierLeague":   {"id": 17,   "season": 61627},
    "LaLiga":          {"id": 8,    "season": 61643},
    "SerieA":          {"id": 23,   "season": 61736},
    "Bundesliga":      {"id": 35,   "season": 61737},
    "Ligue1":          {"id": 34,   "season": 61738},
    "CroatiaHNL":      {"id": 44,   "season": 61827},
    "SerbiaSuper":     {"id": 64,   "season": 61885},
    "UruguayPrimera":  {"id": 278,  "season": 62800},
}

LEAGUES = {k: v["id"] for k, v in TOURNAMENT_IDS.items()}

# SLO team IDs (for mock/form fallback)
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
    def n(s): return re.sub(r"[^a-z]","",s.lower())
    na, nb = n(a), n(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)

def _norm_name(name: str) -> str:
    mapping = {
        "olimpija": "NK Olimpija Ljubljana", "o.ljubljana": "NK Olimpija Ljubljana",
        "maribor": "NK Maribor", "celje": "NK Celje",
        "koper": "FC Koper", "bravo": "NK Bravo",
        "mura": "NS Mura", "domzale": "NK Domžale", "domžale": "NK Domžale",
        "radomlje": "NK Radomlje", "primorje": "NK Primorje Ajdovščina",
        "nafta": "NK Nafta 1903", "aluminij": "NK Aluminij",
        "drava": "FC Drava Ptuj", "ankaran": "NK Ankaran",
        "rogaska": "NK Rogaška", "rogaška": "NK Rogaška", "gorica": "ND Gorica",
    }
    key = re.sub(r"[^a-zčšž]","", name.lower().strip())
    for k, v in mapping.items():
        if k in key or key in k:
            return v
    return name.strip()


# ── Sofascore API ─────────────────────────────────────────────────────────

async def _sf_get(path: str) -> dict:
    async with httpx.AsyncClient(timeout=12, headers=SF_HEADERS) as c:
        r = await c.get(f"https://api.sofascore.com/api/v1{path}")
        if r.status_code == 200:
            return r.json()
        return {}


async def _fetch_sf_fixtures(league: str, days_ahead: int) -> list:
    cfg = TOURNAMENT_IDS.get(league)
    if not cfg:
        return []

    today  = date.today()
    cutoff = today + timedelta(days=days_ahead)
    tid    = cfg["id"]
    season = cfg["season"]

    try:
        # Get scheduled events for this season
        data = await _sf_get(f"/unique-tournament/{tid}/season/{season}/events/next/0")
        events = data.get("events", [])
        matches = []

        for ev in events:
            try:
                ts = ev.get("startTimestamp", 0)
                dt = datetime.fromtimestamp(ts)
                if not (today <= dt.date() <= cutoff):
                    continue
                status = ev.get("status", {}).get("type", "")
                if status not in ("notstarted", "scheduled", ""):
                    continue

                ht = ev.get("homeTeam", {})
                at = ev.get("awayTeam", {})
                home_name = ht.get("name", "")
                away_name = at.get("name", "")

                # Only normalize for SLO leagues
                if league in ("PrvaLiga", "2SNL"):
                    home_name = _norm_name(home_name)
                    away_name = _norm_name(away_name)

                matches.append({
                    "id":           str(ev.get("id", "")),
                    "date":         dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                    "status":       "NS",
                    "league":       league,
                    "league_id":    tid,
                    "round":        ev.get("roundInfo", {}).get("name", ""),
                    "home_team":    home_name,
                    "home_team_id": ht.get("id", _team_id(home_name)),
                    "away_team":    away_name,
                    "away_team_id": at.get("id", _team_id(away_name)),
                    "venue":        ev.get("venue", {}).get("name", "") if ev.get("venue") else "",
                })
            except:
                continue

        print(f"[Sofascore] {league}: {len(matches)} fixtures")
        return matches

    except Exception as e:
        print(f"[Sofascore] {league} error: {e}")
        return []


async def _fetch_sf_team_form(team_id: int, season_id: int, league: str) -> dict:
    """Fetch last 7 results for a team from Sofascore."""
    try:
        data = await _sf_get(f"/team/{team_id}/events/previous/0")
        events = data.get("events", [])
        results, scored, conceded, recent = [], [], [], []

        for ev in events[:10]:
            status = ev.get("status", {}).get("type", "")
            if status != "finished":
                continue
            ht   = ev.get("homeTeam", {})
            at   = ev.get("awayTeam", {})
            hsc  = ev.get("homeScore", {})
            asc  = ev.get("awayScore", {})
            hg   = hsc.get("current", 0) or hsc.get("display", 0)
            ag   = asc.get("current", 0) or asc.get("display", 0)

            is_home = ht.get("id") == team_id
            tg = hg if is_home else ag
            og = ag if is_home else hg
            scored.append(tg); conceded.append(og)

            res = "W" if tg > og else ("D" if tg == og else "L")
            results.append(res)

            ts = ev.get("startTimestamp", 0)
            dt_str = datetime.fromtimestamp(ts).strftime("%d/%m") if ts else ""
            opp = at.get("name", "?") if is_home else ht.get("name", "?")
            recent.append({
                "home":  ht.get("name","?"), "away": at.get("name","?"),
                "score": f"{hg}-{ag}", "date": dt_str,
                "result": res, "is_home": is_home, "opponent": opp,
                "goals_for": tg, "goals_against": og,
            })
            if len(results) >= 7:
                break

        if not results:
            return _mock_form(team_id)

        n = len(results)
        return {
            "form":           results,
            "form_string":    "".join(results[:5]),
            "avg_scored":     round(sum(scored)/n, 2),
            "avg_conceded":   round(sum(conceded)/n, 2),
            "clean_sheets":   sum(1 for g in conceded if g == 0),
            "btts_count":     sum(1 for s,c in zip(scored,conceded) if s>0 and c>0),
            "games_analyzed": n,
            "recent_matches": recent,
        }
    except Exception as e:
        print(f"[Sofascore] form team {team_id}: {e}")
        return _mock_form(team_id)


async def _fetch_sf_h2h(home_id: int, away_id: int) -> dict:
    try:
        data = await _sf_get(f"/event/head2head/{home_id}/{away_id}")
        events = (data.get("firstTeamEvents") or []) + (data.get("secondTeamEvents") or [])
        events = sorted(events, key=lambda e: e.get("startTimestamp",0), reverse=True)[:10]

        hw = aw = draws = btts = 0
        goals = []
        recent = []

        for ev in events:
            ht_id = ev.get("homeTeam", {}).get("id")
            hg = ev.get("homeScore",{}).get("current", ev.get("homeScore",{}).get("display",0)) or 0
            ag = ev.get("awayScore",{}).get("current", ev.get("awayScore",{}).get("display",0)) or 0
            total = hg + ag
            goals.append(total)
            if hg > ag:
                if ht_id == home_id: hw += 1
                else: aw += 1
            elif ag > hg:
                if ht_id == home_id: aw += 1
                else: hw += 1
            else:
                draws += 1
            if hg > 0 and ag > 0: btts += 1

            ts = ev.get("startTimestamp", 0)
            dt_str = datetime.fromtimestamp(ts).strftime("%d/%m/%y") if ts else ""
            recent.append({
                "home":  ev.get("homeTeam",{}).get("name","?"),
                "away":  ev.get("awayTeam",{}).get("name","?"),
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
    except Exception as e:
        print(f"[Sofascore] h2h {home_id}/{away_id}: {e}")
        return _mock_h2h()


async def _fetch_sf_standings(league: str) -> list:
    cfg = TOURNAMENT_IDS.get(league)
    if not cfg:
        return []
    try:
        data = await _sf_get(f"/unique-tournament/{cfg['id']}/season/{cfg['season']}/standings/total")
        rows = data.get("standings", [{}])[0].get("rows", [])
        result = []
        for row in rows:
            t = row.get("team", {})
            name = t.get("name", "")
            if league in ("PrvaLiga", "2SNL"):
                name = _norm_name(name)
            result.append({
                "rank":         row.get("position", 0),
                "team_id":      t.get("id", _team_id(name)),
                "team_name":    name,
                "points":       row.get("points", 0),
                "played":       row.get("matches", 0),
                "won":          row.get("wins", 0),
                "drawn":        row.get("draws", 0),
                "lost":         row.get("losses", 0),
                "goals_for":    row.get("scoresFor", 0),
                "goals_against":row.get("scoresAgainst", 0),
                "goal_diff":    row.get("scoresFor",0) - row.get("scoresAgainst",0),
                "form":         row.get("form", ""),
            })
        print(f"[Sofascore] standings {league}: {len(result)} teams")
        return result
    except Exception as e:
        print(f"[Sofascore] standings {league}: {e}")
        return []


# ── Public API ────────────────────────────────────────────────────────────

async def fetch_upcoming_matches(days_ahead: int = 7, leagues: list = None) -> list:
    import asyncio
    if leagues is None:
        leagues = list(TOURNAMENT_IDS.keys())

    tasks = [_fetch_sf_fixtures(lg, days_ahead) for lg in leagues]
    results = await asyncio.gather(*tasks)

    all_matches = []
    for lg, fixtures in zip(leagues, results):
        if fixtures:
            all_matches.extend(fixtures)
        elif lg in ("PrvaLiga", "2SNL"):
            # Only use mock for SLO leagues
            print(f"[mock] {lg}: using hardcoded fixtures")
            all_matches.extend(_mock_matches(lg))

    return sorted(all_matches, key=lambda x: x["date"])


async def fetch_team_form(team_id: int, league_id: int = 212, last_n: int = 7) -> dict:
    # Find season for this league_id
    league = next((k for k,v in TOURNAMENT_IDS.items() if v["id"] == league_id), "PrvaLiga")
    cfg = TOURNAMENT_IDS.get(league, {})
    season = cfg.get("season", 63839)
    result = await _fetch_sf_team_form(team_id, season, league)
    if not result.get("recent_matches"):
        result["recent_matches"] = []
    return result


async def fetch_h2h(home_id: int, away_id: int) -> dict:
    return await _fetch_sf_h2h(home_id, away_id)


async def fetch_standings(league_id: int) -> list:
    league = next((k for k,v in TOURNAMENT_IDS.items() if v["id"] == league_id), None)
    if not league:
        return _mock_standings()
    result = await _fetch_sf_standings(league)
    if not result:
        if league == "2SNL": return _mock_standings_2snl()
        return _mock_standings()
    return result


async def fetch_past_results(league_id: int = 212, last_n: int = 20) -> list:
    league = next((k for k,v in TOURNAMENT_IDS.items() if v["id"] == league_id), "PrvaLiga")
    cfg = TOURNAMENT_IDS.get(league, {})
    try:
        data = await _sf_get(f"/unique-tournament/{cfg['id']}/season/{cfg['season']}/events/last/0")
        events = data.get("events", [])
        results = []
        for ev in events[:last_n]:
            hg = ev.get("homeScore",{}).get("current",0) or 0
            ag = ev.get("awayScore",{}).get("current",0) or 0
            ts = ev.get("startTimestamp",0)
            results.append({
                "id":         str(ev.get("id","")),
                "date":       datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "",
                "home_team":  ev.get("homeTeam",{}).get("name",""),
                "away_team":  ev.get("awayTeam",{}).get("name",""),
                "home_goals": hg,
                "away_goals": ag,
                "status":     "FT",
            })
        return results
    except:
        return []


# ── Mock data (SLO only) ─────────────────────────────────────────────────

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
        {"id":"p26_01","date":"2026-03-07T20:00:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 26","home_team":"NS Mura","home_team_id":1600,"away_team":"NK Primorje Ajdovščina","away_team_id":99991,"venue":"Fazanerija"},
        {"id":"p26_02","date":"2026-03-08T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 26","home_team":"NK Olimpija Ljubljana","home_team_id":1598,"away_team":"NK Maribor","away_team_id":1601,"venue":"Stožice"},
        {"id":"p26_03","date":"2026-03-08T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 26","home_team":"NK Aluminij","home_team_id":10576,"away_team":"FC Koper","away_team_id":2279,"venue":"Aluminij Stadium"},
        {"id":"p26_04","date":"2026-03-09T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 26","home_team":"NK Radomlje","home_team_id":14370,"away_team":"NK Bravo","away_team_id":10203,"venue":"Radomlje"},
        {"id":"p26_05","date":"2026-03-09T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 26","home_team":"NK Celje","home_team_id":1594,"away_team":"ND Gorica","away_team_id":99993,"venue":"Arena Z'dežele"},
        {"id":"p27_01","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 27","home_team":"NK Maribor","home_team_id":1601,"away_team":"NS Mura","away_team_id":1600,"venue":"Ljudski vrt"},
        {"id":"p27_02","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 27","home_team":"NK Primorje Ajdovščina","home_team_id":99991,"away_team":"NK Celje","away_team_id":1594,"venue":"Ajdovščina"},
        {"id":"p27_03","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 27","home_team":"FC Koper","home_team_id":2279,"away_team":"NK Olimpija Ljubljana","away_team_id":1598,"venue":"Bonifika"},
        {"id":"p27_04","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 27","home_team":"NK Bravo","home_team_id":10203,"away_team":"NK Aluminij","away_team_id":10576,"venue":"ZAK"},
        {"id":"p27_05","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":212,"round":"Round 27","home_team":"ND Gorica","home_team_id":99993,"away_team":"NK Radomlje","away_team_id":14370,"venue":"Nova Gorica"},
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
        sc = [2,1,3,1,2,0,2][seed:] + [2,1,3,1,2,0,2][:seed]
        cc = [0,1,1,2,1,2,1][seed:] + [0,1,1,2,1,2,1][:seed]
        r  = pools[seed]
    n = len(r)
    # Build synthetic recent_matches
    recent = []
    teams = ["NK Olimpija Ljubljana","NK Maribor","NK Celje","FC Koper","NK Bravo",
             "NS Mura","NK Aluminij","NK Radomlje","NK Primorje Ajdovščina","ND Gorica"]
    team_name = ID_TO_NAME.get(team_id, "Equipo")
    for i, (res, tg, og) in enumerate(zip(r, sc, cc)):
        opp = teams[(team_id + i) % len(teams)]
        is_home = i % 2 == 0
        from datetime import date, timedelta
        dt = (date.today() - timedelta(days=(n-i)*7)).strftime("%d/%m")
        recent.append({
            "home": team_name if is_home else opp,
            "away": opp if is_home else team_name,
            "score": f"{tg}-{og}" if is_home else f"{og}-{tg}",
            "date": dt, "result": res, "is_home": is_home, "opponent": opp,
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
