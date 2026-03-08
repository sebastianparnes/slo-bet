"""
ar-xbet Scraper — vía Cloudflare Worker proxy
=============================================
Worker usa: /service-api/LineFeed/Get1x2_VZip?sports=1&champs=CHAMP_ID&...
IDs de campeonato son los de ar-xbet (distintos a 1xbet).
"""

import httpx
import os
import re
from datetime import datetime, timedelta
from typing import Optional

_cache: dict = {}
_cache_expiry: dict = {}
CACHE_TTL = 300  # 5 minutos

# ar-xbet championship IDs (del worker_2_.js que funcionaba)
LEAGUE_IDS = {
    "PrvaLiga":        "30049",
    "2SNL":            "196693",
    "PrimeraDivision": "119599",
    "PrimeraNacional": "2922491",
    "ChampionsLeague": "118587",
    "PremierLeague":   "88637",
    "LaLiga":          "127733",
    "SerieA":          "110163",
    "Bundesliga":      "96463",
    "Ligue1":          "12821",
    "CroatiaHNL":      "27735",
    "SerbiaSuper":     "30035",
    "UruguayPrimera":  "52183",
}

# Mapeo liga → param para el worker helper /xbet/odds?league=X
LEAGUE_SLUG = {
    "PrvaLiga":        "prva",
    "2SNL":            "2snl",
    "PrimeraDivision": "primera",
    "PrimeraNacional": "nacional",
}

def _get_proxy() -> str:
    return os.getenv("XBET_PROXY_URL", "").rstrip("/")

def _has_proxy() -> bool:
    return bool(_get_proxy())

def _cache_get(key: str):
    if key in _cache and datetime.now() < _cache_expiry.get(key, datetime.min):
        return _cache[key]
    _cache.pop(key, None); _cache_expiry.pop(key, None)
    return None

def _cache_set(key: str, value, ttl: int = CACHE_TTL):
    _cache[key] = value
    _cache_expiry[key] = datetime.now() + timedelta(seconds=ttl)

def _norm(name: str) -> str:
    name = (name or "").lower()
    for p in ["nk ", "fc ", "ns ", "nd ", "fk ", "sk ", "cd ", "ca ", "cf "]:
        name = name.replace(p, "")
    return re.sub(r"[^a-z0-9]", "", name).strip()

def _sim(a: str, b: str) -> float:
    if not a or not b: return 0.0
    if a == b: return 1.0
    if a in b or b in a: return 0.85
    def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
    b1, b2 = bg(a), bg(b)
    if not b1 or not b2: return 0.0
    return 2 * len(b1 & b2) / (len(b1) + len(b2))


async def _fetch_league_games(league: str) -> list:
    key = f"xbet_league_{league}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    proxy = _get_proxy()
    if not proxy:
        return []

    champ_id = LEAGUE_IDS.get(league)
    if not champ_id:
        print(f"[ar-xbet] No ID for league '{league}'")
        return []

    # Use worker's generic /xbet route with correct ar-xbet API
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            # Try /xbet/odds helper first if league has a slug
            slug = LEAGUE_SLUG.get(league)
            if slug:
                url = f"{proxy}/xbet/odds"
                resp = await client.get(url, params={"league": slug})
            else:
                # Generic /xbet route
                url = f"{proxy}/xbet"
                params = {
                    "path": "/service-api/LineFeed/Get1x2_VZip",
                    "sports": "1",
                    "champs": champ_id,
                    "count": "50",
                    "lng": "es",
                    "cfview": "2",
                    "mode": "4",
                    "country": "14",
                    "getEmpty": "true",
                    "virtualSports": "true",
                }
                resp = await client.get(url, params=params)

            print(f"[ar-xbet] {league} (champ={champ_id}) → HTTP {resp.status_code}")
            if resp.status_code != 200:
                print(f"[ar-xbet] Error body: {resp.text[:300]}")
                _cache_set(key, [], ttl=60)
                return []

            data = resp.json()
    except Exception as e:
        print(f"[ar-xbet] Exception fetching {league}: {e}")
        _cache_set(key, [], ttl=60)
        return []

    # Parse response — ar-xbet uses Get1x2_VZip format
    # Value contains list of events
    val = data.get("Value") or {}
    games = []
    if isinstance(val, list):
        games = val
    elif isinstance(val, dict):
        games = (
            val.get("TopEvents") or
            val.get("Events") or
            val.get("ChampEvents") or
            []
        )

    if not isinstance(games, list):
        games = []

    print(f"[ar-xbet] {league}: {len(games)} games found")
    if not games:
        print(f"[ar-xbet] Response sample: {str(data)[:400]}")

    _cache_set(key, games)
    return games


def _parse_odds(game: dict) -> dict:
    result = {
        "home": None, "draw": None, "away": None,
        "over25": None, "under25": None,
        "btts_yes": None, "btts_no": None,
        "raw_url": None,
    }

    game_id   = game.get("I") or game.get("Id")
    league_id = game.get("LI") or game.get("L")
    if game_id and league_id:
        result["raw_url"] = f"https://ar-xbet.com/es/line/football/{league_id}-{game_id}"

    # Parse from grouped events (GE)
    for group in (game.get("GE") or []):
        gname  = (group.get("GN") or "").lower()
        events = group.get("E") or []
        if group.get("T") == 1 or "1x2" in gname or "resultado" in gname or "match result" in gname:
            for ev in events:
                t, c = ev.get("T"), ev.get("C")
                if t == 1 and not result["home"]: result["home"] = c
                if t == 2 and not result["draw"]: result["draw"] = c
                if t == 3 and not result["away"]: result["away"] = c
        if "total" in gname and "1st" not in gname and "1er" not in gname:
            for ev in events:
                n, c = (ev.get("N") or "").lower(), ev.get("C")
                if "2.5" in n and "over"  in n and not result["over25"]:  result["over25"]  = c
                if "2.5" in n and "under" in n and not result["under25"]: result["under25"] = c
                if "2.5" in n and "más"   in n and not result["over25"]:  result["over25"]  = c
                if "2.5" in n and "menos" in n and not result["under25"]: result["under25"] = c
        if "both" in gname or "score" in gname or "ambos" in gname:
            for ev in events:
                n, c = (ev.get("N") or "").lower(), ev.get("C")
                if ("yes" in n or "sí" in n) and not result["btts_yes"]: result["btts_yes"] = c
                if "no"  in n and not result["btts_no"]:  result["btts_no"]  = c

    # Flat event fallback (E array directly on game)
    if not result["home"]:
        for ev in (game.get("E") or []):
            t, c = ev.get("T"), ev.get("C")
            if t == 1  and not result["home"]:    result["home"]    = c
            if t == 2  and not result["draw"]:    result["draw"]    = c
            if t == 3  and not result["away"]:    result["away"]    = c
            if t == 9  and not result["over25"]:  result["over25"]  = c
            if t == 10 and not result["under25"]: result["under25"] = c

    return result


async def get_odds_for(home_team: str, away_team: str, league: str) -> Optional[dict]:
    if not _has_proxy():
        print(f"[ar-xbet] No proxy configured — set XBET_PROXY_URL")
        return None

    key = f"odds_{_norm(home_team)}_{_norm(away_team)}_{league}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    games = await _fetch_league_games(league)
    if not games:
        _cache_set(key, None, ttl=120)
        return None

    nh, na = _norm(home_team), _norm(away_team)
    best, best_score = None, 0.0

    for g in games:
        gh = _norm(g.get("O1") or g.get("HT") or g.get("Team1") or "")
        ga = _norm(g.get("O2") or g.get("AT") or g.get("Team2") or "")
        score = (_sim(nh, gh) + _sim(na, ga)) / 2
        if score > best_score:
            best_score = score
            best = g

    if not best or best_score < 0.40:
        print(f"[ar-xbet] No match for '{home_team} vs {away_team}' in {league} (best={best_score:.2f})")
        _cache_set(key, None, ttl=300)
        return None

    odds = _parse_odds(best)
    odds["match_confidence"] = round(best_score, 3)
    odds["xbet_home_name"]   = best.get("O1") or best.get("HT") or best.get("Team1")
    odds["xbet_away_name"]   = best.get("O2") or best.get("AT") or best.get("Team2")
    print(f"[ar-xbet] ✓ Matched '{home_team}' → '{odds['xbet_home_name']}' (conf={best_score:.2f})")
    _cache_set(key, odds)
    return odds


def calc_ev(model_prob_pct: float, xbet_odd: float) -> Optional[float]:
    if not model_prob_pct or not xbet_odd or xbet_odd <= 1:
        return None
    return round((model_prob_pct / 100) * xbet_odd - 1, 4)

def implied_prob(odd: float) -> Optional[float]:
    if not odd or odd <= 1:
        return None
    return round(100 / odd, 2)
