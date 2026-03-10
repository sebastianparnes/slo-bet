from fastapi import APIRouter, HTTPException, Query
from app.football_api import fetch_upcoming_matches, fetch_team_form, fetch_h2h, fetch_standings
from app.analysis_engine import analyze_match
from app.xbet_scraper import get_odds_for
import asyncio

router = APIRouter()


@router.get("/upcoming")
async def get_upcoming_matches(days: int = Query(5, ge=1, le=10)):
    matches = await fetch_upcoming_matches(days_ahead=days)
    return {"count": len(matches), "days_ahead": days, "matches": matches}


@router.get("/analyzed-all")
async def get_all_upcoming_analyzed(days: int = Query(5, ge=1, le=10)):
    """All upcoming matches with full analysis + ar-1xbet odds."""
    matches = await fetch_upcoming_matches(days_ahead=days)

    async def analyse_one(m: dict) -> dict:
        league = m.get("league", "PrvaLiga")
        try:
            home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
                fetch_team_form(m["home_team_id"]),
                fetch_team_form(m["away_team_id"]),
                fetch_h2h(m["home_team_id"], m["away_team_id"]),
                fetch_standings(league),
                get_odds_for(m["home_team"], m["away_team"], league),
            )
            result = analyze_match(m, home_form, away_form, h2h, standings, xbet_odds)
            # Inject recent_matches into score_breakdown for modal display
            if "score_breakdown" in result:
                if "form" not in result["score_breakdown"]:
                    result["score_breakdown"]["form"] = {}
                result["score_breakdown"]["form"]["home_recent_matches"] = home_form.get("recent_matches", [])
                result["score_breakdown"]["form"]["away_recent_matches"] = away_form.get("recent_matches", [])
                result["score_breakdown"]["form"]["home_games_analyzed"] = home_form.get("games_analyzed", 0)
                result["score_breakdown"]["form"]["away_games_analyzed"] = away_form.get("games_analyzed", 0)
            return result
        except Exception as e:
            import traceback
            print(f"[matches] error {m.get('home_team')} vs {m.get('away_team')}: {e}")
            traceback.print_exc()
            return {
                "match_id":   m.get("id", m.get("match_id", "")),
                "home_team":  m["home_team"],
                "away_team":  m["away_team"],
                "league":     league,
                "match_date": m.get("date", m.get("match_date", "")),
                "error": str(e),
            }

    results = await asyncio.gather(*[analyse_one(m) for m in matches])
    results = sorted(results, key=lambda x: x.get("overall_confidence", 0), reverse=True)
    return {"count": len(results), "days_ahead": days, "matches": list(results)}


@router.get("/{match_id}/analysis")
async def get_match_analysis(match_id: str, league: str = "PrvaLiga"):
    matches = await fetch_upcoming_matches(days_ahead=10)
    match = next((m for m in matches if str(m.get("id", m.get("match_id",""))) == match_id), None)
    if not match:
        raise HTTPException(status_code=404, detail=f"Match {match_id} not found")

    lg = match.get("league", league)
    home_form, away_form, h2h, standings, xbet_odds = await asyncio.gather(
        fetch_team_form(match["home_team_id"]),
        fetch_team_form(match["away_team_id"]),
        fetch_h2h(match["home_team_id"], match["away_team_id"]),
        fetch_standings(lg),
        get_odds_for(match["home_team"], match["away_team"], lg),
    )
    result = analyze_match(match, home_form, away_form, h2h, standings, xbet_odds)
    if "score_breakdown" in result:
        if "form" not in result["score_breakdown"]:
            result["score_breakdown"]["form"] = {}
        result["score_breakdown"]["form"]["home_recent_matches"] = home_form.get("recent_matches", [])
        result["score_breakdown"]["form"]["away_recent_matches"] = away_form.get("recent_matches", [])
        result["score_breakdown"]["form"]["home_games_analyzed"] = home_form.get("games_analyzed", 0)
        result["score_breakdown"]["form"]["away_games_analyzed"] = away_form.get("games_analyzed", 0)
    return result
