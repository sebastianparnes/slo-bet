import httpx
from fastapi import APIRouter
from datetime import date, timedelta

router = APIRouter()

@router.get("/api/debug")
async def debug():
    result = {}
    async with httpx.AsyncClient(timeout=15) as client:
        # Test upcoming fixtures
        try:
            r = await client.get("https://www.thesportsdb.com/api/v1/json/3/eventsnextleague.php",
                                 params={"id": "4966"})
            events = r.json().get("events") or []
            today = date.today()
            upcoming = [e for e in events if e.get("dateEvent","") >= str(today)]
            result["upcoming_count"] = len(upcoming)
            result["upcoming_sample"] = [
                f"{e['strHomeTeam']} vs {e['strAwayTeam']} — {e['dateEvent']}"
                for e in upcoming[:5]
            ]
        except Exception as e:
            result["upcoming_error"] = str(e)

        # Test past results
        try:
            r2 = await client.get("https://www.thesportsdb.com/api/v1/json/3/eventspastleague.php",
                                  params={"id": "4966"})
            past = r2.json().get("events") or []
            result["past_count"] = len(past)
            result["past_sample"] = [
                f"{e['strHomeTeam']} {e['intHomeScore']}-{e['intAwayScore']} {e['strAwayTeam']} — {e['dateEvent']}"
                for e in past[-3:]
            ]
        except Exception as e:
            result["past_error"] = str(e)

        # Test standings
        try:
            r3 = await client.get("https://www.thesportsdb.com/api/v1/json/3/lookuptable.php",
                                  params={"l": "4966", "s": "2025-2026"})
            table = r3.json().get("table") or []
            result["standings_count"] = len(table)
            result["standings_top3"] = [
                f"{t['intRank']}. {t['strTeam']} — {t['intPoints']}pts"
                for t in table[:3]
            ]
        except Exception as e:
            result["standings_error"] = str(e)

    return result
