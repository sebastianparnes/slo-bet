"""
Football data — Flashscore (fixtures/results) + TheSportsDB (standings)
========================================================================
Flashscore: no key, fixtures y resultados reales para todas las ligas.
TheSportsDB: standings gratuitos para ligas conocidas.
ar-xbet: cuotas via Worker proxy.
"""

import httpx
import re
from datetime import datetime, date, timedelta
from typing import Optional

FS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.flashscore.com/",
    "x-fsign": "SW9D1eZo",
}

# Flashscore country/league slugs
LEAGUES = {
    "PrvaLiga":        {"fs_country": "slovenia",   "fs_league": "prva-liga"},
    "2SNL":            {"fs_country": "slovenia",   "fs_league": "2-snl"},
    "PrimeraDivision": {"fs_country": "argentina",  "fs_league": "liga-profesional"},
    "PrimeraNacional": {"fs_country": "argentina",  "fs_league": "primera-nacional"},
    "ChampionsLeague": {"fs_country": "europe",     "fs_league": "champions-league"},
    "PremierLeague":   {"fs_country": "england",    "fs_league": "premier-league"},
    "LaLiga":          {"fs_country": "spain",      "fs_league": "laliga"},
    "SerieA":          {"fs_country": "italy",      "fs_league": "serie-a"},
    "Bundesliga":      {"fs_country": "germany",    "fs_league": "bundesliga"},
    "Ligue1":          {"fs_country": "france",     "fs_league": "ligue-1"},
    "CroatiaHNL":      {"fs_country": "croatia",    "fs_league": "hnl"},
    "SerbiaSuper":     {"fs_country": "serbia",     "fs_league": "super-liga"},
    "UruguayPrimera":  {"fs_country": "uruguay",    "fs_league": "primera-division"},
}

# TheSportsDB league IDs for standings
TSDB_IDS = {
    "PrvaLiga":        "4966",
    "PremierLeague":   "4328",
    "LaLiga":          "4335",
    "SerieA":          "4332",
    "Bundesliga":      "4331",
    "Ligue1":          "4334",
    "ChampionsLeague": "4480",
}

TOURNAMENT_IDS = {k: i for i, k in enumerate(LEAGUES.keys(), 212)}

# ar-xbet league IDs (used by xbet_scraper)
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
    # Only normalize for SLO leagues
    if league not in ("PrvaLiga", "2SNL"):
        return name.strip()
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
    key = re.sub(r"[^a-zčšž]", "", name.lower().strip())
    for k, v in mapping.items():
        if k in key or key in k:
            return v
    return name.strip()


# ── Flashscore ────────────────────────────────────────────────────────────

async def _fetch_flashscore(country: str, league: str, feed: str = "f_1") -> str:
    """Feed f_1 = upcoming, f_2 = results."""
    url = f"https://d.flashscore.com/x/feed/{feed}_{country}_{league}_"
    try:
        async with httpx.AsyncClient(timeout=12, headers=FS_HEADERS) as c:
            r = await c.get(url)
            if r.status_code == 200:
                return r.text
    except Exception as e:
        print(f"[Flashscore] {country}/{league} feed={feed}: {e}")
    return ""


def _parse_fs_feed(text: str, league_name: str, today: date, cutoff: date) -> list:
    matches = []
    for seg in text.split("~"):
        try:
            parts = dict(p.split("¬") for p in seg.split("÷") if "¬" in p)
            if not parts:
                continue
            match_id = parts.get("AA", "")
            home     = parts.get("CX", parts.get("AE", ""))
            away     = parts.get("AF", parts.get("CY", ""))
            ts       = parts.get("AD", parts.get("U_", ""))
            if not home or not away or not ts:
                continue
            dt = datetime.fromtimestamp(int(ts))
            if not (today <= dt.date() <= cutoff):
                continue
            home_n = _norm_name(home, league_name)
            away_n = _norm_name(away, league_name)
            matches.append({
                "id":           match_id or f"fs_{abs(hash(home+away+ts))}",
                "date":         dt.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
                "status":       "NS",
                "league":       league_name,
                "league_id":    XBET_LEAGUE_IDS.get(league_name, 212),
                "round":        parts.get("AL", ""),
                "home_team":    home_n,
                "home_team_id": _team_id(home_n),
                "away_team":    away_n,
                "away_team_id": _team_id(away_n),
                "venue":        "",
            })
        except:
            continue
    return matches


def _parse_fs_results(text: str, league_name: str) -> list:
    results = []
    for seg in text.split("~"):
        try:
            parts = dict(p.split("¬") for p in seg.split("÷") if "¬" in p)
            home = parts.get("CX", parts.get("AE", ""))
            away = parts.get("AF", parts.get("CY", ""))
            hg   = parts.get("AG", "")
            ag   = parts.get("AH", "")
            ts   = parts.get("AD", "")
            if not home or not away or hg == "" or ag == "":
                continue
            dt_str = datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d") if ts else ""
            results.append({
                "id":         parts.get("AA", ""),
                "date":       dt_str,
                "home_team":  _norm_name(home, league_name),
                "away_team":  _norm_name(away, league_name),
                "home_goals": int(hg),
                "away_goals": int(ag),
                "status":     "FT",
            })
        except:
            continue
    return results


# ── Public API ────────────────────────────────────────────────────────────

async def fetch_upcoming_matches(days_ahead: int = 7, leagues: list = None) -> list:
    import asyncio
    if leagues is None:
        leagues = list(LEAGUES.keys())

    today  = date.today()
    cutoff = today + timedelta(days=days_ahead)

    async def _fetch_one(league_name):
        cfg = LEAGUES.get(league_name, {})
        if not cfg:
            return []
        text = await _fetch_flashscore(cfg["fs_country"], cfg["fs_league"], "f_1")
        if text:
            fixtures = _parse_fs_feed(text, league_name, today, cutoff)
            print(f"[Flashscore] {league_name}: {len(fixtures)} fixtures")
            return fixtures
        # SLO-only fallback
        if league_name in ("PrvaLiga", "2SNL"):
            print(f"[mock] {league_name}: using hardcoded fixtures")
            return _mock_matches(league_name)
        return []

    results = await asyncio.gather(*[_fetch_one(lg) for lg in leagues])
    all_m = [m for r in results for m in r]
    return sorted(all_m, key=lambda x: x["date"])


async def fetch_past_results(league_id: int = 118593, last_n: int = 30) -> list:
    # Find league name by xbet ID
    league_name = next((k for k, v in XBET_LEAGUE_IDS.items() if v == league_id), "PrvaLiga")
    cfg = LEAGUES.get(league_name, LEAGUES["PrvaLiga"])
    text = await _fetch_flashscore(cfg["fs_country"], cfg["fs_league"], "f_2")
    if text:
        return _parse_fs_results(text, league_name)[-last_n:]
    return []


async def fetch_team_form(team_id: int, league_id: int = 118593, last_n: int = 7) -> dict:
    league_name = next((k for k, v in XBET_LEAGUE_IDS.items() if v == league_id), "PrvaLiga")
    team_name   = ID_TO_NAME.get(team_id, "")
    past        = await fetch_past_results(league_id, last_n=50)

    results, scored, conceded, recent = [], [], [], []
    for m in past:
        is_home = _same_team(m["home_team"], team_name) if team_name else False
        is_away = _same_team(m["away_team"], team_name) if team_name else False
        if not is_home and not is_away:
            continue
        tg = m["home_goals"] if is_home else m["away_goals"]
        og = m["away_goals"] if is_home else m["home_goals"]
        scored.append(tg); conceded.append(og)
        res = "W" if tg > og else ("D" if tg == og else "L")
        results.append(res)
        opp = m["away_team"] if is_home else m["home_team"]
        recent.append({
            "home":         m["home_team"],
            "away":         m["away_team"],
            "score":        f"{m['home_goals']}-{m['away_goals']}",
            "date":         m["date"][5:10].replace("-", "/") if m.get("date") else "",
            "result":       res,
            "is_home":      is_home,
            "opponent":     opp,
            "goals_for":    tg,
            "goals_against":og,
        })
        if len(results) >= last_n:
            break

    if not results:
        return _mock_form(team_id)

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


async def fetch_h2h(home_id: int, away_id: int) -> dict:
    home_name = ID_TO_NAME.get(home_id, "")
    away_name = ID_TO_NAME.get(away_id, "")

    # Try to get results from both teams' leagues
    past = await fetch_past_results(118593, last_n=80)

    hw = aw = draws = btts = 0
    goals, recent = [], []
    for m in past:
        ih = (_same_team(m["home_team"], home_name) and _same_team(m["away_team"], away_name)) if home_name and away_name else False
        ia = (_same_team(m["home_team"], away_name) and _same_team(m["away_team"], home_name)) if home_name and away_name else False
        if not ih and not ia:
            continue
        hg, ag = m["home_goals"], m["away_goals"]
        goals.append(hg + ag)
        if hg > ag: hw += (1 if ih else 0); aw += (1 if ia else 0)
        elif ag > hg: aw += (1 if ih else 0); hw += (1 if ia else 0)
        else: draws += 1
        if hg > 0 and ag > 0: btts += 1
        recent.append({
            "home": m["home_team"], "away": m["away_team"],
            "score": f"{hg}-{ag}",
            "date": m["date"][5:10].replace("-", "/") if m.get("date") else "",
        })

    if not goals:
        return _mock_h2h()

    n = len(goals)
    return {
        "total_matches": n,
        "home_wins":     hw,
        "draws":         draws,
        "away_wins":     aw,
        "avg_goals_h2h": round(sum(goals)/n, 2),
        "btts_pct":      round(btts/n*100, 1),
        "over25_pct":    round(sum(1 for g in goals if g > 2.5)/n*100, 1),
        "recent":        recent[:5],
    }


async def fetch_standings(league_id: int) -> list:
    league_name = next((k for k, v in XBET_LEAGUE_IDS.items() if v == league_id), None)
    if not league_name:
        return _mock_standings()

    # TheSportsDB
    tsdb_id = TSDB_IDS.get(league_name)
    if tsdb_id:
        for season in ["2025-2026", "2024-2025"]:
            try:
                async with httpx.AsyncClient(timeout=10) as c:
                    r = await c.get(
                        "https://www.thesportsdb.com/api/v1/json/3/lookuptable.php",
                        params={"l": tsdb_id, "s": season}
                    )
                    rows = r.json().get("table") or []
                    if rows:
                        result = []
                        for i, row in enumerate(rows):
                            name = _norm_name(row.get("strTeam", ""), league_name)
                            result.append({
                                "rank":          int(row.get("intRank", i+1)),
                                "team_id":       _team_id(name),
                                "team_name":     name,
                                "points":        int(row.get("intPoints", 0)),
                                "played":        int(row.get("intPlayed", 0)),
                                "won":           int(row.get("intWin", 0)),
                                "drawn":         int(row.get("intDraw", 0)),
                                "lost":          int(row.get("intLoss", 0)),
                                "goals_for":     int(row.get("intGoalsFor", 0)),
                                "goals_against": int(row.get("intGoalsAgainst", 0)),
                                "goal_diff":     int(row.get("intGoalDifference", 0)),
                                "form":          row.get("strForm", ""),
                            })
                        print(f"[TheSportsDB] {league_name}: {len(result)} teams")
                        return result
            except Exception as e:
                print(f"[TheSportsDB] {league_name}: {e}")

    # Fallback mocks
    if league_name == "2SNL":
        return _mock_standings_2snl()
    return _mock_standings()


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
        {"id":"p26_02","date":"2026-03-08T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 26","home_team":"NK Olimpija Ljubljana","home_team_id":1598,"away_team":"NK Maribor","away_team_id":1601,"venue":"Stožice"},
        {"id":"p26_03","date":"2026-03-08T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 26","home_team":"NK Aluminij","home_team_id":10576,"away_team":"FC Koper","away_team_id":2279,"venue":"Aluminij Stadium"},
        {"id":"p26_04","date":"2026-03-09T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 26","home_team":"NK Radomlje","home_team_id":14370,"away_team":"NK Bravo","away_team_id":10203,"venue":"Radomlje"},
        {"id":"p26_05","date":"2026-03-09T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 26","home_team":"NK Celje","home_team_id":1594,"away_team":"ND Gorica","away_team_id":99993,"venue":"Arena Z'dežele"},
        {"id":"p27_01","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"NK Maribor","home_team_id":1601,"away_team":"NS Mura","away_team_id":1600,"venue":"Ljudski vrt"},
        {"id":"p27_02","date":"2026-03-14T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"NK Primorje Ajdovščina","home_team_id":99991,"away_team":"NK Celje","away_team_id":1594,"venue":"Ajdovščina"},
        {"id":"p27_03","date":"2026-03-15T17:30:00+01:00","status":"NS","league":"PrvaLiga","league_id":118593,"round":"Round 27","home_team":"FC Koper","home_team_id":2279,"away_team":"NK Olimpija Ljubljana","away_team_id":1598,"venue":"Bonifika"},
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
        sc = ([2,1,3,1,2,0,2][seed:] + [2,1,3,1,2,0,2][:seed])[:7]
        cc = ([0,1,1,2,1,2,1][seed:] + [0,1,1,2,1,2,1][:seed])[:7]
        r  = pools[seed]
    n = len(r)
    recent = []
    teams = ["NK Olimpija Ljubljana","NK Maribor","NK Celje","FC Koper","NK Bravo",
             "NS Mura","NK Aluminij","NK Radomlje","NK Primorje Ajdovščina","ND Gorica"]
    team_name = ID_TO_NAME.get(team_id, "Equipo")
    for i, (res, tg, og) in enumerate(zip(r, sc, cc)):
        opp = teams[(team_id + i) % len(teams)]
        is_home = i % 2 == 0
        dt = (date.today() - timedelta(days=(n-i)*7)).strftime("%m/%d")
        recent.append({
            "home": team_name if is_home else opp,
            "away": opp if is_home else team_name,
            "score": f"{tg}-{og}" if is_home else f"{og}-{tg}",
            "date": dt, "result": res, "is_home": is_home, "opponent": opp,
            "goals_for": tg, "goals_against": og,
        })
    return {
        "form": r, "form_string": "".join(r[-5:]),
        "avg_scored": round(sum(sc)/n, 2), "avg_conceded": round(sum(cc)/n, 2),
        "clean_sheets": sum(1 for g in cc if g == 0),
        "btts_count": sum(1 for s, c in zip(sc, cc) if s > 0 and c > 0),
        "games_analyzed": n, "recent_matches": recent,
    }


def _mock_h2h() -> dict:
    return {"total_matches":8,"home_wins":4,"draws":2,"away_wins":2,
            "avg_goals_h2h":2.4,"btts_pct":62.5,"over25_pct":50.0,"recent":[]}


def _mock_standings() -> list:
    teams = [("NK Olimpija Ljubljana",1598,52),("NK Celje",1594,45),
             ("NK Maribor",1601,42),("FC Koper",2279,38),("NK Bravo",10203,30),
             ("NK Aluminij",10576,27),("NS Mura",1600,24),("NK Radomlje",14370,18),
             ("NK Primorje Ajdovščina",99991,15),("ND Gorica",99993,10)]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":25,
             "won":t[2]//3,"drawn":t[2]%3,"lost":25-t[2]//3-t[2]%3,
             "goals_for":45-i*4,"goals_against":15+i*4,"goal_diff":30-i*8,
             "form":"WWDWW" if i<3 else "WDLLL"} for i,t in enumerate(teams)]


def _mock_standings_2snl() -> list:
    teams = [("NK Nafta 1903",14372,48),("NK Krka",88008,44),("NK Triglav",88004,40),
             ("ND Slovan Ljubljana",99996,37),("Krško Posavje",88006,33),
             ("ND Beltinci",99997,30),("NK Ankaran",14371,26),("NK Rudar",88002,23)]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":24,
             "won":t[2]//3,"drawn":t[2]%3,"lost":24-t[2]//3-t[2]%3,
             "goals_for":35-i*2,"goals_against":12+i*2,"goal_diff":23-i*4,
             "form":"WWDWW" if i<4 else "WDLLL"} for i,t in enumerate(teams)]
