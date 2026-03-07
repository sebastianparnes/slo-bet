"""
Odds Scraper — ar-xbet.com via Cloudflare Worker
=================================================
Consume el worker de Cloudflare que hace de proxy a ar-xbet.com.
Extrae cuotas 1X2 para PrvaLiga y 2SNL.

Variable de entorno requerida:
  XBET_WORKER_URL  →  https://TU_WORKER.workers.dev
"""

import httpx
import re
import json
import os
from datetime import datetime, timedelta
from typing import Optional

# ─── Config ──────────────────────────────────────────────────────────────────

WORKER_URL = os.getenv("XBET_WORKER_URL", "").rstrip("/")

LEAGUE_PARAMS = {
    "PrvaLiga": "prva",
    "2SNL":     "2snl",
}

CACHE_TTL = 120  # segundos
_cache: dict = {}
_cache_expiry: dict = {}

HEADERS = {
    "User-Agent": "slovenian-football-api/1.0",
    "Accept": "application/json",
}

# ─── Cache ────────────────────────────────────────────────────────────────────

def _cache_get(key: str):
    if key in _cache and datetime.now() < _cache_expiry.get(key, datetime.min):
        return _cache[key]
    _cache.pop(key, None)
    _cache_expiry.pop(key, None)
    return None

def _cache_set(key: str, value, ttl: int = CACHE_TTL):
    _cache[key] = value
    _cache_expiry[key] = datetime.now() + timedelta(seconds=ttl)

# ─── Normalización de nombres ─────────────────────────────────────────────────

def _norm(name: str) -> str:
    name = (name or "").lower()
    for prefix in ["nk ", "fc ", "ns ", "nd ", "fk ", "sk ", "nk", "fc"]:
        name = name.replace(prefix, "")
    return re.sub(r"[^a-z0-9]", "", name).strip()

def _similarity(a: str, b: str) -> float:
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.85
    def bigrams(s): return {s[i:i+2] for i in range(len(s) - 1)}
    b1, b2 = bigrams(a), bigrams(b)
    if not b1 or not b2:
        return 0.0
    return 2 * len(b1 & b2) / (len(b1) + len(b2))

# ─── Fetch desde el worker ────────────────────────────────────────────────────

async def _fetch_xbet_matches(league: str) -> list[dict]:
    """Llama al worker de Cloudflare y parsea los partidos con cuotas."""

    cache_key = f"xbet_{league}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    if not WORKER_URL:
        print("[xbet] XBET_WORKER_URL no configurado")
        return []

    league_param = LEAGUE_PARAMS.get(league, "prva")
    url = f"{WORKER_URL}/xbet/odds?league={league_param}"

    try:
        async with httpx.AsyncClient(timeout=15, headers=HEADERS) as client:
            resp = await client.get(url)

        if resp.status_code != 200:
            print(f"[xbet] Worker respondió {resp.status_code}: {resp.text[:200]}")
            _cache_set(cache_key, [], ttl=30)
            return []

        data = resp.json()

    except httpx.TimeoutException:
        print(f"[xbet] Timeout conectando al worker ({url})")
        _cache_set(cache_key, [], ttl=30)
        return []
    except Exception as e:
        print(f"[xbet] Error fetch: {e}")
        _cache_set(cache_key, [], ttl=30)
        return []

    matches = _parse_xbet_response(data, league)
    print(f"[xbet] {league}: {len(matches)} partidos encontrados")
    _cache_set(cache_key, matches, ttl=CACHE_TTL)
    return matches


def _parse_xbet_response(data: dict, league: str) -> list[dict]:
    """
    Parsea la respuesta de /service-api/LineFeed/Get1x2_VZip
    
    Estructura típica:
    {
      "Value": [
        {
          "Id": 123456,
          "O1": "NK Maribor",      ← equipo local
          "O2": "NK Olimpija",     ← equipo visitante
          "E": [                   ← eventos/mercados
            {"T": 1, "C": 2.10},  ← T=1 local, T=2 draw, T=3 away
            {"T": 2, "C": 3.40},
            {"T": 3, "C": 3.20},
          ],
          "S": 0,  ← 0=previa, 1=live
          "D": 1748000000  ← timestamp
        }
      ]
    }
    """
    matches = []
    events = data.get("Value", []) or []

    for ev in events:
        home = ev.get("O1", "").strip()
        away = ev.get("O2", "").strip()

        if not home or not away:
            continue

        odds = _extract_1x2(ev.get("E", []))

        matches.append({
            "id":      ev.get("Id"),
            "home":    home,
            "away":    away,
            "league":  league,
            "odds":    odds,
            "is_live": ev.get("S") == 1,
            "date":    ev.get("D"),
        })

    return matches


def _extract_1x2(events: list) -> dict:
    """Extrae cuotas 1X2 del array de eventos de xbet."""
    result = {"home": None, "draw": None, "away": None}

    for e in (events or []):
        t = e.get("T")
        c = e.get("C")
        if not c or c <= 1:
            continue
        if t == 1:
            result["home"] = round(float(c), 2)
        elif t == 2:
            result["draw"] = round(float(c), 2)
        elif t == 3:
            result["away"] = round(float(c), 2)

    return result


# ─── API pública ──────────────────────────────────────────────────────────────

async def get_odds_for(home_team: str, away_team: str, league: str) -> Optional[dict]:
    """
    Devuelve cuotas de xbet para un partido dado.
    Retorna None si no encuentra el partido o hay error.
    """
    cache_key = f"odds_{_norm(home_team)}_{_norm(away_team)}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    matches = await _fetch_xbet_matches(league)
    if not matches:
        _cache_set(cache_key, None, ttl=60)
        return None

    nh = _norm(home_team)
    na = _norm(away_team)
    best = None
    best_score = 0.0

    for m in matches:
        mh = _norm(m.get("home", ""))
        ma = _norm(m.get("away", ""))
        score = (_similarity(nh, mh) + _similarity(na, ma)) / 2
        if score > best_score:
            best_score = score
            best = m

    if not best or best_score < 0.45:
        print(f"[xbet] Sin match para '{home_team} vs {away_team}' (mejor score={best_score:.2f})")
        _cache_set(cache_key, None, ttl=180)
        return None

    odds = best.get("odds", {})
    result = {
        "home":             odds.get("home"),
        "draw":             odds.get("draw"),
        "away":             odds.get("away"),
        "over25":           None,
        "under25":          None,
        "btts_yes":         None,
        "btts_no":          None,
        "source":           "ar-xbet.com",
        "match_confidence": round(best_score, 3),
        "xbet_match":       f"{best['home']} vs {best['away']}",
        "is_live":          best.get("is_live", False),
    }

    _cache_set(cache_key, result, ttl=CACHE_TTL)
    return result


async def get_all_odds(league: str) -> list[dict]:
    """Devuelve todos los partidos con cuotas de una liga. Útil para debug."""
    return await _fetch_xbet_matches(league)


# ─── Cálculos de valor ────────────────────────────────────────────────────────

def calc_ev(model_prob_pct: float, xbet_odd: float) -> Optional[float]:
    """Expected Value: positivo = hay valor, negativo = no hay valor."""
    if not model_prob_pct or not xbet_odd or xbet_odd <= 1:
        return None
    return round((model_prob_pct / 100) * xbet_odd - 1, 4)

def implied_prob(odd: float) -> Optional[float]:
    """Probabilidad implícita de una cuota."""
    if not odd or odd <= 1:
        return None
    return round(100 / odd, 2)

def value_rating(model_prob: float, xbet_odd: float) -> Optional[str]:
    """Clasifica el valor de una apuesta."""
    ev = calc_ev(model_prob, xbet_odd)
    if ev is None:
        return None
    if ev >= 0.15:
        return "FUERTE"
    if ev >= 0.07:
        return "MODERADO"
    if ev >= 0.02:
        return "LEVE"
    return "SIN_VALOR"
