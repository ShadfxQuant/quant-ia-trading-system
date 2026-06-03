"""
Regime-flip exit V2 — conditional on being in drawdown.

V1 failed because raw HMM-flip cuts winners on their way up through
brief regime noise. V2 adds the precision filter: only exit on flip
when the trade is currently underwater (unrealized PnL < 0).

If V2 works, the Part 8.8 attribution is fully vindicated: the leak
IS at the exit, but a precision condition is needed.

If V2 also regresses, the conclusion changes: raw HMM is too noisy to
be the trigger. The ML regime classifier (next queue item) is then the
only path — it would output a calibrated loser-probability that
combines HMM with other features.
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
RNG = np.random.default_rng(19)
BEAR = {"bear", "0", "down", "low"}
BULL = {"bull", "1", "up", "high"}
def _bear(s): return str(s).lower() in BEAR
def _bull(s): return str(s).lower() in BULL


def side_of(row):
    for c in ("side", "direction"):
        if c in row and row[c] is not None:
            v = str(row[c]).lower()
            if "long" in v or v in ("1","buy","+1"): return +1
            if "short" in v or v in ("-1","sell"):   return -1
    try:
        if row["pnl"] >= 0:
            return +1 if row["exit_price"] >= row["entry_price"] else -1
        return +1 if row["exit_price"] < row["entry_price"] else -1
    except Exception: return +1


def apply_v2(tr, df, n_hold, dd_threshold=0.0, restrict_symbol=None):
    """Exit on HMM-flip AGAINST direction, only if unrealized return ≤ dd_threshold."""
    out = tr.copy()
    if "HMM_state" not in df.columns: return out
    for i, row in out.iterrows():
        if restrict_symbol and row.get("symbol") != restrict_symbol: continue
        side = side_of(row)
        try:
            i0 = df.index.get_indexer([row["entry_time"]], method="bfill")[0]
            i1 = df.index.get_indexer([row["exit_time"]],  method="bfill")[0]
        except Exception: continue
        if i0 < 0 or i1 <= i0 + n_hold: continue
        ep = float(row["entry_price"])
        scan = df.iloc[i0 + n_hold : i1]
        flip_idx = None
        for j, (state, close) in enumerate(zip(scan["HMM_state"].values,
                                                scan["Close"].values)):
            # only flip-exit if currently underwater
            unrealized = side * (close - ep) / ep
            if unrealized > dd_threshold: continue
            if side == +1 and _bear(state): flip_idx = j; break
            if side == -1 and _bull(state): flip_idx = j; break
        if flip_idx is None: continue
        new_t = scan.index[flip_idx]
        new_p = float(scan["Close"].iloc[flip_idx])
        size = float(row.get("size", row.get("position_size",
                  abs(row["pnl"]) / max(abs(row["exit_price"] - ep), 1e-9))))
        pnl_new = side * (new_p - ep) * size
        if new_t < row["exit_time"]:
            out.at[i, "pnl"] = pnl_new
            out.at[i, "exit_time"] = new_t
            out.at[i, "exit_price"] = new_p
            out.at[i, "exit_reason"] = "regime_flip_v2"
    eq = INITIAL; rets = []
    for p in out["pnl"]:
        rets.append(p / eq); eq += p
    out["ret"] = rets
    return out


def mc(rets, days):
    rets = np.asarray(rets, dtype=float)
    idx = RNG.integers(0, len(rets), size=(N_PATHS, len(rets)))
    eq = INITIAL * np.cumprod(1.0 + rets[idx], axis=1)
    final = eq[:, -1]
    rm = np.maximum.accumulate(eq, axis=1)
    dd = ((eq - rm) / rm).min(axis=1)
    years = max(days/365.25, 0.1)
    cagr = (final / INITIAL)**(1.0/years) - 1
    return {
        "cagr_p5": float(np.quantile(cagr, 0.05)),
        "cagr_p50": float(np.quantile(cagr, 0.50)),
        "cagr_p95": float(np.quantile(cagr, 0.95)),
        "dd_p5": float(np.quantile(dd, 0.05)),
        "p_loss": float((final < INITIAL).mean()),
    }


def main():
    print("\n" + "="*100)
    print("  REGIME-FLIP EXIT V2 — only triggers when trade is underwater")
    print("="*100)

    per_sym = {}
    for s in SYMBOLS:
        df = prepare_dual(load_symbol(s))
        bt = run_portfolio(df, [
            StrategySpec("pullback", PULLBACK, pb_exit()),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=s, initial_capital=INITIAL)
        tr = bt["trades"].copy()
        tr["symbol"] = s
        per_sym[s] = (tr, df)

    base = pd.concat([t for t,_ in per_sym.values()], ignore_index=True)\
             .sort_values("entry_time").reset_index(drop=True)
    eq = INITIAL; rets=[]
    for p in base["pnl"]: rets.append(p/eq); eq+=p
    base["ret"] = rets
    days = (base["exit_time"].max() - base["entry_time"].min()).days
    base_eq = INITIAL
    for r in base["ret"]: base_eq *= (1+r)
    base_cagr = (base_eq/INITIAL)**(1/max(days/365.25,0.1))-1
    base_mc = mc(base["ret"].values, days)
    print(f"\n  baseline:  real {base_cagr*100:+.1f}%  MC p5 {base_mc['cagr_p5']*100:+.1f}%  "
          f"p50 {base_mc['cagr_p50']*100:+.1f}%  p5 DD {base_mc['dd_p5']*100:+.1f}%")

    def apply_all(n, dd_thresh, restrict=None):
        out = []
        for sym,(tr,df) in per_sym.items():
            out.append(apply_v2(tr, df, n, dd_thresh, restrict))
        merged = pd.concat(out, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
        eq=INITIAL; rets=[]
        for p in merged["pnl"]: rets.append(p/eq); eq+=p
        merged["ret"]=rets
        return merged

    print(f"\n  {'variant':<48}{'real CAGR':>12}{'Δ vs base':>12}{'MC p5':>10}"
          f"{'Δ p5':>10}{'p5 DD':>10}{'verdict':>14}")
    print("  " + "-"*108)

    def row(name, tr):
        eq = INITIAL
        for r in tr["ret"]: eq *= (1+r)
        rc = (eq/INITIAL)**(1/max(days/365.25,0.1)) - 1
        m = mc(tr["ret"].values, days)
        d_real = (rc - base_cagr)*100
        d_p5 = (m["cagr_p5"] - base_mc["cagr_p5"])*100
        d_dd = (m["dd_p5"] - base_mc["dd_p5"])*100
        if d_p5 >= 3 and d_dd >= -3 and m["p_loss"] <= base_mc["p_loss"] + 0.001:
            v = "✅ PASS"
        elif d_p5 < -3 or d_dd < -3:
            v = "❌ REGRESS"
        else:
            v = "⚠ MIXED"
        print(f"  {name:<48}{rc*100:>+11.1f}%{d_real:>+11.1f}pp"
              f"{m['cagr_p5']*100:>+9.1f}%{d_p5:>+9.1f}pp{m['dd_p5']*100:>+9.1f}%{v:>14}")

    # baseline reference row
    row("baseline", base)
    # sweep: N hold, DD thresholds, scope
    for n in (3, 5, 10):
        for dd in (0.0, -0.01, -0.02):
            tr = apply_all(n, dd, None)
            row(f"V2 N={n} dd≤{dd*100:+.0f}% all", tr)

    print(f"\n  ── PAXG-only V2 ──")
    for n in (3, 5, 10):
        for dd in (0.0, -0.01, -0.02):
            tr = apply_all(n, dd, "PAXGUSDT")
            row(f"V2 N={n} dd≤{dd*100:+.0f}% PAXG", tr)


if __name__ == "__main__":
    main()
