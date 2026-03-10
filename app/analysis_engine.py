"""
Motor de Análisis de Apuestas - Fútbol Esloveno
================================================

Metodología de scoring (0-100 puntos de confianza):

1. FORMA RECIENTE (25 pts)
   - Weighted form últimos 5 partidos (más reciente = más peso)
   - Win=3, Draw=1, Loss=0 → normalizado

2. HEAD-TO-HEAD (20 pts)
   - Historial directo de últimos 10 encuentros
   - Penaliza cuando el historial es muy parejo (mayor incertidumbre)

3. POSICIÓN EN TABLA (20 pts)
   - Diferencia de puntos relativa a total disponible
   - Considera momentum (forma de tabla reciente)

4. ESTADÍSTICAS DE GOLES (20 pts)
   - Expected Goals basado en promedios de temporada
   - Modelo Poisson para over/under y BTTS

5. FACTOR LOCAL (10 pts)
   - Ventaja de jugar en casa (estadístico de la liga)

6. CONSISTENCIA / VOLATILIDAD (5 pts)
   - Equipos con resultados muy erráticos penalizados
   - Mayor consistencia = más confianza en la predicción

Tipos de apuesta analizados:
  - 1X2 (resultado final)
  - Doble oportunidad (1X, X2, 12)
  - Over/Under 2.5 goles
  - Ambos equipos marcan (BTTS)
  - Resultado en el descanso
"""

import math
from typing import Optional
from dataclasses import dataclass, asdict


@dataclass
class BetRecommendation:
    bet_type: str          # '1X2', 'double_chance', 'over_under', 'btts'
    selection: str         # '1', 'X', '2', 'over_2.5', 'yes', '1X', etc.
    confidence: float      # 0-100
    label: str             # Human readable
    reasoning: list[str]   # Bullet points explicando el por qué
    risk_level: str        # 'low', 'medium', 'high'
    min_odds: float        # Cuota mínima recomendada para que valga la pena


@dataclass
class MatchAnalysis:
    match_id: str
    home_team: str
    away_team: str
    league: str
    match_date: str
    
    # Probabilidades calculadas
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    over25_prob: float
    btts_prob: float
    
    # Score componentes
    form_score: dict
    h2h_score: dict
    standings_score: dict
    goals_score: dict
    
    # Recomendaciones ordenadas por confianza
    recommendations: list[BetRecommendation]
    
    # Resumen ejecutivo
    summary: str
    overall_confidence: float
    value_alert: bool       # True si hay apuesta con mucho valor vs cuota 1xbet
    
    # Metadata
    data_quality: str       # 'full', 'partial', 'mock'
    warnings: list[str]


def _form_to_points(form: list[str]) -> float:
    """Convert form string to weighted points. Recent matches weight more."""
    weights = [0.10, 0.15, 0.20, 0.25, 0.30]  # oldest → newest
    result_pts = {"W": 3, "D": 1, "L": 0}
    
    if not form:
        return 1.5  # neutral si no hay datos
    
    recent = form[-5:]
    while len(recent) < 5:
        recent.insert(0, "D")  # pad con draws si faltan datos
    
    total = sum(weights[i] * result_pts.get(r, 1) for i, r in enumerate(recent))
    return total  # max=3.0


def _poisson_prob(lam: float, k: int) -> float:
    """Poisson probability for k goals given lambda."""
    try:
        return (math.exp(-lam) * (lam ** k)) / math.factorial(k)
    except (ValueError, OverflowError):
        return 0.0


def _calculate_match_probs(
    home_attack: float,
    home_defense: float,
    away_attack: float,
    away_defense: float,
    home_advantage: float = 1.15
) -> tuple[float, float, float]:
    """
    Calculates 1X2 probabilities using a Dixon-Coles inspired Poisson model.
    home_attack: average goals scored by home team
    home_defense: average goals conceded by home team
    away_attack: average goals scored by away team
    away_defense: average goals conceded by away team
    """
    # Expected goals for each team
    home_xg = home_attack * away_defense * home_advantage
    away_xg = away_attack * home_defense

    # Cap to avoid crazy values
    home_xg = min(max(home_xg, 0.3), 4.0)
    away_xg = min(max(away_xg, 0.3), 3.5)

    home_win = 0.0
    draw = 0.0
    away_win = 0.0

    # Sum over scorelines 0-0 to 7-7
    for i in range(8):
        for j in range(8):
            p = _poisson_prob(home_xg, i) * _poisson_prob(away_xg, j)
            if i > j:
                home_win += p
            elif i == j:
                draw += p
            else:
                away_win += p

    total = home_win + draw + away_win
    if total == 0:
        return 0.4, 0.25, 0.35

    return round(home_win / total, 4), round(draw / total, 4), round(away_win / total, 4)


def _over25_probability(home_xg: float, away_xg: float) -> float:
    """Probability of over 2.5 goals using Poisson."""
    prob_under = 0.0
    for i in range(3):  # 0, 1, 2 goals total
        for j in range(3 - i + 1):
            if i + j <= 2:
                prob_under += _poisson_prob(home_xg, i) * _poisson_prob(away_xg, j)
    return round(max(0, 1 - prob_under), 4)


def _btts_probability(home_xg: float, away_xg: float) -> float:
    """Both teams to score probability."""
    home_scores = 1 - _poisson_prob(home_xg, 0)
    away_scores = 1 - _poisson_prob(away_xg, 0)
    return round(home_xg * away_xg / (1 + home_xg * away_xg), 4)  # simplified


def _form_component(home_form: dict, away_form: dict) -> tuple[float, dict]:
    """Score 0-25 for form component."""
    home_pts = _form_to_points(home_form.get("form", []))
    away_pts = _form_to_points(away_form.get("form", []))
    
    # Normalize to 0-1 per team
    home_norm = home_pts / 3.0
    away_norm = away_pts / 3.0
    
    # Differential: positive = home better
    differential = home_norm - away_norm  # -1 to +1
    
    # Score 25 pts: 12.5 is neutral
    score = 12.5 + (differential * 12.5)
    
    return round(score, 2), {
        "home_form": home_form.get("form_string", "?????"),
        "away_form": away_form.get("form_string", "?????"),
        "home_weighted_pts": round(home_pts, 2),
        "away_weighted_pts": round(away_pts, 2),
        "home_avg_scored": home_form.get("avg_scored", 0),
        "away_avg_scored": away_form.get("avg_scored", 0),
        "home_avg_conceded": home_form.get("avg_conceded", 0),
        "away_avg_conceded": away_form.get("avg_conceded", 0),
        "home_games_analyzed": home_form.get("games_analyzed", 0),
        "away_games_analyzed": away_form.get("games_analyzed", 0),
        "home_recent_matches": home_form.get("recent_matches", []),
        "away_recent_matches": away_form.get("recent_matches", []),
        "score_out_of_25": round(score, 2),
    }


def _h2h_component(h2h: dict, home_id: int, away_id: int) -> tuple[float, dict]:
    """Score 0-20 for H2H component."""
    total = h2h.get("total_matches", 0)
    
    if total == 0:
        return 10.0, {"note": "Sin historial disponible", "score_out_of_20": 10.0}
    
    home_wins = h2h.get("home_wins", 0)
    away_wins = h2h.get("away_wins", 0)
    draws = h2h.get("draws", 0)
    
    # Home dominance
    home_rate = home_wins / total
    uncertainty_penalty = 1 - abs(home_rate - 0.5) * 0.5  # 1 cuando 50/50, menor cuando dominante
    
    # Score: 10 neutral, más cerca de 20 si home domina, más cerca de 0 si away domina
    score = 10 + (home_rate - 0.3) * 20  # ajustado por home advantage esperada
    score = min(max(score, 0), 20)
    
    return round(score, 2), {
        "total_matches": total,
        "home_wins": home_wins,
        "draws": draws,
        "away_wins": away_wins,
        "home_win_rate": round(home_rate * 100, 1),
        "avg_goals_h2h": h2h.get("avg_goals", 0),
        "btts_pct": h2h.get("btts_pct", 0),
        "over25_pct": h2h.get("over25_pct", 0),
        "score_out_of_20": round(score, 2),
    }


def _standings_component(standings: list[dict], home_id: int, away_id: int) -> tuple[float, dict]:
    """Score 0-20 for standings component."""
    if not standings:
        return 10.0, {"note": "Tabla no disponible", "score_out_of_20": 10.0}
    
    home_entry = next((s for s in standings if s["team_id"] == home_id), None)
    away_entry = next((s for s in standings if s["team_id"] == away_id), None)
    
    if not home_entry or not away_entry:
        return 10.0, {"note": "Equipos no encontrados en tabla", "score_out_of_20": 10.0}
    
    total_teams = len(standings)
    max_pts_diff = max(s["points"] for s in standings) - min(s["points"] for s in standings)
    
    pts_diff = home_entry["points"] - away_entry["points"]
    rank_diff = away_entry["rank"] - home_entry["rank"]  # positive = home ranked higher
    
    # Normalize
    pts_norm = (pts_diff / max_pts_diff) if max_pts_diff > 0 else 0
    rank_norm = rank_diff / total_teams
    
    combined = (pts_norm * 0.6 + rank_norm * 0.4)  # weighted
    score = 10 + combined * 10
    score = min(max(score, 0), 20)
    
    return round(score, 2), {
        "home_rank": home_entry["rank"],
        "home_points": home_entry["points"],
        "home_form_table": home_entry.get("form", ""),
        "away_rank": away_entry["rank"],
        "away_points": away_entry["points"],
        "away_form_table": away_entry.get("form", ""),
        "points_diff": pts_diff,
        "rank_diff": rank_diff,
        "score_out_of_20": round(score, 2),
    }


def _goals_component(home_form: dict, away_form: dict, h2h: dict) -> tuple[float, float, float, dict]:
    """
    Returns: over25_score (0-20), home_xg, away_xg, details dict.
    """
    home_attack = home_form.get("avg_scored", 1.4)
    home_defense = home_form.get("avg_conceded", 1.2)
    away_attack = away_form.get("avg_scored", 1.2)
    away_defense = away_form.get("avg_conceded", 1.3)
    
    # Normalize defense: higher conceded = weaker defense = higher xg for opponent
    home_xg = home_attack * (away_defense / 1.2)
    away_xg = away_attack * (home_defense / 1.2)
    
    # Clamp
    home_xg = min(max(home_xg, 0.4), 3.5)
    away_xg = min(max(away_xg, 0.3), 3.0)
    
    over25_prob = _over25_probability(home_xg, away_xg)
    btts_prob = _btts_probability(home_xg, away_xg)
    
    # H2H adjustment
    h2h_over = h2h.get("over25_pct", 50) / 100
    h2h_btts = h2h.get("btts_pct", 50) / 100
    
    # Blend: 70% model, 30% H2H history
    over25_final = over25_prob * 0.7 + h2h_over * 0.3
    btts_final = btts_prob * 0.7 + h2h_btts * 0.3
    
    over25_score = over25_final * 20
    
    return round(over25_score, 2), round(home_xg, 2), round(away_xg, 2), {
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2),
        "total_xg": round(home_xg + away_xg, 2),
        "over25_prob": round(over25_final * 100, 1),
        "btts_prob": round(btts_final * 100, 1),
        "score_out_of_20": round(over25_score, 2),
    }


def _home_advantage_component(league: str) -> tuple[float, dict]:
    """Score 0-10 for home advantage. Uses known home win rates for Slovenian leagues."""
    home_win_rates = {
        "PrvaLiga": 0.44,
        "2SNL": 0.41,
    }
    rate = home_win_rates.get(league, 0.42)
    score = rate * 10 / 0.5  # 0.5 = neutral reference
    score = min(max(score, 0), 10)
    
    return round(score, 2), {
        "league_home_win_rate": f"{round(rate * 100, 1)}%",
        "score_out_of_10": round(score, 2),
    }


def _consistency_component(home_form: dict, away_form: dict) -> tuple[float, dict]:
    """Score 0-5 for consistency/predictability."""
    def variance_score(results: list) -> float:
        if not results:
            return 0.5
        pts = [{"W": 3, "D": 1, "L": 0}[r] for r in results if r in "WDL"]
        if len(pts) < 2:
            return 0.5
        mean = sum(pts) / len(pts)
        variance = sum((p - mean) ** 2 for p in pts) / len(pts)
        # Max variance = ~4.5 (alternating W/L), 0 = all same result
        return 1 - (variance / 4.5)
    
    home_cons = variance_score(home_form.get("form", []))
    away_cons = variance_score(away_form.get("form", []))
    avg_cons = (home_cons + away_cons) / 2
    score = avg_cons * 5
    
    return round(score, 2), {
        "home_consistency": round(home_cons * 100, 1),
        "away_consistency": round(away_cons * 100, 1),
        "score_out_of_5": round(score, 2),
    }


def _build_recommendations(
    home_win_p: float,
    draw_p: float,
    away_win_p: float,
    over25_p: float,
    btts_p: float,
    form_details: dict,
    h2h_details: dict,
    standings_details: dict,
    goals_details: dict,
) -> list[BetRecommendation]:
    
    recs = []
    
    # === 1X2 ===
    probs_1x2 = [
        ("1", home_win_p, "Victoria local"),
        ("X", draw_p, "Empate"),
        ("2", away_win_p, "Victoria visitante"),
    ]
    best_1x2 = max(probs_1x2, key=lambda x: x[1])
    sel, prob, label = best_1x2
    
    if prob > 0.50:
        confidence = min(prob * 100 * 1.1, 95)
        risk = "low" if prob > 0.65 else "medium"
        reasoning = []
        
        if sel == "1":
            reasoning.append(f"Forma local: {form_details['home_form']} (ponderado {form_details['home_weighted_pts']}/3.0)")
            if standings_details.get("home_rank"):
                reasoning.append(f"Local en posición #{standings_details['home_rank']} con {standings_details['home_points']} pts")
            reasoning.append(f"Tasa de victoria en casa de la liga: {goals_details.get('home_xg', 1.2)} xG esperados")
        elif sel == "2":
            reasoning.append(f"Forma visitante: {form_details['away_form']} (ponderado {form_details['away_weighted_pts']}/3.0)")
            if standings_details.get("away_rank"):
                reasoning.append(f"Visitante en posición #{standings_details['away_rank']} con {standings_details['away_points']} pts")
        else:
            reasoning.append("Equipos muy parejos según todos los indicadores")
            reasoning.append(f"H2H: {h2h_details.get('draws', 0)} empates en {h2h_details.get('total_matches', 0)} partidos históricos")
        
        min_odds = round(1 / max(prob - 0.05, 0.1), 2)
        
        recs.append(BetRecommendation(
            bet_type="1X2",
            selection=sel,
            confidence=round(confidence, 1),
            label=f"1X2 → {label}",
            reasoning=reasoning,
            risk_level=risk,
            min_odds=min_odds,
        ))
    
    # === DOBLE OPORTUNIDAD ===
    double_chance = {
        "1X": home_win_p + draw_p,
        "12": home_win_p + away_win_p,
        "X2": draw_p + away_win_p,
    }
    best_dc = max(double_chance.items(), key=lambda x: x[1])
    dc_sel, dc_prob = best_dc
    
    if dc_prob > 0.70:
        dc_labels = {"1X": "Local o Empate", "12": "No hay empate", "X2": "Visitante o Empate"}
        dc_conf = min(dc_prob * 100 * 0.95, 92)
        min_odds_dc = round(1 / max(dc_prob - 0.03, 0.1), 2)
        
        recs.append(BetRecommendation(
            bet_type="double_chance",
            selection=dc_sel,
            confidence=round(dc_conf, 1),
            label=f"Doble Oportunidad → {dc_labels[dc_sel]}",
            reasoning=[
                f"Probabilidad combinada: {round(dc_prob * 100, 1)}%",
                f"Cubre dos de los tres resultados posibles",
                f"Recomendado cuando la cuota supere {min_odds_dc}",
            ],
            risk_level="low",
            min_odds=min_odds_dc,
        ))
    
    # === OVER/UNDER 2.5 ===
    over_sel = "over_2.5" if over25_p > 0.50 else "under_2.5"
    over_prob = over25_p if over25_p > 0.50 else (1 - over25_p)
    over_label = "Más de 2.5 goles" if over_sel == "over_2.5" else "Menos de 2.5 goles"
    
    if over_prob > 0.52:
        xg_total = goals_details.get("total_xg", 0)
        over_conf = min(over_prob * 100 * 1.05, 90)
        
        reasoning_over = [
            f"xG total del partido: {xg_total} goles esperados",
            f"Promedio goles local últimos partidos: {form_details['home_avg_scored']} anotados / {form_details['home_avg_conceded']} encajados",
            f"Promedio goles visitante: {form_details['away_avg_scored']} anotados / {form_details['away_avg_conceded']} encajados",
        ]
        if h2h_details.get("avg_goals_h2h"):
            reasoning_over.append(f"Promedio goles en H2H: {h2h_details['avg_goals_h2h']}")
        
        recs.append(BetRecommendation(
            bet_type="over_under",
            selection=over_sel,
            confidence=round(over_conf, 1),
            label=f"O/U → {over_label}",
            reasoning=reasoning_over,
            risk_level="medium" if over_prob < 0.65 else "low",
            min_odds=round(1 / max(over_prob - 0.05, 0.1), 2),
        ))
    
    # === BTTS ===
    btts_sel = "yes" if btts_p > 0.50 else "no"
    btts_prob_val = btts_p if btts_p > 0.50 else (1 - btts_p)
    
    if btts_prob_val > 0.55:
        btts_label = "Ambos equipos marcan - SÍ" if btts_sel == "yes" else "Ambos equipos marcan - NO"
        btts_conf = min(btts_prob_val * 100, 88)
        
        reasoning_btts = [
            f"xG local: {goals_details.get('home_xg', 0)} | xG visitante: {goals_details.get('away_xg', 0)}",
        ]
        if btts_sel == "yes":
            btts_pct = h2h_details.get("btts_pct", 0)
            if btts_pct:
                reasoning_btts.append(f"En el H2H, ambos marcaron en el {btts_pct}% de los partidos")
            cs_home = form_details.get("home_avg_conceded", 0)
            if cs_home < 0.8:
                reasoning_btts.append("⚠️ Portería local muy sólida, confianza moderada en BTTS-Sí")
        
        recs.append(BetRecommendation(
            bet_type="btts",
            selection=btts_sel,
            confidence=round(btts_conf, 1),
            label=f"BTTS → {btts_label}",
            reasoning=reasoning_btts,
            risk_level="medium",
            min_odds=round(1 / max(btts_prob_val - 0.05, 0.1), 2),
        ))
    
    # Sort by confidence descending
    recs.sort(key=lambda r: r.confidence, reverse=True)
    return recs


def _generate_summary(
    home_team: str,
    away_team: str,
    home_win_p: float,
    draw_p: float,
    away_win_p: float,
    overall_score: float,
    top_rec: Optional[BetRecommendation],
) -> str:
    
    fav = home_team if home_win_p > away_win_p else away_team
    fav_prob = max(home_win_p, away_win_p)
    
    if overall_score >= 70:
        quality = "ALTA confianza"
    elif overall_score >= 55:
        quality = "MEDIA confianza"
    else:
        quality = "BAJA confianza"
    
    summary = f"Partido {home_team} vs {away_team}. "
    summary += f"Probabilidad: Local {round(home_win_p*100,1)}% | Empate {round(draw_p*100,1)}% | Visitante {round(away_win_p*100,1)}%. "
    
    if top_rec:
        summary += f"Mejor apuesta recomendada: {top_rec.label} con {round(top_rec.confidence,0)}% de confianza. "
    
    summary += f"Calidad del análisis: {quality} ({round(overall_score,0)}/100)."
    return summary


def _calc_ev(model_prob_pct: float, odd: Optional[float]) -> Optional[float]:
    """EV = (model_prob × odd) − 1.  Positive → edge over bookie."""
    if not odd or odd <= 1 or not model_prob_pct:
        return None
    return round((model_prob_pct / 100) * odd - 1, 4)


def _enrich_with_odds(recs_dicts: list[dict], probs: dict, xbet_odds: Optional[dict]) -> list[dict]:
    """
    Attaches 1xbet odds and EV calculation to each recommendation.
    Also re-sorts: EV+ bets bubble to the top.
    """
    if not xbet_odds:
        for r in recs_dicts:
            r["xbet_odd"] = None
            r["ev"] = None
            r["is_value"] = False
        return recs_dicts

    # Map selection → (model_prob, xbet_odd_key)
    odd_map = {
        "1":         (probs["home_win"],  xbet_odds.get("home")),
        "X":         (probs["draw"],      xbet_odds.get("draw")),
        "2":         (probs["away_win"],  xbet_odds.get("away")),
        "over_2.5":  (probs["over_2_5"],  xbet_odds.get("over25")),
        "under_2.5": (100 - probs["over_2_5"], xbet_odds.get("under25")),
        "yes":       (probs["btts"],      xbet_odds.get("btts_yes")),
        "no":        (100 - probs["btts"], xbet_odds.get("btts_no")),
    }
    # Double chance: implied combined odd from 1xbet singles
    def dc_odd(a_key, b_key):
        a = xbet_odds.get(a_key)
        b = xbet_odds.get(b_key)
        if a and b:
            return round(1 / (1/a + 1/b), 3)
        return None

    odd_map["1X"] = (probs["home_win"] + probs["draw"],  dc_odd("home", "draw"))
    odd_map["X2"] = (probs["draw"] + probs["away_win"],  dc_odd("draw", "away"))
    odd_map["12"] = (probs["home_win"] + probs["away_win"], dc_odd("home", "away"))

    for r in recs_dicts:
        model_p, xbet_odd = odd_map.get(r["selection"], (None, None))
        ev = _calc_ev(model_p, xbet_odd)
        r["xbet_odd"] = xbet_odd
        r["ev"] = ev
        r["is_value"] = ev is not None and ev > 0.04

    # Re-sort: value bets first, then by confidence
    recs_dicts.sort(key=lambda r: (not r["is_value"], -r["confidence"]))
    return recs_dicts


def analyze_match(
    match: dict,
    home_form: dict,
    away_form: dict,
    h2h: dict,
    standings: list[dict],
    xbet_odds: Optional[dict] = None,
) -> dict:
    """
    Main analysis function. Returns a complete MatchAnalysis as dict.
    xbet_odds: output of xbet_scraper.get_odds_for() or None.
    """
    home_id = match.get("home_team_id", 0)
    away_id = match.get("away_team_id", 0)
    league = match.get("league", "PrvaLiga")
    
    # --- Component Scores ---
    form_score_val, form_details = _form_component(home_form, away_form)
    h2h_score_val, h2h_details = _h2h_component(h2h, home_id, away_id)
    standings_score_val, standings_details = _standings_component(standings, home_id, away_id)
    goals_score_val, home_xg, away_xg, goals_details = _goals_component(home_form, away_form, h2h)
    home_adv_score, home_adv_details = _home_advantage_component(league)
    consistency_score, consistency_details = _consistency_component(home_form, away_form)
    
    # --- Overall Score ---
    overall = (
        form_score_val +        # max 25
        h2h_score_val +         # max 20
        standings_score_val +   # max 20
        goals_score_val +       # max 20
        home_adv_score +        # max 10
        consistency_score       # max 5
    )
    # Normalize to 0-100
    overall_normalized = min(overall, 100)
    
    # --- Win Probabilities ---
    home_win_p, draw_p, away_win_p = _calculate_match_probs(
        home_attack=home_form.get("avg_scored", 1.3),
        home_defense=home_form.get("avg_conceded", 1.2),
        away_attack=away_form.get("avg_scored", 1.1),
        away_defense=away_form.get("avg_conceded", 1.3),
    )
    
    over25_p = goals_details["over25_prob"] / 100
    btts_p = goals_details["btts_prob"] / 100
    
    # --- Recommendations ---
    recs = _build_recommendations(
        home_win_p, draw_p, away_win_p,
        over25_p, btts_p,
        form_details, h2h_details, standings_details, goals_details,
    )
    
    # --- Warnings ---
    warnings = []
    if not home_form.get("games_analyzed") or home_form.get("games_analyzed", 0) < 3:
        warnings.append("Pocos datos de forma para el equipo local")
    if h2h.get("total_matches", 0) < 3:
        warnings.append("Historial H2H limitado (menos de 3 partidos)")
    if not standings:
        warnings.append("Tabla de posiciones no disponible")
    if not home_form.get("form") or not away_form.get("form"):
        warnings.append("Datos de forma incompletos - usando valores aproximados")
    
    data_quality = "full" if not warnings else ("partial" if len(warnings) <= 2 else "mock")
    
    top_rec = recs[0] if recs else None
    summary = _generate_summary(
        match["home_team"], match["away_team"],
        home_win_p, draw_p, away_win_p,
        overall_normalized, top_rec
    )
    
    # Build response dict
    recs_dicts = [asdict(r) for r in recs]

    probs_dict = {
        "home_win": round(home_win_p * 100, 1),
        "draw":     round(draw_p * 100, 1),
        "away_win": round(away_win_p * 100, 1),
        "over_2_5": round(over25_p * 100, 1),
        "btts":     round(btts_p * 100, 1),
    }

    # Attach 1xbet odds + EV to each recommendation
    recs_dicts = _enrich_with_odds(recs_dicts, probs_dict, xbet_odds)

    if not xbet_odds:
        warnings.append("Cuotas 1xbet no disponibles — comparación de valor desactivada")

    has_value = any(r["is_value"] for r in recs_dicts)

    return {
        "match_id":   match.get("id", ""),
        "home_team":  match["home_team"],
        "away_team":  match["away_team"],
        "league":     league,
        "match_date": match.get("date", ""),
        "round":      match.get("round", ""),

        "probabilities": probs_dict,

        "xbet_odds": xbet_odds,   # raw scraped odds (or None)

        "score_breakdown": {
            "form":           form_details,
            "h2h":            h2h_details,
            "standings":      standings_details,
            "goals":          goals_details,
            "home_advantage": home_adv_details,
            "consistency":    consistency_details,
            "overall":        round(overall_normalized, 1),
        },

        "recommendations":    recs_dicts,
        "top_recommendation": recs_dicts[0] if recs_dicts else None,

        "summary":            summary,
        "overall_confidence": round(overall_normalized, 1),
        "value_alert":        has_value,
        "has_value_bet":      has_value,

        "data_quality": data_quality,
        "warnings":     warnings,
    }
