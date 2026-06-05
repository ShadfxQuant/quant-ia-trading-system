"""
TradingView-style chart inspector — on-demand edge analysis for any symbol.

Usage from CLI:
    python3 -m research.inspect_symbol AAPL
    python3 -m research.inspect_symbol "ES=F" --bars 200
    python3 -m research.inspect_symbol "BTC-USD" --json

Usage in-conversation (agent reads the output):
    just ask "inspect AAPL" / "look at ES=F" and the tool runs.

Returns a TradingView-style snapshot for the requested symbol:
  - current bar context (close, regime, RSI, range z, close pos, RVol pct)
  - which of the 9 shipped edges are firing RIGHT NOW
  - last 20 bars history of edge fires
  - distance to key levels (SMA50, SMA200, recent HH/LL)
  - cross-class context (how this symbol compares to its asset class)
"""
from __future__ import annotations
import argparse
import json
import sys
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from core.data_loader import load_symbol
from research.proxies import (
    cvd_proxy, tick_imbalance, close_position, bar_range_z,
    realized_vol, rsi, bollinger_z,
)


# ────────── edge engine (mirrors the Pine v2 conditions) ──────────
def _enrich(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["RSI"] = rsi(df, 14)
    df["SMA50"]  = df["Close"].rolling(50).mean()
    df["SMA200"] = df["Close"].rolling(200).mean()
    df["RangeZ"] = bar_range_z(df, 50)
    df["CVD"] = cvd_proxy(df)
    df["CVD_slope"] = df["CVD"] - df["CVD"].shift(20)
    df["TickImb"] = tick_imbalance(df, 20)
    df["ClosePos"] = close_position(df)
    df["RVol"] = realized_vol(df, 20)
    # percentile bands (rolling 200)
    df["CVD_low"]  = df["CVD_slope"].rolling(200).quantile(0.20)
    df["CVD_high"] = df["CVD_slope"].rolling(200).quantile(0.80)
    df["TI_low"]   = df["TickImb"].rolling(200).quantile(0.20)
    df["TI_high"]  = df["TickImb"].rolling(200).quantile(0.80)
    df["RVol_low"] = df["RVol"].rolling(500).quantile(0.10)
    df["VolMA20"]  = df["Volume"].rolling(20).mean()
    df["Hour"] = df.index.hour
    return df


def _edges_on_bar(row, prev_row) -> dict:
    """Return which of the 9 edges fire on this bar."""
    golden = row["SMA50"] > row["SMA200"]
    death  = row["SMA50"] < row["SMA200"]
    inside = (row["High"] < prev_row["High"]) and (row["Low"] > prev_row["Low"])
    in_power_hour = row["Hour"] in (19, 20)

    return {
        "E1_tick_exhaustion":   bool(row["TickImb"] < row["TI_low"]),
        "E2_cvd_falling":       bool(row["CVD_slope"] < row["CVD_low"]),
        "E3_vol_compression":   bool(row["RVol"] < row["RVol_low"]),
        "E4_rsi80_continuation": bool(row["RSI"] > 80),
        "E5_buy_dip_stack":     bool(row["RSI"] < 30 and golden),
        "E6_sell_rip_stack":    bool(row["RSI"] > 70 and death),
        "E7_wide_close_high":   bool(row["RangeZ"] > 1.5 and row["ClosePos"] > 0.80),
        "E8_inside_bar":        bool(inside),
        "E9_power_hour_volume": bool(in_power_hour and row["Volume"] > 1.5 * row["VolMA20"]),
    }


EDGE_LABELS = {
    "E1_tick_exhaustion":    ("Tick Imbalance Exhaustion", "BULLISH", "▲"),
    "E2_cvd_falling":        ("CVD Falling Strong",         "BULLISH", "▲"),
    "E3_vol_compression":    ("Vol Compression",            "NEUTRAL", "◆"),
    "E4_rsi80_continuation": ("RSI Extreme High",            "BULLISH", "▲"),
    "E5_buy_dip_stack":      ("Stack: Buy the Dip",          "BULLISH", "○"),
    "E6_sell_rip_stack":     ("Stack: Sell the Rip",         "BEARISH", "▼"),
    "E7_wide_close_high":    ("Wide-Bar Sweep",              "BULLISH", "◆"),
    "E8_inside_bar":         ("Inside Bar Compression",      "NEUTRAL", "×"),
    "E9_power_hour_volume":  ("Power Hour + Vol Spike",      "BULLISH", "⚑"),
}


def inspect(symbol: str, lookback: int = 100) -> dict:
    """Returns a TV-style snapshot dict."""
    df = _enrich(load_symbol(symbol))
    df = df.dropna(subset=["SMA200", "RVol_low", "CVD_low"])
    if len(df) < 20:
        return {"symbol": symbol, "error": "not enough bars after warmup"}

    cur, prev = df.iloc[-1], df.iloc[-2]
    edges_now = _edges_on_bar(cur, prev)

    # rolling 20-bar fire history
    history = []
    for i in range(max(1, len(df) - 20), len(df)):
        e = _edges_on_bar(df.iloc[i], df.iloc[i - 1])
        fired = [k for k, v in e.items() if v]
        if fired:
            history.append({
                "time": str(df.index[i]),
                "close": float(df["Close"].iloc[i]),
                "edges": fired,
            })

    # distance to key levels
    close = float(cur["Close"])
    hh20 = float(df["High"].rolling(20).max().iloc[-1])
    ll20 = float(df["Low"].rolling(20).min().iloc[-1])
    sma50 = float(cur["SMA50"])
    sma200 = float(cur["SMA200"])
    levels = {
        "SMA50":   {"value": sma50,   "pct_away": (close - sma50) / sma50 * 100},
        "SMA200":  {"value": sma200,  "pct_away": (close - sma200) / sma200 * 100},
        "HH_20":   {"value": hh20,    "pct_away": (close - hh20) / hh20 * 100},
        "LL_20":   {"value": ll20,    "pct_away": (close - ll20) / ll20 * 100},
    }

    snap = {
        "symbol": symbol,
        "bar_time_utc": str(df.index[-1]),
        "close": close,
        "regime": ("golden_cross" if cur["SMA50"] > cur["SMA200"]
                   else "death_cross" if cur["SMA50"] < cur["SMA200"]
                   else "mixed"),
        "rsi_14":      round(float(cur["RSI"]),       1),
        "range_z":     round(float(cur["RangeZ"]),    2),
        "close_pos":   round(float(cur["ClosePos"]),  2),
        "cvd_state": ("exhausted" if cur["CVD_slope"] < cur["CVD_low"]
                      else "strong"   if cur["CVD_slope"] > cur["CVD_high"]
                      else "neutral"),
        "tick_state": ("exhausted" if cur["TickImb"] < cur["TI_low"]
                       else "aggressive_bid" if cur["TickImb"] > cur["TI_high"]
                       else "neutral"),
        "rvol_pct":   ("compressed" if cur["RVol"] < cur["RVol_low"]
                       else "normal"),
        "edges_firing_now": [k for k, v in edges_now.items() if v],
        "edges_history_20bar": history,
        "levels": levels,
        "bars_loaded": len(df),
    }
    return snap


def pretty_print(snap: dict) -> str:
    if "error" in snap:
        return f"[{snap['symbol']}] ERROR: {snap['error']}"
    lines = []
    lines.append(f"╭─ {snap['symbol']} @ ${snap['close']:.2f}  ({snap['bar_time_utc']})")
    lines.append(f"│  Regime: {snap['regime']}  ·  RSI {snap['rsi_14']}  ·  "
                 f"RangeZ {snap['range_z']}  ·  ClosePos {snap['close_pos']}")
    lines.append(f"│  CVD: {snap['cvd_state']}  ·  Tick: {snap['tick_state']}  ·  "
                 f"RVol: {snap['rvol_pct']}")
    lines.append("│")
    if snap["edges_firing_now"]:
        lines.append(f"├─ EDGES FIRING NOW ({len(snap['edges_firing_now'])}):")
        for ek in snap["edges_firing_now"]:
            name, direction, marker = EDGE_LABELS.get(ek, (ek, "?", "?"))
            lines.append(f"│    {marker} {name} ({direction})")
    else:
        lines.append("├─ no edges firing on current bar")
    lines.append("│")
    lines.append("├─ levels:")
    for lk, lv in snap["levels"].items():
        sign = "+" if lv["pct_away"] >= 0 else ""
        lines.append(f"│    {lk:<8} ${lv['value']:.2f}  ({sign}{lv['pct_away']:.2f}% away)")
    lines.append("│")
    if snap["edges_history_20bar"]:
        lines.append(f"├─ last 20 bars — edges that fired:")
        for h in snap["edges_history_20bar"][-10:]:
            t = h["time"][:16]
            es = ", ".join(e.split("_")[0].upper() for e in h["edges"])
            lines.append(f"│    {t}  @ ${h['close']:.2f}  →  {es}")
    else:
        lines.append("├─ no edges fired in last 20 bars")
    lines.append(f"╰─ {snap['bars_loaded']} bars loaded")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="TV-style chart inspector for any yfinance symbol")
    p.add_argument("symbol", help="yfinance ticker (e.g. AAPL, ES=F, BTC-USD, ^VIX, EURUSD=X)")
    p.add_argument("--bars", type=int, default=100, help="lookback context size")
    p.add_argument("--json", action="store_true", help="output JSON instead of pretty print")
    args = p.parse_args()

    snap = inspect(args.symbol, args.bars)
    if args.json:
        print(json.dumps(snap, indent=2, default=str))
    else:
        print(pretty_print(snap))


if __name__ == "__main__":
    main()
