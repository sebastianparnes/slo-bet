"""
Odds Scraper — OddsPortal (fuente) + muestra cuotas de 1xbet
=============================================================
OddsPortal tiene cuotas de 1xbet para PrvaLiga y no bloquea servidores.
Scrapea el JSON interno de OddsPortal que incluye todos los bookmakers.

Sin datos: retorna None, la app funciona igual sin cuotas.
"""

import httpx
import re
import json
from datetime import datetime
from typing import Optional

_cache: dict = {}
_cache_expiry: dict = {}
CACHE_TTL = 600  # 10 minutos

LEAGUE_SLUGS = {
    "PrvaLiga": "football/slovenia/prva-liga",
    "2SNL":     "football/slovenia/2-snl",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.oddsportal.com/",
    "x-requested-with": "XMLHttpRequest",
}


def _cache_get(key: str):
    if key in _cache and datetime.now() < _cache_expiry.get(key, datetime.min):
        return _cache[key]
    _cache.pop(key, None); _cache_expiry.pop(key, None)
    return None

def _cache_set(key: str, value, ttl: int = CACHE_TTL):
    from datetime import timedelta
    _cache[key] = value
    _cache_expiry[key] = datetime.now() + timedelta(seconds=ttl)

def _norm(name: str) -> str:
    name = (name or "").lower()
    for p in ["nk ", "fc ", "ns ", "nd ", "fk ", "sk ", "nk", "fc"]:
        name = name.replace(p, "")
    return re.sub(r"[^a-z0-9]", "", name).strip()

def _sim(a: str, b: str) -> float:
    if a == b: return 1.0
    if a in b or b in a: return 0.85
    def bg(s): return {s[i:i+2] for i in range(len(s)-1)}
    b1, b2 = bg(a), bg(b)
    if not b1 or not b2: return 0.0
    return 2 * len(b1 & b2) / (len(b1) + len(b2))


async def _fetch_oddsportal_matches(league: str) -> list[dict]:
    """Fetch match list with odds from OddsPortal JSON feed."""
    slug = LEAGUE_SLUGS.get(league, LEAGUE_SLUGS["PrvaLiga"])
    cache_key = f"op_{slug}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    # OddsPortal exposes a JSON endpoint for their sport pages
    url = f"https://www.oddsportal.com/ajax-sport-country-tournament-archive/{slug}/X0/1/0/0/"

    async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
        try:
            # First try the direct JSON feed
            resp = await client.get(url)
            if resp.status_code == 200:
                text = resp.text
                # OddsPortal wraps JSON in a callback sometimes
                match = re.search(r'\{.*\}', text, re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    matches = _parse_oddsportal(data, league)
                    if matches:
                        _cache_set(cache_key, matches)
                        return matches
        except Exception as e:
            print(f"[OddsPortal] Feed error: {e}")

        # Fallback: scrape the HTML page
        try:
            page_url = f"https://www.oddsportal.com/{slug}/"
            resp2 = await client.get(page_url, headers={**HEADERS, "Accept": "text/html"})
            if resp2.status_code == 200:
                matches = _parse_oddsportal_html(resp2.text, league)
                if matches:
                    _cache_set(cache_key, matches)
                    return matches
        except Exception as e:
            print(f"[OddsPortal] HTML error: {e}")

    _cache_set(cache_key, [], ttl=120)
    return []


def _parse_oddsportal(data: dict, league: str) -> list[dict]:
    """Parse OddsPortal JSON response."""
    matches = []
    rows = data.get("d", {}).get("rows", []) or data.get("rows", []) or []
    for row in rows:
        try:
            home = row.get("home-name", "") or row.get("home", "")
            away = row.get("away-name", "") or row.get("away", "")
            if not home or not away:
                continue
            odds_1xbet = _extract_1xbet_odds(row)
            matches.append({
                "home": home, "away": away,
                "league": league,
                "odds": odds_1xbet,
                "match_url": row.get("url", ""),
            })
        except:
            continue
    return matches


def _parse_oddsportal_html(html: str, league: str) -> list[dict]:
    """Extract match data from OddsPortal HTML via embedded JSON."""
    matches = []
    # OddsPortal embeds match data in a script tag
    patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.*?});',
        r'"eventList":\s*(\[.*?\])',
        r'PageEvent\s*=\s*({.*?});',
    ]
    for pattern in patterns:
        found = re.search(pattern, html, re.DOTALL)
        if found:
            try:
                data = json.loads(found.group(1))
                events = (data.get("sports", {}).get("events") or
                         data.get("events") or
                         data if isinstance(data, list) else [])
                for ev in events[:20]:
                    home = ev.get("homeTeam", ev.get("home", ""))
                    away = ev.get("awayTeam", ev.get("away", ""))
                    if home and away:
                        matches.append({"home": home, "away": away, "league": league, "odds": None})
            except:
                continue

    # Last resort: regex match names from HTML
    if not matches:
        team_matches = re.findall(
            r'<a[^>]+href="/[^"]+/([^/]+)-vs-([^/]+)/"',
            html
        )
        for h, a in team_matches[:15]:
            home = h.replace("-", " ").title()
            away = a.replace("-", " ").title()
            matches.append({"home": home, "away": away, "league": league, "odds": None})

    return matches


def _extract_1xbet_odds(row: dict) -> Optional[dict]:
    """Try to extract 1xbet specific odds from OddsPortal row."""
    # OddsPortal stores odds by bookmaker ID
    # 1xbet bookmaker ID in OddsPortal is typically "16" or "1xbet"
    odds_data = row.get("odds", {}) or {}
    for key in ["16", "1xbet", "1-xbet"]:
        if key in odds_data:
            o = odds_data[key]
            return {
                "home": o[0] if len(o) > 0 else None,
                "draw": o[1] if len(o) > 1 else None,
                "away": o[2] if len(o) > 2 else None,
            }
    return None


async def get_odds_for(home_team: str, away_team: str, league: str) -> Optional[dict]:
    """Main function: returns odds dict or None."""
    key = f"odds_{_norm(home_team)}_{_norm(away_team)}"
    cached = _cache_get(key)
    if cached is not None:
        return cached

    matches = await _fetch_oddsportal_matches(league)
    if not matches:
        _cache_set(key, None, ttl=120)
        return None

    nh, na = _norm(home_team), _norm(away_team)
    best, best_score = None, 0.0

    for m in matches:
        mh = _norm(m.get("home", ""))
        ma = _norm(m.get("away", ""))
        score = (_sim(nh, mh) + _sim(na, ma)) / 2
        if score > best_score:
            best_score = score
            best = m

    if not best or best_score < 0.45:
        print(f"[OddsPortal] No match for '{home_team} vs {away_team}' (best={best_score:.2f})")
        _cache_set(key, None, ttl=300)
        return None

    odds = best.get("odds") or {}
    result = {
        "home":    odds.get("home"),
        "draw":    odds.get("draw"),
        "away":    odds.get("away"),
        "over25":  None,
        "under25": None,
        "btts_yes": None,
        "btts_no":  None,
        "raw_url":  f"https://www.oddsportal.com{best.get('match_url','')}" if best.get('match_url') else None,
        "source":   "OddsPortal/1xbet",
        "match_confidence": round(best_score, 3),
    }

    _cache_set(key, result)
    return result


def calc_ev(model_prob_pct: float, xbet_odd: float) -> Optional[float]:
    if not model_prob_pct or not xbet_odd or xbet_odd <= 1:
        return None
    return round((model_prob_pct / 100) * xbet_odd - 1, 4)

def implied_prob(odd: float) -> Optional[float]:
    if not odd or odd <= 1:
        return None
    return round(100 / odd, 2)
