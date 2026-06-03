"""
Confirmation pass for the "exit is the bottleneck" finding from _diag_entry_vs_exit.

Three sensitivity / falsification tests:
  T1. Sensitivity on the loss-reduction assumption (25% / 50% / 75% / 100%)
      → if the conclusion only holds at 75%+ reduction, it's an artifact.
  T2. Exit-reason breakdown of flipped vs same-state losers
      → if flipped losers concentrate in `max_hold` or `final_tp` (slow exits),
        that's mechanistic confirmation. If they concentrate in `stop_loss`,
        the HMM is just a co-incident, not the cause.
  T3. Per-symbol breakdown of the flip rate
      → if exit problem is uniform across symbols, it's structural.
        If only one symbol drives it, the fix is per-asset.
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

import numpy as np
import pandas as pd
from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "GLD", "PAXGUSDT"]
INITIAL = 100_000.0
N_PATHS = 3000
RNG = np.random.default_rng(13)


def asof(s, t):
    try: return s.asof(t)
    except Exception: return None


def tag(symbol):
    df = prepare_dual(load_symbol(symbol))
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = bt["trades"].copy()
    if len(tr) == 0: return tr
    tr["hmm_entry"] = tr.entry_time.apply(lambda t: asof(df["HMM_state"], t))
    tr["hmm_exit"]  = tr.exit_time.apply (lambda t: asof(df["HMM_state"], t))
    tr["is_win"] = tr.pnl > 0
    tr["symbol"] = symbol
    eq = INITIAL; rets = []
    for p in tr["pnl"]:
        rets.append(p / eq); eq += p
    tr["ret"] = rets
    tr["flipped"] = tr["hmm_entry"].astype(str) != tr["hmm_exit"].astype(str)
    return tr


def mc(rets, days):
    rets = np.asarray(rets, dtype=float)
    idx = RNG.integers(0, len(rets), size=(N_PATHS, len(rets)))
    eq = INITIAL * np.cumprod(1.0 + rets[idx], axis=1)
    final = eq[:, -1]
    rm = np.maximum.accumulate(eq, axis=1)
    dd = ((eq - rm) / rm).min(axis=1)
    years = max(days / 365.25, 0.1)
    cagr = (final / INITIAL) ** (1.0 / years) - 1.0
    return {
        "cagr_p5": float(np.quantile(cagr, 0.05)),
        "cagr_p50": float(np.quantile(cagr, 0.50)),
        "dd_p5": float(np.quantile(dd, 0.05)),
        "p_loss": float((final < INITIAL).mean()),
    }


def main():
    print("\n" + "="*92)
    print("  CONFIRMATION: is EXIT really the bottleneck?")
    print("="*92)

    all_tr = []
    for s in SYMBOLS:
        try:
            t = tag(s)
            if len(t): all_tr.append(t)
        except Exception as e:
            print(f"  [{s}] skipped: {e}")
    tr = pd.concat(all_tr, ignore_index=True).sort_values("entry_time")
    days = (tr.exit_time.max() - tr.entry_time.min()).days
    base = mc(tr["ret"].values, days)
    print(f"  baseline p5 CAGR {base['cagr_p5']*100:+.1f}%  "
          f"p50 {base['cagr_p50']*100:+.1f}%  p5 DD {base['dd_p5']*100:+.1f}%")

    losers = tr[~tr.is_win].copy()

    # ---- T1: sensitivity on loss-reduction parameter ----
    print(f"\n  ── T1.  Sensitivity: how much do we need to reduce flipped-loser loss? ──")
    print(f"  {'reduction':<14}{'p5 CAGR':>12}{'Δp5 vs base':>14}{'p50':>10}{'p5 DD':>10}")
    print("  " + "-"*60)
    for red in (0.0, 0.25, 0.50, 0.75, 1.00):
        cf = tr.copy()
        flipped_losers = (~cf.is_win) & cf.flipped
        cf.loc[flipped_losers, "ret"] *= (1.0 - red)
        m = mc(cf["ret"].values, days)
        d = (m["cagr_p5"] - base["cagr_p5"]) * 100
        print(f"  {red*100:>5.0f}% reduction{m['cagr_p5']*100:>+11.1f}%"
              f"{d:>+12.1f}pp{m['cagr_p50']*100:>+9.1f}%{m['dd_p5']*100:>+9.1f}%")

    # ---- T2: exit-reason concentration ----
    print(f"\n  ── T2.  Exit-reason concentration among LOSERS ──")
    print(f"  {'exit_reason':<22}{'flipped':>10}{'same':>10}{'flipped $':>14}{'same $':>14}")
    print("  " + "-"*72)
    if "exit_reason" in losers.columns:
        for reason, g in losers.groupby("exit_reason"):
            fl = g[g.flipped]; sm = g[~g.flipped]
            print(f"  {str(reason):<22}{len(fl):>10}{len(sm):>10}"
                  f"${fl.pnl.sum():>+12,.0f}${sm.pnl.sum():>+12,.0f}")
        print("  Mechanistic test: if flipped losers concentrate in `time_stop`/`final_tp` (slow exits),")
        print("  exit-ladder is mechanistically confirmed. If they concentrate in `stop_loss`, the HMM")
        print("  flip is co-incident — fast exits already fired and our 'exit fix' would do nothing.")
    else:
        print("  (no exit_reason column)")

    # ---- T3: per-symbol flip rate ----
    print(f"\n  ── T3.  Per-symbol flipped-loser breakdown ──")
    print(f"  {'symbol':<12}{'losers':>8}{'flipped':>10}{'flip%':>9}{'flipped $':>14}{'same $':>12}")
    print("  " + "-"*64)
    for s, g in losers.groupby("symbol"):
        fl = g[g.flipped]
        rate = len(fl) / max(1, len(g)) * 100
        print(f"  {s:<12}{len(g):>8}{len(fl):>10}{rate:>8.1f}%"
              f"${fl.pnl.sum():>+12,.0f}${g[~g.flipped].pnl.sum():>+10,.0f}")

    # ---- Final conclusion ----
    print(f"\n  ── CONFIRMATION CALL ──")
    cf = tr.copy()
    flipped_losers = (~cf.is_win) & cf.flipped
    cf.loc[flipped_losers, "ret"] *= 0.75  # very conservative — only 25% reduction
    m_cons = mc(cf["ret"].values, days)
    d_cons = (m_cons["cagr_p5"] - base["cagr_p5"]) * 100
    print(f"  At a CONSERVATIVE 25% loss reduction (modest earlier-exit assumption):")
    print(f"    p5 CAGR Δ {d_cons:+.1f}pp  ·  p50 CAGR Δ "
          f"{(m_cons['cagr_p50']-base['cagr_p50'])*100:+.1f}pp")
    if d_cons > 5:
        print(f"  → CONFIRMED: exit ladder is the binding constraint. The gain survives a")
        print(f"    conservative assumption.")
    elif d_cons > 2:
        print(f"  → WEAK confirmation. Real but modest. Worth building, not transformative.")
    else:
        print(f"  → NOT confirmed. The 50% assumption was doing the heavy lifting.")


if __name__ == "__main__":
    main()
