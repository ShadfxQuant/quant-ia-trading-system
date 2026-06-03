"""
PROTOTYPE — regime-flip exit rule (Part 8.8 follow-up).

The rule: for each open position, scan forward bar-by-bar. If
  (a) HMM_state at bar t flips against the position direction
      (long held when HMM goes bear, short held when HMM goes bull), AND
  (b) the trade has been open ≥ N bars (avoids whipsaw on entry bar),
then force-exit at that bar's close. Pre-empts the time-stop bucket
identified in Part 8.8 as the leak.

Implementation: post-hoc trade rewrite. We do NOT modify
execution/portfolio.py for the prototype. For each baseline trade:
  1. Slice df from entry_bar+N forward to exit_bar.
  2. Find first bar where HMM_state ∈ opposing labels.
  3. If found before original exit_time, override exit_time / exit_price
     to that bar's close, recompute PnL using the trade's size, label
     exit_reason as "regime_flip".
  4. Otherwise keep original exit.

Tested N ∈ {3, 5, 10}. Also tested PAXG-only application.

MC harness identical to _montecarlo_improvements.py for apples-to-apples.
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
RNG = np.random.default_rng(17)

BULL_LABELS = {"bull", "1", "up", "high"}
BEAR_LABELS = {"bear", "0", "down", "low"}


def _is_bull(s): return str(s).lower() in BULL_LABELS
def _is_bear(s): return str(s).lower() in BEAR_LABELS


def detect_side(row):
    """Return +1 for long, -1 for short. Handles multiple naming conventions."""
    for col in ("side", "direction", "trade_side"):
        if col in row and row[col] is not None:
            v = str(row[col]).lower()
            if "long" in v or v in ("1", "buy", "+1"): return +1
            if "short" in v or v in ("-1", "sell"):    return -1
    # fallback: infer from entry vs exit price + pnl sign
    try:
        if row["pnl"] >= 0:
            return +1 if row["exit_price"] >= row["entry_price"] else -1
        else:
            return +1 if row["exit_price"] < row["entry_price"] else -1
    except Exception:
        return +1


def apply_regime_flip(tr, df, n_bars_hold, restrict_symbol=None):
    """Post-hoc rewrite of exits based on regime-flip rule.

    Returns a copy of tr with exit_time/exit_price/pnl/exit_reason updated
    where regime-flip exit would have triggered earlier than the original
    exit.
    """
    out = tr.copy()
    if "HMM_state" not in df.columns:
        return out
    hmm = df["HMM_state"]
    closes = df["Close"]

    new_pnl = out["pnl"].copy()
    new_exit_time = out["exit_time"].copy()
    new_exit_price = out["exit_price"].copy()
    new_reason = out["exit_reason"].copy()

    for i, row in out.iterrows():
        if restrict_symbol and row.get("symbol") != restrict_symbol:
            continue
        side = detect_side(row)
        # bar index of entry
        try:
            i0 = df.index.get_indexer([row["entry_time"]], method="bfill")[0]
            i1 = df.index.get_indexer([row["exit_time"]],  method="bfill")[0]
        except Exception:
            continue
        if i0 < 0 or i1 <= i0 + n_bars_hold:
            continue

        scan = df.iloc[i0 + n_bars_hold : i1]
        if scan.empty: continue
        # find first bar where HMM flips against direction
        flip_idx = None
        for j, state in enumerate(scan["HMM_state"].values):
            if side == +1 and _is_bear(state):
                flip_idx = j; break
            if side == -1 and _is_bull(state):
                flip_idx = j; break
        if flip_idx is None:
            continue

        new_t = scan.index[flip_idx]
        new_p = float(scan["Close"].iloc[flip_idx])
        ep = float(row["entry_price"])
        size = float(row.get("size", row.get("position_size",
                  abs(row["pnl"]) / max(abs(new_p - ep), 1e-9))))
        # recompute pnl: side * (new_p - ep) * size
        pnl_new = side * (new_p - ep) * size

        # only override if regime-flip exit triggered earlier than original
        if new_t < row["exit_time"]:
            new_pnl.iloc[i] = pnl_new
            new_exit_time.iloc[i] = new_t
            new_exit_price.iloc[i] = new_p
            new_reason.iloc[i] = "regime_flip"

    out["pnl"] = new_pnl
    out["exit_time"] = new_exit_time
    out["exit_price"] = new_exit_price
    out["exit_reason"] = new_reason
    # recompute per-trade return on equity-at-entry approximation
    eq = INITIAL; rets = []
    for p in out["pnl"]:
        rets.append(p / eq); eq += p
    out["ret"] = rets
    return out


def mc(rets, days):
    rets = np.asarray(rets, dtype=float)
    if len(rets) == 0: return None
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
        "cagr_p95": float(np.quantile(cagr, 0.95)),
        "dd_p5": float(np.quantile(dd, 0.05)),
        "dd_p50": float(np.quantile(dd, 0.50)),
        "p_loss": float((final < INITIAL).mean()),
        "final_p50": float(np.quantile(final, 0.50)),
    }


def collect_with_df(symbols):
    per_sym = {}
    for s in symbols:
        try:
            df = prepare_dual(load_symbol(s))
            bt = run_portfolio(df, [
                StrategySpec("pullback", PULLBACK, pb_exit()),
                StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
            ], symbol=s, initial_capital=INITIAL)
            tr = bt["trades"].copy()
            if len(tr) == 0: continue
            tr["symbol"] = s
            per_sym[s] = (tr, df)
        except Exception as e:
            print(f"  [{s}] skipped: {e}")
    return per_sym


def realize(tr):
    eq = INITIAL
    for p in tr["pnl"]: eq *= (1.0 + p / max(eq - p, 1e-9))  # crude back-calc
    # simpler: cum equity from already-computed ret
    eq = INITIAL
    for r in tr["ret"]: eq *= (1.0 + r)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    return eq, days


def report(name, tr_all):
    eq, days = realize(tr_all)
    years = max(days/365.25, 0.1)
    realized_cagr = (eq/INITIAL)**(1/years) - 1
    m = mc(tr_all["ret"].values, days)
    print(f"  {name:<34} real ${eq:>10,.0f} ({realized_cagr*100:+5.1f}%)  "
          f"MC p5/p50/p95 {m['cagr_p5']*100:+5.1f}/{m['cagr_p50']*100:+5.1f}/"
          f"{m['cagr_p95']*100:+5.1f}%  p5 DD {m['dd_p5']*100:+5.1f}%  "
          f"P(loss) {m['p_loss']*100:.1f}%")
    return m, realized_cagr


def main():
    print("\n" + "="*100)
    print("  PROTOTYPE — regime-flip exit rule  (3 N values × 2 scopes + PAXG max_hold tightening)")
    print("="*100)

    per_sym = collect_with_df(SYMBOLS)
    # Build base trades + per-symbol df map
    base_trades = pd.concat([t for t, _ in per_sym.values()],
                            ignore_index=True).sort_values("entry_time").reset_index(drop=True)
    # per-symbol return series for baseline
    eq = INITIAL; rets = []
    for p in base_trades["pnl"]:
        rets.append(p / eq); eq += p
    base_trades["ret"] = rets
    days = (base_trades["exit_time"].max() - base_trades["entry_time"].min()).days
    base_mc = mc(base_trades["ret"].values, days)
    base_eq, _ = realize(base_trades)
    base_cagr = (base_eq/INITIAL)**(1/max(days/365.25,0.1)) - 1
    print(f"\n  baseline: real ${base_eq:,.0f} ({base_cagr*100:+.1f}%)  "
          f"MC p5 {base_mc['cagr_p5']*100:+.1f}%  p50 {base_mc['cagr_p50']*100:+.1f}%  "
          f"p5 DD {base_mc['dd_p5']*100:+.1f}%  P(loss) {base_mc['p_loss']*100:.1f}%")

    # apply regime-flip per symbol then concat
    def apply_all(n_bars, restrict=None):
        out = []
        for sym, (tr, df) in per_sym.items():
            mod = apply_regime_flip(tr, df, n_bars, restrict_symbol=restrict)
            out.append(mod)
        merged = pd.concat(out, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
        eq = INITIAL; rets = []
        for p in merged["pnl"]:
            rets.append(p / eq); eq += p
        merged["ret"] = rets
        return merged

    print(f"\n  {'variant':<35}{'real $':>16}{'MC p5':>22}{'p5 DD':>10}{'P(loss)':>10}")
    print("  " + "-"*92)

    results = []
    for n in (3, 5, 10):
        tr = apply_all(n, restrict=None)
        m, rc = report(f"regime-flip N={n} all symbols", tr)
        results.append((f"N={n} all", n, None, m, rc, tr))

    for n in (3, 5, 10):
        tr = apply_all(n, restrict="PAXGUSDT")
        m, rc = report(f"regime-flip N={n} PAXG only", tr)
        results.append((f"N={n} PAXG", n, "PAXGUSDT", m, rc, tr))

    # PAXG-specific max_hold tightening (independent of regime-flip)
    # Simulate: any PAXG trade whose hold > X bars and is currently losing
    # at bar X is force-exited at that close.
    print(f"\n  ── PAXG-only max_hold tightening (independent of regime-flip) ──")
    for max_hold in (100, 200, 300):
        # post-hoc rewrite
        modified = []
        for sym, (tr, df) in per_sym.items():
            tr_mod = tr.copy()
            if sym == "PAXGUSDT":
                new_pnl = tr_mod["pnl"].copy()
                new_t   = tr_mod["exit_time"].copy()
                new_p   = tr_mod["exit_price"].copy()
                new_r   = tr_mod["exit_reason"].copy()
                for i, row in tr_mod.iterrows():
                    try:
                        i0 = df.index.get_indexer([row["entry_time"]], method="bfill")[0]
                        i1 = df.index.get_indexer([row["exit_time"]],  method="bfill")[0]
                    except Exception: continue
                    if i1 - i0 <= max_hold: continue
                    cut_idx = i0 + max_hold
                    if cut_idx >= len(df): continue
                    new_close = float(df["Close"].iloc[cut_idx])
                    new_time  = df.index[cut_idx]
                    side = detect_side(row)
                    ep = float(row["entry_price"])
                    size = float(row.get("size", row.get("position_size",
                                  abs(row["pnl"]) / max(abs(row["exit_price"]-ep),1e-9))))
                    pnl_new = side * (new_close - ep) * size
                    if new_time < row["exit_time"]:
                        new_pnl.iloc[i] = pnl_new
                        new_t.iloc[i] = new_time
                        new_p.iloc[i] = new_close
                        new_r.iloc[i] = "paxg_max_hold"
                tr_mod["pnl"] = new_pnl
                tr_mod["exit_time"] = new_t
                tr_mod["exit_price"] = new_p
                tr_mod["exit_reason"] = new_r
            modified.append(tr_mod)
        merged = pd.concat(modified, ignore_index=True).sort_values("entry_time").reset_index(drop=True)
        eq = INITIAL; rets = []
        for p in merged["pnl"]:
            rets.append(p / eq); eq += p
        merged["ret"] = rets
        report(f"PAXG max_hold={max_hold} (baseline 390)", merged)

    print(f"\n{'='*100}")
    print("  SUMMARY TABLE")
    print("="*100)
    print(f"  {'variant':<32}{'real CAGR':>12}{'Δ vs base':>12}{'MC p5':>10}"
          f"{'Δ p5':>10}{'p5 DD':>10}{'verdict':>14}")
    print("  " + "-"*100)
    # baseline row
    print(f"  {'baseline':<32}{base_cagr*100:>+11.1f}%{0.0:>+11.1f}pp"
          f"{base_mc['cagr_p5']*100:>+9.1f}%{0.0:>+9.1f}pp{base_mc['dd_p5']*100:>+9.1f}%"
          f"{'reference':>14}")
    for name, n, rsym, m, rc, _ in results:
        d_real = (rc - base_cagr) * 100
        d_p5 = (m["cagr_p5"] - base_mc["cagr_p5"]) * 100
        d_dd = (m["dd_p5"] - base_mc["dd_p5"]) * 100
        if d_p5 >= 3 and d_dd >= -3 and m["p_loss"] <= base_mc["p_loss"] + 0.001:
            v = "✅ PASS"
        elif d_p5 < -3 or d_dd < -3:
            v = "❌ REGRESS"
        else:
            v = "⚠ MIXED"
        print(f"  {name:<32}{rc*100:>+11.1f}%{d_real:>+11.1f}pp"
              f"{m['cagr_p5']*100:>+9.1f}%{d_p5:>+9.1f}pp{m['dd_p5']*100:>+9.1f}%"
              f"{v:>14}")


if __name__ == "__main__":
    main()
