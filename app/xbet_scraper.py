"""
1xbet Odds Scraper — via Cloudflare Worker proxy
=================================================
El Worker corre en IPs de Cloudflare que 1xbet no bloquea.
Configura: XBET_PROXY_URL=https://slo-bet-proxy.sebastianparnes26.workers.dev
"""

import httpx
import os
import re
from datetime import datetime, timedelta
from typing import Optional

_cache: dict = {}
_cache_expiry: dict = {}
CACHE_TTL = 300  # 5 minutos — cuotas cambian rápido

LEAGUE_IDS = {
    "PrvaLiga":        118593,
    "2SNL":            270435,
    "PrimeraDivision": 119599,
    "PrimeraNacional": 2922491,
    "ChampionsLeague": 118587,
    "PremierLeague":   88637,
    "LaLiga":          127733,
    "SerieA":          110163,
    "Bundesliga":      96463,
    "Ligue1":          12821,
    "CroatiaHNL":      27735,
    "SerbiaSuper":     30035,
    "UruguayPrimera":  52183,
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
    for p in ["nk ", "fc ", "ns ", "nd ", "fk ", "sk "]:
        name = name.replace(p, "")
    return re.sub(r"[^a-z0-9]", "", name).strip()

def _sim(a: str, b: str) -> float:
    if a == b: return 1.0
    if a in b or b in a: return 0.85
    def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
    b1, b2 = bg(a), bg(b)
    if not b1 or not b2: return 0.0
    return 2 * len(b1 & b2) / (len(b1) + len(b2))


async def _fetch_via_proxy(path: str, params: str) -> dict | None:
    proxy = _get_proxy()
    if not proxy:
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(proxy, params={"path": path, "params": params})
            if resp.status_code != 200:
                print(f"[1xbet-proxy] HTTP {resp.status_code}: {resp.text[:200]}")
                return None
            return resp.json()
    except Exception as e:
        print(f"[1xbet-proxy] Error: {e}")
        return None


async def _fetch_league_games(league_id: int) -> list:
    key = f"xbet_league_{league_id}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    path   = "/LineFeed/GetChampionship"
    params = f"championshipId={league_id}&lng=en&isSubGames=true&GroupEvents=true&allEventsGrouped=true&mode=4"

    data = await _fetch_via_proxy(path, params)
    if not data:
        _cache_set(key, [], ttl=60)
        return []

    games = (
        data.get("Value", {}).get("TopEvents") or
        data.get("Value", {}).get("Events") or
        data.get("Value") or
        []
    )
    if not isinstance(games, list):
        games = []

    print(f"[1xbet] League {league_id}: {len(games)} games found")
    _cache_set(key, games)
    return games


def _parse_odds(game: dict) -> dict:
    result = {
        "home": None, "draw": None, "away": None,
        "over25": None, "under25": None,
        "btts_yes": None, "btts_no": None,
        "raw_url": None,
    }
    game_id   = game.get("I")
    league_id = game.get("LI")
    if game_id and league_id:
        result["raw_url"] = f"https://1xbet.com/en/line/football/{league_id}-{game_id}"

    for group in (game.get("GE") or []):
        gname  = (group.get("GN") or "").lower()
        events = group.get("E") or []
        if group.get("T") == 1 or "1x2" in gname or "match result" in gname:
            for ev in events:
                t, c = ev.get("T"), ev.get("C")
                if t == 1 and not result["home"]:  result["home"] = c
                if t == 2 and not result["draw"]:  result["draw"] = c
                if t == 3 and not result["away"]:  result["away"] = c
        if "total" in gname and "1st" not in gname:
            for ev in events:
                n, c = (ev.get("N") or "").lower(), ev.get("C")
                if "2.5" in n and "over"  in n and not result["over25"]:  result["over25"]  = c
                if "2.5" in n and "under" in n and not result["under25"]: result["under25"] = c
        if "both" in gname or "score" in gname:
            for ev in events:
                n, c = (ev.get("N") or "").lower(), ev.get("C")
                if "yes" in n and not result["btts_yes"]: result["btts_yes"] = c
                if "no"  in n and not result["btts_no"]:  result["btts_no"]  = c

    # Flat fallback
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
        print(f"[1xbet] No proxy configured — set XBET_PROXY_URL")
        return None

    key = f"odds_{_norm(home_team)}_{_norm(away_team)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    league_id = LEAGUE_IDS.get(league)
    if not league_id:
        print(f"[1xbet] No ID for league '{league}'")
        return None
    games = await _fetch_league_games(league_id)

    if not games:
        _cache_set(key, None, ttl=120)
        return None

    nh, na = _norm(home_team), _norm(away_team)
    best, best_score = None, 0.0

    for g in games:
        gh = _norm(g.get("O1") or g.get("HT") or "")
        ga = _norm(g.get("O2") or g.get("AT") or "")
        score = (_sim(nh, gh) + _sim(na, ga)) / 2
        if score > best_score:
            best_score = score
            best = g

    if not best or best_score < 0.50:
        print(f"[1xbet] No match for '{home_team} vs {away_team}' (best={best_score:.2f})")
        _cache_set(key, None, ttl=300)
        return None

    odds = _parse_odds(best)
    odds["match_confidence"] = round(best_score, 3)
    odds["xbet_home_name"]   = best.get("O1") or best.get("HT")
    odds["xbet_away_name"]   = best.get("O2") or best.get("AT")
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

