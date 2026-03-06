import os
from fastapi import APIRouter
import httpx
from datetime import date, timedelta

router = APIRouter()

@router.get("/api/debug")
async def debug():
    key = os.getenv("API_FOOTBALL_KEY", "")
    result = {
        "api_key_loaded": bool(key),
        "api_key_preview": key[:6] + "..." if key else "NOT SET",
    }

    if not key:
        return result

    headers = {
        "x-apisports-key": key,
        "x-rapidapi-host": "v3.football.api-sports.io",
    }

    async with httpx.AsyncClient(timeout=10) as client:
        # 1. Check account status
        try:
            resp = await client.get("https://v3.football.api-sports.io/status", headers=headers)
            data = resp.json()
            # API returns {"response": {...}} or errors as string
            if isinstance(data.get("response"), dict):
                req = data["response"].get("requests", {})
                result["requests_used"]      = req.get("current", "?")
                result["requests_limit_day"] = req.get("limit_day", "?")
                result["plan"] = data["response"].get("subscription", {}).get("plan", {}).get("name", "?")
                result["api_status"] = "OK"
            else:
                result["api_status"] = f"unexpected response: {data}"
        except Exception as e:
            result["api_status"] = f"ERROR: {e}"

        # 2. Try fetching fixtures for next 7 days - PrvaLiga
        try:
            today    = date.today()
            end_date = today + timedelta(days=7)
            resp2 = await client.get(
                "https://v3.football.api-sports.io/fixtures",
                headers=headers,
                params={"league": 218, "season": 2024, "from": str(today), "to": str(end_date)}
            )
            data2 = resp2.json()
            fixtures = data2.get("response", [])
            result["prvaliga_fixtures_next7d"] = len(fixtures)
            result["prvaliga_sample"] = [
                f"{f['teams']['home']['name']} vs {f['teams']['away']['name']} — {f['fixture']['date'][:10]}"
                for f in fixtures[:3]
            ]
            result["api_errors"] = data2.get("errors", [])
        except Exception as e:
            result["fixtures_error"] = str(e)

    return result
