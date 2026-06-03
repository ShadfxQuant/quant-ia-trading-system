"""
Edge-mining harness.

Defines:
  - EdgeDef: a hypothesis (name + condition fn + category)
  - mine_edge(): runs the hypothesis on a DataFrame across multiple
    forward horizons, computes statistics in BOTH directions (long and
    inverted). A high negative-expectancy edge is equally valuable —
    you flip the entry condition.
  - run_lab(): loops every edge × every symbol × every horizon,
    writes results to research/results/edges_<timestamp>.csv

Statistics computed per (edge, direction, horizon):
  - n_signals
  - hit_rate (fraction of signals where forward_return > 0)
  - mean_return (per-signal mean, in bps)
  - std_return
  - sharpe = mean / std * sqrt(annualization)
  - t_stat = mean / (std / sqrt(n))
  - p_value (two-sided, normal approximation)
  - expectancy = mean * n_signals (total $ edge over the sample)
  - edge_score = abs(t_stat) — used for ranking

The harness automatically picks the "best" direction per edge:
  - if mean_return > 0 → keep "long" direction
  - if mean_return < 0 → flip to "short" direction, statistics negated
  - direction reported in the output

Designed for HIGH-VOLUME mining: 30+ edges × 4 symbols × 4 horizons =
480 cells per run. Each cell aggregates 100s-1000s of signals.
"""
from __future__ import annotations
import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import numpy as np
import pandas as pd

RESULTS_DIR = os.path.join("research", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)


@dataclass
class EdgeDef:
    """A testable market-edge hypothesis."""
    name: str
    category: str
    description: str
    condition_fn: Callable[[pd.DataFrame], pd.Series]   # returns bool mask


@dataclass
class EdgeResult:
    symbol: str
    edge_name: str
    category: str
    horizon_bars: int
    direction: str           # "long" or "short" (auto-flipped to max edge)
    n_signals: int
    hit_rate: float
    mean_return_bps: float
    std_return_bps: float
    sharpe: float
    t_stat: float
    p_value: float
    expectancy_bps: float
    edge_score: float        # abs(t_stat); used for ranking
    bars_evaluated: int
    sample_pct: float        # n_signals / bars_evaluated * 100


def _forward_return(df: pd.DataFrame, h: int) -> pd.Series:
    """Forward return over h bars, in bps."""
    fwd = df["Close"].shift(-h) / df["Close"] - 1.0
    return fwd * 10_000


def _two_sided_p(t: float, n: int) -> float:
    """Two-sided p-value from t-statistic via normal approximation."""
    from math import erf, sqrt
    if not np.isfinite(t):
        return 1.0
    z = abs(t)
    p = 1.0 - (1.0 + erf(z / sqrt(2.0))) / 2.0
    return 2.0 * p


def mine_edge(edge: EdgeDef, df: pd.DataFrame, symbol: str,
              horizons=(5, 20, 100)) -> list[EdgeResult]:
    """Run one EdgeDef against one symbol's DataFrame; returns list of
    EdgeResult, one per horizon (with best direction auto-picked)."""
    try:
        mask = edge.condition_fn(df)
    except Exception as e:
        # broken condition — return empty list, don't crash the whole lab
        print(f"  [skip] {edge.name} on {symbol}: {type(e).__name__}: {e}")
        return []

    if mask is None or not isinstance(mask, pd.Series):
        return []
    mask = mask.fillna(False).astype(bool)
    if mask.sum() < 10:
        return []   # too few signals to be meaningful

    results = []
    bars_eval = len(df)
    for h in horizons:
        fwd_bps = _forward_return(df, h)
        rets = fwd_bps[mask].dropna()
        n = len(rets)
        if n < 10:
            continue
        mean = float(rets.mean())
        std = float(rets.std(ddof=1))
        if std == 0 or np.isnan(std):
            continue
        sharpe = mean / std * np.sqrt(252 * 6.5 / h)  # rough annualization
        t = mean / (std / np.sqrt(n))
        p = _two_sided_p(t, n)
        hit = float((rets > 0).mean())

        # auto-flip to best direction
        direction = "long" if mean >= 0 else "short"
        if direction == "short":
            mean, sharpe, t, hit = -mean, -sharpe, -t, 1.0 - hit
        expectancy = mean * n

        results.append(EdgeResult(
            symbol=symbol,
            edge_name=edge.name,
            category=edge.category,
            horizon_bars=h,
            direction=direction,
            n_signals=n,
            hit_rate=hit,
            mean_return_bps=mean,
            std_return_bps=std,
            sharpe=sharpe,
            t_stat=t,
            p_value=p,
            expectancy_bps=expectancy,
            edge_score=abs(t),
            bars_evaluated=bars_eval,
            sample_pct=n / bars_eval * 100,
        ))
    return results


def _result_to_dict(r: EdgeResult) -> dict:
    return {
        "symbol": r.symbol,
        "edge_name": r.edge_name,
        "category": r.category,
        "horizon_bars": r.horizon_bars,
        "direction": r.direction,
        "n_signals": r.n_signals,
        "hit_rate": round(r.hit_rate, 4),
        "mean_return_bps": round(r.mean_return_bps, 2),
        "std_return_bps": round(r.std_return_bps, 2),
        "sharpe": round(r.sharpe, 3),
        "t_stat": round(r.t_stat, 3),
        "p_value": round(r.p_value, 5),
        "expectancy_bps": round(r.expectancy_bps, 1),
        "edge_score": round(r.edge_score, 3),
        "sample_pct": round(r.sample_pct, 2),
    }


def run_lab(symbols: list[str], edges: list[EdgeDef],
            horizons=(5, 20, 100, 390)) -> pd.DataFrame:
    """Run the full lab and write results to JSON + CSV."""
    from core.data_loader import load_symbol
    from research.proxies import (cvd_proxy, tick_imbalance, close_position,
                                  bar_range_z, momentum_acceleration,
                                  realized_vol, rsi, bollinger_z)

    print(f"\n  EDGE LAB run · {len(edges)} edges × {len(symbols)} symbols × "
          f"{len(horizons)} horizons = {len(edges)*len(symbols)*len(horizons)} cells\n")

    all_rows = []
    for sym in symbols:
        print(f"  ── {sym} ──")
        try:
            df = load_symbol(sym)
        except Exception as e:
            print(f"    data load failed: {e}")
            continue
        # Attach proxies once per symbol so each edge can read them
        df = df.copy()
        df["__CVD"] = cvd_proxy(df)
        df["__TickImb"] = tick_imbalance(df)
        df["__ClosePos"] = close_position(df)
        df["__RangeZ"] = bar_range_z(df)
        df["__MomAccel"] = momentum_acceleration(df)
        df["__RVol"] = realized_vol(df)
        df["__RSI"] = rsi(df)
        df["__BBZ"] = bollinger_z(df)
        df["__Hour"] = df.index.hour
        df["__Dow"] = df.index.dayofweek
        df["__Ret1"] = df["Close"].pct_change(1)
        df["__Ret5"] = df["Close"].pct_change(5)
        df["__Ret20"] = df["Close"].pct_change(20)
        df["__VolMA"] = df["Volume"].rolling(20).mean()
        df["__VolZ"] = ((df["Volume"] - df["__VolMA"]) /
                       df["Volume"].rolling(20).std().replace(0, np.nan)).fillna(0)
        df["__EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
        df["__SMA50"] = df["Close"].rolling(50).mean()
        df["__SMA200"] = df["Close"].rolling(200).mean()

        for edge in edges:
            try:
                results = mine_edge(edge, df, sym, horizons)
                for r in results:
                    all_rows.append(_result_to_dict(r))
            except Exception as e:
                print(f"    [skip] {edge.name}: {type(e).__name__}: {e}")

    if not all_rows:
        print("  no results")
        return pd.DataFrame()

    df_out = pd.DataFrame(all_rows)
    df_out = df_out.sort_values("edge_score", ascending=False).reset_index(drop=True)

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    csv_path = os.path.join(RESULTS_DIR, f"edges_{ts}.csv")
    json_path = os.path.join(RESULTS_DIR, f"edges_{ts}.json")
    df_out.to_csv(csv_path, index=False)
    # latest pointer for the dashboard
    df_out.to_csv(os.path.join(RESULTS_DIR, "edges_latest.csv"), index=False)
    with open(json_path, "w") as f:
        json.dump(all_rows, f, indent=2, allow_nan=False, default=str)
    print(f"\n  wrote {len(df_out)} rows → {csv_path}")
    print(f"  latest pointer → research/results/edges_latest.csv")
    return df_out
