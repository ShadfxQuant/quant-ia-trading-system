"""
SESSION_LOG #23 — Recalibrate HMM size thresholds on the #21 signal-bar
distribution.

Step 1: measure the P_bull distribution conditional on pullback signal bars
        (SPY + DIA combined) under the #21 engine.
Step 2: pick new size_threshold_low / size_threshold_high so the bucket
        densities match the #6 reference (~45% low / ~39% normal / ~16% high).
Step 3: rerun config A vs config B (HMM ON, recalibrated) on SPY+DIA
        Sharpe-weighted book and compare to #21.
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import generate_signals as pullback_signals, exit_profile_for as pb_exit
from strategies.trend_carry import generate_signals as tc_signals, exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_intraday_bar, sharpe_daily, max_drawdown

START = 100_000.0
TARGET_LOW_DENSITY  = 0.45   # bottom bucket fraction
TARGET_HIGH_DENSITY = 0.16   # top bucket fraction (~ #6 reference)


def _prep_all():
    return {s: prepare_dual(load_symbol(s)) for s in ("SPY", "DIA")}


def _signal_pbull(prepped):
    """Combined P_bull on all pullback signal bars across SPY+DIA."""
    pieces = []
    for sym, df in prepped.items():
        df = pullback_signals(df, PULLBACK)
        sig = df[df["pullback_Signal"] != 0]
        if "P_bull" in sig.columns:
            pieces.append(sig["P_bull"].dropna())
    return pd.concat(pieces) if pieces else pd.Series(dtype=float)


def _bucket_density(p_bull, lo, hi):
    n = len(p_bull)
    if n == 0:
        return (0, 0, 0)
    low = float((p_bull < lo).mean())
    high = float((p_bull > hi).mean())
    return low, 1 - low - high, high


def _run_book(prepped, hmm_on):
    PULLBACK.use_hmm_meta = hmm_on
    per = {}
    for sym, raw in prepped.items():
        df = pullback_signals(raw.copy(), PULLBACK)
        df = tc_signals(df, TRENDCARRY)
        specs = [StrategySpec("pullback", PULLBACK, pb_exit()),
                 StrategySpec("trend_carry", TRENDCARRY, tc_exit())]
        bt = run_portfolio(df, specs, symbol=sym)
        per[sym] = bt
    # Sharpe-weight per-symbol equity curves.
    w = {s: max(sharpe_intraday_bar(p["equity_curve"]), 0.05) for s, p in per.items()}
    t = sum(w.values()); w = {k: v / t for k, v in w.items()}
    idx = sorted(set().union(*[p["equity_curve"].index for p in per.values()]))
    a = pd.DataFrame(index=pd.DatetimeIndex(idx))
    for s, p in per.items():
        eqn = p["equity_curve"] / p["equity_curve"].iloc[0]
        a[s] = eqn.reindex(a.index).ffill().bfill()
    port = sum(a[s] * w[s] * START for s in a.columns)
    port.iloc[0] = START
    days = (port.index[-1] - port.index[0]).days
    cagr = (port.iloc[-1] / START) ** (365.25 / max(days, 1)) - 1
    dd = max_drawdown(port)
    pf = {}
    for sym, p in per.items():
        tr = p["trades"]
        pf[sym] = float(tr.loc[tr.pnl > 0, "pnl"].sum() /
                        max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if not tr.empty else 0.0
    return {
        "Final$": float(port.iloc[-1]),
        "PnL$": float(port.iloc[-1] - START),
        "CAGR_pct": cagr * 100,
        "DD_pct": dd * 100,
        "Sh_intr": sharpe_intraday_bar(port),
        "Sh_day": sharpe_daily(port),
        "MAR": (cagr / dd) if dd > 0 else float("inf"),
        "weights": w,
        "PF": pf,
    }


def main():
    warnings.filterwarnings("ignore")
    prepped = _prep_all()

    # --- Step 1: measure P_bull on signal bars ---
    pb_dist = _signal_pbull(prepped)
    print(f"P_bull on pullback signal bars (SPY+DIA): n={len(pb_dist)}")
    qs = [0.05, 0.25, 0.45, 0.50, 0.55, 0.75, 0.84, 0.95]
    qvals = {q: float(pb_dist.quantile(q)) for q in qs}
    for q, v in qvals.items():
        print(f"  q{int(q*100):>2d} = {v:.3f}")

    # Default #6/#7 thresholds → expected density:
    lo_now, mid_now, hi_now = _bucket_density(pb_dist, 0.30, 0.70)
    print(f"\nDefault thresholds (0.30 / 0.70) on #21 signal bars: "
          f"low={lo_now:.0%}  normal={mid_now:.0%}  high={hi_now:.0%}")

    # --- Step 2: pick new thresholds ---
    lo_new = float(pb_dist.quantile(TARGET_LOW_DENSITY))
    hi_new = float(pb_dist.quantile(1.0 - TARGET_HIGH_DENSITY))
    lo_check, mid_check, hi_check = _bucket_density(pb_dist, lo_new, hi_new)
    print(f"\nProposed thresholds (q{int(TARGET_LOW_DENSITY*100)}={lo_new:.3f}, "
          f"q{int((1-TARGET_HIGH_DENSITY)*100)}={hi_new:.3f}) → "
          f"low={lo_check:.0%}  normal={mid_check:.0%}  high={hi_check:.0%}")

    # --- Step 3: rerun A vs B (HMM ON, recalibrated) ---
    # Snapshot defaults so we can restore.
    orig_lo = PULLBACK.size_threshold_low
    orig_hi = PULLBACK.size_threshold_high

    print("\n=== A — #21 baseline (HMM OFF) ===")
    a = _run_book(prepped, hmm_on=False)
    print(f"  Final ${a['Final$']:,.0f}  CAGR {a['CAGR_pct']:.1f}%  DD {a['DD_pct']:.2f}%  "
          f"MAR {a['MAR']:.2f}  Sh_intr {a['Sh_intr']:.3f}  Sh_day {a['Sh_day']:.3f}")

    PULLBACK.size_threshold_low = round(lo_new, 3)
    PULLBACK.size_threshold_high = round(hi_new, 3)
    print(f"\n=== B' — #21 + HMM meta, recalibrated "
          f"(low={PULLBACK.size_threshold_low}, high={PULLBACK.size_threshold_high}) ===")
    b = _run_book(prepped, hmm_on=True)
    print(f"  Final ${b['Final$']:,.0f}  CAGR {b['CAGR_pct']:.1f}%  DD {b['DD_pct']:.2f}%  "
          f"MAR {b['MAR']:.2f}  Sh_intr {b['Sh_intr']:.3f}  Sh_day {b['Sh_day']:.3f}")

    # Also test an intermediate setting: keep high tight, leave low at default.
    PULLBACK.size_threshold_low = orig_lo
    PULLBACK.size_threshold_high = round(hi_new, 3)
    print(f"\n=== B'' — recalibrate only HIGH (low={orig_lo}, high={PULLBACK.size_threshold_high}) ===")
    c = _run_book(prepped, hmm_on=True)
    print(f"  Final ${c['Final$']:,.0f}  CAGR {c['CAGR_pct']:.1f}%  DD {c['DD_pct']:.2f}%  "
          f"MAR {c['MAR']:.2f}  Sh_intr {c['Sh_intr']:.3f}  Sh_day {c['Sh_day']:.3f}")

    # And: keep low tight, leave high at default.
    PULLBACK.size_threshold_low = round(lo_new, 3)
    PULLBACK.size_threshold_high = orig_hi
    print(f"\n=== B''' — recalibrate only LOW (low={PULLBACK.size_threshold_low}, high={orig_hi}) ===")
    d = _run_book(prepped, hmm_on=True)
    print(f"  Final ${d['Final$']:,.0f}  CAGR {d['CAGR_pct']:.1f}%  DD {d['DD_pct']:.2f}%  "
          f"MAR {d['MAR']:.2f}  Sh_intr {d['Sh_intr']:.3f}  Sh_day {d['Sh_day']:.3f}")

    # Restore defaults
    PULLBACK.size_threshold_low = orig_lo
    PULLBACK.size_threshold_high = orig_hi
    PULLBACK.use_hmm_meta = False

    print("\n================ SUMMARY ================")
    print(f"{'cfg':<24}{'Final$':>12}{'CAGR%':>8}{'DD%':>8}{'Sh_day':>9}{'MAR':>7}")
    for label, m in [("A baseline (HMM OFF)", a),
                     (f"B' recal both", b),
                     (f"B'' recal high only", c),
                     (f"B''' recal low only", d)]:
        print(f"{label:<24}{m['Final$']:>12,.0f}{m['CAGR_pct']:>8.1f}{m['DD_pct']:>8.2f}"
              f"{m['Sh_day']:>9.3f}{m['MAR']:>7.2f}")


if __name__ == "__main__":
    main()
