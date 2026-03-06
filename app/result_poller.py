"""
Result Poller — Background Service
====================================
Corre cada 15 minutos. Para cada apuesta con result='pending':
  1. Busca el partido en API-Football por fixture_id o por nombre de equipos + fecha
  2. Si el partido terminó (status FT/AET/PEN), obtiene el marcador final
  3. Evalúa si la apuesta ganó o perdió según el tipo y selección
  4. Actualiza la DB automáticamente

Se lanza como tarea de fondo en el startup de FastAPI (lifespan).
"""

import asyncio
import httpx
from datetime import datetime, date, timedelta
from app.database import get_connection

API_KEY  = __import__('os').getenv("API_FOOTBALL_KEY", "")
BASE_URL = "https://v3.football.api-sports.io"
HEADERS  = {
    "x-apisports-key": API_KEY,
    "x-rapidapi-host": "v3.football.api-sports.io",
}

POLL_INTERVAL = 15 * 60  # 15 minutos


# ── Bet evaluation logic ───────────────────────────────────────────────────

def evaluate_bet(bet: dict, home_goals: int, away_goals: int) -> str:
    """
    Returns 'win' or 'loss' given the final score and the bet.
    bet_type: 1X2 | double_chance | over_under | btts
    bet_selection: 1 | X | 2 | 1X | X2 | 12 | over_2.5 | under_2.5 | over_1.5 | under_1.5 | yes | no
    """
    t   = bet.get("bet_type", "")
    sel = bet.get("bet_selection", "")
    hg, ag = home_goals, away_goals
    total  = hg + ag

    if t == "1X2":
        outcome = "1" if hg > ag else ("X" if hg == ag else "2")
        return "win" if sel == outcome else "loss"

    if t == "double_chance":
        if sel == "1X": return "win" if hg >= ag else "loss"
        if sel == "X2": return "win" if ag >= hg else "loss"
        if sel == "12": return "win" if hg != ag else "loss"

    if t == "over_under":
        line = float(sel.split("_")[1]) if "_" in sel else 2.5
        if sel.startswith("over"):  return "win" if total > line  else "loss"
        if sel.startswith("under"): return "win" if total < line  else "loss"

    if t == "btts":
        both_scored = hg > 0 and ag > 0
        if sel == "yes": return "win" if both_scored     else "loss"
        if sel == "no":  return "win" if not both_scored else "loss"

    return "loss"  # fallback


# ── API-Football fixture lookup ────────────────────────────────────────────

async def find_fixture_result(bet: dict) -> dict | None:
    """
    Tries to find the finished fixture for a pending bet.
    Returns {"home_goals": int, "away_goals": int, "score": str} or None.
    """
    if not API_KEY:
        return None

    match_date = bet.get("match_date", "")[:10]  # YYYY-MM-DD
    if not match_date:
        return None

    # Search by team names + date
    async with httpx.AsyncClient(timeout=12, headers=HEADERS) as client:
        # Try searching by date and both team names
        resp = await client.get(f"{BASE_URL}/fixtures", params={
            "date":   match_date,
            "season": 2024,
            "league": "218",   # PrvaLiga — fallback to also try 219
        })
        data = resp.json()
        fixtures = data.get("response", [])

        # If not found in PrvaLiga, try 2SNL
        if not fixtures:
            resp2 = await client.get(f"{BASE_URL}/fixtures", params={
                "date":   match_date,
                "season": 2024,
                "league": "219",
            })
            fixtures = resp2.json().get("response", [])

    if not fixtures:
        return None

    # Find the fixture matching our bet's teams
    home_norm = _norm(bet.get("home_team", ""))
    away_norm = _norm(bet.get("away_team", ""))

    for f in fixtures:
        status = f["fixture"]["status"]["short"]
        if status not in ("FT", "AET", "PEN"):
            continue  # not finished yet

        fh = _norm(f["teams"]["home"]["name"])
        fa = _norm(f["teams"]["away"]["name"])

        if _sim(home_norm, fh) > 0.55 and _sim(away_norm, fa) > 0.55:
            hg = f["goals"]["home"] or 0
            ag = f["goals"]["away"] or 0
            return {
                "home_goals": hg,
                "away_goals": ag,
                "score": f"{hg}-{ag}",
            }

    return None


def _norm(name: str) -> str:
    import re
    name = (name or "").lower()
    name = re.sub(r"\b(nk|fc|sk|nd)\b", "", name)
    return re.sub(r"[^a-z0-9]", "", name).strip()


def _sim(a: str, b: str) -> float:
    if a == b: return 1.0
    if a in b or b in a: return 0.9
    def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
    b1, b2 = bg(a), bg(b)
    if not b1 or not b2: return 0.0
    return 2 * len(b1 & b2) / (len(b1) + len(b2))


# ── Main polling loop ──────────────────────────────────────────────────────

async def poll_once():
    """Check all pending bets and update results if the match has finished."""
    conn = get_connection()
    cursor = conn.cursor()

    # Only check bets where match_date is in the past
    today = date.today().isoformat()
    pending = cursor.execute("""
        SELECT * FROM bet_history
        WHERE result = 'pending'
        AND match_date <= ?
    """, (today,)).fetchall()

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
                continue  # match not finished or not found

            outcome = evaluate_bet(bet, fixture["home_goals"], fixture["away_goals"])
            actual_win = round(bet["stake"] * (bet["odds"] or 1), 2) if outcome == "win" else 0.0

            cursor.execute("""
                UPDATE bet_history
                SET result=?, match_result=?, actual_win=?
                WHERE id=?
            """, (outcome, fixture["score"], actual_win, bet["id"]))

            print(f"[Poller] ✅ Bet #{bet['id']} {bet['home_team']} vs {bet['away_team']} "
                  f"→ {fixture['score']} → {outcome.upper()}"
                  f"{' $'+str(actual_win) if outcome=='win' else ''}")
            updated += 1

        except Exception as e:
            print(f"[Poller] ⚠️  Bet #{bet['id']} error: {e}")

    conn.commit()
    conn.close()

    if updated:
        print(f"[Poller] Updated {updated} bet(s)")


async def start_poller():
    """Infinite loop — runs poll_once() every POLL_INTERVAL seconds."""
    print(f"[Poller] Started — checking every {POLL_INTERVAL // 60} minutes")
    while True:
        try:
            await poll_once()
        except Exception as e:
            print(f"[Poller] Unexpected error: {e}")
        await asyncio.sleep(POLL_INTERVAL)
