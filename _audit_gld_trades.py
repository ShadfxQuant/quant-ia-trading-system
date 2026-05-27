"""
Trade-level audit of the GLD backtest. Answers:
  1. How many shorts vs longs is the engine firing?
  2. What's the WR / PF per side?
  3. Which trades died in 2026 YTD and why?
  4. What does the equity look like per strategy (pullback vs trend_carry)?
"""
from __future__ import annotations
import warnings
import pandas as pd

warnings.filterwarnings("ignore")
import logging; logging.getLogger("statsmodels").setLevel(logging.ERROR)
try:
    from statsmodels.tools.sm_exceptions import ConvergenceWarning
    warnings.simplefilter("ignore", ConvergenceWarning)
except Exception: pass

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec


SYMBOL = "GLD"
START_CAP = 100_000


def main() -> None:
    df = prepare_dual(load_symbol(SYMBOL))
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=SYMBOL, initial_capital=START_CAP)
    tr = bt["trades"].copy()
    tr["year"] = tr.entry_time.dt.year
    tr["is_short"] = tr.side < 0
    tr["win"] = tr.pnl > 0

    print(f"\nGLD trade audit — {len(tr)} total trades\n")

    # ---- Long vs Short overall ----
    print("="*70)
    print("LONG vs SHORT — overall")
    print("="*70)
    for side_label, mask in [("LONG", ~tr.is_short), ("SHORT", tr.is_short)]:
        sub = tr[mask]
        n = len(sub)
        if n == 0:
            print(f"  {side_label}: 0 trades")
            continue
        wr = sub.win.mean()
        pnl = sub.pnl.sum()
        avg = sub.pnl.mean()
        pf = sub[sub.pnl>0].pnl.sum() / max(1e-9, -sub[sub.pnl<0].pnl.sum())
        print(f"  {side_label:<6}  n={n:>3}  WR={wr*100:>5.1f}%  "
              f"PF={pf:>5.2f}  Σpnl=${pnl:>+10,.0f}  avg=${avg:>+8,.0f}")

    # ---- Long vs Short per year ----
    print("\n" + "="*70)
    print("LONG vs SHORT — per year")
    print("="*70)
    for yr in sorted(tr.year.unique()):
        sub_yr = tr[tr.year == yr]
        print(f"\n{yr}:")
        for side_label, mask in [("LONG", ~sub_yr.is_short), ("SHORT", sub_yr.is_short)]:
            sub = sub_yr[mask]
            n = len(sub)
            if n == 0:
                print(f"  {side_label:<6}  no trades")
                continue
            wr = sub.win.mean()
            pnl = sub.pnl.sum()
            pf = (sub[sub.pnl>0].pnl.sum() /
                  max(1e-9, -sub[sub.pnl<0].pnl.sum())) if (sub.pnl<0).any() else float("inf")
            print(f"  {side_label:<6}  n={n:>3}  WR={wr*100:>5.1f}%  "
                  f"PF={pf:>5.2f}  Σpnl=${pnl:>+10,.0f}")

    # ---- Strategy breakdown overall ----
    print("\n" + "="*70)
    print("PULLBACK vs TREND_CARRY — overall")
    print("="*70)
    for strat in tr.strategy.unique():
        sub = tr[tr.strategy == strat]
        wr = sub.win.mean()
        pnl = sub.pnl.sum()
        pf = (sub[sub.pnl>0].pnl.sum() /
              max(1e-9, -sub[sub.pnl<0].pnl.sum())) if (sub.pnl<0).any() else float("inf")
        n_long = (~sub.is_short).sum()
        n_short = sub.is_short.sum()
        print(f"  {strat:<14}  n={len(sub):>3} (L={n_long}, S={n_short})  "
              f"WR={wr*100:>5.1f}%  PF={pf:>5.2f}  Σpnl=${pnl:>+10,.0f}")

    # ---- 2026 deep dive ----
    if 2026 in tr.year.unique():
        print("\n" + "="*70)
        print("2026 YTD — every trade")
        print("="*70)
        sub = tr[tr.year == 2026].sort_values("entry_time")
        for _, row in sub.iterrows():
            side = "SHORT" if row.is_short else "LONG "
            tag = "✓" if row.win else "✗"
            print(f"  {tag} {row.entry_time.strftime('%Y-%m-%d %H:%M')}  {side}  "
                  f"{row.strategy:<12}  pnl=${row.pnl:>+8,.0f}  "
                  f"entry=${row.entry_price:>7,.2f}  exit=${row.exit_price:>7,.2f}")


if __name__ == "__main__":
    main()
