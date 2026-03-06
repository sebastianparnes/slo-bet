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
    """Test OddsPortal scraping for 1xbet odds."""
    import httpx, re, json
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/json,*/*",
        "Referer": "https://www.oddsportal.com/",
    }
    result = {}
    async with httpx.AsyncClient(timeout=15, headers=headers) as client:
        try:
            r = await client.get("https://www.oddsportal.com/football/slovenia/prva-liga/")
            result["status"] = r.status_code
            result["content_length"] = len(r.text)
            # Look for team names in the page
            teams = re.findall(r'"homeTeam"\s*:\s*"([^"]+)"', r.text)[:5]
            result["teams_found"] = teams
            # Look for match URLs
            urls = re.findall(r'href="(/football/slovenia/[^"]+/)"', r.text)[:5]
            result["match_urls"] = list(set(urls))
        except Exception as e:
            result["error"] = str(e)
    return result
