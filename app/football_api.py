"""
Football data fetcher using API-Football (api-football.com)
Slovenian leagues:
  - PrvaLiga (1st division): league_id = 218
  - 2. SNL (2nd division): league_id = 219

Requires API key from: https://www.api-football.com/
Set env var: API_FOOTBALL_KEY=your_key_here

Free tier: 100 requests/day
"""

import httpx
import os
import json
from datetime import datetime, timedelta
from typing import Optional
from app.database import get_connection

API_KEY = os.getenv("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"

SLOVENIAN_LEAGUES = {
    "PrvaLiga": 218,       # 1a división
    "2SNL": 219,           # 2a división
}

HEADERS = {
    "x-apisports-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io"
}

# Season actual
CURRENT_SEASON = 2024


async def fetch_upcoming_matches(days_ahead: int = 5) -> list[dict]:
    """Fetch matches from the next N days for Slovenian leagues."""
    if not API_KEY:
        return _get_mock_matches()
    
    today = datetime.now().date()
    end_date = today + timedelta(days=days_ahead)
    
    all_matches = []
    
    async with httpx.AsyncClient(timeout=30) as client:
        for league_name, league_id in SLOVENIAN_LEAGUES.items():
            try:
                resp = await client.get(
                    f"{BASE_URL}/fixtures",
                    headers=HEADERS,
                    params={
                        "league": league_id,
                        "season": CURRENT_SEASON,
                        "from": str(today),
                        "to": str(end_date),
                        "timezone": "Europe/Ljubljana"
                    }
                )
                data = resp.json()
                
                if data.get("errors") and data["errors"]:
                    print(f"API error for {league_name}: {data['errors']}")
                    continue
                
                fixtures = data.get("response", [])
                for f in fixtures:
                    match = _parse_fixture(f, league_name)
                    if match:
                        all_matches.append(match)
            except Exception as e:
                print(f"Error fetching {league_name}: {e}")
                all_matches.extend(_get_mock_matches(league_name))
    
    if not all_matches:
        return _get_mock_matches()
    
    return sorted(all_matches, key=lambda x: x["date"])


async def fetch_team_form(team_id: int, league_id: int, last_n: int = 7) -> dict:
    """Fetch last N results for a team."""
    if not API_KEY:
        return _get_mock_form()
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/fixtures",
            headers=HEADERS,
            params={
                "team": team_id,
                "league": league_id,
                "season": CURRENT_SEASON,
                "last": last_n,
                "status": "FT"
            }
        )
        data = resp.json()
        fixtures = data.get("response", [])
        
        results = []
        goals_scored = []
        goals_conceded = []
        
        for f in fixtures:
            home_id = f["teams"]["home"]["id"]
            home_goals = f["goals"]["home"] or 0
            away_goals = f["goals"]["away"] or 0
            
            is_home = (home_id == team_id)
            team_goals = home_goals if is_home else away_goals
            opp_goals = away_goals if is_home else home_goals
            
            goals_scored.append(team_goals)
            goals_conceded.append(opp_goals)
            
            if team_goals > opp_goals:
                results.append("W")
            elif team_goals == opp_goals:
                results.append("D")
            else:
                results.append("L")
        
        return {
            "form": results,
            "form_string": "".join(results[-5:]),
            "avg_scored": round(sum(goals_scored) / len(goals_scored), 2) if goals_scored else 0,
            "avg_conceded": round(sum(goals_conceded) / len(goals_conceded), 2) if goals_conceded else 0,
            "clean_sheets": sum(1 for g in goals_conceded if g == 0),
            "btts_count": sum(1 for s, c in zip(goals_scored, goals_conceded) if s > 0 and c > 0),
            "games_analyzed": len(results)
        }


async def fetch_h2h(home_id: int, away_id: int) -> dict:
    """Fetch head-to-head history between two teams."""
    if not API_KEY:
        return _get_mock_h2h()
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/fixtures/headtohead",
            headers=HEADERS,
            params={
                "h2h": f"{home_id}-{away_id}",
                "last": 10
            }
        )
        data = resp.json()
        fixtures = data.get("response", [])
        
        home_wins = 0
        away_wins = 0
        draws = 0
        total_goals = []
        btts = 0
        
        for f in fixtures:
            hg = f["goals"]["home"] or 0
            ag = f["goals"]["away"] or 0
            total_goals.append(hg + ag)
            
            fh_id = f["teams"]["home"]["id"]
            if hg > ag:
                if fh_id == home_id:
                    home_wins += 1
                else:
                    away_wins += 1
            elif ag > hg:
                if fh_id == away_id:
                    away_wins += 1
                else:
                    home_wins += 1
            else:
                draws += 1
            
            if hg > 0 and ag > 0:
                btts += 1
        
        total = len(fixtures)
        return {
            "total_matches": total,
            "home_wins": home_wins,
            "draws": draws,
            "away_wins": away_wins,
            "avg_goals": round(sum(total_goals) / total, 2) if total else 0,
            "btts_pct": round((btts / total) * 100, 1) if total else 0,
            "over25_pct": round((sum(1 for g in total_goals if g > 2.5) / total) * 100, 1) if total else 0,
        }


async def fetch_standings(league_id: int) -> list[dict]:
    """Fetch current league standings."""
    if not API_KEY:
        return _get_mock_standings()
    
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(
            f"{BASE_URL}/standings",
            headers=HEADERS,
            params={"league": league_id, "season": CURRENT_SEASON}
        )
        data = resp.json()
        
        try:
            standings_raw = data["response"][0]["league"]["standings"][0]
            return [
                {
                    "rank": s["rank"],
                    "team_id": s["team"]["id"],
                    "team_name": s["team"]["name"],
                    "points": s["points"],
                    "played": s["all"]["played"],
                    "won": s["all"]["win"],
                    "drawn": s["all"]["draw"],
                    "lost": s["all"]["lose"],
                    "goals_for": s["all"]["goals"]["for"],
                    "goals_against": s["all"]["goals"]["against"],
                    "goal_diff": s["goalsDiff"],
                    "form": s.get("form", ""),
                }
                for s in standings_raw
            ]
        except (KeyError, IndexError):
            return []


def _parse_fixture(f: dict, league_name: str) -> Optional[dict]:
    try:
        return {
            "id": str(f["fixture"]["id"]),
            "date": f["fixture"]["date"],
            "status": f["fixture"]["status"]["short"],
            "league": league_name,
            "league_id": f["league"]["id"],
            "round": f["league"]["round"],
            "home_team": f["teams"]["home"]["name"],
            "home_team_id": f["teams"]["home"]["id"],
            "away_team": f["teams"]["away"]["name"],
            "away_team_id": f["teams"]["away"]["id"],
            "venue": f["fixture"]["venue"].get("name", ""),
        }
    except Exception:
        return None


def _get_mock_matches(league_name: str = None) -> list[dict]:
    """Mock data when no API key is configured."""
    from datetime import date, timedelta
    
    today = date.today()
    mocks = [
        {
            "id": "mock_001",
            "date": (today + timedelta(days=1)).isoformat() + "T15:00:00+02:00",
            "status": "NS",
            "league": "PrvaLiga",
            "league_id": 218,
            "round": "Regular Season - 28",
            "home_team": "NK Maribor",
            "home_team_id": 1601,
            "away_team": "NK Koper",
            "away_team_id": 2279,
            "venue": "Ljudski vrt",
        },
        {
            "id": "mock_002",
            "date": (today + timedelta(days=2)).isoformat() + "T17:30:00+02:00",
            "status": "NS",
            "league": "PrvaLiga",
            "league_id": 218,
            "round": "Regular Season - 28",
            "home_team": "NK Olimpija Ljubljana",
            "home_team_id": 1598,
            "away_team": "NK Celje",
            "away_team_id": 1594,
            "venue": "Stožice",
        },
        {
            "id": "mock_003",
            "date": (today + timedelta(days=2)).isoformat() + "T17:30:00+02:00",
            "status": "NS",
            "league": "PrvaLiga",
            "league_id": 218,
            "round": "Regular Season - 28",
            "home_team": "NK Bravo",
            "home_team_id": 10203,
            "away_team": "NK Domžale",
            "away_team_id": 1595,
            "venue": "ZAK",
        },
        {
            "id": "mock_004",
            "date": (today + timedelta(days=3)).isoformat() + "T15:00:00+02:00",
            "status": "NS",
            "league": "PrvaLiga",
            "league_id": 218,
            "round": "Regular Season - 28",
            "home_team": "NK Mura",
            "home_team_id": 1600,
            "away_team": "NK Radomlje",
            "away_team_id": 14370,
            "venue": "Fazanerija",
        },
        {
            "id": "mock_005",
            "date": (today + timedelta(days=4)).isoformat() + "T15:00:00+02:00",
            "status": "NS",
            "league": "2SNL",
            "league_id": 219,
            "round": "Regular Season - 25",
            "home_team": "NK Nafta 1903",
            "home_team_id": 14372,
            "away_team": "NK Aluminij",
            "away_team_id": 10576,
            "venue": "Lendava",
        },
        {
            "id": "mock_006",
            "date": (today + timedelta(days=5)).isoformat() + "T17:00:00+02:00",
            "status": "NS",
            "league": "2SNL",
            "league_id": 219,
            "round": "Regular Season - 25",
            "home_team": "NK Drava Ptuj",
            "home_team_id": 10578,
            "away_team": "NK Ankaran",
            "away_team_id": 14371,
            "venue": "Štadion ob Dravi",
        },
    ]
    
    if league_name:
        return [m for m in mocks if m["league"] == league_name]
    return mocks


def _get_mock_form() -> dict:
    import random
    results = random.choices(["W", "D", "L"], weights=[0.45, 0.25, 0.30], k=7)
    goals_s = [random.randint(0, 3) for _ in range(7)]
    goals_c = [random.randint(0, 2) for _ in range(7)]
    return {
        "form": results,
        "form_string": "".join(results[-5:]),
        "avg_scored": round(sum(goals_s) / 7, 2),
        "avg_conceded": round(sum(goals_c) / 7, 2),
        "clean_sheets": sum(1 for g in goals_c if g == 0),
        "btts_count": sum(1 for s, c in zip(goals_s, goals_c) if s > 0 and c > 0),
        "games_analyzed": 7
    }


def _get_mock_h2h() -> dict:
    return {
        "total_matches": 8,
        "home_wins": 3,
        "draws": 2,
        "away_wins": 3,
        "avg_goals": 2.4,
        "btts_pct": 62.5,
        "over25_pct": 50.0,
    }


def _get_mock_standings() -> list[dict]:
    teams = [
        ("NK Olimpija Ljubljana", 1598, 58, 27),
        ("NK Maribor", 1601, 54, 27),
        ("NK Celje", 1594, 48, 27),
        ("NK Koper", 2279, 42, 27),
        ("NK Bravo", 10203, 36, 27),
        ("NK Mura", 1600, 30, 27),
        ("NK Domžale", 1595, 28, 27),
        ("NK Radomlje", 14370, 20, 27),
    ]
    return [
        {
            "rank": i + 1,
            "team_id": t[1],
            "team_name": t[0],
            "points": t[2],
            "played": t[3],
            "won": t[2] // 3,
            "drawn": (t[2] % 3),
            "lost": t[3] - (t[2] // 3) - (t[2] % 3),
            "goals_for": 30 + (8 - i) * 3,
            "goals_against": 15 + i * 4,
            "goal_diff": (30 + (8 - i) * 3) - (15 + i * 4),
            "form": "WWDLW" if i < 3 else "DWLLL",
        }
        for i, t in enumerate(teams)
    ]
