"""
Symbol Edge Explorer — for ANY symbol, find the best edges available
and recommend which engine to use to capture them.

The portfolio_scanner.py tests if THE pullback engine works on a symbol.
This script asks the inverse: "what edge does THIS symbol have, regardless
of which engine captures it?"

For each symbol, it runs the full 46-edge library across 4 horizons,
ranks edges by t-stat, identifies the dominant category, and emits an
engine-build recommendation.

Usage:
    python3 -m research.symbol_edge_explorer AAPL
    python3 -m research.symbol_edge_explorer AAPL TSLA NVDA MSFT --json
    python3 -m research.symbol_edge_explorer --watchlist
        # uses the 60-symbol portfolio_scanner universe

Output (per symbol):
    - Best edge: name, category, t-stat, hit rate, mean bps, direction
    - Top 5 edges
    - Category density: which engine families have signal
    - Engine recommendation
    - Edge density score (count of |t|>3 edges)
"""
from __future__ import annotations
import argparse, json, sys, warnings, logging
sys.stdout.reconfigure(line_buffering=True)
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

import numpy as np
import pandas as pd

from core.data_loader import load_symbol
from research.edge_lab import mine_edge
from research.edge_library import EDGES
from research.proxies import (cvd_proxy, tick_imbalance, close_position,
                              bar_range_z, momentum_acceleration,
                              realized_vol, rsi, bollinger_z)

HORIZONS = (5, 20, 100)   # skip 390 (drift artifact per Part 8.18 lesson)

CATEGORY_TO_ENGINE = {
    "MOMENTUM":    "pullback engine (already shipped)",
    "MEAN_REV":    "mean-reversion engine (NEW — needs build)",
    "VOL_REGIME":  "volatility-breakout engine (NEW — needs build)",
    "ORDERFLOW":   "orderflow-exhaustion engine (NEW — needs build)",
    "GAMMA_PROXY": "vol-compression-expansion engine (NEW — needs build)",
    "TIME_OF_DAY": "calendar-effect overlay (NEW — applies on top of base)",
    "STRUCTURE":   "structure-based engine (inside bars, engulfings)",
    "VOLUME":      "volume-anomaly engine (NEW — needs build)",
    "STACK":       "stacked-conditions engine (multi-feature)",
}


def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    """Attach all features needed by edge_library conditions."""
    out = df.copy()
    out["__CVD"]      = cvd_proxy(out)
    out["__TickImb"]  = tick_imbalance(out)
    out["__ClosePos"] = close_position(out)
    out["__RangeZ"]   = bar_range_z(out)
    out["__MomAccel"] = momentum_acceleration(out)
    out["__RVol"]     = realized_vol(out)
    out["__RSI"]      = rsi(out)
    out["__BBZ"]      = bollinger_z(out)
    out["__Hour"]     = out.index.hour
    out["__Dow"]      = out.index.dayofweek
    out["__Ret1"]     = out["Close"].pct_change(1)
    out["__Ret5"]     = out["Close"].pct_change(5)
    out["__Ret20"]    = out["Close"].pct_change(20)
    out["__VolMA"]    = out["Volume"].rolling(20).mean()
    out["__VolZ"]     = ((out["Volume"] - out["__VolMA"]) /
                          out["Volume"].rolling(20).std().replace(0, np.nan)).fillna(0)
    out["__EMA20"]    = out["Close"].ewm(span=20, adjust=False).mean()
    out["__SMA50"]    = out["Close"].rolling(50).mean()
    out["__SMA200"]   = out["Close"].rolling(200).mean()
    return out


def explore_symbol(symbol: str) -> dict:
    """Run every edge × every horizon on this symbol, return ranked fingerprint."""
    try:
        df = _enrich(load_symbol(symbol))
    except Exception as e:
        return {"symbol": symbol, "error": f"{type(e).__name__}: {e}"}

    all_results = []
    for edge in EDGES:
        results = mine_edge(edge, df, symbol, HORIZONS)
        for r in results:
            if r.n_signals >= 100 and r.p_value < 0.001:
                all_results.append(r)

    if not all_results:
        return {"symbol": symbol, "error": "no significant edges found"}

    # rank by |t-stat|
    all_results.sort(key=lambda r: abs(r.t_stat), reverse=True)
    best = all_results[0]

    # category density
    cat_counts = {}
    cat_max_t = {}
    for r in all_results:
        if abs(r.t_stat) > 3:
            cat_counts[r.category] = cat_counts.get(r.category, 0) + 1
            cat_max_t[r.category] = max(cat_max_t.get(r.category, 0), abs(r.t_stat))

    # top 5 distinct edges by t-stat
    seen_edges = set()
    top5 = []
    for r in all_results:
        if r.edge_name not in seen_edges:
            top5.append({
                "edge":       r.edge_name,
                "category":   r.category,
                "horizon":    r.horizon_bars,
                "direction":  r.direction,
                "t_stat":     round(r.t_stat, 2),
                "hit_rate":   round(r.hit_rate, 3),
                "mean_bps":   round(r.mean_return_bps, 1),
                "n":          r.n_signals,
            })
            seen_edges.add(r.edge_name)
        if len(top5) >= 5: break

    return {
        "symbol":           symbol,
        "n_strong_edges":   sum(1 for r in all_results if abs(r.t_stat) > 3),
        "n_significant":    len(all_results),
        "best_edge":        best.edge_name,
        "best_category":    best.category,
        "best_t_stat":      round(best.t_stat, 2),
        "best_hit_rate":    round(best.hit_rate, 3),
        "best_mean_bps":    round(best.mean_return_bps, 1),
        "best_horizon":     best.horizon_bars,
        "best_direction":   best.direction,
        "dominant_engine":  CATEGORY_TO_ENGINE.get(best.category, "?"),
        "category_density": cat_counts,
        "category_max_t":   {k: round(v, 2) for k, v in cat_max_t.items()},
        "top_5_edges":      top5,
        "bars_loaded":      len(df),
    }


def pretty_print(snap: dict) -> str:
    if "error" in snap:
        return f"[{snap['symbol']}] {snap['error']}"
    lines = []
    lines.append(f"╭─ {snap['symbol']}  ·  {snap['bars_loaded']} bars  ·  "
                 f"{snap['n_strong_edges']} strong edges (|t|>3)")
    lines.append(f"│")
    lines.append(f"│  BEST: {snap['best_edge']}  ({snap['best_category']})")
    lines.append(f"│    t = {snap['best_t_stat']:+.2f}  ·  "
                 f"hit = {snap['best_hit_rate']*100:.1f}%  ·  "
                 f"mean = {snap['best_mean_bps']:+.1f} bp  ·  "
                 f"h = {snap['best_horizon']} bars  ·  dir = {snap['best_direction']}")
    lines.append(f"│")
    lines.append(f"│  ENGINE TO BUILD: {snap['dominant_engine']}")
    lines.append(f"│")
    lines.append(f"│  Category density (n strong edges per family):")
    for cat, n in sorted(snap['category_density'].items(),
                         key=lambda kv: kv[1], reverse=True):
        max_t = snap['category_max_t'].get(cat, 0)
        lines.append(f"│    {cat:<14} {n:>3} edges (max |t|={max_t:.2f})")
    lines.append(f"│")
    lines.append(f"│  Top 5 distinct edges:")
    for i, e in enumerate(snap['top_5_edges'], 1):
        lines.append(f"│    {i}. {e['edge']:<32} {e['category']:<12} "
                     f"h={e['horizon']:<4} t={e['t_stat']:+.2f}  "
                     f"hit={e['hit_rate']*100:.1f}%  +{e['mean_bps']:.1f}bp")
    lines.append(f"╰─")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Find edges on any symbol regardless of engine")
    p.add_argument("symbols", nargs="*", help="yfinance tickers to explore")
    p.add_argument("--watchlist", action="store_true",
                   help="explore the 60-symbol portfolio_scanner universe")
    p.add_argument("--json", action="store_true", help="output JSON only")
    args = p.parse_args()

    if args.watchlist:
        from research.portfolio_scanner import UNIVERSE
        symbols = UNIVERSE
    else:
        symbols = args.symbols or ["AAPL"]

    results = []
    for s in symbols:
        snap = explore_symbol(s)
        results.append(snap)
        if not args.json:
            print(pretty_print(snap))
            print()

    if args.json:
        print(json.dumps(results, indent=2, default=str))

    # summary leaderboard if multiple symbols
    if len(symbols) > 1 and not args.json:
        print("\n  SUMMARY LEADERBOARD")
        print("  " + "-"*88)
        valid = [r for r in results if "error" not in r]
        valid.sort(key=lambda r: r["best_t_stat"], reverse=True)
        for r in valid:
            print(f"  {r['symbol']:<10} best={r['best_edge']:<32} "
                  f"cat={r['best_category']:<12} t={r['best_t_stat']:+.2f}  "
                  f"density={r['n_strong_edges']:>3}")


if __name__ == "__main__":
    main()
