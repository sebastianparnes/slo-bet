"""
Football data — Argentina (Primera División & Primera Nacional)
===============================================================
Flashscore para fixtures en vivo. Mock con equipos reales como fallback.
"""

import httpx
import re
from datetime import datetime, date, timedelta

FS_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.flashscore.com/",
    "x-fsign": "SW9D1eZo",
}

LEAGUES = {
    "PrimeraDivision": {"fs_sport": "football", "fs_country": "argentina", "fs_league": "liga-profesional"},
    "PrimeraNacional": {"fs_sport": "football", "fs_country": "argentina", "fs_league": "primera-nacional"},
}

TEAM_IDS = {
    # Primera División
    "River Plate": 3196, "Boca Juniors": 3197, "Racing Club": 3198,
    "Independiente": 3199, "San Lorenzo": 3200, "Huracán": 3201,
    "Vélez Sársfield": 3202, "Estudiantes LP": 3203, "Lanús": 3204,
    "Talleres": 3205, "Belgrano": 3206, "Godoy Cruz": 3207,
    "Defensa y Justicia": 3208, "Arsenal Sarandí": 3209, "Banfield": 3210,
    "Platense": 3211, "San Martín SJ": 3212, "Tigre": 3213,
    "Atlético Tucumán": 3214, "Newells": 3215, "Rosario Central": 3216,
    "Unión Santa Fe": 3217, "Colón": 3218, "Olimpo": 3219,
    "Central Córdoba": 3220, "Gimnasia LP": 3221, "Barracas Central": 3222,
    "Instituto": 3223, "Riestra": 3224, "Sarmiento": 3225,
    # Primera Nacional
    "San Martín Tucumán": 4001, "Atlético Rafaela": 4002, "Quilmes": 4003,
    "Brown PM": 4004, "Almirante Brown": 4005, "Deportivo Morón": 4006,
    "Los Andes": 4007, "Almagro": 4008, "Chacarita": 4009,
    "Agropecuario": 4010, "Temperley": 4011, "Sacachispas": 4012,
    "Deportivo Riestra": 4013, "Deportivo Maipú": 4014, "Mitre": 4015,
    "Flandria": 4016, "Ferro": 4017, "Gimnasia Mendoza": 4018,
}
ID_TO_NAME = {v: k for k, v in TEAM_IDS.items()}

def _team_id(name: str) -> int:
    if name in TEAM_IDS: return TEAM_IDS[name]
    for k, v in TEAM_IDS.items():
        if _same_team(k, name): return v
    return abs(hash(name)) % 90000 + 10000

def _team_name_from_id(tid: int) -> str:
    return ID_TO_NAME.get(tid, "")

def _same_team(a: str, b: str) -> bool:
    def n(s): return re.sub(r"[^a-záéíóúüñ]", "", s.lower())
    na, nb = n(a), n(b)
    return na == nb or (len(na) > 4 and na in nb) or (len(nb) > 4 and nb in na)

def _norm_name(name: str) -> str:
    mapping = {
        "river": "River Plate", "boca": "Boca Juniors", "racing": "Racing Club",
        "independiente": "Independiente", "sanlorenzo": "San Lorenzo",
        "huracan": "Huracán", "velez": "Vélez Sársfield",
        "estudiantes": "Estudiantes LP", "lanus": "Lanús",
        "talleres": "Talleres", "belgrano": "Belgrano",
        "godoy": "Godoy Cruz", "defensa": "Defensa y Justicia",
        "banfield": "Banfield", "platense": "Platense",
        "tigre": "Tigre", "atleticotucuman": "Atlético Tucumán",
        "newells": "Newells", "rosariocentral": "Rosario Central",
        "union": "Unión Santa Fe", "colon": "Colón",
        "centrallcordoba": "Central Córdoba", "gimnasia": "Gimnasia LP",
        "barracas": "Barracas Central", "instituto": "Instituto",
        "riestra": "Riestra", "sarmiento": "Sarmiento",
    }
    key = re.sub(r"[^a-záéíóúüñ]", "", name.lower().strip())
    for k, v in mapping.items():
        if k in key or key in k:
            return v
    return name.strip()


async def _fetch_flashscore_fixtures(league_name: str) -> list[dict]:
    cfg = LEAGUES.get(league_name, {})
    if not cfg:
        return []
    today = date.today()
    cutoff = today + timedelta(days=14)
    async with httpx.AsyncClient(timeout=15, headers=FS_HEADERS) as client:
        try:
            url = f"https://d.flashscore.com/x/feed/f_1_{cfg['fs_country']}_{cfg['fs_league']}_"
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            return _parse_flashscore_feed(resp.text, league_name, today, cutoff)
        except Exception as e:
            print(f"[Flashscore ARG] Error {league_name}: {e}")
            return []


def _parse_flashscore_feed(text: str, league_name: str, today: date, cutoff: date) -> list[dict]:
    matches = []
    league_id = 385 if league_name == "PrimeraDivision" else 386
    for seg in text.split("~"):
        try:
            parts = dict(p.split("¬") for p in seg.split("÷") if "¬" in p)
            if not parts:
                continue
            match_id = parts.get("AA", "")
            home = parts.get("CX", parts.get("AE", ""))
            away = parts.get("AF", parts.get("CY", ""))
            ts = parts.get("AD", parts.get("U_", ""))
            if not home or not away or not ts:
                continue
            try:
                dt = datetime.fromtimestamp(int(ts))
            except:
                continue
            if not (today <= dt.date() <= cutoff):
                continue
            home_n = _norm_name(home)
            away_n = _norm_name(away)
            matches.append({
                "id": match_id or f"arg_{hash(home+away+str(ts))}",
                "date": dt.strftime("%Y-%m-%dT%H:%M:%S-03:00"),
                "status": "NS",
                "league": league_name,
                "league_id": league_id,
                "round": parts.get("AL", ""),
                "home_team": home_n,
                "home_team_id": _team_id(home_n),
                "away_team": away_n,
                "away_team_id": _team_id(away_n),
                "venue": "",
            })
        except:
            continue
    return matches


def _parse_flashscore_results(text: str, league_name: str) -> list[dict]:
    results = []
    for seg in text.split("~"):
        try:
            parts = dict(p.split("¬") for p in seg.split("÷") if "¬" in p)
            home = parts.get("CX", parts.get("AE", ""))
            away = parts.get("AF", parts.get("CY", ""))
            hg = parts.get("AG", "")
            ag = parts.get("AH", "")
            dt = parts.get("AD", "")
            if not home or not away or hg == "" or ag == "":
                continue
            match_date = datetime.fromtimestamp(int(dt)).strftime("%Y-%m-%d") if dt else ""
            results.append({
                "id": parts.get("AA", ""),
                "date": match_date,
                "home_team": _norm_name(home),
                "away_team": _norm_name(away),
                "home_goals": int(hg),
                "away_goals": int(ag),
                "status": "FT",
            })
        except:
            continue
    return results


async def fetch_past_results(league_id: int = 385, last_n: int = 20) -> list[dict]:
    league_name = "PrimeraDivision" if league_id == 385 else "PrimeraNacional"
    cfg = LEAGUES.get(league_name, {})
    async with httpx.AsyncClient(timeout=15, headers=FS_HEADERS) as client:
        try:
            url = f"https://d.flashscore.com/x/feed/f_2_{cfg['fs_country']}_{cfg['fs_league']}_"
            resp = await client.get(url)
            if resp.status_code != 200:
                return []
            return _parse_flashscore_results(resp.text, league_name)[-last_n:]
        except Exception as e:
            print(f"[Flashscore ARG] past results error: {e}")
            return []


async def fetch_upcoming_matches(days_ahead: int = 14) -> list[dict]:
    all_matches = []
    for league_name in LEAGUES:
        fixtures = await _fetch_flashscore_fixtures(league_name)
        if fixtures:
            all_matches.extend(fixtures)
            print(f"[Flashscore ARG] {league_name}: {len(fixtures)} fixtures")
        else:
            print(f"[Flashscore ARG] {league_name}: no data, using mock")
            all_matches.extend(_mock_matches(league_name))
    if not all_matches:
        return _mock_matches()
    return sorted(all_matches, key=lambda x: x["date"])


async def fetch_team_form(team_id: int, league_id: int, last_n: int = 7) -> dict:
    past = await fetch_past_results(league_id, last_n=40)
    team_name = _team_name_from_id(team_id)
    results, scored, conceded = [], [], []
    for m in past:
        is_home = _same_team(m["home_team"], team_name)
        is_away = _same_team(m["away_team"], team_name)
        if not is_home and not is_away:
            continue
        tg = m["home_goals"] if is_home else m["away_goals"]
        og = m["away_goals"] if is_home else m["home_goals"]
        scored.append(tg); conceded.append(og)
        results.append("W" if tg > og else ("D" if tg == og else "L"))
        if len(results) >= last_n:
            break
    if not results:
        return _mock_form(team_id)
    n = len(results)
    return {
        "form": results, "form_string": "".join(results[-5:]),
        "avg_scored": round(sum(scored)/n, 2), "avg_conceded": round(sum(conceded)/n, 2),
        "clean_sheets": sum(1 for g in conceded if g == 0),
        "btts_count": sum(1 for s, c in zip(scored, conceded) if s > 0 and c > 0),
        "games_analyzed": n,
    }


async def fetch_h2h(home_id: int, away_id: int) -> dict:
    home_name = _team_name_from_id(home_id)
    away_name = _team_name_from_id(away_id)
    past = await fetch_past_results(385, last_n=60)
    hw = aw = draws = btts = 0
    goals = []
    for m in past:
        ih = _same_team(m["home_team"], home_name) and _same_team(m["away_team"], away_name)
        ia = _same_team(m["home_team"], away_name) and _same_team(m["away_team"], home_name)
        if not ih and not ia: continue
        hg = m["home_goals"]; ag = m["away_goals"]
        goals.append(hg + ag)
        if hg > ag: hw += (1 if ih else 0); aw += (1 if ia else 0)
        elif ag > hg: aw += (1 if ih else 0); hw += (1 if ia else 0)
        else: draws += 1
        if hg > 0 and ag > 0: btts += 1
    if not goals:
        return {"total_matches": 8, "home_wins": 4, "draws": 2, "away_wins": 2, "avg_goals_h2h": 2.6, "btts_pct": 62.5, "over25_pct": 55.0}
    n = len(goals)
    return {
        "total_matches": n, "home_wins": hw, "draws": draws, "away_wins": aw,
        "avg_goals_h2h": round(sum(goals)/n, 2),
        "btts_pct": round(btts/n*100, 1), "over25_pct": round(sum(1 for g in goals if g > 2.5)/n*100, 1),
    }


async def fetch_standings(league_id: int) -> list[dict]:
    if league_id == 386:
        return _mock_standings_nacional()
    return _mock_standings_primera()


# ── Mock data ──────────────────────────────────────────────────────────────

_TEAM_FORM_DATA = {
    3196: {"form": ["W","W","W","D","W","W","D"], "sc": [3,2,2,1,3,2,1], "cc": [0,1,0,1,0,1,1]},  # River
    3197: {"form": ["W","D","W","W","L","W","W"], "sc": [2,1,2,2,0,2,1], "cc": [0,1,0,1,2,0,0]},  # Boca
    3198: {"form": ["W","W","D","W","W","L","W"], "sc": [2,2,1,1,2,0,2], "cc": [0,0,1,0,1,2,0]},  # Racing
    3199: {"form": ["L","D","W","L","D","W","L"], "sc": [0,1,2,0,1,1,0], "cc": [2,1,0,2,1,0,2]},  # Independiente
    3200: {"form": ["D","W","W","L","W","D","W"], "sc": [1,2,1,0,2,1,2], "cc": [1,0,0,2,1,1,0]},  # San Lorenzo
    3201: {"form": ["W","L","D","W","W","L","D"], "sc": [2,0,1,2,1,0,1], "cc": [0,2,1,0,1,2,1]},  # Huracán
    3202: {"form": ["W","W","D","W","L","W","W"], "sc": [2,2,1,2,0,2,1], "cc": [0,1,1,0,2,1,0]},  # Vélez
    3203: {"form": ["D","W","W","D","W","W","L"], "sc": [1,2,2,1,2,1,0], "cc": [1,0,0,1,0,0,2]},  # Estudiantes
    3204: {"form": ["W","D","L","W","D","W","W"], "sc": [1,1,0,2,1,2,2], "cc": [0,1,2,0,1,0,1]},  # Lanús
    3205: {"form": ["W","W","W","D","W","L","W"], "sc": [2,3,2,1,2,0,1], "cc": [0,0,1,1,0,2,0]},  # Talleres
    3206: {"form": ["D","W","L","W","D","L","W"], "sc": [1,2,0,1,1,0,2], "cc": [1,0,2,0,1,2,0]},  # Belgrano
    3207: {"form": ["W","L","D","W","W","D","L"], "sc": [2,0,1,1,2,0,1], "cc": [0,2,1,0,1,1,2]},  # Godoy Cruz
    3208: {"form": ["W","W","D","L","W","W","D"], "sc": [2,1,1,0,2,1,1], "cc": [0,0,1,2,0,1,1]},  # Defensa
    3213: {"form": ["L","W","D","L","W","D","W"], "sc": [0,2,1,0,1,1,2], "cc": [2,0,1,2,0,1,0]},  # Tigre
    3214: {"form": ["W","D","W","W","D","L","W"], "sc": [1,1,2,1,1,0,2], "cc": [0,1,0,0,1,2,0]},  # Atl. Tucumán
    3215: {"form": ["D","L","W","D","L","W","D"], "sc": [1,0,2,1,0,1,1], "cc": [1,2,0,1,2,0,1]},  # Newells
    3216: {"form": ["W","W","L","D","W","W","L"], "sc": [2,1,0,1,2,1,0], "cc": [0,1,2,1,0,0,2]},  # Rosario Central
    3221: {"form": ["L","D","W","L","D","W","L"], "sc": [0,1,2,0,1,1,0], "cc": [2,1,0,2,1,0,2]},  # Gimnasia
    3222: {"form": ["W","L","W","D","W","L","W"], "sc": [1,0,2,1,1,0,2], "cc": [0,2,0,1,0,2,0]},  # Barracas
    3223: {"form": ["D","W","D","W","L","W","D"], "sc": [1,2,1,1,0,2,1], "cc": [1,0,1,0,2,1,1]},  # Instituto
    3224: {"form": ["W","D","L","W","W","D","W"], "sc": [2,1,0,1,2,1,2], "cc": [0,1,2,0,1,1,0]},  # Riestra
    3225: {"form": ["L","W","D","L","W","L","D"], "sc": [0,1,1,0,2,0,1], "cc": [2,0,1,2,0,2,1]},  # Sarmiento
}

def _mock_form(team_id: int = 0) -> dict:
    data = _TEAM_FORM_DATA.get(team_id)
    if data:
        r, sc, cc = data["form"], data["sc"], data["cc"]
    else:
        seed = team_id % 7
        pools = [["W","W","W","D","W","L","W"],["W","D","W","W","L","W","D"],["D","W","L","W","W","D","W"],["L","W","D","L","W","W","W"],["W","L","W","D","L","W","D"],["D","D","W","L","D","W","L"],["L","W","L","W","D","L","W"]]
        sc = [2,1,3,1,2,0,2][seed:]+[2,1,3,1,2,0,2][:seed]
        cc = [0,1,1,2,1,2,1][seed:]+[0,1,1,2,1,2,1][:seed]
        r = pools[seed]
    n = len(r)
    return {"form": r, "form_string": "".join(r[-5:]), "avg_scored": round(sum(sc)/n, 2), "avg_conceded": round(sum(cc)/n, 2), "clean_sheets": sum(1 for g in cc if g == 0), "btts_count": sum(1 for s, c in zip(sc, cc) if s > 0 and c > 0), "games_analyzed": n}


def _mock_matches(league_name: str = None) -> list[dict]:
    from datetime import date as _date
    today = _date.today()
    # Generate next 10 days of fixtures
    fixtures = []
    primera_matchups = [
        ("River Plate", 3196, "Boca Juniors", 3197),
        ("Racing Club", 3198, "Independiente", 3199),
        ("San Lorenzo", 3200, "Huracán", 3201),
        ("Vélez Sársfield", 3202, "Talleres", 3205),
        ("Estudiantes LP", 3203, "Belgrano", 3206),
        ("Lanús", 3204, "Godoy Cruz", 3207),
        ("Defensa y Justicia", 3208, "Tigre", 3213),
        ("Atlético Tucumán", 3214, "Newells", 3215),
        ("Rosario Central", 3216, "Gimnasia LP", 3221),
        ("Instituto", 3223, "Barracas Central", 3222),
    ]
    nacional_matchups = [
        ("San Martín Tucumán", 4001, "Quilmes", 4003),
        ("Almirante Brown", 4005, "Deportivo Morón", 4006),
        ("Los Andes", 4007, "Almagro", 4008),
        ("Chacarita", 4009, "Agropecuario", 4010),
        ("Temperley", 4011, "Ferro", 4017),
        ("Deportivo Maipú", 4014, "Mitre", 4015),
    ]
    for i, (h, hid, a, aid) in enumerate(primera_matchups):
        d = today + timedelta(days=i % 5 + 1)
        fixtures.append({"id": f"arg_p_{i}", "date": f"{d.isoformat()}T20:00:00-03:00", "status": "NS", "league": "PrimeraDivision", "league_id": 385, "round": "Fecha 12", "home_team": h, "home_team_id": hid, "away_team": a, "away_team_id": aid, "venue": ""})
    for i, (h, hid, a, aid) in enumerate(nacional_matchups):
        d = today + timedelta(days=i % 5 + 1)
        fixtures.append({"id": f"arg_n_{i}", "date": f"{d.isoformat()}T18:00:00-03:00", "status": "NS", "league": "PrimeraNacional", "league_id": 386, "round": "Fecha 12", "home_team": h, "home_team_id": hid, "away_team": a, "away_team_id": aid, "venue": ""})
    if league_name:
        return [f for f in fixtures if f["league"] == league_name]
    return fixtures


def _mock_standings_primera() -> list[dict]:
    teams = [
        ("River Plate", 3196, 55), ("Racing Club", 3198, 48), ("Boca Juniors", 3197, 45),
        ("Vélez Sársfield", 3202, 42), ("Talleres", 3205, 40), ("Estudiantes LP", 3203, 37),
        ("San Lorenzo", 3200, 35), ("Defensa y Justicia", 3208, 33), ("Lanús", 3204, 30),
        ("Godoy Cruz", 3207, 28), ("Atlético Tucumán", 3214, 26), ("Rosario Central", 3216, 25),
        ("Belgrano", 3206, 23), ("Instituto", 3223, 21), ("Huracán", 3201, 20),
        ("Barracas Central", 3222, 18), ("Independiente", 3199, 17), ("Newells", 3215, 15),
        ("Gimnasia LP", 3221, 14), ("Tigre", 3213, 12), ("Riestra", 3224, 11), ("Sarmiento", 3225, 9),
    ]
    return [{"rank": i+1, "team_id": t[1], "team_name": t[0], "points": t[2], "played": 22, "won": t[2]//3, "drawn": t[2]%3, "lost": 22-t[2]//3-t[2]%3, "goals_for": 40-i*1, "goals_against": 12+i*1, "goal_diff": 28-i*2, "form": "WWDWW" if i < 4 else ("WDLWL" if i < 12 else "LDLLL")} for i, t in enumerate(teams)]


def _mock_standings_nacional() -> list[dict]:
    teams = [
        ("San Martín Tucumán", 4001, 50), ("Almirante Brown", 4005, 44), ("Chacarita", 4009, 40),
        ("Deportivo Morón", 4006, 37), ("Quilmes", 4003, 34), ("Los Andes", 4007, 31),
        ("Almagro", 4008, 28), ("Temperley", 4011, 26), ("Mitre", 4015, 23),
        ("Agropecuario", 4010, 21), ("Ferro", 4017, 19), ("Deportivo Maipú", 4014, 17),
    ]
    return [{"rank": i+1, "team_id": t[1], "team_name": t[0], "points": t[2], "played": 22, "won": t[2]//3, "drawn": t[2]%3, "lost": 22-t[2]//3-t[2]%3, "goals_for": 35-i*2, "goals_against": 12+i*2, "goal_diff": 23-i*4, "form": "WWDWW" if i < 3 else "WDLLL"} for i, t in enumerate(teams)]
