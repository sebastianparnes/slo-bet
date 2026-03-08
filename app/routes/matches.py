from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.football_api import (
    fetch_upcoming_matches, fetch_team_form,
    fetch_h2h, fetch_standings,
    TOURNAMENT_IDS, XBET_LEAGUE_IDS,
)
from app.analysis_engine import analyze_match
from app.xbet_scraper import get_odds_for
import asyncio

router = APIRouter()


@router.get("/upcoming")
async def get_upcoming_matches(
    days: int = Query(7, ge=1, le=14),
    league: Optional[str] = None,
):
    leagues = [league] if league and league in TOURNAMENT_IDS else None
    matches = await fetch_upcoming_matches(days_ahead=days, leagues=leagues)
    return {"count": len(matches), "days_ahead": days, "matches": matches}


@router.get("/analyzed-all")
async def get_all_upcoming_analyzed(
    days: int = Query(7, ge=1, le=14),
    leagues: Optional[str] = Query(None, description="Comma-separated league keys"),
):
    if leagues:
        league_list = [l.strip() for l in leagues.split(",") if l.strip() in TOURNAMENT_IDS]
    else:
        league_list = None

    matches = await fetch_upcoming_matches(days_ahead=days, leagues=league_list)

    async def analyse_one(m: dict) -> dict:
        league_id = m.get("league_id", 118593)
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
                "match_id":   m["id"],
                "home_team":  m["home_team"],
                "away_team":  m["away_team"],
                "league":     m["league"],
                "match_date": m["date"],
                "error":      str(e),
            }

    results = await asyncio.gather(*[analyse_one(m) for m in matches])
    results = sorted(results, key=lambda x: x.get("overall_confidence", 0), reverse=True)
    return {"count": len(results), "days_ahead": days, "matches": list(results)}


@router.get("/{match_id}/analysis")
async def get_match_analysis(
    match_id: str,
    league: Optional[str] = None,
):
    leagues = [league] if league and league in TOURNAMENT_IDS else None
    matches = await fetch_upcoming_matches(days_ahead=14, leagues=leagues)
    match   = next((m for m in matches if str(m["id"]) == match_id), None)

    if not match:
        all_m = await fetch_upcoming_matches(days_ahead=14)
        match = next((m for m in all_m if str(m["id"]) == match_id), None)

    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    league_id = match.get("league_id", 118593)
    home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
        fetch_team_form(match["home_team_id"], league_id),
        fetch_team_form(match["away_team_id"], league_id),
        fetch_h2h(match["home_team_id"], match["away_team_id"]),
        fetch_standings(league_id),
        get_odds_for(match["home_team"], match["away_team"], match["league"]),
    )
    return analyze_match(match, home_form, away_form, h2h, standings, xbet_odds)
