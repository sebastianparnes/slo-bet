import os
from fastapi import APIRouter
import httpx

router = APIRouter()

@router.get("/api/debug")
async def debug():
    """Quick diagnostic — shows if key is loaded and API responds."""
    key = os.getenv("API_FOOTBALL_KEY", "")
    result = {
        "api_key_loaded": bool(key),
        "api_key_preview": key[:6] + "..." if key else "NOT SET",
    }

    if key:
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(
                    "https://v3.football.api-sports.io/status",
                    headers={"x-apisports-key": key, "x-rapidapi-host": "v3.football.api-sports.io"}
                )
                data = resp.json()
                sub = data.get("response", {}).get("subscription", {})
                result["api_status"] = "OK"
                result["plan"] = sub.get("plan", {}).get("name", "unknown")
                result["requests_today"] = data.get("response", {}).get("requests", {})
        except Exception as e:
            result["api_status"] = f"ERROR: {e}"

    return result
