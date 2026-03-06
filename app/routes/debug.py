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
    today = date.today()

    async with httpx.AsyncClient(timeout=15) as client:
        # Requests status
        try:
            s = await client.get("https://v3.football.api-sports.io/status", headers=headers)
            sd = s.json()
            if isinstance(sd.get("response"), dict):
                result["requests_used"]  = sd["response"].get("requests", {}).get("current", "?")
                result["requests_limit"] = sd["response"].get("requests", {}).get("limit_day", "?")
        except: pass

        # What seasons does api-football have for PrvaLiga (218)?
        try:
            sr = await client.get("https://v3.football.api-sports.io/leagues",
                                  headers=headers, params={"id": 218})
            seasons_raw = sr.json().get("response", [])
            result["prvaliga_seasons"] = [
                {"year": s["year"], "start": s["start"], "end": s["end"], "current": s.get("current")}
                for lg in seasons_raw for s in lg.get("seasons", [])
            ][-6:]  # last 6 seasons
        except Exception as e:
            result["seasons_error"] = str(e)

        # Try next 30 days with both seasons
        for season in [2024, 2025]:
            try:
                r = await client.get(
                    "https://v3.football.api-sports.io/fixtures",
                    headers=headers,
                    params={"league": 218, "season": season,
                            "from": str(today), "to": str(today + timedelta(days=30))}
                )
                fx = r.json().get("response", [])
                result[f"s{season}_next30d"] = len(fx)
                if fx:
                    result[f"s{season}_sample"] = [
                        f"{f['teams']['home']['name']} vs {f['teams']['away']['name']} — {f['fixture']['date'][:10]}"
                        for f in fx[:5]
                    ]
            except Exception as e:
                result[f"s{season}_error"] = str(e)

        # Also check last 3 played matches to confirm API works
        try:
            r2 = await client.get(
                "https://v3.football.api-sports.io/fixtures",
                headers=headers,
                params={"league": 218, "season": 2024, "last": 3}
            )
            fx2 = r2.json().get("response", [])
            result["last_played"] = [
                f"{f['teams']['home']['name']} vs {f['teams']['away']['name']} — {f['fixture']['date'][:10]} ({f['fixture']['status']['short']})"
                for f in fx2
            ]
        except: pass

    return result
