from fastapi import APIRouter, Query
from app.football_api import fetch_standings, LEAGUES

router = APIRouter()


@router.get("/standings/{league}")
async def get_standings(league: str):
    """Get current standings for a Slovenian league."""
    league_id = LEAGUES.get(league)
    if not league_id:
        return {"error": f"League '{league}' not found. Options: {list(LEAGUES.keys())}"}
    
    standings = await fetch_standings(league_id)
    return {
        "league": league,
        "league_id": league_id,
        "standings": standings
    }


@router.get("/value-bets")
async def get_value_bets(days: int = Query(5, ge=1, le=10)):
    """
    Returns only the highest-confidence bets from upcoming matches.
    Shortcut endpoint for quick recommendations.
    """
    from app.routes.matches import get_all_upcoming_analyzed
    
    data = await get_all_upcoming_analyzed(days=days)
    
    value_bets = []
    for match in data.get("matches", []):
        if match.get("value_alert") or match.get("overall_confidence", 0) >= 65:
            top = match.get("top_recommendation")
            if top:
                value_bets.append({
                    "match": f"{match['home_team']} vs {match['away_team']}",
                    "league": match.get("league"),
                    "date": match.get("match_date"),
                    "recommendation": top["label"],
                    "confidence": top["confidence"],
                    "min_odds": top["min_odds"],
                    "risk": top["risk_level"],
                    "reasoning": top["reasoning"],
                    "overall_confidence": match.get("overall_confidence"),
                })
    
    value_bets.sort(key=lambda x: x["confidence"], reverse=True)
    
    return {
        "count": len(value_bets),
        "value_bets": value_bets,
    }
