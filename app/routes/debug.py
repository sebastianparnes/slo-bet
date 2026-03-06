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


@router.get("/api/debug/xbet")
async def debug_xbet():
    """Test 1xbet endpoints to verify league IDs."""
    import httpx
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json",
        "Referer": "https://1xbet.com/en/line/football",
    }
    result = {}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        for name, lid in [("PrvaLiga_118593", 118593), ("2SNL_270435", 270435)]:
            try:
                r = await client.get(
                    "https://1xbet.com/LineFeed/GetChampionship",
                    params={"championshipId": lid, "lng": "en", "country": 1, "partner": 3, "getEmpty": True}
                )
                d = r.json()
                games = d.get("Value", {}).get("GE", []) or d.get("Value", []) or []
                result[name] = {
                    "status": r.status_code,
                    "games_count": len(games),
                    "sample": [
                        f"{g.get('O1','?')} vs {g.get('O2','?')}"
                        for g in games[:3]
                    ] if games else [],
                    "raw_keys": list(d.keys())[:8] if d else []
                }
            except Exception as e:
                result[name] = {"error": str(e)}
    return result
