"""
Motor de Análisis de Apuestas
==============================

Metodología de scoring (0-100 puntos):

1. FORMA RECIENTE (25 pts)
   — Últimos 5 partidos ponderados: pesos 10/15/20/25/30% (más reciente = más peso)
   — Win=3, Draw=1, Loss=0 → normalizado a 0-3

2. HEAD-TO-HEAD (20 pts)
   — Historial directo real (Sofascore)
   — Penaliza cuando el historial es muy parejo (mayor incertidumbre)

3. POSICIÓN EN TABLA (20 pts)
   — Diferencia de puntos relativa al máximo disponible
   — 60% diferencia de puntos + 40% diferencia de posición

4. ESTADÍSTICAS DE GOLES (20 pts)
   — xG con modelo Poisson (Dixon-Coles simplificado)
   — 70% modelo + 30% histórico H2H

5. FACTOR LOCAL (10 pts)
   — Tasa histórica de victoria local por liga

6. CONSISTENCIA (5 pts)
   — Equipos erráticos penalizados (varianza alta = menos confianza)

Mercados analizados:
  — 1X2 (resultado final)
  — Doble oportunidad (1X / X2 / 12)
  — Over/Under 2.5 goles
  — BTTS (ambos equipos marcan)
  — Handicap Asiático −0.5 / −1 / −1.5 / +0.5 / +1
  — Draw No Bet
"""

import math
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class BetRecommendation:
    bet_type:   str          # '1X2', 'double_chance', 'over_under', 'btts', 'asian_handicap', 'dnb'
    selection:  str          # '1', 'X', '2', 'ah_home_-1', 'dnb_home', etc.
    confidence: float        # 0-100
    label:      str
    reasoning:  list[str]
    risk_level: str          # 'low', 'medium', 'high'
    min_odds:   float


# ─── Modelo Poisson ───────────────────────────────────────────────────────────

def _poisson(lam: float, k: int) -> float:
    try:
        return (math.exp(-lam) * (lam ** k)) / math.factorial(k)
    except (ValueError, OverflowError):
        return 0.0


def _build_score_matrix(home_xg: float, away_xg: float, max_goals: int = 8) -> list[list[float]]:
    """Matriz de probabilidades de marcador [home_goals][away_goals]."""
    return [
        [_poisson(home_xg, i) * _poisson(away_xg, j) for j in range(max_goals)]
        for i in range(max_goals)
    ]


def _probs_1x2(matrix: list[list[float]]) -> tuple[float, float, float]:
    hw = draw = aw = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:   hw   += p
            elif i == j: draw += p
            else:         aw   += p
    total = hw + draw + aw or 1
    return hw/total, draw/total, aw/total


def _prob_over(matrix: list[list[float]], line: float = 2.5) -> float:
    under = sum(
        matrix[i][j]
        for i in range(len(matrix))
        for j in range(len(matrix[i]))
        if i + j <= line
    )
    return max(0.0, 1.0 - under)


def _prob_btts(matrix: list[list[float]]) -> float:
    return sum(
        matrix[i][j]
        for i in range(1, len(matrix))
        for j in range(1, len(matrix[i]))
    )


def _prob_asian_handicap(matrix: list[list[float]], handicap: float) -> tuple[float, float]:
    """
    Calcula prob de cubrir el handicap para el equipo local.
    handicap negativo = local da ventaja (e.g. −1 = local debe ganar por ≥2)
    handicap positivo = local recibe ventaja (e.g. +0.5 = local no puede perder)

    Retorna (prob_home_covers, prob_away_covers).
    Para handicaps con .5 no hay push. Para handicaps enteros hay push (devuelve stake).
    """
    prob_home = 0.0
    prob_away = 0.0
    prob_push = 0.0

    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            margin = i - j + handicap   # margen ajustado para home
            if margin > 0:
                prob_home += p
            elif margin < 0:
                prob_away += p
            else:
                prob_push += p

    # En handicap con .5 nunca hay push (garantizado)
    # En enteros, el push se distribuye 50/50 o se excluye según la casa
    # Para EV calculamos excluyendo push (mercado estándar)
    live_total = prob_home + prob_away or 1
    return prob_home / live_total, prob_away / live_total


def _prob_dnb(matrix: list[list[float]]) -> tuple[float, float]:
    """Draw No Bet: si empata devuelve stake. Prob home win / away win (sin empate)."""
    hw = aw = 0.0
    for i, row in enumerate(matrix):
        for j, p in enumerate(row):
            if i > j:  hw += p
            elif i < j: aw += p
    total = hw + aw or 1
    return hw/total, aw/total


def _xg_from_form(home_form: dict, away_form: dict) -> tuple[float, float]:
    ha = home_form.get("avg_scored", 1.3)
    hd = home_form.get("avg_conceded", 1.2)
    aa = away_form.get("avg_scored", 1.1)
    ad = away_form.get("avg_conceded", 1.3)
    home_xg = ha * (ad / 1.2) * 1.12   # factor local +12%
    away_xg = aa * (hd / 1.2)
    return (
        min(max(home_xg, 0.35), 4.0),
        min(max(away_xg, 0.25), 3.5),
    )


# ─── Componentes de scoring ───────────────────────────────────────────────────

def _form_to_pts(form: list[str]) -> float:
    weights = [0.10, 0.15, 0.20, 0.25, 0.30]
    # Acepta inglés (W/D/L) y español (G/E/P)
    pts_map = {"W": 3, "D": 1, "L": 0, "G": 3, "E": 1, "P": 0}
    recent = (form or [])[-5:]
    while len(recent) < 5:
        recent.insert(0, "E")
    return sum(weights[i] * pts_map.get(r, 1) for i, r in enumerate(recent))


def _form_component(home_form: dict, away_form: dict) -> tuple[float, dict]:
    hp = _form_to_pts(home_form.get("form", []))
    ap = _form_to_pts(away_form.get("form", []))
    diff = (hp - ap) / 3.0
    score = min(max(12.5 + diff * 12.5, 0), 25)
    return round(score, 2), {
        "home_form":          home_form.get("form_string", "?????"),
        "away_form":          away_form.get("form_string", "?????"),
        "home_weighted_pts":  round(hp, 2),
        "away_weighted_pts":  round(ap, 2),
        "home_avg_scored":    home_form.get("avg_scored", 0),
        "away_avg_scored":    away_form.get("avg_scored", 0),
        "home_avg_conceded":  home_form.get("avg_conceded", 0),
        "away_avg_conceded":  away_form.get("avg_conceded", 0),
        "home_source":           home_form.get("source", "unknown"),
        "away_source":           away_form.get("source", "unknown"),
        "home_recent_matches":   home_form.get("recent_matches", []),
        "away_recent_matches":   away_form.get("recent_matches", []),
        "score_out_of_25":       round(score, 2),
    }


def _h2h_component(h2h: dict, home_id: int, away_id: int) -> tuple[float, dict]:
    total = h2h.get("total_matches", 0)
    if total == 0:
        return 10.0, {"note": "Sin historial disponible", "score_out_of_20": 10.0}
    hw = h2h.get("home_wins", 0)
    home_rate = hw / total
    score = min(max(10 + (home_rate - 0.3) * 20, 0), 20)
    return round(score, 2), {
        "total_matches":  total,
        "home_wins":      hw,
        "draws":          h2h.get("draws", 0),
        "away_wins":      h2h.get("away_wins", 0),
        "home_win_rate":  round(home_rate * 100, 1),
        "avg_goals_h2h":  h2h.get("avg_goals_h2h", 0),
        "btts_pct":       h2h.get("btts_pct", 0),
        "over25_pct":     h2h.get("over25_pct", 0),
        "source":         h2h.get("source", "unknown"),
        "score_out_of_20": round(score, 2),
    }


def _standings_component(standings: list[dict], home_id: int, away_id: int) -> tuple[float, dict]:
    if not standings:
        return 10.0, {"note": "Tabla no disponible", "score_out_of_20": 10.0}
    home_e = next((s for s in standings if s["team_id"] == home_id), None)
    away_e = next((s for s in standings if s["team_id"] == away_id), None)
    if not home_e or not away_e:
        return 10.0, {"note": "Equipos no encontrados en tabla", "score_out_of_20": 10.0}
    pts_range = max(s["points"] for s in standings) - min(s["points"] for s in standings) or 1
    pts_norm  = (home_e["points"] - away_e["points"]) / pts_range
    rank_norm = (away_e["rank"] - home_e["rank"]) / len(standings)
    score = min(max(10 + (pts_norm * 0.6 + rank_norm * 0.4) * 10, 0), 20)
    return round(score, 2), {
        "home_rank":        home_e["rank"],
        "home_points":      home_e["points"],
        "home_form_table":  home_e.get("form", ""),
        "away_rank":        away_e["rank"],
        "away_points":      away_e["points"],
        "away_form_table":  away_e.get("form", ""),
        "points_diff":      home_e["points"] - away_e["points"],
        "score_out_of_20":  round(score, 2),
    }


def _goals_component(home_form: dict, away_form: dict, h2h: dict) -> tuple[float, float, float, dict]:
    home_xg, away_xg = _xg_from_form(home_form, away_form)
    matrix = _build_score_matrix(home_xg, away_xg)
    over25  = _prob_over(matrix, 2.5)
    btts    = _prob_btts(matrix)
    h2h_over = h2h.get("over25_pct", 50) / 100
    h2h_btts = h2h.get("btts_pct",   50) / 100
    over25_f = over25 * 0.7 + h2h_over * 0.3
    btts_f   = btts   * 0.7 + h2h_btts * 0.3
    score = over25_f * 20
    return round(score, 2), round(home_xg, 2), round(away_xg, 2), {
        "home_xg":       round(home_xg, 2),
        "away_xg":       round(away_xg, 2),
        "total_xg":      round(home_xg + away_xg, 2),
        "over25_prob":   round(over25_f * 100, 1),
        "btts_prob":     round(btts_f   * 100, 1),
        "score_out_of_20": round(score, 2),
    }


def _home_advantage_component(league: str) -> tuple[float, dict]:
    rates = {
        "PrvaLiga": 0.44, "2SNL": 0.41,
        "PrimeraDivision": 0.46, "PrimeraNacional": 0.43,
    }
    rate  = rates.get(league, 0.43)
    score = min(max(rate * 10 / 0.5, 0), 10)
    return round(score, 2), {
        "league_home_win_rate": f"{round(rate * 100, 1)}%",
        "score_out_of_10": round(score, 2),
    }


def _consistency_component(home_form: dict, away_form: dict) -> tuple[float, dict]:
    def var_score(form):
        if not form: return 0.5
        pts_map = {"W": 3, "D": 1, "L": 0, "G": 3, "E": 1, "P": 0}
        pts = [pts_map[r] for r in form if r in pts_map]
        if len(pts) < 2: return 0.5
        mu  = sum(pts) / len(pts)
        var = sum((p - mu) ** 2 for p in pts) / len(pts)
        return 1 - (var / 4.5)
    hc = var_score(home_form.get("form", []))
    ac = var_score(away_form.get("form", []))
    score = ((hc + ac) / 2) * 5
    return round(score, 2), {
        "home_consistency": round(hc * 100, 1),
        "away_consistency": round(ac * 100, 1),
        "score_out_of_5":   round(score, 2),
    }


# ─── Recomendaciones ──────────────────────────────────────────────────────────

def _build_recommendations(
    home_win_p: float,
    draw_p: float,
    away_win_p: float,
    over25_p: float,
    btts_p: float,
    home_xg: float,
    away_xg: float,
    form_details: dict,
    h2h_details: dict,
    standings_details: dict,
    goals_details: dict,
    matrix: list[list[float]],
) -> list[BetRecommendation]:

    recs = []

    # ── 1X2 ──────────────────────────────────────────────────────────────────
    best_sel, best_prob, best_label = max(
        [("1", home_win_p, "Victoria local"),
         ("X", draw_p,     "Empate"),
         ("2", away_win_p, "Victoria visitante")],
        key=lambda x: x[1]
    )
    if best_prob > 0.48:
        reas = []
        if best_sel == "1":
            reas.append(f"Forma local: {form_details['home_form']} ({form_details['home_weighted_pts']}/3.0 pts ponderados)")
            if standings_details.get("home_rank"):
                reas.append(f"Local #{standings_details['home_rank']} ({standings_details['home_points']} pts) — Visitante #{standings_details['away_rank']} ({standings_details['away_points']} pts)")
            reas.append(f"xG esperado: {home_xg} local vs {away_xg} visitante")
        elif best_sel == "2":
            reas.append(f"Forma visitante: {form_details['away_form']} ({form_details['away_weighted_pts']}/3.0 pts ponderados)")
            if standings_details.get("away_rank"):
                reas.append(f"Visitante #{standings_details['away_rank']} ({standings_details['away_points']} pts)")
            reas.append(f"xG esperado: {away_xg} visitante vs {home_xg} local")
        else:
            reas.append("Equipos muy parejos en todos los indicadores")
            reas.append(f"H2H: {h2h_details.get('draws',0)} empates en {h2h_details.get('total_matches',0)} partidos")
        recs.append(BetRecommendation(
            bet_type="1X2", selection=best_sel,
            confidence=min(best_prob * 100 * 1.08, 94),
            label=f"1X2 → {best_label}", reasoning=reas,
            risk_level="low" if best_prob > 0.62 else "medium",
            min_odds=round(1 / max(best_prob - 0.05, 0.1), 2),
        ))

    # ── Doble oportunidad ────────────────────────────────────────────────────
    dc_opts = {
        "1X": home_win_p + draw_p,
        "12": home_win_p + away_win_p,
        "X2": draw_p + away_win_p,
    }
    dc_sel, dc_prob = max(dc_opts.items(), key=lambda x: x[1])
    dc_labels = {"1X": "Local o Empate", "12": "No hay empate", "X2": "Visitante o Empate"}
    if dc_prob > 0.68:
        recs.append(BetRecommendation(
            bet_type="double_chance", selection=dc_sel,
            confidence=min(dc_prob * 100 * 0.93, 91),
            label=f"Doble Oportunidad → {dc_labels[dc_sel]}",
            reasoning=[
                f"Probabilidad combinada: {round(dc_prob*100,1)}%",
                f"Cubre dos de los tres resultados posibles",
                f"Cuota mínima recomendada: {round(1/max(dc_prob-0.03,0.1),2)}",
            ],
            risk_level="low",
            min_odds=round(1 / max(dc_prob - 0.03, 0.1), 2),
        ))

    # ── Over/Under 2.5 ───────────────────────────────────────────────────────
    ou_sel  = "over_2.5"  if over25_p > 0.50 else "under_2.5"
    ou_prob = over25_p    if over25_p > 0.50 else (1 - over25_p)
    if ou_prob > 0.52:
        recs.append(BetRecommendation(
            bet_type="over_under", selection=ou_sel,
            confidence=min(ou_prob * 100 * 1.04, 90),
            label=f"O/U → {'Más' if ou_sel=='over_2.5' else 'Menos'} de 2.5 goles",
            reasoning=[
                f"xG total esperado: {goals_details['total_xg']} goles",
                f"Local: {form_details['home_avg_scored']} anotados / {form_details['home_avg_conceded']} encajados promedio",
                f"Visitante: {form_details['away_avg_scored']} anotados / {form_details['away_avg_conceded']} encajados promedio",
                f"H2H promedio: {h2h_details.get('avg_goals_h2h','—')} goles por partido",
            ],
            risk_level="medium" if ou_prob < 0.65 else "low",
            min_odds=round(1 / max(ou_prob - 0.05, 0.1), 2),
        ))

    # ── BTTS ─────────────────────────────────────────────────────────────────
    btts_sel  = "yes" if btts_p > 0.50 else "no"
    btts_prob = btts_p if btts_p > 0.50 else (1 - btts_p)
    if btts_prob > 0.55:
        reas_btts = [f"xG: {home_xg} local | {away_xg} visitante"]
        if btts_sel == "yes" and h2h_details.get("btts_pct"):
            reas_btts.append(f"Ambos marcaron en el {h2h_details['btts_pct']}% de los H2H")
        if btts_sel == "yes" and form_details.get("home_avg_conceded", 99) < 0.8:
            reas_btts.append("⚠️ Portería local muy sólida — confianza moderada en BTTS-Sí")
        recs.append(BetRecommendation(
            bet_type="btts", selection=btts_sel,
            confidence=min(btts_prob * 100, 87),
            label=f"BTTS → {'SÍ' if btts_sel=='yes' else 'NO'}",
            reasoning=reas_btts,
            risk_level="medium",
            min_odds=round(1 / max(btts_prob - 0.05, 0.1), 2),
        ))

    # ── Draw No Bet ──────────────────────────────────────────────────────────
    dnb_home, dnb_away = _prob_dnb(matrix)
    dnb_sel  = "dnb_home"  if dnb_home > dnb_away else "dnb_away"
    dnb_prob = dnb_home    if dnb_home > dnb_away else dnb_away
    dnb_team = form_details["home_form"].split()[0] if dnb_sel == "dnb_home" else form_details["away_form"].split()[0]
    if dnb_prob > 0.60 and abs(home_win_p - away_win_p) > 0.10:
        recs.append(BetRecommendation(
            bet_type="dnb", selection=dnb_sel,
            confidence=min(dnb_prob * 100 * 0.95, 88),
            label=f"Draw No Bet → {'Local' if dnb_sel=='dnb_home' else 'Visitante'}",
            reasoning=[
                f"Si empata te devuelven la apuesta",
                f"Prob de ganar (excluyendo empate): {round(dnb_prob*100,1)}%",
                f"Alternativa más segura al 1X2 directo cuando el empate es plausible ({round(draw_p*100,1)}%)",
            ],
            risk_level="low" if dnb_prob > 0.72 else "medium",
            min_odds=round(1 / max(dnb_prob - 0.05, 0.1), 2),
        ))

    # ── Handicap Asiático ────────────────────────────────────────────────────
    # Solo recomendamos si hay una diferencia clara entre equipos
    dominance = abs(home_win_p - away_win_p)
    if dominance > 0.15:
        fav_is_home = home_win_p > away_win_p

        # Elegir línea según nivel de dominancia
        if dominance > 0.40:
            lines = [(-1.5, fav_is_home), (-1.0, fav_is_home)]
        elif dominance > 0.28:
            lines = [(-1.0, fav_is_home), (-0.5, fav_is_home)]
        else:
            lines = [(-0.5, fav_is_home)]

        for (line, is_home) in lines:
            # Si el favorito es visitante, el handicap se aplica desde su perspectiva
            h = line if is_home else -line
            ph, pa = _prob_asian_handicap(matrix, h)
            fav_prob = ph if is_home else pa
            team_label = "Local" if is_home else "Visitante"

            # Solo agregar si la confianza es razonable
            if fav_prob < 0.52:
                continue

            line_display = f"{line:+.1f}".replace(".0", "")
            recs.append(BetRecommendation(
                bet_type="asian_handicap",
                selection=f"ah_{'home' if is_home else 'away'}_{line_display}",
                confidence=min(fav_prob * 100 * 1.02, 88),
                label=f"Hándicap Asiático → {team_label} {line_display}",
                reasoning=[
                    f"xG: {home_xg} local vs {away_xg} visitante — diferencia significativa",
                    f"Dominancia: Local {round(home_win_p*100,1)}% vs Visitante {round(away_win_p*100,1)}%",
                    f"Prob de cubrir {line_display}: {round(fav_prob*100,1)}%",
                    f"Línea {'−0.5: basta con ganar' if abs(line)==0.5 else '−1: el favorito debe ganar por ≥2' if abs(line)==1 else '−1.5: el favorito debe ganar por ≥2 (sin push)'}",
                ],
                risk_level="medium" if abs(line) <= 0.5 else "high",
                min_odds=round(1 / max(fav_prob - 0.04, 0.1), 2),
            ))

    # Ordenar: primero las de valor (se enriquecen después), luego por confianza
    recs.sort(key=lambda r: -r.confidence)
    return recs


# ─── Summary ──────────────────────────────────────────────────────────────────

def _generate_summary(home_team, away_team, hw, dp, aw, score, top_rec):
    q = "ALTA" if score >= 70 else ("MEDIA" if score >= 55 else "BAJA")
    s = f"{home_team} vs {away_team}. "
    s += f"Local {round(hw*100,1)}% | Empate {round(dp*100,1)}% | Visitante {round(aw*100,1)}%. "
    if top_rec:
        s += f"Mejor apuesta: {top_rec.label} ({round(top_rec.confidence,0)}% confianza). "
    s += f"Calidad del análisis: {q} ({round(score,0)}/100)."
    return s


# ─── EV enrichment ───────────────────────────────────────────────────────────

def _calc_ev(model_prob_pct: float, odd: Optional[float]) -> Optional[float]:
    if not odd or odd <= 1 or not model_prob_pct:
        return None
    return round((model_prob_pct / 100) * odd - 1, 4)


def _dc_odd(xbet_odds, a, b):
    oa = xbet_odds.get(a)
    ob = xbet_odds.get(b)
    if oa and ob:
        return round(1 / (1/oa + 1/ob), 3)
    return None


def _enrich_with_odds(recs_dicts: list[dict], probs: dict, xbet_odds: Optional[dict]) -> list[dict]:
    if not xbet_odds:
        for r in recs_dicts:
            r["xbet_odd"] = None; r["ev"] = None; r["is_value"] = False
        return recs_dicts

    hw = probs["home_win"]
    dp = probs["draw"]
    aw = probs["away_win"]
    ov = probs["over_2_5"]
    bt = probs["btts"]

    # Mapa selección → (prob_modelo, cuota_xbet)
    odd_map: dict[str, tuple] = {
        "1":          (hw,        xbet_odds.get("home")),
        "X":          (dp,        xbet_odds.get("draw")),
        "2":          (aw,        xbet_odds.get("away")),
        "over_2.5":   (ov,        xbet_odds.get("over25")),
        "under_2.5":  (100 - ov,  xbet_odds.get("under25")),
        "yes":        (bt,        xbet_odds.get("btts_yes")),
        "no":         (100 - bt,  xbet_odds.get("btts_no")),
        "1X":         (hw + dp,   _dc_odd(xbet_odds, "home", "draw")),
        "X2":         (dp + aw,   _dc_odd(xbet_odds, "draw", "away")),
        "12":         (hw + aw,   _dc_odd(xbet_odds, "home", "away")),
        # DNB: la cuota de 1xbet para DNB no siempre está disponible en el scraper
        # por eso mapeamos a None — se puede ampliar cuando el scraper lo soporte
        "dnb_home":   (hw / (hw + aw) * 100 if (hw + aw) > 0 else 50, None),
        "dnb_away":   (aw / (hw + aw) * 100 if (hw + aw) > 0 else 50, None),
    }
    # Handicap asiático: sin cuota directa del scraper por ahora
    for r in recs_dicts:
        sel = r["selection"]
        if sel.startswith("ah_"):
            r["xbet_odd"] = xbet_odds.get("asian_handicap")  # None si no disponible
            ev = _calc_ev(r["confidence"], r["xbet_odd"])
            r["ev"] = ev
            r["is_value"] = bool(ev and ev > 0.04)
            continue
        model_p, xbet_odd = odd_map.get(sel, (None, None))
        ev = _calc_ev(model_p, xbet_odd)
        r["xbet_odd"] = xbet_odd
        r["ev"] = ev
        r["is_value"] = bool(ev and ev > 0.04)

    recs_dicts.sort(key=lambda r: (not r["is_value"], -r["confidence"]))
    return recs_dicts


# ─── Función principal ────────────────────────────────────────────────────────

def analyze_match(
    match:      dict,
    home_form:  dict,
    away_form:  dict,
    h2h:        dict,
    standings:  list[dict],
    xbet_odds:  Optional[dict] = None,
) -> dict:

    home_id = match.get("home_team_id", 0)
    away_id = match.get("away_team_id", 0)
    league  = match.get("league", "PrvaLiga")

    # ── Componentes ──────────────────────────────────────────────────────────
    form_s,  form_d  = _form_component(home_form, away_form)
    h2h_s,   h2h_d   = _h2h_component(h2h, home_id, away_id)
    stand_s, stand_d = _standings_component(standings, home_id, away_id)
    goals_s, hxg, axg, goals_d = _goals_component(home_form, away_form, h2h)
    hadv_s,  hadv_d  = _home_advantage_component(league)
    cons_s,  cons_d  = _consistency_component(home_form, away_form)

    overall = min(form_s + h2h_s + stand_s + goals_s + hadv_s + cons_s, 100)

    # ── Probabilidades ───────────────────────────────────────────────────────
    matrix = _build_score_matrix(hxg, axg)
    hw, dp, aw = _probs_1x2(matrix)
    over25_p = _prob_over(matrix, 2.5)
    btts_p   = _prob_btts(matrix)

    # Blend con H2H
    h2h_over = h2h.get("over25_pct", 50) / 100
    h2h_btts = h2h.get("btts_pct",   50) / 100
    over25_f = over25_p * 0.7 + h2h_over * 0.3
    btts_f   = btts_p   * 0.7 + h2h_btts * 0.3

    probs = {
        "home_win": round(hw * 100, 1),
        "draw":     round(dp * 100, 1),
        "away_win": round(aw * 100, 1),
        "over_2_5": round(over25_f * 100, 1),
        "btts":     round(btts_f   * 100, 1),
    }

    # ── Recomendaciones ──────────────────────────────────────────────────────
    recs = _build_recommendations(
        hw, dp, aw, over25_f, btts_f, hxg, axg,
        form_d, h2h_d, stand_d, goals_d, matrix,
    )
    recs_dicts = [asdict(r) for r in recs]
    recs_dicts = _enrich_with_odds(recs_dicts, probs, xbet_odds)

    # ── Warnings ─────────────────────────────────────────────────────────────
    warnings = []
    if home_form.get("source") == "mock":
        warnings.append(f"Datos de forma de {match['home_team']} son estimados (Sofascore no respondió)")
    if away_form.get("source") == "mock":
        warnings.append(f"Datos de forma de {match['away_team']} son estimados")
    if h2h.get("source") == "mock" or h2h.get("total_matches", 0) == 0:
        warnings.append("Sin historial H2H disponible — usando valor neutral")
    if not xbet_odds:
        warnings.append("Cuotas 1xbet no disponibles — cálculo de EV desactivado")
    if match.get("sofascore_id") is None:
        warnings.append("Partido sin ID de Sofascore — datos de contexto limitados")

    dq = "full" if len(warnings) == 0 else ("partial" if len(warnings) <= 2 else "mock")

    top = recs_dicts[0] if recs_dicts else None
    summary = _generate_summary(
        match["home_team"], match["away_team"], hw, dp, aw, overall, recs[0] if recs else None
    )

    return {
        "match_id":    match.get("id", ""),
        "home_team":   match["home_team"],
        "away_team":   match["away_team"],
        "league":      league,
        "match_date":  match.get("date", ""),
        "round":       match.get("round", ""),

        "probabilities": probs,
        "xbet_odds":     xbet_odds,

        "score_breakdown": {
            "form":           form_d,
            "h2h":            h2h_d,
            "standings":      stand_d,
            "goals":          goals_d,
            "home_advantage": hadv_d,
            "consistency":    cons_d,
            "overall":        round(overall, 1),
        },

        "recommendations":    recs_dicts,
        "top_recommendation": top,

        "summary":            summary,
        "overall_confidence": round(overall, 1),
        "value_alert":        any(r["is_value"] for r in recs_dicts),
        "has_value_bet":      any(r["is_value"] for r in recs_dicts),

        "data_quality": dq,
        "warnings":     warnings,
    }
