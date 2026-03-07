from fastapi import APIRouter, HTTPException, Query
from app.football_api import (
    fetch_upcoming_matches, fetch_team_form,
    fetch_team_form_for_event, fetch_h2h, fetch_standings,
)
from app.analysis_engine import analyze_match
from app.odds_scraper import get_odds_for
import asyncio

router = APIRouter()
SLO_LEAGUES = ["PrvaLiga", "2SNL"]


@router.get("/upcoming")
async def get_upcoming(days: int = Query(5, ge=1, le=10)):
    matches = await fetch_upcoming_matches(SLO_LEAGUES, days_ahead=days)
    return {"count": len(matches), "days_ahead": days, "matches": matches}


@router.get("/analyzed-all")
async def get_all_analyzed(days: int = Query(5, ge=1, le=10)):
    matches = await fetch_upcoming_matches(SLO_LEAGUES, days_ahead=days)
    results = await asyncio.gather(*[_analyse(m) for m in matches])
    results = sorted(results, key=lambda x: x.get("overall_confidence", 0), reverse=True)
    return {"count": len(results), "days_ahead": days, "matches": list(results)}


@router.get("/{match_id}/analysis")
async def get_match_analysis(match_id: str):
    matches = await fetch_upcoming_matches(SLO_LEAGUES, days_ahead=10)
    m = next((x for x in matches if x["id"] == match_id), None)
    if not m:
        raise HTTPException(404, f"Partido {match_id} no encontrado")
    return await _analyse(m)


async def _analyse(m: dict) -> dict:
    league   = m.get("league", "PrvaLiga")
    event_id = m.get("sofascore_id")
    try:
        # 1) Intentar pregame-form (datos reales para ese partido específico)
        if event_id:
            hf, af = await asyncio.gather(
                fetch_team_form_for_event(event_id, True),
                fetch_team_form_for_event(event_id, False),
            )
        else:
            hf = af = None

        # 2) Fallback a forma general del equipo
        if not hf or not af:
            hf, af = await asyncio.gather(
                fetch_team_form(m["home_team_id"], league),
                fetch_team_form(m["away_team_id"], league),
            )

        h2h = await fetch_h2h(event_id, m["home_team"], m["away_team"]) \
            if event_id else {"total_matches": 0, "source": "no_event_id"}

        standings, xbet_odds = await asyncio.gather(
            fetch_standings(league),
            get_odds_for(m["home_team"], m["away_team"], league),
        )

        return analyze_match(m, _enrich(hf), _enrich(af), h2h, standings, xbet_odds)

    except Exception as e:
        return {
            "match_id": m["id"], "home_team": m["home_team"], "away_team": m["away_team"],
            "league": league, "match_date": m.get("date", ""), "error": str(e),
        }


def _enrich(f: dict) -> dict:
    """Garantiza que el form dict tenga avg_scored/conceded para el motor."""
    if not f:
        return {"form": [], "form_string": "?????", "avg_scored": 1.2, "avg_conceded": 1.2,
                "clean_sheets": 0, "btts_count": 0, "games_analyzed": 0, "source": "empty"}
    if "avg_scored" not in f:
        f.update({"avg_scored": 1.3, "avg_conceded": 1.2, "clean_sheets": 0,
                  "btts_count": 0, "games_analyzed": len(f.get("form", []))})
    return f
