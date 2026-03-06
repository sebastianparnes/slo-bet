"""
Football data fetcher — API-Football v3
Slovenian leagues:
  PrvaLiga : league_id 218
  2.SNL    : league_id 219

Free tier: 100 req/day — https://dashboard.api-football.com/register
Set env var: API_FOOTBALL_KEY=your_key
"""

import httpx
import os
from datetime import datetime, timedelta, date
from typing import Optional

BASE_URL = "https://v3.football.api-sports.io"

SLOVENIAN_LEAGUES = {
    "PrvaLiga": 218,
    "2SNL":     219,
}

# PrvaLiga runs Aug–May, so 2024 = 2024/25 season
CURRENT_SEASON = 2024


def _get_headers() -> dict:
    """Read API key fresh on every call so env vars set after import work."""
    key = os.getenv("API_FOOTBALL_KEY", "")
    return {
        "x-apisports-key": key,
        "x-rapidapi-host": "v3.football.api-sports.io",
    }

def _has_key() -> bool:
    return bool(os.getenv("API_FOOTBALL_KEY", "").strip())


# ── Upcoming fixtures ──────────────────────────────────────────────────────

async def fetch_upcoming_matches(days_ahead: int = 7) -> list[dict]:
    if not _has_key():
        print("[API-Football] No API key — returning mock data")
        return _mock_matches()

    today    = date.today()
    end_date = today + timedelta(days=days_ahead)
    all_matches = []

    async with httpx.AsyncClient(timeout=30) as client:
        for league_name, league_id in SLOVENIAN_LEAGUES.items():
            try:
                resp = await client.get(
                    f"{BASE_URL}/fixtures",
                    headers=_get_headers(),
                    params={
                        "league":   league_id,
                        "season":   CURRENT_SEASON,
                        "from":     str(today),
                        "to":       str(end_date),
                        "timezone": "Europe/Ljubljana",
                    }
                )
                data = resp.json()
                errors = data.get("errors", {})
                if errors and (isinstance(errors, dict) and errors or isinstance(errors, list) and errors):
                    print(f"[API-Football] Error {league_name}: {errors}")
                    continue
                for f in data.get("response", []):
                    m = _parse_fixture(f, league_name)
                    if m:
                        all_matches.append(m)
            except Exception as e:
                print(f"[API-Football] Exception fetching {league_name}: {e}")

    if not all_matches:
        print("[API-Football] No fixtures returned — check key or season")
        return _mock_matches()

    return sorted(all_matches, key=lambda x: x["date"])


# ── Team form ──────────────────────────────────────────────────────────────

async def fetch_team_form(team_id: int, league_id: int, last_n: int = 7) -> dict:
    if not _has_key():
        return _mock_form(team_id)

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{BASE_URL}/fixtures",
                headers=_get_headers(),
                params={
                    "team":   team_id,
                    "league": league_id,
                    "season": CURRENT_SEASON,
                    "last":   last_n,
                    "status": "FT",
                }
            )
            fixtures = resp.json().get("response", [])
        except Exception as e:
            print(f"[API-Football] form fetch error: {e}")
            return _mock_form(team_id)

    if not fixtures:
        return _mock_form(team_id)

    results, scored, conceded = [], [], []
    for f in fixtures:
        is_home = f["teams"]["home"]["id"] == team_id
        tg = (f["goals"]["home"] or 0) if is_home else (f["goals"]["away"] or 0)
        og = (f["goals"]["away"] or 0) if is_home else (f["goals"]["home"] or 0)
        scored.append(tg); conceded.append(og)
        results.append("W" if tg > og else ("D" if tg == og else "L"))

    n = len(results) or 1
    return {
        "form":           results,
        "form_string":    "".join(results[-5:]),
        "avg_scored":     round(sum(scored)   / n, 2),
        "avg_conceded":   round(sum(conceded) / n, 2),
        "clean_sheets":   sum(1 for g in conceded if g == 0),
        "btts_count":     sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed": n,
    }


# ── Head-to-head ───────────────────────────────────────────────────────────

async def fetch_h2h(home_id: int, away_id: int) -> dict:
    if not _has_key():
        return _mock_h2h()

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{BASE_URL}/fixtures/headtohead",
                headers=_get_headers(),
                params={"h2h": f"{home_id}-{away_id}", "last": 10}
            )
            fixtures = resp.json().get("response", [])
        except Exception as e:
            print(f"[API-Football] h2h fetch error: {e}")
            return _mock_h2h()

    hw = aw = draws = btts = 0
    goals = []
    for f in fixtures:
        hg = f["goals"]["home"] or 0
        ag = f["goals"]["away"] or 0
        goals.append(hg + ag)
        fh_id = f["teams"]["home"]["id"]
        if hg > ag:
            if fh_id == home_id: hw += 1
            else: aw += 1
        elif ag > hg:
            if fh_id == away_id: aw += 1
            else: hw += 1
        else:
            draws += 1
        if hg > 0 and ag > 0:
            btts += 1

    n = len(fixtures) or 1
    return {
        "total_matches": len(fixtures),
        "home_wins":     hw,
        "draws":         draws,
        "away_wins":     aw,
        "avg_goals_h2h": round(sum(goals) / n, 2),
        "btts_pct":      round(btts / n * 100, 1),
        "over25_pct":    round(sum(1 for g in goals if g > 2.5) / n * 100, 1),
    }


# ── Standings ──────────────────────────────────────────────────────────────

async def fetch_standings(league_id: int) -> list[dict]:
    if not _has_key():
        return _mock_standings()

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.get(
                f"{BASE_URL}/standings",
                headers=_get_headers(),
                params={"league": league_id, "season": CURRENT_SEASON}
            )
            data = resp.json()
            rows = data["response"][0]["league"]["standings"][0]
        except Exception as e:
            print(f"[API-Football] standings error: {e}")
            return _mock_standings()

    return [
        {
            "rank":          s["rank"],
            "team_id":       s["team"]["id"],
            "team_name":     s["team"]["name"],
            "points":        s["points"],
            "played":        s["all"]["played"],
            "won":           s["all"]["win"],
            "drawn":         s["all"]["draw"],
            "lost":          s["all"]["lose"],
            "goals_for":     s["all"]["goals"]["for"],
            "goals_against": s["all"]["goals"]["against"],
            "goal_diff":     s["goalsDiff"],
            "form":          s.get("form", ""),
        }
        for s in rows
    ]


# ── Helpers ────────────────────────────────────────────────────────────────

def _parse_fixture(f: dict, league_name: str) -> Optional[dict]:
    try:
        return {
            "id":           str(f["fixture"]["id"]),
            "date":         f["fixture"]["date"],
            "status":       f["fixture"]["status"]["short"],
            "league":       league_name,
            "league_id":    f["league"]["id"],
            "round":        f["league"]["round"],
            "home_team":    f["teams"]["home"]["name"],
            "home_team_id": f["teams"]["home"]["id"],
            "away_team":    f["teams"]["away"]["name"],
            "away_team_id": f["teams"]["away"]["id"],
            "venue":        f["fixture"]["venue"].get("name", ""),
        }
    except Exception:
        return None


# ── Mock data ──────────────────────────────────────────────────────────────
# Used only when no API key is set.
# Teams and IDs are real; fixtures are placeholder dates.

def _mock_matches() -> list[dict]:
    today = date.today()
    add   = lambda d: (today + timedelta(days=d)).isoformat() + "T17:00:00+01:00"
    return [
        {"id":"mock_01","date":add(1),"status":"NS","league":"PrvaLiga","league_id":218,"round":"Demo","home_team":"NK Maribor","home_team_id":1601,"away_team":"NK Koper","away_team_id":2279,"venue":"Ljudski vrt"},
        {"id":"mock_02","date":add(2),"status":"NS","league":"PrvaLiga","league_id":218,"round":"Demo","home_team":"NK Olimpija Ljubljana","home_team_id":1598,"away_team":"NK Celje","away_team_id":1594,"venue":"Stožice"},
        {"id":"mock_03","date":add(3),"status":"NS","league":"PrvaLiga","league_id":218,"round":"Demo","home_team":"NK Bravo","home_team_id":10203,"away_team":"NK Domžale","away_team_id":1595,"venue":"ZAK"},
        {"id":"mock_04","date":add(4),"status":"NS","league":"PrvaLiga","league_id":218,"round":"Demo","home_team":"NK Mura","home_team_id":1600,"away_team":"NK Radomlje","away_team_id":14370,"venue":"Fazanerija"},
        {"id":"mock_05","date":add(3),"status":"NS","league":"2SNL","league_id":219,"round":"Demo","home_team":"NK Nafta 1903","home_team_id":14372,"away_team":"NK Aluminij","away_team_id":10576,"venue":"Lendava"},
        {"id":"mock_06","date":add(5),"status":"NS","league":"2SNL","league_id":219,"round":"Demo","home_team":"NK Drava Ptuj","home_team_id":10578,"away_team":"NK Ankaran","away_team_id":14371,"venue":"Ptuj"},
    ]

def _mock_form(team_id: int = 0) -> dict:
    # Deterministic per team_id so same team always gets same mock form
    seed  = team_id % 7
    pools = [
        ["W","W","W","D","W","L","W"],
        ["W","D","W","W","L","W","D"],
        ["D","W","L","W","W","D","W"],
        ["L","W","D","L","W","W","W"],
        ["W","L","W","D","L","W","D"],
        ["D","D","W","L","D","W","L"],
        ["L","W","L","W","D","L","W"],
    ]
    results  = pools[seed]
    scored   = [2,1,3,1,2,0,2][seed:] + [2,1,3,1,2,0,2][:seed]
    conceded = [0,1,1,2,1,2,1][seed:] + [0,1,1,2,1,2,1][:seed]
    n = 7
    return {
        "form":           results,
        "form_string":    "".join(results[-5:]),
        "avg_scored":     round(sum(scored)   / n, 2),
        "avg_conceded":   round(sum(conceded) / n, 2),
        "clean_sheets":   sum(1 for g in conceded if g == 0),
        "btts_count":     sum(1 for s,c in zip(scored,conceded) if s>0 and c>0),
        "games_analyzed": n,
    }

def _mock_h2h() -> dict:
    return {"total_matches":8,"home_wins":4,"draws":2,"away_wins":2,
            "avg_goals_h2h":2.4,"btts_pct":62.5,"over25_pct":50.0}

def _mock_standings() -> list[dict]:
    teams = [
        ("NK Olimpija Ljubljana",1598,58),("NK Maribor",1601,54),
        ("NK Celje",1594,48),("NK Koper",2279,42),
        ("NK Bravo",10203,36),("NK Mura",1600,30),
        ("NK Domžale",1595,28),("NK Radomlje",14370,20),
    ]
    return [{"rank":i+1,"team_id":t[1],"team_name":t[0],"points":t[2],"played":27,
             "won":t[2]//3,"drawn":t[2]%3,"lost":27-t[2]//3-t[2]%3,
             "goals_for":55-i*4,"goals_against":18+i*4,"goal_diff":37-i*8,
             "form":"WWDWW" if i<3 else "WDLLL"}
            for i,t in enumerate(teams)]
