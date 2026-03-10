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
    """Ver estructura RAW de Sofascore para un equipo"""
    from app.football_api import SF_HEADERS
    async with httpx.AsyncClient(timeout=12, headers=SF_HEADERS) as client:
        try:
            r = await client.get(f"https://api.sofascore.com/api/v1/team/{team_id}/events/previous/0")
            data = r.json()
            events = data.get("events", [])
            if not events:
                return {"status": r.status_code, "team_id": team_id, "events": 0, "raw_error": data}
            # Find a finished event for score structure
            finished = [e for e in events if e.get("status",{}).get("type") == "finished"]
            e = finished[0] if finished else events[0]
            return {
                "status": r.status_code,
                "team_id": team_id,
                "total_events": len(events),
                "finished_count": len(finished),
                "all_statuses": list({ev.get("status",{}).get("type","?") for ev in events}),
                "sample_finished_event": {
                    "status": e.get("status", {}).get("type"),
                    "homeTeam_name": e.get("homeTeam", {}).get("name"),
                    "awayTeam_name": e.get("awayTeam", {}).get("name"),
                    "homeScore": e.get("homeScore", {}),
                    "awayScore": e.get("awayScore", {}),
                    "startTimestamp": e.get("startTimestamp"),
                } if finished else {"note": "no finished events found", "first_event": e.get("status",{})},
            }
        except Exception as ex:
            return {"error": str(ex), "team_id": team_id}


@router.get("/api/debug/real-team-ids")
async def debug_real_team_ids():
    """Obtener IDs reales de Sofascore de los equipos del próximo partido de PrvaLiga"""
    from app.football_api import _fetch_sf_fixtures, _get_season, SF_HEADERS, TOURNAMENT_IDS
    fixtures = await _fetch_sf_fixtures("PrvaLiga", days_ahead=14)
    if not fixtures:
        return {"error": "No fixtures found for PrvaLiga"}
    result = []
    for e in fixtures[:5]:
        ht = e.get("homeTeam", {})
        at = e.get("awayTeam", {})
        result.append({
            "home": ht.get("name"), "home_sf_id": ht.get("id"),
            "away": at.get("name"), "away_sf_id": at.get("id"),
        })
    return {"fixtures": result, "use_these_ids_for_raw_team": [r["home_sf_id"] for r in result[:3]]}


@router.get("/api/debug/pipeline-test")
async def debug_pipeline_test(league: str = "PrvaLiga"):
    """Test completo: fixture → IDs → forma → análisis de UN partido"""
    from app.football_api import _fetch_sf_fixtures, fetch_team_form, fetch_h2h, fetch_standings, _parse_fixture
    from app.xbet_scraper import get_odds_for

    # Step 1: get fixture
    fixtures_raw = await _fetch_sf_fixtures(league, days_ahead=14)
    if not fixtures_raw:
        return {"step": "fixtures", "error": "No fixtures found", "league": league}

    e = fixtures_raw[0]
    ht = e.get("homeTeam", {})
    at = e.get("awayTeam", {})
    home_id = ht.get("id", 0)
    away_id = at.get("id", 0)
    match = _parse_fixture(e, league)

    result = {
        "step1_fixture": {"home": ht.get("name"), "home_id": home_id,
                          "away": at.get("name"), "away_id": away_id},
    }

    # Step 2: fetch form with REAL SF IDs
    import asyncio
    home_form, away_form = await asyncio.gather(
        fetch_team_form(home_id),
        fetch_team_form(away_id),
    )
    result["step2_home_form"] = {
        "games_analyzed": home_form.get("games_analyzed", 0),
        "form_string": home_form.get("form_string", ""),
        "avg_scored": home_form.get("avg_scored"),
        "recent_count": len(home_form.get("recent_matches", [])),
        "first_recent": home_form.get("recent_matches", [{}])[0] if home_form.get("recent_matches") else None,
        "is_mock": home_form.get("games_analyzed", 0) == 0,
    }
    result["step2_away_form"] = {
        "games_analyzed": away_form.get("games_analyzed", 0),
        "form_string": away_form.get("form_string", ""),
        "recent_count": len(away_form.get("recent_matches", [])),
        "is_mock": away_form.get("games_analyzed", 0) == 0,
    }

    # Step 3: H2H
    h2h = await fetch_h2h(home_id, away_id)
    result["step3_h2h"] = {"total_matches": h2h.get("total_matches", 0)}

    # Step 4: odds
    odds = await get_odds_for(match["home_team"], match["away_team"], league)
    result["step4_odds"] = {"found": odds is not None, "home": odds.get("home") if odds else None}

    result["overall_status"] = "✓ REAL" if home_form.get("games_analyzed", 0) > 0 else "⚠ MOCK (SF blocking form)"
    return result


@router.get("/api/debug/raw-form/{team_id}")
async def debug_raw_form(team_id: int):
    """Ver estructura RAW del primer evento de forma de un equipo (ID real de SF)"""
    from app.football_api import SF_HEADERS
    async with httpx.AsyncClient(timeout=12, headers=SF_HEADERS) as client:
        try:
            r = await client.get(f"https://api.sofascore.com/api/v1/team/{team_id}/events/previous/0")
            data = r.json()
            events = data.get("events", [])
            finished = [e for e in events if (e.get("status") or {}).get("type") == "finished"]
            if not finished:
                return {"team_id": team_id, "total": len(events),
                        "statuses": list({e.get("status",{}).get("type") for e in events}),
                        "note": "no finished events"}
            e = finished[0]
            return {
                "team_id": team_id,
                "total_events": len(events),
                "finished_count": len(finished),
                "first_finished": {
                    "status": e.get("status", {}).get("type"),
                    "homeTeam": e.get("homeTeam", {}),
                    "awayTeam": e.get("awayTeam", {}),
                    "homeScore": e.get("homeScore", {}),
                    "awayScore": e.get("awayScore", {}),
                    "all_keys": list(e.keys()),
                }
            }
        except Exception as ex:
            return {"error": str(ex)}
