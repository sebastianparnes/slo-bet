from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from typing import Optional
from app.database import get_connection
import json
from datetime import datetime

router = APIRouter()


class BetCreate(BaseModel):
    home_team: str
    away_team: str
    league: str
    match_date: str
    bet_type: str = Field(..., description="1X2, over_under, btts, double_chance")
    bet_selection: str = Field(..., description="1, X, 2, over_2.5, under_2.5, yes, no, 1X, X2, 12")
    odds: Optional[float] = None
    stake: float = Field(..., gt=0)
    confidence_score: Optional[float] = None
    recommendation: Optional[str] = None
    notes: Optional[str] = None
    match_id: Optional[str] = None


class BetUpdate(BaseModel):
    result: str = Field(..., description="win, loss, void, pending")
    match_result: Optional[str] = None
    actual_win: Optional[float] = 0


@router.get("/")
async def get_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    result: Optional[str] = Query(None, description="Filter: win, loss, pending, void"),
    league: Optional[str] = Query(None),
):
    """Get betting history with optional filters."""
    conn = get_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM bet_history WHERE 1=1"
    params = []
    
    if result:
        query += " AND result = ?"
        params.append(result)
    if league:
        query += " AND league = ?"
        params.append(league)
    
    query += " ORDER BY created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    
    rows = cursor.execute(query, params).fetchall()
    conn.close()
    
    bets = [dict(row) for row in rows]
    return {"count": len(bets), "bets": bets}


@router.get("/stats")
async def get_stats():
    """Overall betting statistics and P&L."""
    conn = get_connection()
    cursor = conn.cursor()
    
    stats = cursor.execute("""
        SELECT
            COUNT(*) as total_bets,
            SUM(stake) as total_staked,
            SUM(actual_win) as total_returned,
            SUM(actual_win) - SUM(stake) as profit_loss,
            SUM(CASE WHEN result = 'win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN result = 'loss' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN result = 'pending' THEN 1 ELSE 0 END) as pending,
            SUM(CASE WHEN result = 'void' THEN 1 ELSE 0 END) as voids,
            ROUND(AVG(odds), 2) as avg_odds,
            ROUND(AVG(stake), 2) as avg_stake
        FROM bet_history
        WHERE result != 'void'
    """).fetchone()
    
    stats = dict(stats)
    
    # Win rate
    finished = (stats["wins"] or 0) + (stats["losses"] or 0)
    stats["win_rate"] = round((stats["wins"] or 0) / finished * 100, 1) if finished > 0 else 0
    
    # ROI
    staked = stats["total_staked"] or 0
    stats["roi"] = round(((stats["profit_loss"] or 0) / staked * 100), 2) if staked > 0 else 0
    
    # By league
    by_league = cursor.execute("""
        SELECT league,
            COUNT(*) as bets,
            SUM(stake) as staked,
            SUM(actual_win) - SUM(stake) as pnl,
            SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins
        FROM bet_history
        WHERE result != 'void'
        GROUP BY league
    """).fetchall()
    
    # By bet type
    by_type = cursor.execute("""
        SELECT bet_type,
            COUNT(*) as bets,
            SUM(CASE WHEN result='win' THEN 1 ELSE 0 END) as wins,
            SUM(stake) as staked,
            SUM(actual_win) - SUM(stake) as pnl
        FROM bet_history
        WHERE result != 'void'
        GROUP BY bet_type
    """).fetchall()
    
    # Monthly P&L
    monthly = cursor.execute("""
        SELECT strftime('%Y-%m', match_date) as month,
            COUNT(*) as bets,
            SUM(stake) as staked,
            SUM(actual_win) - SUM(stake) as pnl
        FROM bet_history
        WHERE result != 'void'
        GROUP BY month
        ORDER BY month DESC
        LIMIT 12
    """).fetchall()
    
    conn.close()
    
    return {
        "overall": stats,
        "by_league": [dict(r) for r in by_league],
        "by_bet_type": [dict(r) for r in by_type],
        "monthly_pnl": [dict(r) for r in monthly],
    }


@router.post("/", status_code=201)
async def create_bet(bet: BetCreate):
    """Log a new bet."""
    conn = get_connection()
    cursor = conn.cursor()
    
    potential_win = round(bet.stake * bet.odds, 2) if bet.odds else None
    
    cursor.execute("""
        INSERT INTO bet_history 
        (match_id, home_team, away_team, league, match_date, bet_type, bet_selection,
         odds, stake, potential_win, confidence_score, recommendation, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        bet.match_id, bet.home_team, bet.away_team, bet.league,
        bet.match_date, bet.bet_type, bet.bet_selection,
        bet.odds, bet.stake, potential_win,
        bet.confidence_score, bet.recommendation, bet.notes
    ))
    
    bet_id = cursor.lastrowid
    conn.commit()
    conn.close()
    
    return {"id": bet_id, "message": "Apuesta registrada", "potential_win": potential_win}


@router.patch("/{bet_id}/result")
async def update_bet_result(bet_id: int, update: BetUpdate):
    """Update the result of a bet after the match."""
    conn = get_connection()
    cursor = conn.cursor()
    
    bet = cursor.execute("SELECT * FROM bet_history WHERE id = ?", (bet_id,)).fetchone()
    if not bet:
        conn.close()
        raise HTTPException(status_code=404, detail="Apuesta no encontrada")
    
    actual_win = update.actual_win or 0
    if update.result == "win" and actual_win == 0 and bet["odds"]:
        actual_win = round(bet["stake"] * bet["odds"], 2)
    
    cursor.execute("""
        UPDATE bet_history SET result=?, match_result=?, actual_win=? WHERE id=?
    """, (update.result, update.match_result, actual_win, bet_id))
    
    conn.commit()
    conn.close()
    return {"message": "Resultado actualizado", "bet_id": bet_id, "result": update.result}


@router.delete("/{bet_id}")
async def delete_bet(bet_id: int):
    """Delete a bet from history."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM bet_history WHERE id = ?", (bet_id,))
    if cursor.rowcount == 0:
        conn.close()
        raise HTTPException(status_code=404, detail="Apuesta no encontrada")
    conn.commit()
    conn.close()
    return {"message": "Apuesta eliminada"}


@router.post("/poll-now")
async def poll_results_now():
    """Trigger an immediate result check for all pending bets."""
    from app.result_poller import poll_once
    await poll_once()
    conn = get_connection()
    pending = conn.execute(
        "SELECT COUNT(*) as n FROM bet_history WHERE result='pending'"
    ).fetchone()["n"]
    conn.close()
    return {"message": "Revisión completada", "pending_remaining": pending}
