"""
HMM-based diagnostic: is the entry or the exit holding the system back?

Method:
  1. Pull all baseline trades (SPY+GLD+PAXG, V0 config).
  2. Tag each trade with HMM state + P_bull at ENTRY and at EXIT bar.
  3. Four cross-tabs:
       A. WR / expectancy by entry HMM state, split by direction
          → if entry HMM doesn't separate winners/losers, entry isn't HMM-improvable
       B. WR by HMM-agreement at entry (bull state + long, bear state + short)
          → does aligning entry with HMM filter winners from losers?
       C. Of losers, did HMM state flip entry → exit?
          → if yes, regime turned and we held too long  → EXIT problem
          → if no, entry was structurally wrong         → ENTRY problem
       D. Counterfactual: "exit immediately when HMM flips against position"
          → compare resulting PnL distribution vs baseline
  4. Bootstrap-MC both counterfactuals (HMM-filtered entries, HMM-flip exits)
     and compare p5/p50/p95 against baseline.

Conclusion logic:
  - Entry counterfactual improves p5 CAGR > exit counterfactual  → entry was the bottleneck
  - Exit counterfactual improves p5 CAGR > entry counterfactual   → exit was the bottleneck
  - Neither improves                                              → look elsewhere
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
RNG = np.random.default_rng(11)


def asof(series, t):
    try: return series.asof(t)
    except Exception: return None


def tag_trades(symbol):
    df = prepare_dual(load_symbol(symbol))
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = bt["trades"].copy()
    if len(tr) == 0:
        return tr, df

    # tag HMM state + P_bull at entry and exit
    state_col = "HMM_state" if "HMM_state" in df.columns else None
    p_col = None
    for c in ("HMM_P_bull", "P_bull", "Hmm_p_bull"):
        if c in df.columns: p_col = c; break

    tr["hmm_entry"] = tr.entry_time.apply(lambda t: asof(df[state_col], t)) if state_col else None
    tr["hmm_exit"]  = tr.exit_time.apply (lambda t: asof(df[state_col], t)) if state_col else None
    tr["pbull_entry"] = tr.entry_time.apply(lambda t: asof(df[p_col], t)) if p_col else np.nan
    tr["pbull_exit"]  = tr.exit_time.apply (lambda t: asof(df[p_col], t)) if p_col else np.nan
    tr["is_win"] = tr.pnl > 0
    tr["symbol"] = symbol
    # per-trade return on equity-at-entry approximation
    eq = INITIAL; rets = []
    for p in tr["pnl"]:
        rets.append(p / eq); eq += p
    tr["ret"] = rets
    return tr, df


def mc(rets, days):
    rets = np.asarray(rets, dtype=float)
    if len(rets) == 0: return None
    n = len(rets)
    idx = RNG.integers(0, n, size=(N_PATHS, n))
    eq = INITIAL * np.cumprod(1.0 + rets[idx], axis=1)
    final = eq[:, -1]
    dd = ((eq - np.maximum.accumulate(eq, axis=1)) / np.maximum.accumulate(eq, axis=1)).min(axis=1)
    years = max(days / 365.25, 0.1)
    cagr = (final / INITIAL) ** (1.0 / years) - 1.0
    return {
        "n": n,
        "cagr_p5": float(np.quantile(cagr, 0.05)),
        "cagr_p50": float(np.quantile(cagr, 0.50)),
        "cagr_p95": float(np.quantile(cagr, 0.95)),
        "dd_p5": float(np.quantile(dd, 0.05)),
        "p_loss": float((final < INITIAL).mean()),
        "final_p50": float(np.quantile(final, 0.50)),
    }


def bullish_label(s):
    s = str(s).lower()
    return any(k in s for k in ("bull", "1", "up", "high"))

def bearish_label(s):
    s = str(s).lower()
    return any(k in s for k in ("bear", "0", "down", "low"))


def main():
    print("\n" + "="*92)
    print("  ENTRY vs EXIT ATTRIBUTION via HMM state diagnostic")
    print("="*92)

    all_tr = []
    for s in SYMBOLS:
        try:
            tr, _ = tag_trades(s)
            if len(tr): all_tr.append(tr)
        except Exception as e:
            print(f"  [{s}] skipped: {e}")
    tr = pd.concat(all_tr, ignore_index=True).sort_values("entry_time")
    days = (tr.exit_time.max() - tr.entry_time.min()).days

    print(f"\n  Total trades: {len(tr)}  ·  span {days}d  ·  symbols {SYMBOLS}")
    print(f"  Realized PnL: ${tr.pnl.sum():+,.0f}   WR {tr.is_win.mean()*100:.1f}%")

    # ------------------------------------------------------------------
    # A. WR + expectancy by entry HMM state
    # ------------------------------------------------------------------
    print(f"\n  ── A.  Outcome by ENTRY HMM state ──")
    print(f"  {'entry_hmm':<14}{'n':>6}{'WR':>9}{'avg ret':>11}{'$ pnl':>14}")
    print("  " + "-"*56)
    for h, g in tr.groupby("hmm_entry"):
        wr = g.is_win.mean()*100
        avg = g.ret.mean()*100
        print(f"  {str(h):<14}{len(g):>6}{wr:>8.1f}%{avg:>+10.2f}% ${g.pnl.sum():>+12,.0f}")

    # ------------------------------------------------------------------
    # B. HMM-direction agreement at entry (bull-state long, bear-state short)
    # ------------------------------------------------------------------
    if "side" in tr.columns:
        side_col = "side"
    else:
        # detect long/short from position size sign or another column
        side_col = "direction" if "direction" in tr.columns else None

    agreement = None
    if side_col is not None:
        def agree(row):
            h = str(row["hmm_entry"]).lower()
            sd = str(row[side_col]).lower()
            long_side = "long" in sd or sd in ("1", "buy")
            short_side = "short" in sd or sd in ("-1", "sell")
            if long_side and bullish_label(h): return "aligned"
            if short_side and bearish_label(h): return "aligned"
            if long_side and bearish_label(h): return "fighting"
            if short_side and bullish_label(h): return "fighting"
            return "neutral"
        tr["hmm_align"] = tr.apply(agree, axis=1)
        print(f"\n  ── B.  Outcome by HMM-direction alignment at entry ──")
        print(f"  {'alignment':<14}{'n':>6}{'WR':>9}{'avg ret':>11}{'$ pnl':>14}")
        print("  " + "-"*56)
        for a, g in tr.groupby("hmm_align"):
            wr = g.is_win.mean()*100
            avg = g.ret.mean()*100
            print(f"  {str(a):<14}{len(g):>6}{wr:>8.1f}%{avg:>+10.2f}% ${g.pnl.sum():>+12,.0f}")
        agreement = tr

    # ------------------------------------------------------------------
    # C. Of LOSERS, did the HMM state flip between entry and exit?
    # ------------------------------------------------------------------
    losers = tr[~tr.is_win].copy()
    losers["hmm_flipped"] = losers["hmm_entry"].astype(str) != losers["hmm_exit"].astype(str)
    print(f"\n  ── C.  Of {len(losers)} losers: did HMM flip from entry → exit? ──")
    flip = losers.hmm_flipped.sum(); same = len(losers) - flip
    print(f"  flipped:  {flip} losers ({flip/max(1,len(losers))*100:.1f}%)  "
          f"$ pnl ${losers[losers.hmm_flipped].pnl.sum():+,.0f}")
    print(f"  same:     {same} losers ({same/max(1,len(losers))*100:.1f}%)  "
          f"$ pnl ${losers[~losers.hmm_flipped].pnl.sum():+,.0f}")
    print("  Interpretation:")
    print("    > 60% flipped → exit too slow (regime turned, we held too long) = EXIT problem")
    print("    < 40% flipped → entered into wrong regime in the first place    = ENTRY problem")
    print("    40-60%        → mixed; HMM may not have enough signal")

    # ------------------------------------------------------------------
    # D. Counterfactual MC
    # ------------------------------------------------------------------
    print(f"\n  ── D.  Counterfactual MC vs baseline (3,000 paths each) ──")
    baseline = mc(tr["ret"].values, days)
    print(f"  baseline:        p5 {baseline['cagr_p5']*100:+5.1f}%  "
          f"p50 {baseline['cagr_p50']*100:+5.1f}%  p95 {baseline['cagr_p95']*100:+5.1f}%  "
          f"p5 DD {baseline['dd_p5']*100:+5.1f}%  P(loss) {baseline['p_loss']*100:.1f}%  "
          f"n={baseline['n']}")

    # CF1 — entry filter: keep only HMM-aligned trades
    if agreement is not None:
        cf1 = tr[tr.hmm_align == "aligned"]
        if len(cf1):
            m1 = mc(cf1["ret"].values, days)
            d = (m1["cagr_p5"] - baseline["cagr_p5"]) * 100
            print(f"  CF entry-align:  p5 {m1['cagr_p5']*100:+5.1f}%  "
                  f"p50 {m1['cagr_p50']*100:+5.1f}%  p95 {m1['cagr_p95']*100:+5.1f}%  "
                  f"p5 DD {m1['dd_p5']*100:+5.1f}%  P(loss) {m1['p_loss']*100:.1f}%  "
                  f"n={m1['n']}  Δp5 {d:+.1f}pp")
        else:
            print("  CF entry-align: no aligned trades, skipping")

    # CF2 — exit cutoff: simulate forcing losers that flipped HMM to exit at 50% loss instead of full
    cf2 = tr.copy()
    # For losers where HMM flipped against direction, scale loss by 0.5 (proxy for earlier exit)
    flipped_losers = (~cf2.is_win) & (cf2.hmm_entry.astype(str) != cf2.hmm_exit.astype(str))
    cf2.loc[flipped_losers, "ret"] = cf2.loc[flipped_losers, "ret"] * 0.5
    m2 = mc(cf2["ret"].values, days)
    d2 = (m2["cagr_p5"] - baseline["cagr_p5"]) * 100
    print(f"  CF exit-on-flip: p5 {m2['cagr_p5']*100:+5.1f}%  "
          f"p50 {m2['cagr_p50']*100:+5.1f}%  p95 {m2['cagr_p95']*100:+5.1f}%  "
          f"p5 DD {m2['dd_p5']*100:+5.1f}%  P(loss) {m2['p_loss']*100:.1f}%  "
          f"n={m2['n']}  Δp5 {d2:+.1f}pp  (assumes 50% loss reduction on flipped losers)")

    # ------------------------------------------------------------------
    # Verdict
    # ------------------------------------------------------------------
    print(f"\n  ── VERDICT ──")
    entry_gain = (m1["cagr_p5"] - baseline["cagr_p5"]) if agreement is not None and len(cf1) else -999
    exit_gain  = (m2["cagr_p5"] - baseline["cagr_p5"])
    if entry_gain > exit_gain and entry_gain > 0.02:
        print(f"  → ENTRY is the binding constraint. HMM-aligned entry filter gains "
              f"{entry_gain*100:+.1f}pp on p5 CAGR vs the exit counterfactual ({exit_gain*100:+.1f}pp).")
    elif exit_gain > entry_gain and exit_gain > 0.02:
        print(f"  → EXIT is the binding constraint. HMM-flip exit gains "
              f"{exit_gain*100:+.1f}pp on p5 CAGR vs the entry counterfactual ({entry_gain*100:+.1f}pp).")
    else:
        print(f"  → NEITHER. Entry CF Δp5 {entry_gain*100:+.1f}pp, exit CF Δp5 {exit_gain*100:+.1f}pp.")
        print(f"    HMM doesn't separate winners from losers at entry, and forcing earlier")
        print(f"    exits on flipped losers doesn't help. Bottleneck lives elsewhere —")
        print(f"    most likely sizing (V3 multi-symbol result) or feature engineering.")


if __name__ == "__main__":
    main()
