from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from app.football_api import (
    fetch_upcoming_matches, fetch_team_form,
    fetch_h2h, fetch_standings, LEAGUES, TOURNAMENT_IDS
)
from app.analysis_engine import analyze_match
from app.xbet_scraper import get_odds_for
import asyncio

router = APIRouter()

# All leagues served by this endpoint (SLO + EUR + any others not ARG-specific)
ALL_SUPPORTED = list(LEAGUES.keys())


@router.get("/upcoming")
async def get_upcoming_matches(
    days: int = Query(5, ge=1, le=14),
    league: Optional[str] = Query(None, description="Filter by league name"),
):
    leagues = [league] if league and league in LEAGUES else None
    matches = await fetch_upcoming_matches(leagues=leagues, days_ahead=days)
    return {"count": len(matches), "days_ahead": days, "matches": matches}


@router.get("/analyzed-all")
async def get_all_upcoming_analyzed(
    days: int = Query(5, ge=1, le=14),
    league: Optional[str] = Query(None, description="Specific league to analyze"),
):
    """All upcoming matches with full analysis + live 1xbet odds.
    Supports any league in LEAGUES: PrvaLiga, 2SNL, LaLiga, PremierLeague,
    SerieA, Bundesliga, Ligue1, ChampionsLeague, PrimeraDivision, PrimeraNacional.
    """
    leagues = [league] if league and league in LEAGUES else None
    matches = await fetch_upcoming_matches(leagues=leagues, days_ahead=days)

    async def analyse_one(m: dict) -> dict:
        league_name = m.get("league", "PrvaLiga")
        league_id   = m.get("league_id") or TOURNAMENT_IDS.get(league_name, 212)
        try:
            home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
                fetch_team_form(m["home_team_id"], league_id),
                fetch_team_form(m["away_team_id"], league_id),
                fetch_h2h(
                    event_id=m.get("sofascore_id") or m.get("id"),
                    home_team=m["home_team"], away_team=m["away_team"],
                    home_team_id=m["home_team_id"], away_team_id=m["away_team_id"],
                ),
                fetch_standings(league_id),
                get_odds_for(m["home_team"], m["away_team"], league_name),
            )
            return analyze_match(m, home_form, away_form, h2h, standings, xbet_odds)
        except Exception as e:
            return {
                "match_id":   m["id"],
                "home_team":  m["home_team"],
                "away_team":  m["away_team"],
                "league":     league_name,
                "match_date": m.get("date", ""),
                "error":      str(e),
            }

    results = await asyncio.gather(*[analyse_one(m) for m in matches])
    results = sorted(results, key=lambda x: x.get("overall_confidence", 0), reverse=True)
    return {"count": len(results), "days_ahead": days, "matches": list(results)}


@router.get("/{match_id}/analysis")
async def get_match_analysis(match_id: str, league: Optional[str] = Query(None), days: int = Query(14)):
    """Full analysis for a single match by ID."""
    # Try the given league first, then all leagues as fallback
    search_leagues = [league] if league and league in LEAGUES else list(LEAGUES.keys())
    matches = await fetch_upcoming_matches(leagues=search_leagues, days_ahead=days)
    match = next((m for m in matches if m["id"] == match_id), None)
    if not match:
        raise HTTPException(404, f"Match {match_id} not found in {search_leagues}")

    league_name = match.get("league", "PrvaLiga")
    league_id   = match.get("league_id") or TOURNAMENT_IDS.get(league_name, 212)

    home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
        fetch_team_form(match["home_team_id"], league_id),
        fetch_team_form(match["away_team_id"], league_id),
        fetch_h2h(
            event_id=match.get("sofascore_id") or match.get("id"),
            home_team=match["home_team"], away_team=match["away_team"],
            home_team_id=match["home_team_id"], away_team_id=match["away_team_id"],
        ),
        fetch_standings(league_id),
        get_odds_for(match["home_team"], match["away_team"], league_name),
    )
    return analyze_match(match, home_form, away_form, h2h, standings, xbet_odds)
