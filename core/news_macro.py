"""
Macro-sanity news filter.

Purpose: NOT an alpha source. A *warn-only* contextual check that prints
the headlines driving a risk-on / risk-off / neutral verdict so you can
sanity-check the engine's trigger before manually entering. The engine
itself is never gated by this module — the verdict is informational.

Sources (free, stdlib-only):
    * RSS feeds (BBC world, Yahoo Finance markets, CNBC top, MarketWatch)
    * NewsAPI top-headlines (optional, when NEWSAPI_KEY env var is set)

Classifier: keyword bucket counter across a curated theme map. Transparent
on purpose — you see exactly which headlines triggered which theme, and
override manually if you disagree. v2 can swap in an LLM call without
changing the gate contract.

CLI:
    python -m core.news_macro                 # one-shot verdict + headlines
    python -m core.news_macro --side long     # also show trade-side warning

Public API:
    macro_verdict() -> MacroVerdict           # cached 30 min by default
    print_news_warning(side: int) -> None     # warn-only, called by live_signal
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Iterable

from config.settings import NEWS_FILTER

_CACHE: dict[str, tuple[float, "MacroVerdict"]] = {}


# ---------------------------------------------------------------------------
# Theme keyword map — risk-off themes are negative, risk-on themes positive.
# ---------------------------------------------------------------------------
THEME_KEYWORDS: dict[str, tuple[str, ...]] = {
    # Risk-off (each hit subtracts from the risk score)
    "war":         ("war", "invasion", "missile", "airstrike", "strike on",
                    "ceasefire", "escalation", "military", "troops",
                    "geopolit", "tensions", "sanctions"),
    "banking":     ("bank run", "bank fails", "bank collapse",
                    "banking crisis", "liquidity crunch", "credit crunch"),
    "recession":   ("recession", "layoffs", "jobless", "unemployment surges",
                    "contraction", "gdp shrinks", "downturn"),
    "rate_hike":   ("rate hike", "hawkish", "tightening", "cpi hot",
                    "inflation surges", "inflation jumps", "fed raises"),
    "crash":       ("crash", "selloff", "plunge", "tumble", "panic",
                    "rout", "wipe out", "freefall"),
    # Risk-on (each hit adds to the risk score)
    "rate_cut":    ("rate cut", "dovish", "easing", "fed cuts", "cpi cool",
                    "inflation cools", "rate cuts ahead"),
    "risk_on":     ("rally", "record high", "all-time high", "earnings beat",
                    "strong jobs", "soft landing", "optimism"),
}

RISK_OFF_THEMES = {"war", "banking", "recession", "rate_hike", "crash"}
RISK_ON_THEMES  = {"rate_cut", "risk_on"}


@dataclass
class MacroVerdict:
    verdict: str                              # "RISK_ON" | "RISK_OFF" | "NEUTRAL"
    risk_off_score: int
    risk_on_score: int
    theme_hits: dict[str, int]                # theme name → count of matching headlines
    sample_headlines: dict[str, list[str]]    # theme → up to 3 example headlines
    n_headlines: int
    sources_used: list[str]
    fetched_at: float = field(default_factory=time.time)


# ---------------------------------------------------------------------------
# Fetchers
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: int = 6) -> bytes | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 (quant_ia_news_filter)"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


def _fetch_rss(url: str, limit: int = 25) -> list[str]:
    raw = _http_get(url)
    if raw is None:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    titles: list[str] = []
    # RSS 2.0: channel/item/title ; Atom: entry/title (with ns)
    for it in root.iter():
        tag = it.tag.lower().split("}")[-1]
        if tag == "title" and it.text:
            t = it.text.strip()
            # Skip the channel's own title (first one); accept item-level titles.
            if t and t not in titles:
                titles.append(t)
        if len(titles) > limit + 1:
            break
    # Drop the first title (channel name) when present.
    return titles[1: limit + 1] if len(titles) > 1 else titles


def _fetch_newsapi(api_key: str, limit: int = 30) -> list[str]:
    """NewsAPI free tier: /v2/top-headlines (business, US)."""
    base = "https://newsapi.org/v2/top-headlines"
    qs = urllib.parse.urlencode({
        "country": "us", "category": "business",
        "pageSize": limit, "apiKey": api_key,
    })
    raw = _http_get(f"{base}?{qs}")
    if raw is None:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if data.get("status") != "ok":
        return []
    return [a.get("title", "") for a in data.get("articles", []) if a.get("title")]


# ---------------------------------------------------------------------------
# Classifier
# ---------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _kw_regex(kw: str) -> re.Pattern:
    """Word-boundary regex so 'war' does NOT match 'warns', 'warm', etc.
    Multi-word keywords (e.g. 'rate cut') match as a phrase, also bounded."""
    return re.compile(r"\b" + re.escape(kw) + r"\b", re.IGNORECASE)


def _classify(headlines: Iterable[str]) -> tuple[dict[str, int], dict[str, list[str]]]:
    hits: dict[str, int] = {k: 0 for k in THEME_KEYWORDS}
    samples: dict[str, list[str]] = {k: [] for k in THEME_KEYWORDS}
    for h in headlines:
        for theme, kws in THEME_KEYWORDS.items():
            for kw in kws:
                if _kw_regex(kw).search(h):
                    hits[theme] += 1
                    if len(samples[theme]) < 3:
                        samples[theme].append(h)
                    break
    return hits, samples


def _verdict_from_hits(hits: dict[str, int]) -> tuple[str, int, int]:
    off = sum(hits[t] for t in RISK_OFF_THEMES)
    on  = sum(hits[t] for t in RISK_ON_THEMES)
    margin = NEWS_FILTER.verdict_margin
    if off >= on + margin:
        v = "RISK_OFF"
    elif on >= off + margin:
        v = "RISK_ON"
    else:
        v = "NEUTRAL"
    return v, off, on


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def macro_verdict(force_refresh: bool = False) -> MacroVerdict:
    """Cached fetch + classify. Cache TTL from NEWS_FILTER.cache_seconds."""
    now = time.time()
    cached = _CACHE.get("verdict")
    if cached and not force_refresh and now - cached[0] < NEWS_FILTER.cache_seconds:
        return cached[1]

    headlines: list[str] = []
    sources_used: list[str] = []
    for src_name, url in NEWS_FILTER.rss_feeds.items():
        titles = _fetch_rss(url, NEWS_FILTER.per_feed_limit)
        if titles:
            headlines.extend(titles)
            sources_used.append(src_name)

    if NEWS_FILTER.use_newsapi:
        key = os.environ.get("NEWSAPI_KEY", "").strip()
        if key:
            t2 = _fetch_newsapi(key, NEWS_FILTER.newsapi_limit)
            if t2:
                headlines.extend(t2)
                sources_used.append("newsapi")

    hits, samples = _classify(headlines)
    v, off, on = _verdict_from_hits(hits)
    out = MacroVerdict(
        verdict=v, risk_off_score=off, risk_on_score=on,
        theme_hits=hits, sample_headlines=samples,
        n_headlines=len(headlines), sources_used=sources_used,
    )
    _CACHE["verdict"] = (now, out)
    return out


def print_news_warning(side: int) -> None:
    """Warn-only macro filter, called from live_signal before trigger output.

    side: +1 long, -1 short, 0 no signal. Prints a single block only when
    macro disagrees with the trade direction. Never blocks execution.
    """
    if side == 0:
        return
    v = macro_verdict()
    long_disagrees  = (side ==  1 and v.verdict == "RISK_OFF")
    short_disagrees = (side == -1 and v.verdict == "RISK_ON")
    if not (long_disagrees or short_disagrees):
        return
    print(f"\n   ⚠️  MACRO MISMATCH — engine says "
          f"{'LONG' if side == 1 else 'SHORT'} but macro reads "
          f"{v.verdict}  (off={v.risk_off_score}  on={v.risk_on_score}  "
          f"n_headlines={v.n_headlines})")
    print(f"   Sources: {', '.join(v.sources_used) or 'none'}")
    for theme, hits in sorted(v.theme_hits.items(), key=lambda x: -x[1]):
        if hits == 0:
            continue
        marker = "OFF" if theme in RISK_OFF_THEMES else "ON "
        print(f"     [{marker}] {theme:<10s} ×{hits}")
        for h in v.sample_headlines.get(theme, [])[:2]:
            print(f"          · {h[:100]}")
    print("   (warn only — engine has NOT blocked the trade; you decide manually)")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli() -> None:
    import argparse
    p = argparse.ArgumentParser(description="Macro-sanity news verdict.")
    p.add_argument("--side", type=int, default=0,
                   help="+1 long / -1 short (to also print warn-block)")
    p.add_argument("--refresh", action="store_true", help="bypass cache")
    args = p.parse_args()

    v = macro_verdict(force_refresh=args.refresh)
    print(f"\n=== MACRO VERDICT: {v.verdict} ===")
    print(f"  risk-off score = {v.risk_off_score}   risk-on score = {v.risk_on_score}")
    print(f"  {v.n_headlines} headlines from: {', '.join(v.sources_used) or 'none'}")
    print("  theme hits:")
    for theme, hits in sorted(v.theme_hits.items(), key=lambda x: -x[1]):
        if hits == 0:
            continue
        marker = "OFF" if theme in RISK_OFF_THEMES else "ON "
        print(f"    [{marker}] {theme:<10s} ×{hits}")
        for h in v.sample_headlines.get(theme, [])[:3]:
            print(f"        · {h[:110]}")
    print_news_warning(args.side)


if __name__ == "__main__":
    _cli()
