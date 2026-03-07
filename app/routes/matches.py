from fastapi import APIRouter, HTTPException, Query
from app.football_api import (
    fetch_upcoming_matches, fetch_team_form,
    fetch_h2h, fetch_standings
)
from app.analysis_engine import analyze_match
from app.odds_scraper import get_odds_for
import asyncio

router = APIRouter()


@router.get("/upcoming")
async def get_upcoming_matches(days: int = Query(5, ge=1, le=10)):
    """Get raw upcoming fixtures (no analysis)."""
    matches = await fetch_upcoming_matches(days_ahead=days)
    return {"count": len(matches), "days_ahead": days, "matches": matches}


@router.get("/analyzed-all")
async def get_all_upcoming_analyzed(days: int = Query(5, ge=1, le=10)):
    """All upcoming matches with full analysis + live 1xbet odds."""
    matches = await fetch_upcoming_matches(days_ahead=days)

    async def analyse_one(m: dict) -> dict:
        league_id = m.get("league_id", 218)
        try:
            home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
                fetch_team_form(m["home_team_id"], league_id),
                fetch_team_form(m["away_team_id"], league_id),
                fetch_h2h(m["home_team_id"], m["away_team_id"]),
                fetch_standings(league_id),
                get_odds_for(m["home_team"], m["away_team"], m["league"]),
            )
            return analyze_match(m, home_form, away_form, h2h, standings, xbet_odds)
        except Exception as e:
            return {
                "match_id":  m["id"],
                "home_team": m["home_team"],
                "away_team": m["away_team"],
                "league":    m["league"],
                "match_date": m["date"],
                "error": str(e),
            }

    results = await asyncio.gather(*[analyse_one(m) for m in matches])
    results = sorted(results, key=lambda x: x.get("overall_confidence", 0), reverse=True)
    return {"count": len(results), "days_ahead": days, "matches": list(results)}


@router.get("/{match_id}/analysis")
async def get_match_analysis(match_id: str):
    """Full analysis for a single match by ID."""
    matches = await fetch_upcoming_matches(days_ahead=10)
    match = next((m for m in matches if m["id"] == match_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    league_id = match.get("league_id", 218)
    home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
        fetch_team_form(match["home_team_id"], league_id),
        fetch_team_form(match["away_team_id"], league_id),
        fetch_h2h(match["home_team_id"], match["away_team_id"]),
        fetch_standings(league_id),
        get_odds_for(match["home_team"], match["away_team"], match["league"]),
    )
    return analyze_match(match, home_form, away_form, h2h, standings, xbet_odds)
