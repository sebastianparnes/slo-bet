import asyncio
import httpx
from datetime import date
from app.database import get_connection

API_KEY  = __import__('os').getenv("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {
    "x-apisports-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io",
}
POLL_INTERVAL = 15 * 60

def evaluate_bet(bet, home_goals, away_goals):
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

def _norm(name):
    import re
    name = (name or "").lower()
    name = re.sub(r"\b(nk|fc|sk|nd)\b", "", name)
    return re.sub(r"[^a-z0-9]", "", name).strip()

def _sim(a, b):
    if a == b: return 1.0
    if a in b or b in a: return 0.9
    def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
    b1, b2 = bg(a), bg(b)
    if not b1 or not b2: return 0.0
    return 2 * len(b1 & b2) / (len(b1) + len(b2))

async def find_fixture_result(bet):
    if not API_KEY:
        return None
    match_date = bet.get("match_date","")[:10]
    if not match_date:
        return None
    async with httpx.AsyncClient(timeout=12, headers=HEADERS) as client:
        resp = await client.get(f"{BASE_URL}/fixtures", params={"date": match_date, "season": 2024, "league": "218"})
        fixtures = resp.json().get("response", [])
        if not fixtures:
            resp2 = await client.get(f"{BASE_URL}/fixtures", params={"date": match_date, "season": 2024, "league": "219"})
            fixtures = resp2.json().get("response", [])
    if not fixtures:
        return None
    hn, an = _norm(bet.get("home_team","")), _norm(bet.get("away_team",""))
    for f in fixtures:
        if f["fixture"]["status"]["short"] not in ("FT","AET","PEN"):
            continue
        if _sim(hn, _norm(f["teams"]["home"]["name"])) > 0.55 and _sim(an, _norm(f["teams"]["away"]["name"])) > 0.55:
            hg = f["goals"]["home"] or 0
            ag = f["goals"]["away"] or 0
            return {"home_goals": hg, "away_goals": ag, "score": f"{hg}-{ag}"}
    return None

async def poll_once():
    conn = get_connection()
    cursor = conn.cursor()
    today = date.today().isoformat()
    pending = cursor.execute("SELECT * FROM bet_history WHERE result='pending' AND match_date <= ?", (today,)).fetchall()
    if not pending:
        conn.close()
        return
    print(f"[Poller] Checking {len(pending)} pending bet(s)...")
    updated = 0
    for bet in pending:
        bet = dict(bet)
        try:
            fixture = await find_fixture_result(bet)
            if not fixture:
                continue
            outcome = evaluate_bet(bet, fixture["home_goals"], fixture["away_goals"])
            actual_win = round(bet["stake"] * (bet["odds"] or 1), 2) if outcome == "win" else 0.0
            cursor.execute("UPDATE bet_history SET result=?, match_result=?, actual_win=? WHERE id=?",
                           (outcome, fixture["score"], actual_win, bet["id"]))
            print(f"[Poller] Bet #{bet['id']} -> {fixture['score']} -> {outcome.upper()}")
            updated += 1
        except Exception as e:
            print(f"[Poller] Error bet #{bet['id']}: {e}")
    conn.commit()
    conn.close()
    if updated:
        print(f"[Poller] Updated {updated} bet(s)")

async def start_poller():
    print(f"[Poller] Started - checking every {POLL_INTERVAL // 60} minutes")
    while True:
        try:
            await poll_once()
        except Exception as e:
            print(f"[Poller] Unexpected error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
