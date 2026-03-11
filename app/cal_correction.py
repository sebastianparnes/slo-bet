"""
cal_correction.py — Corrección de sesgo basada en calibración histórica
========================================================================

Lee los sesgos acumulados en model_calibration y aplica correcciones
a las probabilidades ANTES de generar recomendaciones.

Método: Platt scaling simplificado + corrección aditiva de sesgo.

Caché en memoria: se refresca cada 6 horas para no martillar Turso.
Mínimo 30 partidos por liga para aplicar corrección (sino usa global).
"""

import time
from typing import Optional
from app.database import _run, _build_stmt, _rows

# ── Cache ─────────────────────────────────────────────────────────────────────
_correction_cache: dict = {}   # league → CorrectionFactors
_cache_ts: float = 0.0
_CACHE_TTL = 6 * 3600          # 6 horas
_MIN_SAMPLES = 30              # mínimo partidos para confiar en la corrección


class CorrectionFactors:
    """
    Additive bias corrections derived from calibration data.
    bias = model_avg_pred - real_rate
    correction = -bias (subtract the overestimation)
    """
    def __init__(self,
                 home_win_bias: float = 0.0,
                 draw_bias: float = 0.0,
                 away_win_bias: float = 0.0,
                 over25_bias: float = 0.0,
                 btts_bias: float = 0.0,
                 xg_scale: float = 1.0,
                 samples: int = 0,
                 source: str = "default"):
        self.home_win_bias = home_win_bias
        self.draw_bias     = draw_bias
        self.away_win_bias = away_win_bias
        self.over25_bias   = over25_bias
        self.btts_bias     = btts_bias
        self.xg_scale      = xg_scale   # multiplicador para xG total
        self.samples       = samples
        self.source        = source     # 'league', 'global', 'default'

    def apply_1x2(self, hw: float, d: float, aw: float) -> tuple[float, float, float]:
        """Apply bias correction to 1X2 probabilities and renormalize."""
        if self.samples < _MIN_SAMPLES:
            return hw, d, aw
        # Clamp corrections to max ±15% to avoid overcorrection
        c = 0.15
        hw2 = hw - min(max(self.home_win_bias, -c), c)
        d2  = d  - min(max(self.draw_bias,     -c), c)
        aw2 = aw - min(max(self.away_win_bias,  -c), c)
        # Ensure positive + renormalize
        hw2 = max(hw2, 0.05)
        d2  = max(d2,  0.05)
        aw2 = max(aw2, 0.05)
        total = hw2 + d2 + aw2
        return round(hw2/total, 4), round(d2/total, 4), round(aw2/total, 4)

    def apply_xg(self, home_xg: float, away_xg: float) -> tuple[float, float]:
        """Scale xG based on historical model vs real goals."""
        if self.samples < _MIN_SAMPLES or abs(self.xg_scale - 1.0) < 0.02:
            return home_xg, away_xg
        scale = min(max(self.xg_scale, 0.75), 1.30)  # cap at ±30%
        return round(home_xg * scale, 3), round(away_xg * scale, 3)

    def correction_summary(self) -> dict:
        return {
            "source": self.source,
            "samples": self.samples,
            "applied": self.samples >= _MIN_SAMPLES,
            "home_win_bias": round(self.home_win_bias * 100, 1),
            "draw_bias":     round(self.draw_bias * 100, 1),
            "away_win_bias": round(self.away_win_bias * 100, 1),
            "over25_bias":   round(self.over25_bias * 100, 1),
            "btts_bias":     round(self.btts_bias * 100, 1),
            "xg_scale":      round(self.xg_scale, 3),
        }


def _fetch_corrections_from_db() -> dict:
    """
    Query model_calibration for bias per league and globally.
    Returns dict: league_name → CorrectionFactors
    """
    corrections = {}

    try:
        # Per-league corrections (only where enough samples)
        res = _run(_build_stmt("""
            SELECT
                league,
                COUNT(*) as n,
                AVG(pred_home_win) - AVG(CASE WHEN real_result='H' THEN 1.0 ELSE 0.0 END) as hw_bias,
                AVG(pred_draw)     - AVG(CASE WHEN real_result='D' THEN 1.0 ELSE 0.0 END) as d_bias,
                AVG(pred_away_win) - AVG(CASE WHEN real_result='A' THEN 1.0 ELSE 0.0 END) as aw_bias,
                AVG(pred_over25)   - AVG(CASE WHEN real_total_goals > 2 THEN 1.0 ELSE 0.0 END) as o25_bias,
                AVG(pred_btts)     - AVG(CAST(real_btts AS REAL)) as btts_bias,
                AVG(home_xg + away_xg) as avg_xg,
                AVG(CAST(real_total_goals AS REAL)) as avg_real_goals
            FROM model_calibration
            GROUP BY league
            HAVING COUNT(*) >= ?
        """, [_MIN_SAMPLES]))

        for row in _rows(res[0]):
            league = row["league"]
            n = int(row["n"] or 0)
            avg_xg = float(row["avg_xg"] or 2.4)
            avg_real = float(row["avg_real_goals"] or 2.5)
            xg_scale = avg_real / avg_xg if avg_xg > 0 else 1.0

            corrections[league] = CorrectionFactors(
                home_win_bias = float(row["hw_bias"] or 0),
                draw_bias     = float(row["d_bias"]  or 0),
                away_win_bias = float(row["aw_bias"] or 0),
                over25_bias   = float(row["o25_bias"] or 0),
                btts_bias     = float(row["btts_bias"] or 0),
                xg_scale      = xg_scale,
                samples       = n,
                source        = "league",
            )

        # Global fallback (all leagues combined)
        res_global = _run(_build_stmt("""
            SELECT
                COUNT(*) as n,
                AVG(pred_home_win) - AVG(CASE WHEN real_result='H' THEN 1.0 ELSE 0.0 END) as hw_bias,
                AVG(pred_draw)     - AVG(CASE WHEN real_result='D' THEN 1.0 ELSE 0.0 END) as d_bias,
                AVG(pred_away_win) - AVG(CASE WHEN real_result='A' THEN 1.0 ELSE 0.0 END) as aw_bias,
                AVG(pred_over25)   - AVG(CASE WHEN real_total_goals > 2 THEN 1.0 ELSE 0.0 END) as o25_bias,
                AVG(pred_btts)     - AVG(CAST(real_btts AS REAL)) as btts_bias,
                AVG(home_xg + away_xg) as avg_xg,
                AVG(CAST(real_total_goals AS REAL)) as avg_real_goals
            FROM model_calibration
        """))
        grow = _rows(res_global[0])
        if grow:
            g = grow[0]
            gn = int(g["n"] or 0)
            avg_xg = float(g["avg_xg"] or 2.4)
            avg_real = float(g["avg_real_goals"] or 2.5)
            corrections["__global__"] = CorrectionFactors(
                home_win_bias = float(g["hw_bias"] or 0),
                draw_bias     = float(g["d_bias"]  or 0),
                away_win_bias = float(g["aw_bias"] or 0),
                over25_bias   = float(g["o25_bias"] or 0),
                btts_bias     = float(g["btts_bias"] or 0),
                xg_scale      = avg_real / avg_xg if avg_xg > 0 else 1.0,
                samples       = gn,
                source        = "global",
            )

    except Exception as e:
        print(f"[cal_correction] DB error: {e}")

    return corrections


def get_correction(league: str) -> CorrectionFactors:
    """
    Returns CorrectionFactors for a league.
    Uses cache, refreshes every 6h.
    Falls back: league → global → default (no correction).
    """
    global _correction_cache, _cache_ts

    now = time.time()
    if now - _cache_ts > _CACHE_TTL or not _correction_cache:
        try:
            _correction_cache = _fetch_corrections_from_db()
            _cache_ts = now
            total = sum(v.samples for v in _correction_cache.values() if v.source != "__global__")
            print(f"[cal_correction] cache refreshed — {len(_correction_cache)} leagues, {total} total samples")
        except Exception as e:
            print(f"[cal_correction] refresh failed: {e}")

    # 1. Try league-specific
    if league in _correction_cache and _correction_cache[league].samples >= _MIN_SAMPLES:
        return _correction_cache[league]

    # 2. Try global
    g = _correction_cache.get("__global__")
    if g and g.samples >= _MIN_SAMPLES:
        cf = CorrectionFactors(
            home_win_bias=g.home_win_bias,
            draw_bias=g.draw_bias,
            away_win_bias=g.away_win_bias,
            over25_bias=g.over25_bias,
            btts_bias=g.btts_bias,
            xg_scale=g.xg_scale,
            samples=g.samples,
            source="global",
        )
        return cf

    # 3. Default — no correction
    return CorrectionFactors(source="default", samples=0)


def force_refresh():
    """Force cache refresh on next call."""
    global _cache_ts
    _cache_ts = 0.0
