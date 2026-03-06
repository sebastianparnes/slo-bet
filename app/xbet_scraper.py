"""
1xbet Odds Scraper
==================
Estrategia: usa los endpoints JSON internos de 1xbet (no parsea HTML),
que son mucho más estables que el DOM.

Endpoints usados:
  /LineFeed/GetChampionship?championshipId=<ID>&lng=en&...
  /LineFeed/GetGameZip?id=<GAME_ID>&lng=en&...

IDs de ligas eslovenas en 1xbet:
  PrvaLiga → 118593
  2.SNL    → 270435

Cache: 8 minutos (1xbet actualiza cuotas cada ~5 min)

FALLBACK: Si 1xbet bloquea o no encuentra el partido,
retorna None — el análisis sigue funcionando sin comparación de valor.
"""

import httpx
import asyncio
import re
from datetime import datetime, timedelta
from typing import Optional

# Cache simple en memoria
_cache: dict = {}
_cache_expiry: dict = {}

CACHE_TTL = 480  # segundos

LEAGUE_IDS = {
    "PrvaLiga": 118593,
    "2SNL":     270435,
}

BASE = "https://1xbet.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://1xbet.com/en/line/football",
    "Origin": "https://1xbet.com",
}


def _cache_get(key: str):
    if key in _cache:
        if datetime.now() < _cache_expiry.get(key, datetime.min):
            return _cache[key]
        _cache.pop(key, None)
        _cache_expiry.pop(key, None)
    return None


def _cache_set(key: str, value, ttl: int = CACHE_TTL):
    _cache[key] = value
    _cache_expiry[key] = datetime.now() + timedelta(seconds=ttl)


def _norm(name: str) -> str:
    """NK Olimpija Ljubljana → olimpijalubliana"""
    name = (name or "").lower()
    name = re.sub(r"\b(nk|fc|sk|nd|sd)\b", "", name)
    name = re.sub(r"[^a-z0-9]", "", name)
    return name.strip()


def _similarity(a: str, b: str) -> float:
    """Bigram overlap similarity, 0..1"""
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    def bigrams(s):
        return {s[i:i+2] for i in range(len(s)-1)}
    bg1, bg2 = bigrams(a), bigrams(b)
    if not bg1 or not bg2:
        return 0.0
    inter = len(bg1 & bg2)
    return 2 * inter / (len(bg1) + len(bg2))


async def _fetch_league_games(league_id: int) -> list:
    key = f"xbet_league_{league_id}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    url = (
        f"{BASE}/LineFeed/GetChampionship"
        f"?championshipId={league_id}&lng=en"
        f"&isSubGames=true&GroupEvents=true&allEventsGrouped=true&mode=4"
    )
    try:
        async with httpx.AsyncClient(timeout=10, headers=HEADERS) as client:
            resp = await client.get(url)
            if resp.status_code != 200:
                _cache_set(key, [], ttl=120)
                return []
            data = resp.json()
            games = (
                data.get("Value", {}).get("TopEvents") or
                data.get("Value", {}).get("Events") or
                []
            )
            _cache_set(key, games)
            return games
    except Exception as e:
        print(f"[1xbet] League {league_id} fetch error: {e}")
        _cache_set(key, [], ttl=60)
        return []


def _parse_odds(game: dict) -> dict:
    """Extract 1X2, over/under 2.5 and BTTS odds from a 1xbet game object."""
    result = {
        "xbet_game_id": game.get("I"),
        "home": None, "draw": None, "away": None,
        "over25": None, "under25": None,
        "btts_yes": None, "btts_no": None,
        "raw_url": None,
    }

    game_id = game.get("I")
    league_id = game.get("LI")
    if game_id and league_id:
        result["raw_url"] = f"https://1xbet.com/en/line/football/{league_id}-{game_id}"

    groups = game.get("GE") or []
    for group in groups:
        gname = (group.get("GN") or "").lower()
        events = group.get("E") or []

        # 1X2 result
        if group.get("T") == 1 or "1x2" in gname or "match result" in gname:
            for ev in events:
                t = ev.get("T")
                c = ev.get("C")
                if t == 1 and not result["home"]:  result["home"] = c
                if t == 2 and not result["draw"]:  result["draw"] = c
                if t == 3 and not result["away"]:  result["away"] = c

        # Over/Under 2.5
        if "total" in gname and "1st" not in gname:
            for ev in events:
                n = (ev.get("N") or "").lower()
                c = ev.get("C")
                t = ev.get("T")
                if ("2.5" in n or t == 9)  and "over"  in n and not result["over25"]:  result["over25"]  = c
                if ("2.5" in n or t == 10) and "under" in n and not result["under25"]: result["under25"] = c

        # BTTS
        if "both" in gname or "score" in gname:
            for ev in events:
                n = (ev.get("N") or "").lower()
                c = ev.get("C")
                if "yes" in n and not result["btts_yes"]: result["btts_yes"] = c
                if "no"  in n and not result["btts_no"]:  result["btts_no"]  = c

    # Flat events fallback
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
    """
    Main function: given home/away teams and league name,
    returns scraped odds dict or None if not found.
    """
    key = f"odds_{_norm(home_team)}_{_norm(away_team)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached  # can be None (not found) or dict

    league_id = LEAGUE_IDS.get(league, LEAGUE_IDS["PrvaLiga"])
    games = await _fetch_league_games(league_id)

    if not games:
        _cache_set(key, None, ttl=120)
        return None

    nh = _norm(home_team)
    na = _norm(away_team)
    best_game = None
    best_score = 0.0

    for g in games:
        gh = _norm(g.get("O1") or g.get("HT") or "")
        ga = _norm(g.get("O2") or g.get("AT") or "")
        score = (_similarity(nh, gh) + _similarity(na, ga)) / 2
        if score > best_score:
            best_score = score
            best_game = g

    if not best_game or best_score < 0.55:
        print(f"[1xbet] No confident match for '{home_team} vs {away_team}' (best={best_score:.2f})")
        _cache_set(key, None, ttl=300)
        return None

    odds = _parse_odds(best_game)
    odds["match_confidence"] = round(best_score, 3)
    odds["xbet_home_name"] = best_game.get("O1") or best_game.get("HT")
    odds["xbet_away_name"] = best_game.get("O2") or best_game.get("AT")

    _cache_set(key, odds)
    return odds


def calc_ev(model_prob_pct: float, xbet_odd: float) -> Optional[float]:
    """
    Expected Value = (model_probability × odd) − 1
    Positive → we have an edge over the bookmaker.
    > 0.05 is considered a meaningful value bet.
    """
    if not model_prob_pct or not xbet_odd or xbet_odd <= 1:
        return None
    return round((model_prob_pct / 100) * xbet_odd - 1, 4)


def implied_prob(odd: float) -> Optional[float]:
    """Convert decimal odd to implied probability (0-100)."""
    if not odd or odd <= 1:
        return None
    return round(100 / odd, 2)
