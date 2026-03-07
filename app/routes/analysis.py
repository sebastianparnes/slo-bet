from fastapi import APIRouter, Query
from app.football_api import fetch_standings, TOURNAMENT_IDS

router = APIRouter()


@router.get("/standings/{league}")
async def get_standings(league: str):
    if league not in TOURNAMENT_IDS:
        return {"error": f"Liga '{league}' no encontrada. Opciones: {list(TOURNAMENT_IDS.keys())}"}
    standings = await fetch_standings(league)
    return {"league": league, "standings": standings}


@router.get("/value-bets")
async def get_value_bets(days: int = Query(5, ge=1, le=10)):
    from app.routes.matches import get_all_analyzed
    data = await get_all_analyzed(days=days)
    value_bets = []
    for match in data.get("matches", []):
        if match.get("value_alert") or match.get("overall_confidence", 0) >= 65:
            top = match.get("top_recommendation")
            if top:
                value_bets.append({
                    "match":               f"{match['home_team']} vs {match['away_team']}",
                    "league":              match.get("league"),
                    "date":                match.get("match_date"),
                    "recommendation":      top["label"],
                    "confidence":          top["confidence"],
                    "min_odds":            top["min_odds"],
                    "risk":                top["risk_level"],
                    "reasoning":           top["reasoning"],
                    "overall_confidence":  match.get("overall_confidence"),
                })
    value_bets.sort(key=lambda x: x["confidence"], reverse=True)
    return {"count": len(value_bets), "value_bets": value_bets}
