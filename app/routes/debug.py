import os
from fastapi import APIRouter
import httpx
from datetime import date, timedelta

router = APIRouter()

@router.get("/api/debug")
async def debug():
    key = os.getenv("API_FOOTBALL_KEY", "")
    result = {"api_key_loaded": bool(key), "api_key_preview": key[:6] + "..." if key else "NOT SET"}
    if not key:
        return result

    headers = {"x-apisports-key": key, "x-rapidapi-host": "v3.football.api-sports.io"}
    today    = date.today()
    end_date = today + timedelta(days=7)

    async with httpx.AsyncClient(timeout=15) as client:
        try:
            s = await client.get("https://v3.football.api-sports.io/status", headers=headers)
            sd = s.json()
            if isinstance(sd.get("response"), dict):
                result["requests_used"]  = sd["response"].get("requests", {}).get("current", "?")
                result["requests_limit"] = sd["response"].get("requests", {}).get("limit_day", "?")
                result["api_status"] = "OK"
        except Exception as e:
            result["api_status"] = f"ERROR: {e}"

        try:
            r = await client.get("https://v3.football.api-sports.io/fixtures",
                headers=headers,
                params={"league": 218, "season": 2025, "from": str(today), "to": str(end_date)})
            fx = r.json().get("response", [])
            result["season"] = 2025
            result["prvaliga_fixtures_next7d"] = len(fx)
            result["sample"] = [
                f"{f['teams']['home']['name']} vs {f['teams']['away']['name']} — {f['fixture']['date'][:10]}"
                for f in fx[:5]
            ]
        except Exception as e:
            result["fixtures_error"] = str(e)

    return result
