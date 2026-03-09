"""
Debug router — endpoints para diagnosticar el estado real del sistema
Accesibles en /api/debug/*
"""
import os, httpx
from fastapi import APIRouter
from app.football_api import fetch_upcoming_matches, _fetch_sf_form, _fetch_sf_standings, _fetch_sf_h2h, TOURNAMENT_IDS
from app.xbet_scraper import _fetch_league_games, LEAGUE_IDS

router = APIRouter()


@router.get("/api/debug/form/{league}/{team_id}")
async def debug_form(league: str, team_id: int):
    """Ver forma real de un equipo desde Sofascore"""
    form = await _fetch_sf_form(team_id, last_n=7)
    return {"league": league, "team_id": team_id, "form": form}


@router.get("/api/debug/fixtures/{league}")
async def debug_fixtures(league: str, days: int = 7):
    """Ver fixtures próximos de una liga"""
    from app.football_api import _fetch_sf_fixtures, TOURNAMENT_IDS
    tid = TOURNAMENT_IDS.get(league)
    if not tid:
        return {"error": f"Liga desconocida: {league}", "available": list(TOURNAMENT_IDS.keys())}
    fixtures = await _fetch_sf_fixtures(league, days_ahead=days)
    return {
        "league": league,
        "tournament_id": tid,
        "days_ahead": days,
        "count": len(fixtures),
        "fixtures": [
            {
                "id": f.get("id"),
                "home": f.get("homeTeam", {}).get("name"),
                "home_id": f.get("homeTeam", {}).get("id"),
                "away": f.get("awayTeam", {}).get("name"),
                "away_id": f.get("awayTeam", {}).get("id"),
                "date": f.get("startTimestamp"),
                "status": f.get("status", {}).get("type"),
            }
            for f in fixtures[:20]
        ]
    }


@router.get("/api/debug/standings/{league}")
async def debug_standings(league: str):
    """Ver tabla de posiciones real de Sofascore"""
    st = await _fetch_sf_standings(league)
    return {"league": league, "standings": st}


@router.get("/api/debug/odds/{league}")
async def debug_odds(league: str):
    """Ver cuotas reales de ar-1xbet para una liga"""
    games = await _fetch_league_games(league)
    champ_id = LEAGUE_IDS.get(league, "?")
    return {
        "league": league,
        "champ_id": champ_id,
        "games_found": len(games),
        "proxy_url": os.getenv("XBET_PROXY_URL", "NOT SET"),
        "sample": [
            {
                "home": g.get("O1") or g.get("HT"),
                "away": g.get("O2") or g.get("AT"),
                "keys": list(g.keys())[:8],
            }
            for g in games[:5]
        ],
        "raw_first": games[0] if games else None,
    }


@router.get("/api/debug/analysis-sample")
async def debug_analysis_sample(league: str = "PrvaLiga"):
    """Ver análisis completo del primer partido de una liga"""
    from app.football_api import fetch_upcoming_matches
    from app.analysis_engine import analyze_match
    matches = await fetch_upcoming_matches(days_ahead=7, leagues=[league])
    if not matches:
        return {"error": "Sin partidos", "league": league}
    m = matches[0]
    result = await analyze_match(m, league)
    return result


@router.get("/api/debug/sofascore-proxy")
async def debug_sofascore_proxy():
    """Chequear si Sofascore responde desde Railway"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(
                "https://api.sofascore.com/api/v1/unique-tournament/212/season/63814/events/next/0",
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Accept": "application/json",
                    "Referer": "https://www.sofascore.com/",
                }
            )
            data = r.json()
            events = data.get("events", [])
            return {
                "status": r.status_code,
                "events_found": len(events),
                "sample": [
                    {"home": e.get("homeTeam",{}).get("name"), "away": e.get("awayTeam",{}).get("name")}
                    for e in events[:3]
                ]
            }
        except Exception as e:
            return {"error": str(e)}


@router.get("/api/debug/full")
async def debug_full():
    """Estado completo del sistema en una sola llamada"""
    from app.football_api import fetch_upcoming_matches, TOURNAMENT_IDS
    from app.xbet_scraper import LEAGUE_IDS as XBET_IDS

    results = {
        "env": {
            "XBET_PROXY_URL": os.getenv("XBET_PROXY_URL", "NOT SET"),
            "TURSO_URL": os.getenv("TURSO_URL", "NOT SET")[:30] + "..." if os.getenv("TURSO_URL") else "NOT SET",
        },
        "leagues_configured": list(TOURNAMENT_IDS.keys()),
        "xbet_ids": XBET_IDS,
    }

    # Test Sofascore
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(
                "https://api.sofascore.com/api/v1/unique-tournament/212/season/63814/events/next/0",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", "Referer": "https://www.sofascore.com/"}
            )
            events = r.json().get("events", [])
            results["sofascore"] = {"status": r.status_code, "events": len(events)}
        except Exception as e:
            results["sofascore"] = {"error": str(e)}

    # Test ar-xbet proxy
    proxy = os.getenv("XBET_PROXY_URL", "")
    if proxy:
        async with httpx.AsyncClient(timeout=10) as client:
            try:
                r = await client.get(f"{proxy}/xbet/odds", params={"league": "prva"})
                results["xbet_proxy"] = {"status": r.status_code, "len": len(r.text)}
            except Exception as e:
                results["xbet_proxy"] = {"error": str(e)}
    else:
        results["xbet_proxy"] = {"error": "XBET_PROXY_URL not set"}

    # Test fetch matches PrvaLiga
    try:
        matches = await fetch_upcoming_matches(days_ahead=7, leagues=["PrvaLiga"])
        results["prvaliga_matches"] = len(matches)
        results["prvaliga_sample"] = [
            {"home": m.get("homeTeam",{}).get("name") or m.get("home_team"),
             "away": m.get("awayTeam",{}).get("name") or m.get("away_team"),
             "id": m.get("id")}
            for m in matches[:3]
        ]
    except Exception as e:
        results["prvaliga_matches"] = f"ERROR: {e}"

    return results
