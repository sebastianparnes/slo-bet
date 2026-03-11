"""
routes/calibration_routes.py — Endpoints de calibración del modelo
"""
from fastapi import APIRouter, BackgroundTasks, Query
from app.calibration import run_calibration, get_calibration_metrics, init_calibration_table
from app.football_api import TOURNAMENT_IDS

router = APIRouter(prefix="/calibration", tags=["calibration"])

_running = False  # simple lock


@router.get("/run")
async def trigger_calibration(
    background_tasks: BackgroundTasks,
    leagues: str = Query(None, description="Comma-separated league names, or empty for all"),
    pages: int = Query(3, ge=1, le=8, description="Pages of finished matches per league (~20 matches/page)"),
):
    """
    Trigger calibration run. Runs in background so it doesn't timeout.
    Each page = ~20 matches. 3 pages × 13 ligas = ~780 partidos procesados.
    """
    global _running
    if _running:
        return {"status": "already_running", "message": "Calibración ya en curso, esperá unos minutos"}

    target = [l.strip() for l in leagues.split(",")] if leagues else list(TOURNAMENT_IDS.keys())
    invalid = [l for l in target if l not in TOURNAMENT_IDS]
    if invalid:
        return {"error": f"Ligas desconocidas: {invalid}"}

    async def _run_bg():
        global _running
        _running = True
        try:
            result = await run_calibration(leagues=target, pages_per_league=pages)
            print(f"[cal] background run done: {result}")
        finally:
            _running = False

    background_tasks.add_task(_run_bg)
    return {
        "status": "started",
        "leagues": target,
        "pages_per_league": pages,
        "estimated_matches": len(target) * pages * 20,
        "message": "Calibración iniciada en background. Consultá /calibration/metrics en ~2-3 minutos",
    }


@router.get("/status")
async def calibration_status():
    """Check if calibration is running and total records in DB."""
    try:
        from app.database import _run, _build_stmt, _rows
        res = _run(_build_stmt("SELECT COUNT(*) as total FROM model_calibration"))
        rows = _rows(res[0])
        total = int(rows[0]["total"]) if rows else 0
    except Exception:
        total = 0

    return {
        "running": _running,
        "total_records": total,
        "message": "Calibración en curso..." if _running else "En reposo",
    }


@router.get("/metrics")
async def get_metrics(league: str = Query(None, description="Filtrar por liga, o vacío para todas")):
    """
    Returns calibration metrics: accuracy per market, real vs predicted rates,
    Brier scores, calibration buckets, and per-league breakdown.
    """
    if league and league not in TOURNAMENT_IDS:
        return {"error": f"Liga desconocida: {league}"}

    metrics = get_calibration_metrics(league=league)
    return metrics


@router.delete("/reset")
async def reset_calibration():
    """Delete all calibration data (use with caution)."""
    try:
        from app.database import _run, _build_stmt
        _run(_build_stmt("DELETE FROM model_calibration"))
        return {"status": "ok", "message": "Todos los datos de calibración eliminados"}
    except Exception as e:
        return {"error": str(e)}
