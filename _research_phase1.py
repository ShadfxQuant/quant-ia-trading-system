"""
Phase 1 + Phase 2 research harness.

Runs the deterministic pullback engine under N exit-structure variants
and prints a compact comparison matrix. Each run uses the same prepared
dataframe (cached) so total wall-time is roughly N × (backtest only).

Metrics surfaced:
    legs · entries · trades/wk · WR · PF · expectancy · avg_win · avg_loss ·
    avg_hold · max_DD · CAGR · Sharpe · Sortino · MAR · final$ · runner_pct
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Callable

import numpy as np
import pandas as pd

from config.settings import PULLBACK
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as _default_exit_profile
from strategies.exit_profile import ExitProfile
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import max_drawdown, sharpe_ratio, summarize


# ---------------------------------------------------------------------------
# Variant definitions
# ---------------------------------------------------------------------------

def _baseline() -> ExitProfile:
    return ExitProfile(
        stop_loss_pct=0.025,
        partial_tp_pct=0.04,
        partial_tp_size=0.50,
        final_tp_pct=0.10,
        final_tp_size=0.50,
        move_stop_to_be_after_partial=True,
        trailing_stop_enabled=True,
        trailing_logic_type="ema_50",
        trailing_starts_at="after_partial",
        atr_multiplier=2.0,
        max_hold_bars=390,
    )


def _option_1_tp15() -> ExitProfile:
    return replace(_baseline(), final_tp_pct=0.15)


def _option_2_no_trail() -> ExitProfile:
    return replace(_baseline(), trailing_stop_enabled=False,
                   move_stop_to_be_after_partial=False)


def _option_3_tight_stop() -> ExitProfile:
    return replace(_baseline(), stop_loss_pct=0.020)


def _option_4_atr(multiplier: float) -> ExitProfile:
    return replace(_baseline(),
                   trailing_stop_enabled=True,
                   trailing_logic_type="atr",
                   trailing_starts_at="after_partial",
                   atr_multiplier=multiplier,
                   move_stop_to_be_after_partial=False)


def _phase2_combined() -> ExitProfile:
    """Optimized engine: TP2 0.15 + ATR trailing + no BE shift."""
    return replace(_baseline(),
                   final_tp_pct=0.15,
                   trailing_stop_enabled=True,
                   trailing_logic_type="atr",
                   trailing_starts_at="after_partial",
                   atr_multiplier=2.0,
                   move_stop_to_be_after_partial=False)


def _p2_tp15_atr15_nobe() -> ExitProfile:
    """OPT1 + OPT4-ATR1.5 + no-BE — three winners stacked."""
    return replace(_baseline(),
                   final_tp_pct=0.15,
                   trailing_stop_enabled=True,
                   trailing_logic_type="atr",
                   trailing_starts_at="after_partial",
                   atr_multiplier=1.5,
                   move_stop_to_be_after_partial=False)


def _p2_tp15_notrail_be() -> ExitProfile:
    """OPT1 + OPT2 with BE-after-partial kept on (purely fixed-target with BE)."""
    return replace(_baseline(),
                   final_tp_pct=0.15,
                   trailing_stop_enabled=False,
                   move_stop_to_be_after_partial=True)


def _p2_tp15_notrail_nobe() -> ExitProfile:
    """OPT1 + OPT2 + no BE — 'just ride the runner to fixed targets'."""
    return replace(_baseline(),
                   final_tp_pct=0.15,
                   trailing_stop_enabled=False,
                   move_stop_to_be_after_partial=False)


def _p2_atr15_be() -> ExitProfile:
    """OPT4-ATR1.5 with BE-after-partial kept on (OPT4 default has BE off)."""
    return replace(_baseline(),
                   trailing_stop_enabled=True,
                   trailing_logic_type="atr",
                   trailing_starts_at="after_partial",
                   atr_multiplier=1.5,
                   move_stop_to_be_after_partial=True)


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _sortino(equity: pd.Series, periods_per_year: int = 252 * 7) -> float:
    if equity.empty or len(equity) < 2:
        return 0.0
    rets = equity.pct_change().dropna()
    downside = rets[rets < 0]
    if downside.std() == 0 or downside.empty:
        return 0.0
    return float(np.sqrt(periods_per_year) * rets.mean() / downside.std())


def _summarise(name: str, trades: pd.DataFrame, equity: pd.Series, weeks: float) -> dict:
    if trades.empty:
        return {"name": name, "legs": 0, "entries": 0, "tw": 0.0, "WR": 0.0,
                "PF": 0.0, "E%": 0.0, "avg_win%": 0.0, "avg_loss%": 0.0,
                "hold̄": 0.0, "MaxDD%": 0.0, "CAGR%": 0.0, "Sharpe": 0.0,
                "Sortino": 0.0, "MAR": 0.0, "Final$": float(equity.iloc[-1]) if not equity.empty else 0.0,
                "runner%": 0.0}

    wins = trades[trades["pnl"] > 0]
    losses = trades[trades["pnl"] < 0]
    avg_win = float(wins["return_pct"].mean()) if len(wins) else 0.0
    avg_loss = float(losses["return_pct"].mean()) if len(losses) else 0.0
    pf = float(wins["pnl"].sum() / max(1e-9, -losses["pnl"].sum())) if len(losses) else float("inf")

    base = summarize(trades, equity)
    cagr_v = base["cagr"]
    dd_v = base["max_drawdown"]
    mar = float(cagr_v / dd_v) if dd_v > 0 else float("inf")

    runner_legs = trades[trades["exit_reason"].isin(["tp2_final", "trail"])]
    runner_pct = float(len(runner_legs) / len(trades)) if len(trades) else 0.0

    return {
        "name": name,
        "legs": int(len(trades)),
        "entries": int(trades["entry_time"].nunique()),
        "tw": round(trades["entry_time"].nunique() / weeks, 3) if weeks else 0,
        "WR": round(float((trades["pnl"] > 0).mean()), 4),
        "PF": round(pf, 3),
        "E%": round(float(trades["return_pct"].mean()) * 100, 3),
        "avg_win%": round(avg_win * 100, 3),
        "avg_loss%": round(avg_loss * 100, 3),
        "hold̄": round(float(trades["bars_held"].mean()), 1),
        "MaxDD%": round(dd_v * 100, 2),
        "CAGR%": round(cagr_v * 100, 2),
        "Sharpe": round(base["sharpe"], 3),
        "Sortino": round(_sortino(equity), 3),
        "MAR": round(mar, 3),
        "Final$": round(float(equity.iloc[-1]), 0),
        "runner%": round(runner_pct * 100, 1),
    }


# ---------------------------------------------------------------------------
# Run harness
# ---------------------------------------------------------------------------

def _run_one(prepared: pd.DataFrame, profile_factory: Callable[[], ExitProfile],
             name: str, weeks: float) -> dict:
    profile = profile_factory()
    spec = StrategySpec(name="pullback", cfg=PULLBACK, exit_profile=profile)
    bt = run_portfolio(prepared, [spec], symbol="SPY")
    return _summarise(name, bt["trades"], bt["equity_curve"], weeks)


def main() -> None:
    raw = load_symbol("SPY")
    prepared = prepare_dual(raw)
    weeks = (prepared.index.max() - prepared.index.min()).days / 7.0
    print(f"period: {prepared.index.min()} → {prepared.index.max()}  ({weeks:.1f} wks)\n")

    variants = [
        ("BASELINE",         _baseline),
        ("OPT1_TP15",        _option_1_tp15),
        ("OPT2_NoTrail",     _option_2_no_trail),
        ("OPT3_Stop2.0",     _option_3_tight_stop),
        ("OPT4_ATR1.0",      lambda: _option_4_atr(1.0)),
        ("OPT4_ATR1.5",      lambda: _option_4_atr(1.5)),
        ("OPT4_ATR2.0",      lambda: _option_4_atr(2.0)),
        ("PHASE2_COMBINED",  _phase2_combined),
        ("P2_TP15+ATR1.5",   _p2_tp15_atr15_nobe),
        ("P2_TP15+NoTr+BE",  _p2_tp15_notrail_be),
        ("P2_TP15+NoTr-BE",  _p2_tp15_notrail_nobe),
        ("P2_ATR1.5+BE",     _p2_atr15_be),
    ]

    results = [_run_one(prepared, fn, name, weeks) for name, fn in variants]

    cols = ["name", "legs", "entries", "tw", "WR", "PF", "E%",
            "avg_win%", "avg_loss%", "hold̄", "MaxDD%", "CAGR%",
            "Sharpe", "Sortino", "MAR", "runner%", "Final$"]
    df = pd.DataFrame(results)[cols]
    print(df.to_string(index=False))


if __name__ == "__main__":
    main()
