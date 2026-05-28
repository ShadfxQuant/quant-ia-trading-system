"""
Research harness for three new gold strategy hypotheses on PAXGUSDT 1h.

Strategies under test:
  A) gold_asian_meanrev    — Asian-session mean reversion in chop
  B) gold_adx_breakout     — ADX threshold crossover after sustained chop
  C) gold_rollover_short   — EMA50 slope rollover before structural cross

For each: PF / WR / CAGR / DD / Sharpe / final equity.
Then: parameter sweeps for A and B.
Then: inter-strategy correlation + correlation vs existing pullback engine.

Gate to ship: PF ≥ 1.5 AND n ≥ 20.
"""
from __future__ import annotations
import warnings
import os
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
import logging; logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_daily, max_drawdown

from strategies.gold_asian_meanrev import (
    generate_signals as asian_sigs,
    exit_profile_for as asian_exit,
    GoldAsianMeanRevConfig,
)
from strategies.gold_adx_breakout import (
    generate_signals as adx_sigs,
    exit_profile_for as adx_exit,
    GoldAdxBreakoutConfig,
)
from strategies.gold_rollover_short import (
    generate_signals as roll_sigs,
    exit_profile_for as roll_exit,
    GoldRolloverShortConfig,
)


SYMBOL = "PAXGUSDT"
INITIAL_CAPITAL = 100_000


def _metrics(bt: dict) -> dict:
    eq = bt["equity_curve"]
    tr = bt["trades"]
    final = float(eq.iloc[-1])
    days = max((eq.index[-1] - eq.index[0]).days, 1)
    cagr = (final / INITIAL_CAPITAL) ** (365.25 / days) - 1
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if (tr.pnl < 0).any() else float("inf")
    wr = float((tr.pnl > 0).mean()) if not tr.empty else 0.0
    return dict(final=final, cagr=cagr, dd=dd, pf=pf, wr=wr,
                n=int(len(tr)), shday=sharpe_daily(eq), eq=eq, trades=tr)


def _run_single(df: pd.DataFrame, signals_fn, exit_fn, cfg) -> dict:
    """Inject the strategy's signals onto a copy of df, then run portfolio."""
    out = signals_fn(df, cfg)
    spec = StrategySpec(cfg.name, cfg, exit_fn(cfg))
    bt = run_portfolio(out, [spec], symbol=SYMBOL, initial_capital=INITIAL_CAPITAL)
    return _metrics(bt)


def _print_row(name: str, r: dict) -> str:
    gate = "✅" if (r["pf"] >= 1.5 and r["n"] >= 20) else "❌"
    return (f"{gate} {name:<22} n={r['n']:>4}  WR={r['wr']*100:>5.1f}%  "
            f"PF={r['pf']:>5.2f}  CAGR={r['cagr']*100:>6.1f}%  "
            f"DD={r['dd']*100:>5.1f}%  Sh={r['shday']:>5.2f}  "
            f"final=${r['final']:>10,.0f}")


def main() -> None:
    print("\n" + "=" * 100)
    print(f"GOLD STRATEGIES RESEARCH · {SYMBOL} 1h · capital ${INITIAL_CAPITAL:,}")
    print("=" * 100)

    df_full = prepare_dual(load_symbol(SYMBOL))
    print(f"Data: {df_full.index[0]} → {df_full.index[-1]}  ({len(df_full):,} bars)")
    days_total = (df_full.index[-1] - df_full.index[0]).days
    print(f"Span: {days_total} days ({days_total/365.25:.2f} years)\n")

    # ----- Baseline: existing pullback (uses prepare_dual's signals) -----
    from strategies.pullback import exit_profile_for as pb_exit_fn
    from config.settings import PULLBACK
    print("─" * 100); print("BASELINE — existing pullback engine"); print("─" * 100)
    bt_pb = run_portfolio(df_full, [StrategySpec("pullback", PULLBACK, pb_exit_fn())],
                          symbol=SYMBOL, initial_capital=INITIAL_CAPITAL)
    r_pb = _metrics(bt_pb)
    print(_print_row("pullback (existing)", r_pb))

    # ----- Strategy A -----
    print("\n" + "─" * 100); print("STRATEGY A — gold_asian_meanrev"); print("─" * 100)
    r_a = _run_single(df_full, asian_sigs, asian_exit, GoldAsianMeanRevConfig())
    print(_print_row("A — asian_meanrev", r_a))

    # ----- Strategy B -----
    print("\n" + "─" * 100); print("STRATEGY B — gold_adx_breakout"); print("─" * 100)
    r_b = _run_single(df_full, adx_sigs, adx_exit, GoldAdxBreakoutConfig())
    print(_print_row("B — adx_breakout", r_b))

    # ----- Strategy C -----
    print("\n" + "─" * 100); print("STRATEGY C — gold_rollover_short"); print("─" * 100)
    r_c = _run_single(df_full, roll_sigs, roll_exit, GoldRolloverShortConfig())
    print(_print_row("C — rollover_short", r_c))

    # ----- Parameter sweep A -----
    print("\n" + "─" * 100); print("SWEEP A — asian_meanrev (TP × SL × σ)"); print("─" * 100)
    best_a = None
    for tp in (0.008, 0.010, 0.012):
        for sl in (0.012, 0.015, 0.020):
            for sigma in (0.8, 1.0, 1.5):
                cfg = GoldAsianMeanRevConfig(
                    final_tp_pct=tp, stop_pct=sl, sigma_threshold=sigma)
                r = _run_single(df_full, asian_sigs, asian_exit, cfg)
                tag = "✅" if (r["pf"] >= 1.5 and r["n"] >= 20) else "  "
                print(f"  {tag} TP={tp*100:.1f}% SL={sl*100:.1f}% σ={sigma}  "
                      f"n={r['n']:>3} WR={r['wr']*100:>5.1f}% PF={r['pf']:>5.2f} "
                      f"CAGR={r['cagr']*100:>+6.1f}% DD={r['dd']*100:>5.1f}%")
                if r["n"] >= 20 and (best_a is None or r["pf"] > best_a[1]["pf"]):
                    best_a = ((tp, sl, sigma), r)
    if best_a:
        (tp, sl, sigma), r = best_a
        print(f"\n  → A best (by PF, n≥20): TP={tp*100:.1f}% SL={sl*100:.1f}% σ={sigma}  "
              f"PF={r['pf']:.2f} CAGR={r['cagr']*100:.1f}% DD={r['dd']*100:.1f}%")

    # ----- Parameter sweep B -----
    print("\n" + "─" * 100); print("SWEEP B — adx_breakout (chop_lookback × partial_tp)"); print("─" * 100)
    best_b = None
    for cb in (2, 3, 4, 5):
        for ptp in (0.02, 0.03, 0.04):
            cfg = GoldAdxBreakoutConfig(chop_lookback=cb, partial_tp_pct=ptp)
            r = _run_single(df_full, adx_sigs, adx_exit, cfg)
            tag = "✅" if (r["pf"] >= 1.5 and r["n"] >= 20) else "  "
            print(f"  {tag} chop_lookback={cb} partial_tp={ptp*100:.0f}%  "
                  f"n={r['n']:>3} WR={r['wr']*100:>5.1f}% PF={r['pf']:>5.2f} "
                  f"CAGR={r['cagr']*100:>+6.1f}% Sh={r['shday']:.2f}")
            if r["n"] >= 20 and (best_b is None or r["shday"] > best_b[1]["shday"]):
                best_b = ((cb, ptp), r)
    if best_b:
        (cb, ptp), r = best_b
        print(f"\n  → B best (by Sharpe, n≥20): chop_lookback={cb} partial_tp={ptp*100:.0f}%  "
              f"Sh={r['shday']:.2f} PF={r['pf']:.2f}")

    # ----- Correlation matrix on daily returns -----
    print("\n" + "─" * 100); print("INTER-STRATEGY CORRELATION (daily returns)"); print("─" * 100)
    eq_map = {
        "pullback": r_pb["eq"], "A_meanrev": r_a["eq"],
        "B_breakout": r_b["eq"], "C_rollover": r_c["eq"],
    }
    daily_rets = pd.DataFrame({
        k: (v.resample("D").last().pct_change().fillna(0.0))
        for k, v in eq_map.items()
    })
    corr = daily_rets.corr()
    print(corr.round(3).to_string())

    # ----- Equity curves PNG -----
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(figsize=(12, 6))
        for name, eq in eq_map.items():
            ax.plot(eq.index, eq.values, label=name, lw=1.2)
        ax.set_title(f"Gold strategies — equity curves on {SYMBOL}")
        ax.set_ylabel("Equity ($)"); ax.legend(); ax.grid(True, alpha=0.3)
        out_path = os.path.join("data", "research_gold_strategies.png")
        os.makedirs("data", exist_ok=True)
        fig.tight_layout(); fig.savefig(out_path, dpi=120)
        print(f"\nEquity curves saved → {out_path}")
    except Exception as e:
        print(f"(matplotlib unavailable, skipping PNG: {e})")

    # ----- Final recommendation -----
    print("\n" + "=" * 100); print("RECOMMENDATION"); print("=" * 100)
    summary = [
        ("A — asian_meanrev",   r_a),
        ("B — adx_breakout",    r_b),
        ("C — rollover_short",  r_c),
    ]
    passed = [(n, r) for n, r in summary if r["pf"] >= 1.5 and r["n"] >= 20]
    failed = [(n, r) for n, r in summary if (r["pf"] < 1.5 or r["n"] < 20)]
    for n, r in failed:
        print(f"  ❌ FAIL: {n}  (PF={r['pf']:.2f}, n={r['n']})  — do not ship")
    if not passed:
        print("\n  No strategy cleared the PF ≥ 1.5 AND n ≥ 20 gate.")
        print("  Recommendation: keep the existing pullback-only PAXG system.")
        return
    passed.sort(key=lambda x: x[1]["pf"], reverse=True)
    print("\n  Passed gate (ranked by PF):")
    for n, r in passed:
        c_to_pb = corr.loc["pullback", n.split(" ")[0].replace("A", "A_meanrev").replace("B", "B_breakout").replace("C", "C_rollover")] if "pullback" in corr.index else 0.0
        print(f"  ✅ {n:<22}  PF={r['pf']:.2f}  CAGR={r['cagr']*100:.1f}%  "
              f"DD={r['dd']*100:.1f}%  corr-vs-pullback={c_to_pb:+.2f}")
    print()
    top_name, top_r = passed[0]
    print(f"  → Ship {top_name} as a complement to existing pullback.")
    print(f"     Low correlation is a stronger signal than raw PF — prefer a")
    print(f"     PF 1.6 / corr 0.1 strategy to a PF 2.0 / corr 0.8 strategy")
    print(f"     because the former adds diversification rather than amplifying")
    print(f"     existing risk.")


if __name__ == "__main__":
    main()
