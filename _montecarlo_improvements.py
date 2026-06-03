"""
Monte Carlo evaluation of the four brain-identified improvements.

Each variant is applied as an in-process mutation to the live config dataclasses
(PULLBACK / REGIME_FILTERS), the backtest is re-run, and the realized trades
are bootstrap-resampled 3,000 times to estimate the distribution of CAGR,
max-DD, and P(lose $).

The point: prove these improvements are robust, not path-lucky.

Variants:
  V0  baseline                 — production config, no change
  V1  exit ladder fix          — re-enable EMA50 trailing after partial TP
  V2  HMM sizing reactivated   — use_hmm_meta = True (2.0x / 1.0x / 0.5x)
  V3  multi-symbol diversify   — combine SPY/GLD/PAXG + DIA + QQQ in pool
  V4  gold-native regime       — apply ADX_25_NO_ASIA_SLOPE to GLD too

Pass criteria (vs V0):
  - p5 CAGR not worse than baseline p5 by more than 3pp
  - p5 max-DD not worse than baseline p5 DD by more than 3pp
  - P(lose $) ≤ baseline P(lose $)
A variant passes if it improves median outcome without breaking these bounds.
"""
from __future__ import annotations
import warnings, logging, copy
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

import numpy as np
import pandas as pd
from config import settings
from config.settings import PULLBACK, TRENDCARRY, REGIME_FILTERS
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

N_PATHS = 3000
INITIAL = 100_000.0
RUIN = 50_000.0
DOUBLE = 200_000.0
BASE_SYMBOLS = ["SPY", "GLD", "PAXGUSDT"]
EXT_SYMBOLS  = ["SPY", "GLD", "PAXGUSDT", "DIA", "QQQ"]
RNG = np.random.default_rng(7)


# ---------------------------------------------------------------------------
# Config mutation helpers (always reverted in finally:)
# ---------------------------------------------------------------------------
def snapshot():
    return {
        "trailing_stop_enabled": PULLBACK.trailing_stop_enabled,
        "trailing_starts_at": PULLBACK.trailing_starts_at,
        "trailing_logic_type": PULLBACK.trailing_logic_type,
        "use_hmm_meta": PULLBACK.use_hmm_meta,
        "regime_filters": dict(REGIME_FILTERS),
    }

def restore(snap):
    PULLBACK.trailing_stop_enabled = snap["trailing_stop_enabled"]
    PULLBACK.trailing_starts_at = snap["trailing_starts_at"]
    PULLBACK.trailing_logic_type = snap["trailing_logic_type"]
    PULLBACK.use_hmm_meta = snap["use_hmm_meta"]
    REGIME_FILTERS.clear()
    REGIME_FILTERS.update(snap["regime_filters"])


# ---------------------------------------------------------------------------
# Backtest helper
# ---------------------------------------------------------------------------
def trades_for(symbol):
    df = prepare_dual(load_symbol(symbol))
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = bt["trades"].copy()
    if len(tr) == 0:
        tr["ret"] = []
        return tr
    eq = INITIAL
    rets = []
    for p in tr["pnl"]:
        rets.append(p / eq)
        eq += p
    tr["ret"] = rets
    return tr


def collect(symbols):
    """Run backtest on each symbol, return combined trade DF + per-symbol map."""
    per_sym = {}
    for s in symbols:
        try:
            tr = trades_for(s)
            if len(tr) > 0:
                per_sym[s] = tr
        except Exception as e:
            print(f"    [{s}] skipped: {e}")
    if not per_sym:
        return None, None
    combined = (pd.concat([t.assign(__sym=k) for k, t in per_sym.items()],
                          ignore_index=True)
                .sort_values("entry_time").reset_index(drop=True))
    return combined, per_sym


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------
def mc(rets, days):
    rets = np.asarray(rets, dtype=float)
    if len(rets) == 0:
        return None
    n = len(rets)
    idx = RNG.integers(0, n, size=(N_PATHS, n))
    sampled = rets[idx]
    eq = INITIAL * np.cumprod(1.0 + sampled, axis=1)
    final = eq[:, -1]
    rmax = np.maximum.accumulate(eq, axis=1)
    dd = ((eq - rmax) / rmax).min(axis=1)
    years = max(days / 365.25, 0.1)
    cagr = (final / INITIAL) ** (1.0 / years) - 1.0
    return {
        "n_trades": n,
        "final_p5":  float(np.quantile(final, 0.05)),
        "final_p50": float(np.quantile(final, 0.50)),
        "final_p95": float(np.quantile(final, 0.95)),
        "cagr_p5":   float(np.quantile(cagr, 0.05)),
        "cagr_p50":  float(np.quantile(cagr, 0.50)),
        "cagr_p95":  float(np.quantile(cagr, 0.95)),
        "dd_p5":     float(np.quantile(dd, 0.05)),
        "dd_p50":    float(np.quantile(dd, 0.50)),
        "p_loss":    float((final < INITIAL).mean()),
        "p_double":  float((final > DOUBLE).mean()),
        "p_ruin":    float((final < RUIN).mean()),
    }


def realized(trades):
    eq = INITIAL; peak = INITIAL; dd_min = 0.0
    for r in trades["ret"]:
        eq *= (1.0 + r)
        peak = max(peak, eq)
        dd_min = min(dd_min, (eq - peak) / peak)
    days = (trades["exit_time"].max() - trades["entry_time"].min()).days
    years = max(days / 365.25, 0.1)
    return {
        "final": eq,
        "cagr": (eq / INITIAL) ** (1.0 / years) - 1.0,
        "dd": dd_min,
        "n": len(trades),
        "days": days,
    }


# ---------------------------------------------------------------------------
# Variants
# ---------------------------------------------------------------------------
def apply_v1():
    """Exit ladder fix: re-enable EMA50 trailing after partial."""
    PULLBACK.trailing_stop_enabled = True
    PULLBACK.trailing_starts_at = "after_partial"
    PULLBACK.trailing_logic_type = "ema_50"

def apply_v2():
    """HMM sizing reactivated."""
    PULLBACK.use_hmm_meta = True

def apply_v4():
    """Gold-native: extend ADX_25_NO_ASIA_SLOPE regime filter to GLD."""
    REGIME_FILTERS["GLD"] = "ADX_25_NO_ASIA_SLOPE"


VARIANTS = [
    ("V0  baseline",                    None,       BASE_SYMBOLS),
    ("V1  exit ladder fix",             apply_v1,   BASE_SYMBOLS),
    ("V2  HMM sizing reactivated",      apply_v2,   BASE_SYMBOLS),
    ("V3  multi-symbol diversify",      None,       EXT_SYMBOLS),
    ("V4  gold-native regime on GLD",   apply_v4,   BASE_SYMBOLS),
]


def run_variant(name, mutator, symbols):
    print(f"\n{'='*92}\n  {name}   universe={symbols}\n{'='*92}")
    snap = snapshot()
    try:
        if mutator:
            mutator()
        combined, per_sym = collect(symbols)
        if combined is None:
            print("  no trades"); return None
        r = realized(combined)
        print(f"  realized: final ${r['final']:,.0f}  CAGR {r['cagr']*100:+.1f}%  "
              f"DD {r['dd']*100:+.1f}%  n={r['n']}  span={r['days']}d")
        m = mc(combined["ret"].values, r["days"])
        print(f"  MC CAGR   p5 {m['cagr_p5']*100:+5.1f}%  "
              f"p50 {m['cagr_p50']*100:+5.1f}%  p95 {m['cagr_p95']*100:+5.1f}%")
        print(f"  MC DD     p5 {m['dd_p5']*100:+5.1f}%  p50 {m['dd_p50']*100:+5.1f}%")
        print(f"  MC final  p5 ${m['final_p5']:,.0f}   p50 ${m['final_p50']:,.0f}   "
              f"p95 ${m['final_p95']:,.0f}")
        print(f"  P(loss) {m['p_loss']*100:5.1f}%  P(2×) {m['p_double']*100:5.1f}%  "
              f"P(ruin) {m['p_ruin']*100:5.1f}%")
        return {"name": name, "realized": r, "mc": m}
    finally:
        restore(snap)


def main():
    print("\n" + "="*92)
    print(f"  MC EVALUATION — brain-identified improvements ({N_PATHS:,} paths each)")
    print("="*92)
    results = []
    for name, mut, syms in VARIANTS:
        try:
            res = run_variant(name, mut, syms)
            if res: results.append(res)
        except Exception as e:
            print(f"  ERROR in {name}: {e!r}")
            import traceback; traceback.print_exc()

    print(f"\n{'='*92}\n  HEADLINE COMPARISON\n{'='*92}")
    print(f"  {'variant':<32}{'real CAGR':>11}{'MC p5':>9}{'MC p50':>9}{'MC p95':>9}"
          f"{'p5 DD':>9}{'P(loss)':>10}")
    print("  " + "-"*88)
    base = results[0] if results else None
    for r in results:
        nm = r["name"]; m = r["mc"]; rl = r["realized"]
        flag = ""
        if base and r is not base:
            d_p5 = (m["cagr_p5"] - base["mc"]["cagr_p5"]) * 100
            d_dd = (m["dd_p5"] - base["mc"]["dd_p5"]) * 100
            d_pl = (m["p_loss"] - base["mc"]["p_loss"]) * 100
            if d_p5 >= -3 and d_dd >= -3 and d_pl <= 0 and m["cagr_p50"] > base["mc"]["cagr_p50"]:
                flag = " ✅ PASS"
            elif d_p5 < -3 or d_dd < -3 or d_pl > 5:
                flag = " ❌ REGRESS"
            else:
                flag = " ⚠ MIXED"
        print(f"  {nm:<32}{rl['cagr']*100:>+10.1f}%"
              f"{m['cagr_p5']*100:>+8.1f}%{m['cagr_p50']*100:>+8.1f}%"
              f"{m['cagr_p95']*100:>+8.1f}%{m['dd_p5']*100:>+8.1f}%"
              f"{m['p_loss']*100:>9.1f}%{flag}")


if __name__ == "__main__":
    main()
