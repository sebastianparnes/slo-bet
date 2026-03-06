"""
Result Poller — usa TheSportsDB para verificar resultados (gratis, sin key)
Corre cada 15 minutos desde el startup de FastAPI.
"""
import asyncio
import httpx
import re
from datetime import date
from app.database import get_connection

BASE = "https://www.thesportsdb.com/api/v1/json/3"
POLL_INTERVAL = 15 * 60


def evaluate_bet(bet: dict, home_goals: int, away_goals: int) -> str:
    t, sel = bet.get("bet_type",""), bet.get("bet_selection","")
    hg, ag, total = home_goals, away_goals, home_goals + away_goals
    if t == "1X2":
        outcome = "1" if hg > ag else ("X" if hg == ag else "2")
        return "win" if sel == outcome else "loss"
    if t == "double_chance":
        if sel == "1X": return "win" if hg >= ag else "loss"
        if sel == "X2": return "win" if ag >= hg else "loss"
        if sel == "12": return "win" if hg != ag else "loss"
    if t == "over_under":
        line = float(sel.split("_")[1]) if "_" in sel else 2.5
        if sel.startswith("over"):  return "win" if total > line else "loss"
        if sel.startswith("under"): return "win" if total < line else "loss"
    if t == "btts":
        both = hg > 0 and ag > 0
        if sel == "yes": return "win" if both else "loss"
        if sel == "no":  return "win" if not both else "loss"
    return "loss"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", (s or "").lower())

def _match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    return na == nb or na in nb or nb in na


async def find_result_tsdb(bet: dict) -> dict | None:
    match_date = (bet.get("match_date") or "")[:10]
    if not match_date:
        return None

    home = bet.get("home_team", "")
    away = bet.get("away_team", "")

    async with httpx.AsyncClient(timeout=12) as client:
        try:
            # Get past results for PrvaLiga
            resp = await client.get(f"{BASE}/eventspastleague.php", params={"id": "4966"})
            events = resp.json().get("events") or []
        except:
            return None

    for e in events:
        if e.get("dateEvent", "")[:10] != match_date:
            continue
        if e.get("intHomeScore") is None:
            continue
        eh = e.get("strHomeTeam", "")
        ea = e.get("strAwayTeam", "")
        if _match(home, eh) and _match(away, ea):
            hg = int(e["intHomeScore"])
            ag = int(e["intAwayScore"])
            return {"home_goals": hg, "away_goals": ag, "score": f"{hg}-{ag}"}

    return None


async def poll_once():
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today().isoformat()
    pending = cursor.execute(
        "SELECT * FROM bet_history WHERE result='pending' AND match_date <= ?", (today,)
    ).fetchall()

    if not pending:
        conn.close()
        return

    print(f"[Poller] Checking {len(pending)} pending bet(s)...")
    updated = 0

    for bet in pending:
        bet = dict(bet)
        try:
            fixture = await find_result_tsdb(bet)
            if not fixture:
                continue
            outcome = evaluate_bet(bet, fixture["home_goals"], fixture["away_goals"])
            actual_win = round(bet["stake"] * (bet["odds"] or 1), 2) if outcome == "win" else 0.0
            cursor.execute(
                "UPDATE bet_history SET result=?, match_result=?, actual_win=? WHERE id=?",
                (outcome, fixture["score"], actual_win, bet["id"])
            )
            print(f"[Poller] Bet #{bet['id']} → {fixture['score']} → {outcome.upper()}")
            updated += 1
        except Exception as e:
            print(f"[Poller] Error bet #{bet['id']}: {e}")

    conn.commit()
    conn.close()
    if updated:
        print(f"[Poller] ✅ Updated {updated} bet(s)")


async def start_poller():
    print(f"[Poller] Started — checking every {POLL_INTERVAL // 60} min via TheSportsDB")
    while True:
        try:
            await poll_once()
        except Exception as e:
            print(f"[Poller] Error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
