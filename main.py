"""
End-to-end orchestrator for the quant_ia_trading_system.

Pipeline:
    1. Load market data via yfinance (cached to data/raw).
    2. Compute indicators (EMA, SMA, slope, momentum, deviation, volatility).
    3. Classify market regime (subperiod model).
    4. Label market structure (bullish / bearish / neutral).
    5. Generate entry signals (pullback + imbalance + momentum continuation).
    6. Run the backtester on every symbol.
    7. Print per-symbol metrics and persist the processed data.

Run from the project root:
    python -m main                # uses defaults from config.settings
    python -m main AAPL MSFT      # override the symbol list
"""

from __future__ import annotations

import os
import sys
from typing import Iterable

import pandas as pd

from config.settings import DATA
from core.data_loader import load_universe
from core.indicators import compute_indicators
from core.regime_model import classify_regime, regime_summary
from strategy.structure import label_structure
from strategy.entry_logic import generate_signals
from config.settings import STRATEGY
try:
    from core.hmm_regime import attach_hmm_probabilities
except ImportError:
    attach_hmm_probabilities = None
from backtest.backtester import run_backtest
from backtest.metrics import summarize


def prepare(df: pd.DataFrame) -> pd.DataFrame:
    """Run the full feature pipeline on a single OHLCV DataFrame."""
    df = compute_indicators(df)
    df = classify_regime(df)
    df = label_structure(df)
    # HMM overlay only runs when the filter is enabled, to keep cold-start fast.
    if STRATEGY.use_hmm_filter and attach_hmm_probabilities is not None:
        df = attach_hmm_probabilities(df)
    df = generate_signals(df)
    return df.dropna(subset=["EMA", "SMA", "EMA_slope", "Momentum", "Deviation"])


def _save_processed(symbol: str, df: pd.DataFrame) -> str:
    os.makedirs(DATA.processed_dir, exist_ok=True)
    path = os.path.join(DATA.processed_dir, f"{symbol}.csv")
    df.to_csv(path)
    return path


def run(symbols: Iterable[str] | None = None) -> dict[str, dict]:
    universe = load_universe(symbols=list(symbols) if symbols else None)
    if not universe:
        print("No data loaded; aborting.")
        return {}

    results: dict[str, dict] = {}
    for symbol, raw in universe.items():
        print(f"\n=== {symbol} ===")
        prepared = prepare(raw)
        path = _save_processed(symbol, prepared)
        print(f"Processed data written to {path}")

        regime_pct = regime_summary(prepared)
        print("Regime distribution:")
        for label, pct in regime_pct.items():
            print(f"  {label:<14s} {pct:.1%}")

        bt = run_backtest(prepared, symbol=symbol)
        metrics = summarize(bt["trades"], bt["equity_curve"])
        results[symbol] = metrics

        print("Performance:")
        for key, value in metrics.items():
            print(f"  {key:<14s} {value}")

    print("\n=== Summary ===")
    summary_df = pd.DataFrame(results).T
    print(summary_df.to_string())
    return results


if __name__ == "__main__":
    cli_symbols = sys.argv[1:] or None
    run(cli_symbols)
