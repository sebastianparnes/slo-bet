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
try:
    from app.cal_correction import get_correction
    _HAS_CORRECTION = True
except ImportError:
    _HAS_CORRECTION = False


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


def _score_matrix(home_xg: float, away_xg: float, max_goals: int = 8) -> list[list[float]]:
    """Full scoreline probability matrix up to max_goals x max_goals."""
    return [
        [_poisson_prob(home_xg, i) * _poisson_prob(away_xg, j)
         for j in range(max_goals)]
        for i in range(max_goals)
    ]

def _over_probability(home_xg: float, away_xg: float, line: float) -> float:
    """P(total goals > line) for any line (1.5, 2.5, 3.5).
    For half-lines (x.5), P(over) = 1 - P(total <= floor(line)).
    E.g. line=2.5: P(over) = 1 - P(total=0) - P(total=1) - P(total=2)
    """
    max_under = int(line)  # 2.5->2, 1.5->1, 3.5->3, 0.5->0
    prob_under = sum(
        _poisson_prob(home_xg, i) * _poisson_prob(away_xg, j)
        for i in range(max_under + 1)
        for j in range(max_under + 1)
        if i + j <= max_under
    )
    return round(max(0, 1 - prob_under), 4)

def _btts_probability(home_xg: float, away_xg: float) -> float:
    """Both teams to score: P(home≥1) * P(away≥1)."""
    return round((1 - _poisson_prob(home_xg, 0)) * (1 - _poisson_prob(away_xg, 0)), 4)

def _asian_handicap(home_xg: float, away_xg: float, line: float) -> tuple[float, float]:
    """
    Asian handicap probabilities for given line (e.g. -1.5, -1, -0.5, 0, +0.5, +1, +1.5).
    Returns (home_cover_prob, away_cover_prob).
    line < 0 means home gives goals (favorite), line > 0 means home gets goals (underdog).
    """
    matrix = _score_matrix(home_xg, away_xg, 9)
    home_cover = 0.0
    away_cover = 0.0
    push = 0.0
    for i in range(9):
        for j in range(9):
            p = matrix[i][j]
            diff = i - j  # home - away
            adjusted = diff + line  # add handicap to home score
            if abs(adjusted - round(adjusted)) < 0.01:  # integer line = possible push
                if adjusted > 0:
                    home_cover += p
                elif adjusted < 0:
                    away_cover += p
                else:
                    push += p
            else:
                if diff > -line:
                    home_cover += p
                else:
                    away_cover += p
    # Distribute push equally
    home_cover += push / 2
    away_cover += push / 2
    return round(home_cover, 4), round(away_cover, 4)

def _ht_ft_probs(home_xg: float, away_xg: float) -> dict:
    """
    Estimate 1st half result and full-time result probabilities.
    Assumes ~45% of goals occur in each half (slight home bias in 2nd).
    Returns dict with ht_home, ht_draw, ht_away and btts_ht probs.
    """
    ht_home_xg = home_xg * 0.45
    ht_away_xg = away_xg * 0.43
    ht_h, ht_d, ht_a = _calculate_match_probs(ht_home_xg, ht_home_xg, ht_away_xg, ht_away_xg, 1.10)
    btts_ht = _btts_probability(ht_home_xg, ht_away_xg)
    over15_ht = _over_probability(ht_home_xg, ht_away_xg, 1.5)
    return {
        "ht_home": ht_h, "ht_draw": ht_d, "ht_away": ht_a,
        "btts_ht": round(btts_ht, 4),
        "over15_ht": round(over15_ht, 4),
    }

def _exact_score_top(home_xg: float, away_xg: float, top_n: int = 5) -> list[dict]:
    """Top N most likely exact scorelines."""
    matrix = _score_matrix(home_xg, away_xg, 7)
    scores = []
    for i in range(7):
        for j in range(7):
            scores.append({"score": f"{i}-{j}", "prob": round(matrix[i][j], 4)})
    scores.sort(key=lambda x: -x["prob"])
    return scores[:top_n]

def _corners_estimate(home_xg: float, away_xg: float, league: str) -> dict:
    """
    Estimate corner over/under probabilities.
    Base: ~10-11 corners per game in top leagues, 8-9 in smaller leagues.
    Correlated with game intensity (xG).
    """
    base = {"PrvaLiga": 8.5, "2SNL": 8.0, "ChampionsLeague": 10.5,
            "PremierLeague": 10.5, "LaLiga": 10.0, "SerieA": 10.0,
            "Bundesliga": 10.0, "Ligue1": 9.5, "CroatiaHNL": 9.0,
            "SerbiaSuper": 9.0, "PrimeraDivision": 9.0,
            "PrimeraNacional": 8.5, "UruguayPrimera": 8.5}.get(league, 9.0)
    # xG intensity modifies base
    intensity = (home_xg + away_xg) / 2.5  # normalized around 2.5 xG total
    expected_corners = base * intensity
    # Poisson approximation for corners
    over85 = _over_probability(expected_corners * 0.55, expected_corners * 0.45, 8.5)
    over95 = _over_probability(expected_corners * 0.55, expected_corners * 0.45, 9.5)
    over105 = _over_probability(expected_corners * 0.55, expected_corners * 0.45, 10.5)
    return {
        "expected_corners": round(expected_corners, 1),
        "over_8_5": round(over85, 4),
        "over_9_5": round(over95, 4),
        "over_10_5": round(over105, 4),
    }

def _cards_estimate(league: str, is_derby: bool = False) -> dict:
    """
    Estimate card over/under. Based on league averages.
    """
    base = {"PrvaLiga": 3.8, "2SNL": 3.5, "ChampionsLeague": 3.2,
            "PremierLeague": 3.0, "LaLiga": 4.2, "SerieA": 4.0,
            "Bundesliga": 3.2, "Ligue1": 3.8, "CroatiaHNL": 4.0,
            "SerbiaSuper": 4.5, "PrimeraDivision": 4.5,
            "PrimeraNacional": 4.2, "UruguayPrimera": 4.8}.get(league, 3.8)
    if is_derby:
        base *= 1.25
    over35 = min(0.55 + (base - 3.8) * 0.1, 0.85)
    over45 = min(0.35 + (base - 3.8) * 0.1, 0.70)
    return {
        "expected_cards": round(base, 1),
        "over_3_5": round(over35, 4),
        "over_4_5": round(over45, 4),
    }


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

    # Calibration scale factor: model historically overestimates xG by ~27%
    # Derived from 948 real matches: avg model xG=3.52 vs avg real goals=2.77
    # Scale = 2.77 / 3.52 = 0.787
    _XG_SCALE = 0.787
    home_xg = home_xg * _XG_SCALE
    away_xg = away_xg * _XG_SCALE

    # Clamp
    home_xg = min(max(home_xg, 0.35), 3.0)
    away_xg = min(max(away_xg, 0.25), 2.8)
    
    over25_prob = _over_probability(home_xg, away_xg, 2.5)
    btts_prob = _btts_probability(home_xg, away_xg)
    
    # H2H adjustment
    h2h_over = h2h.get("over25_pct", 50) / 100
    h2h_btts = h2h.get("btts_pct", 50) / 100
    
    # Blend: 70% model, 30% H2H — only if H2H has real data
    h2h_matches = h2h.get("total_matches", 0)
    if h2h_matches >= 3 and h2h.get("over25_pct", 0) > 0:
        over25_final = over25_prob * 0.70 + h2h_over * 0.30
        btts_final   = btts_prob   * 0.70 + h2h_btts * 0.30
    elif h2h_matches >= 3:
        # H2H exists but 0% over25 — weight it less (likely low-scoring rivalry)
        over25_final = over25_prob * 0.85 + h2h_over * 0.15
        btts_final   = btts_prob   * 0.85 + h2h_btts * 0.15
    else:
        # No H2H data — use model only
        over25_final = over25_prob
        btts_final   = btts_prob
    
    over25_score = over25_final * 20
    
    return round(over25_score, 2), round(home_xg, 2), round(away_xg, 2), {
        "home_xg": round(home_xg, 2),
        "away_xg": round(away_xg, 2),
        "total_xg": round(home_xg + away_xg, 2),
        "over25_prob": round(over25_final * 100, 1),
        "btts_prob": round(btts_final * 100, 1),
        "score_out_of_20": round(over25_score, 2),
    }


def _home_advantage_component(league: str, home_form: dict = None) -> tuple[float, dict]:
    """Score 0-10 for home advantage using league stats + real team home record."""
    home_win_rates = {
        "PrvaLiga": 0.44, "2SNL": 0.41, "PrimeraDivision": 0.43,
        "PrimeraNacional": 0.42, "ChampionsLeague": 0.47, "PremierLeague": 0.46,
        "LaLiga": 0.47, "SerieA": 0.46, "Bundesliga": 0.46, "Ligue1": 0.45,
        "CroatiaHNL": 0.44, "SerbiaSuper": 0.45, "UruguayPrimera": 0.43,
    }
    league_rate = home_win_rates.get(league, 0.42)
    score = league_rate * 10 / 0.5
    score = min(max(score, 0), 10)
    return round(score, 2), {
        "league_home_win_rate": f"{round(league_rate * 100, 1)}%",
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
    home_xg: float = 1.3,
    away_xg: float = 1.1,
    league: str = "PrvaLiga",
) -> list[BetRecommendation]:

    recs = []
    fd, hd, sd, gd = form_details, h2h_details, standings_details, goals_details

    # ── helpers ──────────────────────────────────────────────────────────
    def add(bet_type, selection, prob, label, reasoning, risk="medium"):
        if prob < 0.52:
            return
        conf = min(prob * 100 * (1.05 if risk == "low" else 0.98), 93)
        recs.append(BetRecommendation(
            bet_type=bet_type, selection=selection,
            confidence=round(conf, 1), label=label,
            reasoning=reasoning, risk_level=risk,
            min_odds=round(1 / max(prob - 0.04, 0.08), 2),
        ))

    home_str = fd.get("home_form", "?????")
    away_str = fd.get("away_form", "?????")
    h_sc = fd.get("home_avg_scored", 1.2)
    h_cc = fd.get("home_avg_conceded", 1.2)
    a_sc = fd.get("away_avg_scored", 1.0)
    a_cc = fd.get("away_avg_conceded", 1.2)
    h_pts = fd.get("home_weighted_pts", 1.5)
    a_pts = fd.get("away_weighted_pts", 1.5)

    # ── 1. 1X2 ───────────────────────────────────────────────────────────
    best_1x2 = max([("1", home_win_p, "Victoria local"),
                    ("X", draw_p, "Empate"),
                    ("2", away_win_p, "Victoria visitante")], key=lambda x: x[1])
    sel, prob, lbl = best_1x2
    if prob > 0.50:
        r = [f"Forma: local {home_str} vs visita {away_str}"]
        if sel == "1":
            r.append(f"xG local {round(home_xg,2)} > xG visita {round(away_xg,2)}")
            if sd.get("home_rank"): r.append(f"Local #{sd['home_rank']} ({sd['home_points']} pts) vs visita #{sd.get('away_rank','?')}")
        elif sel == "2":
            r.append(f"xG visita {round(away_xg,2)} supera al local {round(home_xg,2)}")
            if sd.get("away_rank"): r.append(f"Visita #{sd['away_rank']} ({sd['away_points']} pts)")
        else:
            r.append(f"H2H: {hd.get('draws',0)} empates en {hd.get('total_matches',0)} partidos")
            r.append("Equipos muy parejos en todos los indicadores")
        add("1X2", sel, prob, f"1X2 → {lbl}", r, "low" if prob > 0.62 else "medium")

    # ── 2. DOBLE OPORTUNIDAD ─────────────────────────────────────────────
    dc_opts = {"1X": home_win_p+draw_p, "12": home_win_p+away_win_p, "X2": draw_p+away_win_p}
    dc_lbls = {"1X":"Local o Empate","12":"No hay empate","X2":"Visitante o Empate"}
    dc_sel, dc_prob = max(dc_opts.items(), key=lambda x: x[1])
    if dc_prob > 0.68:
        add("double_chance", dc_sel, dc_prob,
            f"Doble Oportunidad → {dc_lbls[dc_sel]}",
            [f"Probabilidad combinada: {round(dc_prob*100,1)}%",
             "Cubre dos de los tres resultados posibles",
             f"Cuota mínima recomendada: {round(1/max(dc_prob-0.03,0.1),2)}"],
            "low")

    # ── 3. OVER/UNDER 2.5 ───────────────────────────────────────────────
    for line, label_o, label_u in [(1.5,"Más de 1.5","Menos de 1.5"),
                                    (2.5,"Más de 2.5","Menos de 2.5"),
                                    (3.5,"Más de 3.5","Menos de 3.5")]:
        op = _over_probability(home_xg, away_xg, line)
        up = 1 - op
        sel_ou = f"over_{line}" if op > up else f"under_{line}"
        p_ou = max(op, up)
        lbl_ou = (label_o if op > up else label_u) + " goles"
        r_ou = [f"xG total esperado: {round(home_xg+away_xg,2)} goles",
                f"Local: {h_sc} an / {h_cc} enc  |  Visita: {a_sc} an / {a_cc} enc"]
        if hd.get("avg_goals_h2h"): r_ou.append(f"Promedio H2H: {hd['avg_goals_h2h']} goles")
        add("over_under", sel_ou, p_ou, f"O/U {line} → {lbl_ou}", r_ou,
            "low" if p_ou > 0.68 else "medium")

    # ── 4. BTTS ──────────────────────────────────────────────────────────
    btts_sel = "yes" if btts_p > 0.50 else "no"
    btts_pv = btts_p if btts_p > 0.50 else 1 - btts_p
    btts_lbl = "Ambos marcan - SÍ" if btts_sel == "yes" else "Ambos marcan - NO"
    r_bt = [f"xG local {round(home_xg,2)} · xG visita {round(away_xg,2)}",
            f"P(home≥1 gol)={round((1-_poisson_prob(home_xg,0))*100,1)}%  P(away≥1 gol)={round((1-_poisson_prob(away_xg,0))*100,1)}%"]
    if hd.get("btts_pct"): r_bt.append(f"BTTS en H2H: {hd['btts_pct']}%")
    add("btts", btts_sel, btts_pv, f"BTTS → {btts_lbl}", r_bt)

    # ── 5. BTTS PRIMER TIEMPO ────────────────────────────────────────────
    ht = _ht_ft_probs(home_xg, away_xg)
    btts_ht_p = ht["btts_ht"]
    if btts_ht_p > 0.30 or (1 - btts_ht_p) > 0.70:
        ht_sel = "yes_ht" if btts_ht_p > 0.40 else "no_ht"
        ht_pv  = btts_ht_p if btts_ht_p > 0.40 else 1 - btts_ht_p
        add("btts_ht", ht_sel, ht_pv,
            f"BTTS 1er Tiempo → {'SÍ' if ht_sel=='yes_ht' else 'NO'}",
            [f"xG 1er tiempo: local ~{round(home_xg*0.45,2)} · visita ~{round(away_xg*0.43,2)}",
             f"P(ambos marcan en 1T): {round(btts_ht_p*100,1)}%",
             "Asume ~45% de goles en 1er tiempo (promedio estadístico)"],
            "high" if ht_pv < 0.62 else "medium")

    # ── 6. GANADOR POR TIEMPO ────────────────────────────────────────────
    ht_probs = [("ht_1", ht["ht_home"], "Local gana 1er Tiempo"),
                ("ht_X", ht["ht_draw"], "Empate 1er Tiempo"),
                ("ht_2", ht["ht_away"], "Visita gana 1er Tiempo")]
    best_ht = max(ht_probs, key=lambda x: x[1])
    sel_ht, p_ht, lbl_ht = best_ht
    if p_ht > 0.45:
        add("halftime", sel_ht, p_ht, f"1er Tiempo → {lbl_ht}",
            [f"Probabilidad calculada con xG parcial",
             f"xG 1T local: {round(home_xg*0.45,2)} · visita: {round(away_xg*0.43,2)}",
             "Mayor incertidumbre que resultado final — cuota mínima más alta"],
            "high")

    # ── 7. HANDICAP ASIÁTICO ─────────────────────────────────────────────
    diff = home_xg - away_xg
    # Suggest the line that gives closest to 50/50
    for line in [-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5]:
        hc, ac = _asian_handicap(home_xg, away_xg, line)
        side = "home" if hc > ac else "away"
        p_ah = max(hc, ac)
        if 0.54 <= p_ah <= 0.72:  # sweet spot for AH
            team_label = "Local" if side == "home" else "Visita"
            sign = f"{'+' if line > 0 else ''}{line if side=='home' else -line}"
            add("asian_handicap", f"ah_{side}_{line}",
                p_ah, f"Handicap Asiático → {team_label} {sign}",
                [f"xG: local {round(home_xg,2)} vs visita {round(away_xg,2)}",
                 f"Con handicap {sign}: cobertura {round(p_ah*100,1)}%",
                 f"Línea que mejor refleja la diferencia real entre equipos"],
                "medium")
            break  # one AH recommendation

    # ── 8. CORNERS ───────────────────────────────────────────────────────
    corn = _corners_estimate(home_xg, away_xg, league)
    exp_c = corn["expected_corners"]
    # Pick best line
    for c_line, c_prob_key in [(8.5,"over_8_5"),(9.5,"over_9_5"),(10.5,"over_10_5")]:
        cp = corn[c_prob_key]
        cup = 1 - cp
        c_sel = f"corners_over_{c_line}" if cp > cup else f"corners_under_{c_line}"
        c_pv = max(cp, cup)
        if c_pv > 0.58:
            add("corners", c_sel, c_pv,
                f"Corners {'Más' if cp>cup else 'Menos'} de {c_line}",
                [f"Corners esperados en este partido: ~{exp_c}",
                 f"Basado en intensidad xG ({round(home_xg+away_xg,2)} total) y media de la liga",
                 "Mercado con menor correlación directa — usar cuota >1.80"],
                "high")
            break

    # ── 9. TARJETAS ──────────────────────────────────────────────────────
    cards = _cards_estimate(league)
    exp_cards = cards["expected_cards"]
    c35 = cards["over_3_5"]
    c45 = cards["over_4_5"]
    best_cards_p = c35 if c35 > 0.55 else (1 - c35 if c35 < 0.45 else None)
    if best_cards_p and best_cards_p > 0.56:
        add("cards", "cards_over_3_5" if c35 > 0.5 else "cards_under_3_5",
            best_cards_p,
            f"Tarjetas {'Más' if c35>0.5 else 'Menos'} de 3.5",
            [f"Media de tarjetas en {league}: ~{exp_cards}/partido",
             f"Probabilidad Over 3.5: {round(c35*100,1)}% | Over 4.5: {round(c45*100,1)}%",
             "Basado en estadística histórica de la liga"],
            "high")

    # ── 10. RESULTADO EXACTO (top 3 más probables) ──────────────────────
    top_scores = _exact_score_top(home_xg, away_xg, 3)
    for sc in top_scores:
        if sc["prob"] > 0.12:  # only if >12% probability
            add("exact_score", f"score_{sc['score']}", sc["prob"],
                f"Resultado Exacto → {sc['score']}",
                [f"Probabilidad Poisson: {round(sc['prob']*100,1)}%",
                 f"xG local {round(home_xg,2)} · xG visita {round(away_xg,2)}",
                 "Mercado de alto riesgo — cuota mínima recomendada >5.0"],
                "high")

    # Sort: EV-aware sort will happen in _enrich_with_odds; here sort by confidence
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
    def dc_odd(a_key, b_key):
        a = xbet_odds.get(a_key)
        b = xbet_odds.get(b_key)
        if a and b:
            return round(1 / (1/a + 1/b), 3)
        return None

    odd_map = {
        "1":          (probs["home_win"],              xbet_odds.get("home")),
        "X":          (probs["draw"],                  xbet_odds.get("draw")),
        "2":          (probs["away_win"],              xbet_odds.get("away")),
        "over_2.5":   (probs["over_2_5"],              xbet_odds.get("over25")),
        "under_2.5":  (100 - probs["over_2_5"],        xbet_odds.get("under25")),
        "over_1.5":   (probs.get("over_1_5", 70),      None),
        "under_1.5":  (100 - probs.get("over_1_5",70), None),
        "over_3.5":   (probs.get("over_3_5", 30),      None),
        "under_3.5":  (100 - probs.get("over_3_5",30), None),
        "yes":        (probs["btts"],                  xbet_odds.get("btts_yes")),
        "no":         (100 - probs["btts"],            xbet_odds.get("btts_no")),
        "1X":         (probs["home_win"]+probs["draw"],dc_odd("home","draw")),
        "X2":         (probs["draw"]+probs["away_win"],dc_odd("draw","away")),
        "12":         (probs["home_win"]+probs["away_win"],dc_odd("home","away")),
    }
    # Dynamic keys for AH, corners, cards, exact scores — no direct xbet key mapping yet
    for key in list(odd_map.keys()):
        pass  # above covers static keys

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
    home_adv_score, home_adv_details = _home_advantage_component(league, home_form)
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

    # --- Calibration correction ---
    _correction_applied = {}
    if _HAS_CORRECTION:
        try:
            cf = get_correction(league)
            home_win_p, draw_p, away_win_p = cf.apply_1x2(home_win_p, draw_p, away_win_p)
            home_xg, away_xg = cf.apply_xg(home_xg, away_xg)
            # Recalculate over/under and btts with corrected xG
            if cf.samples >= 30:
                over25_p = _over_probability(home_xg, away_xg, 2.5)
                btts_p   = _btts_probability(home_xg, away_xg)
            _correction_applied = cf.correction_summary()
        except Exception as _ce:
            print(f"[analyze_match] correction error: {_ce}")

    # --- Recommendations ---
    recs = _build_recommendations(
        home_win_p, draw_p, away_win_p,
        over25_p, btts_p,
        form_details, h2h_details, standings_details, goals_details,
        home_xg=home_xg, away_xg=away_xg, league=league,
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
    if '_correction_applied' not in dir(): _correction_applied = {}
    
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
        "calibration_correction": _correction_applied,

        "data_quality": data_quality,
        "warnings":     warnings,
    }
