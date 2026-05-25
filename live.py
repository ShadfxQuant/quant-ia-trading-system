"""
Real-time runner.

Pulls the latest SPY 1h bars from yfinance, runs the full feature pipeline,
prints the most recent regime / structure / signal, and shows what the
backtester would do if it saw this bar in production.

Usage:
    python -m live
    python -m live SPY            # different symbol
    python -m live SPY --refresh  # force-refetch the cache
"""

from __future__ import annotations

import sys

import pandas as pd

from config.settings import DATA, STRATEGY, BACKTEST
from core.data_loader import load_symbol
from core.indicators import compute_indicators
from core.regime_model import classify_regime
from strategy.structure import label_structure
from strategy.entry_logic import generate_signals

try:
    from core.hmm_regime import attach_hmm_probabilities
except ImportError:
    attach_hmm_probabilities = None


def prepare_live(symbol: str, refresh: bool = False) -> pd.DataFrame:
    raw = load_symbol(symbol, force_refresh=refresh)
    df = compute_indicators(raw)
    df = classify_regime(df)
    df = label_structure(df)
    if STRATEGY.use_hmm_filter and attach_hmm_probabilities is not None:
        df = attach_hmm_probabilities(df)
    df = generate_signals(df)
    return df.dropna(subset=["EMA", "SMA", "EMA_slope", "Momentum", "Deviation"])


def report(df: pd.DataFrame, symbol: str) -> None:
    last = df.iloc[-1]
    ts = df.index[-1]

    print(f"\n=== {symbol} live snapshot @ {ts} ===")
    print(f"  Close       : {last['Close']:.2f}")
    print(f"  EMA / SMA   : {last['EMA']:.2f} / {last['SMA']:.2f}")
    print(f"  EMA slope   : {last['EMA_slope']:+.5f}")
    print(f"  Momentum    : {last['Momentum']:+.4f}")
    print(f"  Deviation   : {last['Deviation']:+.4f}")
    print(f"  Vol ratio   : {last['Vol_ratio']:.2f}")
    print(f"  RVOL        : {last.get('RVOL', float('nan')):.2f}")
    print(f"  VWAP        : {last.get('VWAP', float('nan')):.2f}  "
          f"({'above' if last['Close'] > last.get('VWAP', last['Close']) else 'below'})")
    print(f"  Structure   : {last['Structure']}")
    print(f"  Regime      : {last['Regime']}")
    if "P_bull" in df.columns and pd.notna(last["P_bull"]):
        print(f"  HMM         : P_bull={last['P_bull']:.2f}  "
              f"P_bear={last['P_bear']:.2f}  P_range={last['P_range']:.2f}")

    sig = int(last["Signal"])
    print(f"\n  Filters active: "
          f"RVOL={'ON' if STRATEGY.use_rvol_filter else 'off'}  "
          f"VWAP={'ON' if STRATEGY.use_vwap_filter else 'off'}  "
          f"HMM={'ON' if STRATEGY.use_hmm_filter else 'off'}")
    if sig == 1:
        notional = BACKTEST.initial_capital * BACKTEST.position_size_pct
        qty = notional / last["Close"]
        sl = last["Close"] * (1 - STRATEGY.stop_loss_pct)
        tp1 = last["Close"] * (1 + STRATEGY.take_profit_partial_pct)
        tp2 = last["Close"] * (1 + STRATEGY.take_profit_runner_pct)
        print(f"\n  >>> LONG SIGNAL <<<")
        print(f"      qty   ~= {qty:.2f}  (notional {notional:,.0f})")
        print(f"      stop   = {sl:.2f}   ({-STRATEGY.stop_loss_pct:.1%})")
        print(f"      TP1    = {tp1:.2f}  ({STRATEGY.take_profit_partial_pct:.1%}, "
              f"close {STRATEGY.take_profit_partial_size:.0%}, then trail to BE)")
        print(f"      TP2    = {tp2:.2f}  ({STRATEGY.take_profit_runner_pct:.1%}, runner)")
    elif sig == -1:
        print("\n  >>> SHORT SIGNAL <<<")
    else:
        print("\n  No new signal on the current bar.")

    # Recent activity (last 10 signal bars)
    recent_signals = df[df["Signal"] != 0].tail(10)
    if not recent_signals.empty:
        print("\n  Recent signal bars:")
        for ts_sig, row_sig in recent_signals.iterrows():
            side = "LONG " if row_sig["Signal"] == 1 else "SHORT"
            print(f"    {ts_sig}  {side}  Close={row_sig['Close']:.2f}  "
                  f"regime={row_sig['Regime']}")


def main(argv: list[str]) -> None:
    refresh = "--refresh" in argv
    args = [a for a in argv if not a.startswith("--")]
    symbol = args[0] if args else DATA.symbols[0]
    df = prepare_live(symbol, refresh=refresh)
    report(df, symbol)


if __name__ == "__main__":
    main(sys.argv[1:])
