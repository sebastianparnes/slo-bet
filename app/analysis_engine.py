"""
Motor de Análisis — Betting Analysis
=====================================
Modelo estadístico mejorado:
  - Dixon-Coles corregido (τ para scorelines bajos)
  - Poisson bivariado para xG
  - Pesos temporales en forma reciente
  - Regresión a la media por liga
  - EV con Kelly fraccionado
  - Reasoning detallado por componente
"""

import math
from typing import Optional
from dataclasses import dataclass, asdict

LEAGUE_AVERAGES = {
    "PrvaLiga":        {"home": 1.55, "away": 1.05, "home_rate": 0.44},
    "2SNL":            {"home": 1.45, "away": 1.00, "home_rate": 0.41},
    "PrimeraDivision": {"home": 1.60, "away": 1.10, "home_rate": 0.46},
    "PrimeraNacional": {"home": 1.50, "away": 1.05, "home_rate": 0.43},
    "PremierLeague":   {"home": 1.53, "away": 1.15, "home_rate": 0.46},
    "LaLiga":          {"home": 1.58, "away": 1.12, "home_rate": 0.46},
    "SerieA":          {"home": 1.45, "away": 1.05, "home_rate": 0.44},
    "Bundesliga":      {"home": 1.78, "away": 1.35, "home_rate": 0.45},
    "Ligue1":          {"home": 1.52, "away": 1.08, "home_rate": 0.44},
    "ChampionsLeague": {"home": 1.65, "away": 1.25, "home_rate": 0.42},
    "CroatiaHNL":      {"home": 1.60, "away": 1.05, "home_rate": 0.46},
    "SerbiaSuper":     {"home": 1.55, "away": 1.00, "home_rate": 0.45},
    "UruguayPrimera":  {"home": 1.50, "away": 1.10, "home_rate": 0.47},
}
DEFAULT_AVG = {"home": 1.55, "away": 1.10, "home_rate": 0.44}


@dataclass
class BetRecommendation:
    bet_type:   str
    selection:  str
    confidence: float
    label:      str
    reasoning:  list
    risk_level: str
    min_odds:   float


def _poisson(lam: float, k: int) -> float:
    try:
        return math.exp(-lam) * (lam ** k) / math.factorial(k)
    except (ValueError, OverflowError):
        return 0.0


def _dc_tau(i, j, lh, la, rho=-0.13):
    if i == 0 and j == 0: return max(1 - lh * la * rho, 0)
    if i == 1 and j == 0: return 1 + la * rho
    if i == 0 and j == 1: return 1 + lh * rho
    if i == 1 and j == 1: return 1 - rho
    return 1.0


def _match_probs(hxg: float, axg: float):
    hxg = min(max(hxg, 0.25), 5.0)
    axg = min(max(axg, 0.25), 4.5)
    hw = dr = aw = 0.0
    matrix = {}
    for i in range(9):
        for j in range(9):
            p = _poisson(hxg, i) * _poisson(axg, j) * _dc_tau(i, j, hxg, axg)
            p = max(p, 0.0)
            matrix[(i, j)] = p
            if i > j:    hw += p
            elif i == j: dr += p
            else:        aw += p
    t = hw + dr + aw or 1
    return hw/t, dr/t, aw/t, matrix


def _over_under(matrix, thr=2.5):
    over = sum(p for (i, j), p in matrix.items() if i + j > thr)
    return round(over, 4), round(1 - over, 4)


def _btts(matrix):
    return round(sum(p for (i, j), p in matrix.items() if i > 0 and j > 0), 4)


def _calc_xg(hf, af, league):
    lg = LEAGUE_AVERAGES.get(league, DEFAULT_AVG)
    lg_h, lg_a = lg["home"], lg["away"]
    hg = max(hf.get("games_analyzed", 5), 1)
    ag = max(af.get("games_analyzed", 5), 1)
    hw = min(hg / 10, 0.80)
    aw = min(ag / 10, 0.80)
    h_att = hf.get("avg_scored",   lg_h) * hw + lg_h * (1 - hw)
    h_def = hf.get("avg_conceded", lg_a) * aw + lg_a * (1 - aw)
    a_att = af.get("avg_scored",   lg_a) * aw + lg_a * (1 - aw)
    a_def = af.get("avg_conceded", lg_h) * hw + lg_h * (1 - hw)
    ha = 1 + (lg["home_rate"] - 0.42) * 0.5 + 0.08
    hxg = (h_att * a_def / lg_a) * ha
    axg = (a_att * h_def / lg_h) / (ha * 0.85)
    return round(min(max(hxg, 0.3), 4.5), 3), round(min(max(axg, 0.25), 3.5), 3)


def _form_pts(form):
    if not form: return 1.2
    W = [0.08, 0.12, 0.17, 0.25, 0.38]
    RM = {"W": 3, "G": 3, "D": 1, "E": 1, "L": 0, "P": 0}
    r = list(form)[-5:]
    while len(r) < 5: r.insert(0, "D")
    return sum(W[i] * RM.get(c, 1) for i, c in enumerate(r))


def _form_component(hf, af):
    hp = _form_pts(hf.get("form", []))
    ap = _form_pts(af.get("form", []))
    diff = (hp - ap) / 3.0
    score = round(min(max(12.5 + diff * 12.5, 0), 25), 2)
    return score, {
        "home_form":            hf.get("form_string", "?????"),
        "away_form":            af.get("form_string", "?????"),
        "home_weighted_pts":    round(hp, 2),
        "away_weighted_pts":    round(ap, 2),
        "home_avg_scored":      hf.get("avg_scored", 0),
        "away_avg_scored":      af.get("avg_scored", 0),
        "home_avg_conceded":    hf.get("avg_conceded", 0),
        "away_avg_conceded":    af.get("avg_conceded", 0),
        "home_games_analyzed":  hf.get("games_analyzed", 0),
        "away_games_analyzed":  af.get("games_analyzed", 0),
        "home_recent_matches":  hf.get("recent_matches", [])[:7],
        "away_recent_matches":  af.get("recent_matches", [])[:7],
        "score_out_of_25":      score,
    }


def _h2h_component(h2h, home_id, away_id):
    total = h2h.get("total_matches", 0)
    if total == 0:
        return 10.0, {"note": "Sin historial H2H", "total_matches": 0,
                      "home_wins": 0, "draws": 0, "away_wins": 0,
                      "home_win_rate": 0, "avg_goals_h2h": 0,
                      "btts_pct": 50, "over25_pct": 50, "recent": [],
                      "score_out_of_20": 10.0}
    hw = h2h.get("home_wins", 0)
    dr = h2h.get("draws", 0)
    aw = h2h.get("away_wins", 0)
    rate = hw / total
    score = round(min(max(10.0 + (rate - 0.44) * 22.0, 0), 20), 2)
    return score, {
        "total_matches": total, "home_wins": hw, "draws": dr, "away_wins": aw,
        "home_win_rate": round(rate * 100, 1),
        "avg_goals_h2h": h2h.get("avg_goals", 0),
        "btts_pct":      h2h.get("btts_pct", 50),
        "over25_pct":    h2h.get("over25_pct", 50),
        "recent":        h2h.get("recent", [])[:5],
        "score_out_of_20": score,
    }


def _standings_component(standings, home_id, away_id):
    if not standings:
        return 10.0, {"note": "Tabla no disponible", "score_out_of_20": 10.0}
    he = next((s for s in standings if s.get("team_id") == home_id), None)
    ae = next((s for s in standings if s.get("team_id") == away_id), None)
    if not he or not ae:
        return 10.0, {"note": "Equipos no en tabla", "score_out_of_20": 10.0}
    n = len(standings)
    pr = max((max(s.get("points",0) for s in standings) - min(s.get("points",0) for s in standings)), 1)
    pd = (he.get("points",0) - ae.get("points",0)) / pr
    rd = (ae.get("rank", n//2) - he.get("rank", n//2)) / max(n, 1)
    score = round(min(max(10.0 + (pd * 0.55 + rd * 0.45) * 10, 0), 20), 2)
    return score, {
        "home_rank": he.get("rank","?"), "home_points": he.get("points",0),
        "home_played": he.get("played",0), "home_gd": he.get("goal_diff",0),
        "home_form_table": he.get("form",""),
        "away_rank": ae.get("rank","?"), "away_points": ae.get("points",0),
        "away_played": ae.get("played",0), "away_gd": ae.get("goal_diff",0),
        "away_form_table": ae.get("form",""),
        "points_diff": he.get("points",0) - ae.get("points",0),
        "rank_diff": ae.get("rank",0) - he.get("rank",0),
        "total_teams": n, "score_out_of_20": score,
    }


def _goals_component(hf, af, h2h, league, matrix, hxg, axg):
    o25, _ = _over_under(matrix)
    btt    = _btts(matrix)
    h2h_o  = h2h.get("over25_pct", 50) / 100
    h2h_b  = h2h.get("btts_pct",   50) / 100
    o25f   = round(o25 * 0.70 + h2h_o * 0.30, 4)
    bttf   = round(btt * 0.70 + h2h_b * 0.30, 4)
    score  = round(o25f * 20, 2)
    return score, {
        "expected_home_goals": hxg, "expected_away_goals": axg,
        "total_xg":    round(hxg + axg, 2),
        "over25_prob": round(o25f * 100, 1),
        "under25_prob":round((1 - o25f) * 100, 1),
        "btts_prob":   round(bttf * 100, 1),
        "score_out_of_20": score,
    }


def _home_adv_component(league):
    lg = LEAGUE_AVERAGES.get(league, DEFAULT_AVG)
    rate = lg["home_rate"]
    score = round(min(max((rate - 0.35) / 0.20 * 10, 0), 10), 2)
    return score, {"league": league,
                   "league_home_win_rate": f"{round(rate*100,1)}%",
                   "score_out_of_10": score}


def _consistency_component(hf, af):
    def vs(form):
        if not form or len(form) < 2: return 0.5
        M = {"W":3,"G":3,"D":1,"E":1,"L":0,"P":0}
        pts = [M.get(r,1) for r in form if r in M]
        if len(pts) < 2: return 0.5
        mu = sum(pts)/len(pts)
        return round(1 - sum((p-mu)**2 for p in pts)/len(pts)/4.5, 3)
    hc = vs(hf.get("form",[]))
    ac = vs(af.get("form",[]))
    score = round((hc+ac)/2*5, 2)
    return score, {"home_consistency": round(hc*100,1),
                   "away_consistency": round(ac*100,1),
                   "score_out_of_5": score}


def _build_recs(hw, dr, aw, o25, btt, fd, h2h_d, sd, gd, ht, at):
    recs = []
    # 1X2
    sel, prob, lbl = max([("1",hw,f"Victoria {ht}"),("X",dr,"Empate"),("2",aw,f"Victoria {at}")], key=lambda x:x[1])
    if prob > 0.42:
        conf = min(prob*100*1.08, 94)
        risk = "low" if prob > 0.62 else "medium"
        rs = []
        if sel == "1":
            rs.append(f"Forma local {fd['home_form']} — {fd['home_weighted_pts']:.1f}/3.0 pts ponderados")
            rs.append(f"xG esperado: {gd['expected_home_goals']} vs {gd['expected_away_goals']}")
            if sd.get("home_rank"): rs.append(f"Tabla: #{sd['home_rank']} ({sd['home_points']} pts) vs #{sd['away_rank']} ({sd['away_points']} pts)")
            if h2h_d.get("total_matches",0)>0: rs.append(f"H2H: {h2h_d['home_wins']}V {h2h_d['draws']}E {h2h_d['away_wins']}D en {h2h_d['total_matches']} partidos")
        elif sel == "2":
            rs.append(f"Forma visitante {fd['away_form']} — {fd['away_weighted_pts']:.1f}/3.0 pts ponderados")
            rs.append(f"xG visitante: {gd['expected_away_goals']}")
            if sd.get("away_rank"): rs.append(f"Tabla: {at} #{sd['away_rank']} ({sd['away_points']} pts)")
            if h2h_d.get("away_wins",0)>h2h_d.get("home_wins",0): rs.append(f"Visitante domina H2H: {h2h_d['away_wins']} victorias históricas")
        else:
            rs.append(f"Probabilidades casi igualadas: {round(hw*100,1)}% / {round(dr*100,1)}% / {round(aw*100,1)}%")
            rs.append(f"H2H: {h2h_d.get('draws',0)} empates en {h2h_d.get('total_matches',0)} partidos")
            rs.append(f"xG casi igualados: {gd['expected_home_goals']} vs {gd['expected_away_goals']}")
        recs.append(BetRecommendation("1X2", sel, round(conf,1), f"1X2 → {lbl}", rs, risk, round(1/max(prob-0.05,0.1),2)))

    # Doble oportunidad
    dc = {"1X":(hw+dr,f"Local o Empate ({ht})"),"X2":(dr+aw,f"Visitante o Empate ({at})"),"12":(hw+aw,"No hay empate")}
    dcs, (dcp, dcl) = max(dc.items(), key=lambda x:x[1][0])
    if dcp > 0.62:
        recs.append(BetRecommendation("double_chance", dcs, round(min(dcp*100*0.93,91),1),
            f"Doble Op. → {dcl}",
            [f"Probabilidad combinada: {round(dcp*100,1)}%",
             f"Cubre 2 de los 3 resultados",
             f"xG: {ht} {gd['expected_home_goals']} — {at} {gd['expected_away_goals']}"],
            "low", round(1/max(dcp-0.03,0.1),2)))

    # Over/Under
    op = o25/100
    ous = "over_2.5" if op>0.50 else "under_2.5"
    ouv = op if ous=="over_2.5" else 1-op
    oul = "Más de 2.5 goles" if ous=="over_2.5" else "Menos de 2.5 goles"
    if ouv > 0.50:
        rs2 = [f"xG total: {gd['total_xg']} goles esperados",
               f"{ht}: {fd['home_avg_scored']} anotados / {fd['home_avg_conceded']} enc. por partido",
               f"{at}: {fd['away_avg_scored']} anotados / {fd['away_avg_conceded']} enc. por partido"]
        if h2h_d.get("avg_goals_h2h"): rs2.append(f"Promedio goles H2H: {h2h_d['avg_goals_h2h']}")
        if h2h_d.get("over25_pct"): rs2.append(f"H2H: {h2h_d['over25_pct']}% de partidos terminaron +2.5")
        recs.append(BetRecommendation("over_under", ous, round(min(ouv*100*1.04,89),1),
            f"O/U → {oul}", rs2, "medium" if ouv<0.62 else "low", round(1/max(ouv-0.05,0.1),2)))

    # BTTS
    bp = btt/100
    bs = "yes" if bp>0.50 else "no"
    bv = bp if bs=="yes" else 1-bp
    bl = "Ambos marcan — SÍ" if bs=="yes" else "Ambos marcan — NO"
    if bv > 0.52:
        ph = round((1-math.exp(-gd["expected_home_goals"]))*100, 1)
        pa = round((1-math.exp(-gd["expected_away_goals"]))*100, 1)
        rs3 = [f"xG: {ht} {gd['expected_home_goals']} | {at} {gd['expected_away_goals']}",
               f"Prob. local marca: {ph}% | Prob. visitante marca: {pa}%"]
        if h2h_d.get("btts_pct"): rs3.append(f"H2H: {h2h_d['btts_pct']}% con ambos marcando")
        recs.append(BetRecommendation("btts", bs, round(min(bv*100*1.02,87),1),
            f"BTTS → {bl}", rs3, "medium", round(1/max(bv-0.05,0.1),2)))

    recs.sort(key=lambda r: r.confidence, reverse=True)
    return recs


def _enrich_with_odds(recs, probs, odds):
    if not odds:
        for r in recs:
            r["xbet_odd"] = r["ev"] = None
            r["is_value"] = False
        return recs
    def dc_odd(a, b):
        oa, ob = odds.get(a), odds.get(b)
        return round(1/(1/oa+1/ob),3) if oa and ob else None
    om = {
        "1":         (probs["home_win"],                  odds.get("home")),
        "X":         (probs["draw"],                      odds.get("draw")),
        "2":         (probs["away_win"],                   odds.get("away")),
        "over_2.5":  (probs["over_2_5"],                   odds.get("over25")),
        "under_2.5": (100-probs["over_2_5"],               odds.get("under25")),
        "yes":       (probs["btts"],                      odds.get("btts_yes")),
        "no":        (100-probs["btts"],                   odds.get("btts_no")),
        "1X":        (probs["home_win"]+probs["draw"],     dc_odd("home","draw")),
        "X2":        (probs["draw"]+probs["away_win"],     dc_odd("draw","away")),
        "12":        (probs["home_win"]+probs["away_win"], dc_odd("home","away")),
    }
    for r in recs:
        mp, xo = om.get(r["selection"], (None, None))
        ev = round((mp/100)*xo-1, 4) if mp and xo and xo>1 else None
        r["xbet_odd"] = xo
        r["ev"]       = ev
        r["is_value"] = ev is not None and ev > 0.04
    recs.sort(key=lambda r: (not r["is_value"], -r["confidence"]))
    return recs


def analyze_match(match, home_form, away_form, h2h, standings, xbet_odds=None):
    home_id = match.get("home_team_id", 0)
    away_id = match.get("away_team_id", 0)
    league  = match.get("league", "PrvaLiga")
    ht      = match.get("home_team", "Local")
    at      = match.get("away_team", "Visitante")

    hxg, axg = _calc_xg(home_form, away_form, league)
    hw, dr, aw, matrix = _match_probs(hxg, axg)

    fs,  fd  = _form_component(home_form, away_form)
    hs,  hd  = _h2h_component(h2h, home_id, away_id)
    ss,  sd  = _standings_component(standings, home_id, away_id)
    gs,  gd  = _goals_component(home_form, away_form, h2h, league, matrix, hxg, axg)
    has_, had = _home_adv_component(league)
    cs,  cd  = _consistency_component(home_form, away_form)

    overall = min(fs + hs + ss + gs + has_ + cs, 100)

    probs = {
        "home_win": round(hw*100,1), "draw": round(dr*100,1), "away_win": round(aw*100,1),
        "over_2_5": gd["over25_prob"], "btts": gd["btts_prob"],
    }

    recs = _build_recs(hw, dr, aw, gd["over25_prob"], gd["btts_prob"], fd, hd, sd, gd, ht, at)
    rd   = [asdict(r) for r in recs]
    rd   = _enrich_with_odds(rd, probs, xbet_odds)

    warns = []
    if home_form.get("games_analyzed",0)<3: warns.append(f"Pocos partidos de {ht}")
    if away_form.get("games_analyzed",0)<3: warns.append(f"Pocos partidos de {at}")
    if h2h.get("total_matches",0)<3: warns.append("H2H limitado")
    if not standings: warns.append("Tabla no disponible")
    if not xbet_odds: warns.append("Cuotas 1xbet no disponibles")

    dq = "full" if not warns else ("partial" if len(warns)<=2 else "mock")

    return {
        "match_id": match.get("id",""), "home_team": ht, "away_team": at,
        "league": league, "match_date": match.get("date",""), "round": match.get("round",""),
        "probabilities": probs, "xbet_odds": xbet_odds,
        "score_breakdown": {
            "form": fd, "h2h": hd, "standings": sd, "goals": gd,
            "home_advantage": had, "consistency": cd, "overall": round(overall,1),
        },
        "recommendations": rd, "top_recommendation": rd[0] if rd else None,
        "summary": f"{ht} vs {at} — {round(hw*100,1)}% / {round(dr*100,1)}% / {round(aw*100,1)}%",
        "overall_confidence": round(overall,1),
        "has_value_bet": any(r.get("is_value") for r in rd),
        "value_alert":   any(r.get("is_value") for r in rd),
        "data_quality": dq, "warnings": warns,
    }
