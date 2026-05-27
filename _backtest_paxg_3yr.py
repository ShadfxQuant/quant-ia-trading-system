"""
Honest 3-year backtest of PAXGUSDT with the shipped COMBO_E filter.
Reports actual equity curve, per-year breakdown, drawdown, trade stats.
No extrapolations — only what the data actually shows.
"""
from __future__ import annotations
import warnings
import numpy as np
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
from core.regime_filter import apply_regime_filter
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_daily, max_drawdown


SYMBOL = "PAXGUSDT"
START_CAP = 100_000


def run() -> None:
    print(f"\n{'='*78}")
    print(f"PAXGUSDT backtest · COMBO_E filter (ADX≥25 + skip Asia + slope persistence)")
    print(f"Engine: pullback + trend_carry · capital: ${START_CAP:,}")
    print(f"{'='*78}\n")

    df = apply_regime_filter(prepare_dual(load_symbol(SYMBOL)), SYMBOL)
    print(f"Data: {df.index[0]} → {df.index[-1]}  ({len(df):,} hourly bars)")
    days_total = (df.index[-1] - df.index[0]).days
    years_avail = days_total / 365.25
    print(f"Span: {days_total} days ({years_avail:.2f} years)\n")

    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=SYMBOL, initial_capital=START_CAP)
    eq = bt["equity_curve"]
    tr = bt["trades"]

    # ---- Overall ----
    final = float(eq.iloc[-1])
    total_ret = final / START_CAP - 1
    cagr = (final / START_CAP) ** (365.25 / max(days_total, 1)) - 1
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if (tr.pnl < 0).any() else float("inf")
    wr = float((tr.pnl > 0).mean()) if not tr.empty else 0.0
    sh = sharpe_daily(eq)

    print(f"{'─'*78}\nOVERALL RESULT\n{'─'*78}")
    print(f"  Starting capital:   ${START_CAP:>10,}")
    print(f"  Ending capital:     ${final:>10,.0f}")
    print(f"  Total return:       {total_ret*100:>10.1f}%")
    print(f"  CAGR:               {cagr*100:>10.1f}%")
    print(f"  Max drawdown:       {dd*100:>10.1f}%")
    print(f"  Profit factor:      {pf:>10.2f}")
    print(f"  Win rate:           {wr*100:>10.1f}%")
    print(f"  Sharpe (daily):     {sh:>10.2f}")
    print(f"  Total trades:       {len(tr):>10d}")
    print(f"  Avg trade PnL:      ${tr['pnl'].mean() if len(tr) else 0:>10,.0f}")
    print(f"  Best trade:         ${tr['pnl'].max() if len(tr) else 0:>10,.0f}")
    print(f"  Worst trade:        ${tr['pnl'].min() if len(tr) else 0:>10,.0f}")

    # ---- Year-by-year ----
    print(f"\n{'─'*78}\nYEAR-BY-YEAR\n{'─'*78}")
    print(f"{'Year':<8}{'Start $':>12}{'End $':>12}{'Return':>10}{'DD':>8}{'Trades':>8}{'WR':>7}")
    print("-" * 65)
    eq_by_year = eq.groupby(eq.index.year)
    cur_cap = START_CAP
    for yr, group in eq_by_year:
        y_start = float(group.iloc[0])
        y_end = float(group.iloc[-1])
        y_ret = y_end / y_start - 1
        y_dd = max_drawdown(group)
        y_tr = tr[tr.entry_time.dt.year == yr] if "entry_time" in tr.columns else tr.iloc[0:0]
        y_n = len(y_tr)
        y_wr = float((y_tr.pnl > 0).mean()) if y_n else 0.0
        print(f"{yr:<8}${y_start:>10,.0f}  ${y_end:>10,.0f}  {y_ret*100:>7.1f}%  "
              f"{y_dd*100:>6.1f}%  {y_n:>6d}  {y_wr*100:>5.0f}%")

    # ---- 3-year compounding (if data spans ≥3yr, slice to last 3yr) ----
    if years_avail >= 3.0:
        cutoff = eq.index[-1] - pd.Timedelta(days=int(365.25 * 3))
        eq3 = eq[eq.index >= cutoff]
        ret3 = float(eq3.iloc[-1] / eq3.iloc[0] - 1)
        cagr3 = (1 + ret3) ** (1 / 3) - 1
        print(f"\n{'─'*78}\nLAST 3 YEARS ONLY\n{'─'*78}")
        print(f"  Window:             {eq3.index[0].date()} → {eq3.index[-1].date()}")
        print(f"  Return:             {ret3*100:>10.1f}%")
        print(f"  CAGR:               {cagr3*100:>10.1f}%")
        print(f"  Max DD:             {max_drawdown(eq3)*100:>10.1f}%")
    else:
        print(f"\n{'─'*78}")
        print(f"Note: only {years_avail:.2f} years of PAXGUSDT data available on Binance.")
        print(f"Cannot slice a clean 3-year window — overall result above is the honest answer.")
        print(f"{'─'*78}")


if __name__ == "__main__":
    run()
