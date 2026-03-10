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
    """Chequear si Sofascore responde desde Railway — usa season ID dinámico"""
    from app.football_api import _get_season, SF_HEADERS
    sid = await _get_season(212)
    if not sid:
        return {"error": "No se pudo obtener season ID para PrvaLiga (tid=212)"}
    async with httpx.AsyncClient(timeout=10, headers=SF_HEADERS) as client:
        try:
            url = f"https://api.sofascore.com/api/v1/unique-tournament/212/season/{sid}/events/next/0"
            r = await client.get(url)
            data = r.json()
            events = data.get("events", [])
            # Show raw structure of first event so we can debug field names
            raw_first = {}
            if events:
                e = events[0]
                raw_first = {
                    "homeTeam_keys": list(e.get("homeTeam", {}).keys()),
                    "homeScore_keys": list(e.get("homeScore", {}).keys()),
                    "homeScore_val": e.get("homeScore", {}),
                    "startTimestamp": e.get("startTimestamp"),
                    "status_type": e.get("status", {}).get("type"),
                }
            return {
                "season_id": sid,
                "status": r.status_code,
                "events_found": len(events),
                "sample": [
                    {
                        "home": e.get("homeTeam",{}).get("name"),
                        "away": e.get("awayTeam",{}).get("name"),
                        "status": e.get("status",{}).get("type"),
                    }
                    for e in events[:3]
                ],
                "raw_first_event_debug": raw_first,
            }
        except Exception as e:
            return {"season_id": sid, "error": str(e)}


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
            from app.football_api import _get_season, SF_HEADERS
            sid = await _get_season(212)
            url = f"https://api.sofascore.com/api/v1/unique-tournament/212/season/{sid}/events/next/0"
            r = await client.get(url, headers=SF_HEADERS)
            events = r.json().get("events", [])
            results["sofascore"] = {"status": r.status_code, "season_id": sid, "events": len(events)}
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


@router.get("/api/debug/raw-team/{team_id}")
async def debug_raw_team(team_id: int):
    """Ver estructura RAW de Sofascore para un equipo — útil para debuggear campos"""
    from app.football_api import SF_HEADERS
    async with httpx.AsyncClient(timeout=12, headers=SF_HEADERS) as client:
        try:
            r = await client.get(f"https://api.sofascore.com/api/v1/team/{team_id}/events/previous/0")
            data = r.json()
            events = data.get("events", [])
            if not events:
                return {"status": r.status_code, "events": 0, "raw": data}
            e = events[0]
            return {
                "status": r.status_code,
                "total_events": len(events),
                "first_event_structure": {
                    "id": e.get("id"),
                    "status": e.get("status", {}).get("type"),
                    "startTimestamp": e.get("startTimestamp"),
                    "homeTeam": e.get("homeTeam", {}),
                    "awayTeam": e.get("awayTeam", {}),
                    "homeScore": e.get("homeScore", {}),
                    "awayScore": e.get("awayScore", {}),
                },
                "all_statuses": list({ev.get("status",{}).get("type","?") for ev in events}),
                "finished_count": sum(1 for ev in events if ev.get("status",{}).get("type") == "finished"),
            }
        except Exception as ex:
            return {"error": str(ex)}
