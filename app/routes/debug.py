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
    """Test Cloudflare proxy → 1xbet pipeline."""
    import os, httpx
    proxy = os.getenv("XBET_PROXY_URL", "")
    result = {
        "proxy_configured": bool(proxy),
        "proxy_url": proxy or "NOT SET — add XBET_PROXY_URL in Railway variables",
    }
    if not proxy:
        return result

    async with httpx.AsyncClient(timeout=15) as client:
        # Test 1: proxy is alive
        try:
            r = await client.get(proxy, params={"path": "/en/line/football", "params": ""})
            result["proxy_status"] = r.status_code
            result["proxy_response_len"] = len(r.text)
        except Exception as e:
            result["proxy_error"] = str(e)
            return result

        # Test 2: fetch PrvaLiga games
        try:
            r2 = await client.get(proxy, params={
                "path": "/LineFeed/GetChampionship",
                "params": "championshipId=118593&lng=en&isSubGames=true&GroupEvents=true&allEventsGrouped=true&mode=4"
            })
            result["xbet_status"] = r2.status_code
            if r2.status_code == 200:
                data = r2.json()
                games = (data.get("Value", {}).get("TopEvents") or
                         data.get("Value", {}).get("Events") or
                         data.get("Value") or [])
                if isinstance(games, list):
                    result["games_found"] = len(games)
                    result["sample"] = [
                        f"{g.get('O1','?')} vs {g.get('O2','?')}"
                        for g in games[:5]
                    ]
                else:
                    result["raw_keys"] = list(data.keys())[:10]
                    result["value_type"] = type(data.get("Value")).__name__
            else:
                result["xbet_response"] = r2.text[:300]
        except Exception as e:
            result["xbet_error"] = str(e)

    return result
