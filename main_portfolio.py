"""
Production orchestrator — Deterministic Pullback Engine.

Pipeline:
    1. Load OHLCV (yfinance, cached).
    2. Compute shared indicators (EMA/SMA/momentum/deviation/vol/RVOL/VWAP).
    3. Classify deterministic regime + label structure.
    4. Attach HMM probabilities (informational only).
    5. Generate Pullback signals (deterministic-only execution).
    6. Run portfolio backtester.
    7. Print metrics + RVOL win/loss split + pyramid contribution + exposure.
"""

from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from config.settings import DATA, BACKTEST, PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from core.indicators import compute_indicators
from core.regime_model import classify_regime
from core.regime_score import attach_regime_score
from core.vol_targeting import attach_vol_target_mult
from core.vix import attach_vix_leverage_mult
from strategy.structure import label_structure
from strategies.pullback import (
    generate_signals as pullback_signals,
    exit_profile_for as pullback_exit_profile,
)
from strategies.trend_carry import (
    generate_signals as trend_carry_signals,
    exit_profile_for as trend_carry_exit_profile,
)
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import summarize, max_drawdown, sharpe_ratio

try:
    from core.hmm_regime import attach_hmm_probabilities
except ImportError:
    attach_hmm_probabilities = None


def prepare_dual(df: pd.DataFrame) -> pd.DataFrame:
    """Full feature pipeline. HMM attached for diagnostics, never for execution.

    Pipeline order (load-bearing — DO NOT reorder):
        indicators → deterministic regime → structure → HMM (optional)
        → RegimeScore (Layer 4 input) → pullback signals → trend_carry signals
    """
    df = compute_indicators(df)
    df = classify_regime(df)
    df = label_structure(df)
    if attach_hmm_probabilities is not None:
        df = attach_hmm_probabilities(df)
    # Layer 4: regime score (proxy for GEX/DEX; consumed by Layers 2 & 3).
    df = attach_regime_score(df)
    # Lever 3: vol-targeting multiplier (consumed by strategy size_mult when
    # the strategy's `use_vol_targeting` flag is on). Cheap to attach.
    df = attach_vol_target_mult(df, target_vol_annual=PULLBACK.vol_target_annual)
    # Lever 4: VIX-conditional dynamic leverage (institutional standard).
    df = attach_vix_leverage_mult(df)
    # Core alpha engine (now reads RegimeScore if use_adaptive_entry=True).
    df = pullback_signals(df)
    # Layer 3 carry sleeve — activates only when RegimeScore is high enough.
    df = trend_carry_signals(df)
    # Layer 5: RSI size multiplier (post-signal so it modulates size only,
    # never blocks entries). Verified 2026-05-30 to be cleanly additive
    # on baseline #0 (CAGR 17.1→17.3, PF 3.16→3.18, n unchanged).
    if getattr(PULLBACK, "use_rsi_size_mult", False):
        df = _apply_rsi_size_mult(df)
    return df.dropna(subset=["EMA", "SMA", "EMA_slope", "Momentum", "Deviation"])


def _apply_rsi_size_mult(df):
    """Multiply pullback_SizeMult by an RSI-derived factor.
    NEVER blocks entries — only modulates size. Bounded to [0.5, 1.5]
    so a misconfigured threshold can't zero out a position."""
    import pandas as pd
    cfg = PULLBACK
    close = df["Close"]
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1/14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi = (100 - 100 / (1 + rs)).fillna(50.0)
    mult = pd.Series(1.0, index=df.index)
    mult[rsi < cfg.rsi_oversold] = cfg.rsi_mult_oversold
    mult[rsi > cfg.rsi_overbought] = cfg.rsi_mult_overbought
    mult = mult.clip(lower=0.5, upper=1.5)
    out = df.copy()
    existing = out.get("pullback_SizeMult", pd.Series(1.0, index=out.index)).fillna(1.0)
    out["pullback_SizeMult"] = (existing * mult).clip(lower=0.2)  # never zero
    out["RSI_14"] = rsi
    return out


def per_strategy_block(
    trades: pd.DataFrame,
    curve: pd.Series,
    name: str,
    weeks: float,
    initial_capital: float,
) -> dict:
    sub = trades[trades["strategy"] == name].copy() if not trades.empty else trades
    if sub.empty:
        return {"strategy": name, "trades": 0, "unique_entries": 0,
                "trades_per_week": 0, "win_rate": 0, "profit_factor": 0,
                "expectancy": 0, "total_pnl": 0, "max_drawdown": 0,
                "avg_hold_bars": 0, "sharpe": 0}
    pnl_curve = curve + initial_capital
    return {
        "strategy": name,
        "trades": int(len(sub)),
        "unique_entries": int(sub["entry_time"].nunique()),
        "trades_per_week": round(sub["entry_time"].nunique() / weeks, 3) if weeks else 0,
        "win_rate": round(float((sub["pnl"] > 0).mean()), 4),
        "profit_factor": round(
            float(sub.loc[sub.pnl > 0, "pnl"].sum() / max(1e-9, -sub.loc[sub.pnl < 0, "pnl"].sum())), 4
        ),
        "expectancy": round(float(sub["return_pct"].mean()), 6),
        "total_pnl": round(float(sub["pnl"].sum()), 2),
        "max_drawdown": round(float(max_drawdown(pnl_curve)), 4),
        "avg_hold_bars": round(float(sub["bars_held"].mean()), 1),
        "sharpe": round(float(sharpe_ratio(pnl_curve)), 4),
    }


def _safe_mean(s: pd.Series) -> float:
    s = s.dropna()
    return float(s.mean()) if len(s) else float("nan")


def run(symbol: str = "SPY") -> dict:
    raw = load_symbol(symbol)
    prepared = prepare_dual(raw)
    weeks = (prepared.index.max() - prepared.index.min()).days / 7.0

    strategies = [
        StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_exit_profile()),
        StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_exit_profile()),
    ]
    bt = run_portfolio(prepared, strategies, symbol=symbol)
    trades = bt["trades"]
    equity = bt["equity_curve"]
    strat_curves = bt["strategy_curves"]
    exposure_curve = bt.get("exposure_curve", pd.Series(dtype=float))
    cap0 = bt["initial_capital"]

    print(f"\n=== {symbol} production pullback engine ===")
    print(f"  period      : {prepared.index.min()} → {prepared.index.max()}  ({weeks:.1f} wks)")
    print(f"  total legs  : {len(trades)}")
    print(f"  unique entries: {trades['entry_time'].nunique() if not trades.empty else 0}")

    # ---------- Per-strategy ----------
    print("\n  --- Per-strategy attribution ---")
    per_strat = {}
    for s in strategies:
        m = per_strategy_block(trades, strat_curves[s.name], s.name, weeks, cap0)
        per_strat[s.name] = m
        print(f"  [{s.name:>8s}] legs={m['trades']:>4d}  entries={m['unique_entries']:>3d}  "
              f"tw={m['trades_per_week']:.3f}  WR={m['win_rate']:.2%}  PF={m['profit_factor']}  "
              f"E={m['expectancy']:+.4f}  DD={m['max_drawdown']:.2%}  hold̄={m['avg_hold_bars']}  "
              f"Sharpe={m['sharpe']}  $={m['total_pnl']:,}")

    # ---------- Combined ----------
    combined = summarize(trades, equity)
    if not trades.empty:
        combined["trades_per_week"] = round(trades["entry_time"].nunique() / weeks, 3)
    print("\n  --- Combined portfolio ---")
    for k, v in combined.items():
        print(f"  {k:<16s} {v}")

    # ---------- RVOL win/loss diagnostic ----------
    if not trades.empty and "rvol_at_entry" in trades.columns:
        winners = trades[trades["pnl"] > 0]
        losers = trades[trades["pnl"] < 0]
        avg_rvol_win = _safe_mean(winners["rvol_at_entry"])
        avg_rvol_loss = _safe_mean(losers["rvol_at_entry"])
        print("\n  --- RVOL at entry (informational) ---")
        print(f"  avg RVOL @ winners : {avg_rvol_win:.3f}  (n={len(winners)})")
        print(f"  avg RVOL @ losers  : {avg_rvol_loss:.3f}  (n={len(losers)})")
        rvol_quartiles = trades["rvol_at_entry"].dropna().quantile([0.25, 0.5, 0.75]).round(3).to_dict()
        print(f"  RVOL quartiles     : Q1={rvol_quartiles.get(0.25, float('nan'))}  "
              f"med={rvol_quartiles.get(0.5, float('nan'))}  "
              f"Q3={rvol_quartiles.get(0.75, float('nan'))}")

    # ---------- Pyramid attribution (VWAP-confirmed contribution) ----------
    if not trades.empty and "stack_idx" in trades.columns:
        initial = trades[trades["stack_idx"] == 0]
        pyramid = trades[trades["stack_idx"] >= 1]
        total_pnl = float(trades["pnl"].sum())
        print("\n  --- Pyramid attribution ---")
        print(f"  initial entries   : {initial['entry_time'].nunique()} unique  "
              f"legs={len(initial)}  $={initial['pnl'].sum():,.2f}  "
              f"({(initial['pnl'].sum()/total_pnl if total_pnl else 0):+.1%} of total)")
        print(f"  pyramid stacks    : {pyramid['entry_time'].nunique()} unique  "
              f"legs={len(pyramid)}  $={pyramid['pnl'].sum():,.2f}  "
              f"({(pyramid['pnl'].sum()/total_pnl if total_pnl else 0):+.1%} of total)")

        # VWAP-confirmed pyramid contribution (all pyramids should be VWAP-confirmed
        # under the new gate, but check & report explicitly).
        if "vwap_alignment_at_entry" in trades.columns:
            vwap_pyramids = pyramid[pyramid["vwap_alignment_at_entry"] == 1]
            print(f"  VWAP-confirmed pyramids: {len(vwap_pyramids)}/{len(pyramid)} legs  "
                  f"$={vwap_pyramids['pnl'].sum():,.2f}  "
                  f"({(vwap_pyramids['pnl'].sum()/total_pnl if total_pnl else 0):+.1%} of total)")

        # Average and max stack depth (per unique entry timestamp)
        if not pyramid.empty:
            depths = trades.groupby("entry_time")["stack_idx"].first()
            avg_depth = depths.mean() + 1
            max_depth = int(depths.max()) + 1
            print(f"  avg stack depth   : {avg_depth:.2f}    max stack depth: {max_depth}")

    # ---------- Exposure utilisation ----------
    if not exposure_curve.empty:
        nonzero = exposure_curve[exposure_curve > 0]
        avg_util_when_open = float(nonzero.mean()) if len(nonzero) else 0.0
        max_util = float(exposure_curve.max())
        pct_invested = float((exposure_curve > 0).mean())
        cap_pct = PULLBACK.capital_cap_pct
        print("\n  --- Exposure utilisation ---")
        print(f"  cap (configured)  : {cap_pct:.0%}")
        print(f"  max utilisation   : {max_util:.1%}  ({max_util/cap_pct:.0%} of cap)")
        print(f"  avg when open     : {avg_util_when_open:.1%}")
        print(f"  pct bars invested : {pct_invested:.1%}")

    # ---------- HMM informational ----------
    if "P_bull" in prepared.columns:
        hmm_covered = prepared["P_bull"].notna().sum()
        total_bars = len(prepared)
        sig_bars = prepared[prepared["pullback_Signal"] != 0]
        avg_conf = float(sig_bars["pullback_Confidence"].mean()) if len(sig_bars) else float("nan")
        print("\n  --- HMM (informational only — does NOT gate execution) ---")
        print(f"  HMM coverage      : {hmm_covered}/{total_bars} bars ({hmm_covered/total_bars:.1%})")
        print(f"  Avg max-prob @ sig: {avg_conf:.3f}")

    return {
        "trades": trades,
        "equity": equity,
        "strategy_curves": strat_curves,
        "exposure_curve": exposure_curve,
        "per_strategy": per_strat,
        "combined": combined,
        "weeks": weeks,
    }


if __name__ == "__main__":
    sym = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    run(sym)
