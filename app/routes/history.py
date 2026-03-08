"""
history.py — Historial de apuestas con Turso + username + liga
"""
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from app.database import _run, _build_stmt, _rows, _last_insert_id
from datetime import datetime

router = APIRouter()


class BetCreate(BaseModel):
    username: str = "default"
    home_team: str
    away_team: str
    league: str
    match_date: str
    bet_type: str
    bet_selection: str
    odds: Optional[float] = None
    stake: float = Field(..., gt=0)
    confidence_score: Optional[float] = None
    recommendation: Optional[str] = None
    notes: Optional[str] = None
    match_id: Optional[str] = None


class BetUpdate(BaseModel):
    result: str
    match_result: Optional[str] = None
    actual_win: Optional[float] = 0


@router.get("/")
async def get_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    result: Optional[str] = Query(None),
    league: Optional[str] = Query(None),
    username: Optional[str] = Query(None),
):
    sql = "SELECT * FROM bet_history WHERE 1=1"
    params = []
    if result:
        sql += " AND result = ?"; params.append(result)
    if league:
        sql += " AND league = ?"; params.append(league)
    if username:
        sql += " AND username = ?"; params.append(username)
    sql += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    results = _run(_build_stmt(sql, params))
    bets = _rows(results[0]) if results else []
    return {"count": len(bets), "bets": bets}


@router.get("/stats")
async def get_stats(username: Optional[str] = Query(None)):
    where = "WHERE result != 'void'"
    params = []
    if username:
        where += " AND username = ?"; params.append(username)

    results = _run(
        _build_stmt(f"""
            SELECT
                COUNT(*) as total_bets,
                SUM(stake) as total_staked,
                SUM(actual_win) as total_returned,
                SUM(actual_win) - SUM(stake) as profit_loss,
                SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN result='loss' THEN 1 ELSE 0 END) as losses,
                SUM(CASE WHEN result='pending' THEN 1 ELSE 0 END) as pending,
                SUM(CASE WHEN result='void' THEN 1 ELSE 0 END) as voids,
                ROUND(AVG(odds), 2) as avg_odds,
                ROUND(AVG(stake), 2) as avg_stake
            FROM bet_history {where}
        """, params or None),
        _build_stmt(f"""
            SELECT league,
                COUNT(*) as bets,
                SUM(stake) as staked,
                SUM(actual_win) - SUM(stake) as pnl,
                SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins
            FROM bet_history {where}
            GROUP BY league
        """, params or None),
        _build_stmt(f"""
            SELECT bet_type,
                COUNT(*) as bets,
                SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
                SUM(stake) as staked,
                SUM(actual_win) - SUM(stake) as pnl
            FROM bet_history {where}
            GROUP BY bet_type
        """, params or None),
        _build_stmt(f"""
            SELECT substr(match_date,1,7) as month,
                COUNT(*) as bets,
                SUM(stake) as staked,
                SUM(actual_win) - SUM(stake) as pnl
            FROM bet_history {where}
            GROUP BY month ORDER BY month DESC LIMIT 12
        """, params or None),
    )

    stats_rows = _rows(results[0])
    stats = stats_rows[0] if stats_rows else {}
    wins = int(stats.get("wins") or 0)
    losses = int(stats.get("losses") or 0)
    finished = wins + losses
    stats["win_rate"] = round(wins / finished * 100, 1) if finished > 0 else 0
    staked = float(stats.get("total_staked") or 0)
    pl = float(stats.get("profit_loss") or 0)
    stats["roi"] = round(pl / staked * 100, 2) if staked > 0 else 0

    return {
        "overall": stats,
        "by_league": _rows(results[1]) if len(results) > 1 else [],
        "by_bet_type": _rows(results[2]) if len(results) > 2 else [],
        "monthly_pnl": _rows(results[3]) if len(results) > 3 else [],
    }


@router.post("/", status_code=201)
async def create_bet(bet: BetCreate):
    potential_win = round(bet.stake * bet.odds, 2) if bet.odds else None
    results = _run(_build_stmt("""
        INSERT INTO bet_history
        (username, match_id, home_team, away_team, league, match_date,
         bet_type, bet_selection, odds, stake, potential_win,
         confidence_score, recommendation, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, [bet.username, bet.match_id, bet.home_team, bet.away_team,
          bet.league, bet.match_date, bet.bet_type, bet.bet_selection,
          bet.odds, bet.stake, potential_win,
          bet.confidence_score, bet.recommendation, bet.notes]))
    bet_id = _last_insert_id(results[0]) if results else 0
    return {"id": bet_id, "message": "Apuesta registrada", "potential_win": potential_win}


@router.patch("/{bet_id}/result")
async def update_bet_result(bet_id: int, update: BetUpdate):
    check = _run(_build_stmt("SELECT odds, stake FROM bet_history WHERE id = ?", [bet_id]))
    rows = _rows(check[0]) if check else []
    if not rows:
        raise HTTPException(404, "Apuesta no encontrada")
    bet = rows[0]
    actual_win = update.actual_win or 0
    if update.result == "win" and actual_win == 0 and bet.get("odds"):
        actual_win = round(float(bet["stake"]) * float(bet["odds"]), 2)
    _run(_build_stmt(
        "UPDATE bet_history SET result=?, match_result=?, actual_win=? WHERE id=?",
        [update.result, update.match_result, actual_win, bet_id]
    ))
    return {"message": "Resultado actualizado", "bet_id": bet_id, "result": update.result}


@router.delete("/{bet_id}")
async def delete_bet(bet_id: int):
    _run(_build_stmt("DELETE FROM bet_history WHERE id = ?", [bet_id]))
    return {"message": "Apuesta eliminada"}


@router.post("/poll-now")
async def poll_results_now():
    from app.result_poller import poll_once
    await poll_once()
    results = _run(_build_stmt("SELECT COUNT(*) as n FROM bet_history WHERE result='pending'"))
    rows = _rows(results[0]) if results else [{"n": 0}]
    return {"message": "Revisión completada", "pending_remaining": rows[0].get("n", 0)}
