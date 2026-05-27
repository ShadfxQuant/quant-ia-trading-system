"""
Long-term validation: same pullback + trend_carry engine on GLD.

PAXG perp only goes back 2 years on Binance, so we can't backtest it
across a full cycle. GLD (SPDR Gold Shares ETF) has 20+ years of NYSE
data, prices gold within basis points of spot, and runs on NYSE hours —
i.e. no need for the 24/7 regime filter that PAXG requires.

This is the cleanest available proxy for "does the gold thesis hold
across multiple market regimes (2008 crash, 2011 gold peak, 2015 dip,
2020 COVID, 2022 hike cycle, 2024+ run)."
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
from backtest.metrics import sharpe_daily, max_drawdown


SYMBOL = "GLD"
START_CAP = 100_000


def run() -> None:
    print(f"\n{'='*78}")
    print(f"GLD long-term backtest · same pullback + trend_carry engine")
    print(f"Capital: ${START_CAP:,}  ·  No regime filter (NYSE-hours data is clean)")
    print(f"{'='*78}\n")

    df = prepare_dual(load_symbol(SYMBOL))
    print(f"Data: {df.index[0]} → {df.index[-1]}  ({len(df):,} bars)")
    days_total = (df.index[-1] - df.index[0]).days
    years_avail = days_total / 365.25
    print(f"Span: {days_total} days ({years_avail:.2f} years)\n")

    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=SYMBOL, initial_capital=START_CAP)
    eq = bt["equity_curve"]
    tr = bt["trades"]

    final = float(eq.iloc[-1])
    total_ret = final / START_CAP - 1
    cagr = (final / START_CAP) ** (365.25 / max(days_total, 1)) - 1
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if (tr.pnl < 0).any() else float("inf")
    wr = float((tr.pnl > 0).mean()) if not tr.empty else 0.0
    sh = sharpe_daily(eq)

    print(f"{'─'*78}\nOVERALL RESULT (full available history)\n{'─'*78}")
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

    # Year-by-year
    print(f"\n{'─'*78}\nYEAR-BY-YEAR\n{'─'*78}")
    print(f"{'Year':<8}{'Start $':>14}{'End $':>14}{'Return':>10}{'DD':>8}{'Trades':>8}{'WR':>7}")
    print("-" * 70)
    for yr, group in eq.groupby(eq.index.year):
        y_start = float(group.iloc[0])
        y_end = float(group.iloc[-1])
        y_ret = y_end / y_start - 1
        y_dd = max_drawdown(group)
        y_tr = tr[tr.entry_time.dt.year == yr] if "entry_time" in tr.columns else tr.iloc[0:0]
        y_n = len(y_tr)
        y_wr = float((y_tr.pnl > 0).mean()) if y_n else 0.0
        print(f"{yr:<8}${y_start:>12,.0f}  ${y_end:>12,.0f}  {y_ret*100:>7.1f}%  "
              f"{y_dd*100:>6.1f}%  {y_n:>6d}  {y_wr*100:>5.0f}%")

    # Last 3 / 5 / 10 year windows
    for win_yrs in (3, 5, 10):
        if years_avail < win_yrs: continue
        cutoff = eq.index[-1] - pd.Timedelta(days=int(365.25 * win_yrs))
        eqw = eq[eq.index >= cutoff]
        if len(eqw) < 30: continue
        ret = float(eqw.iloc[-1] / eqw.iloc[0] - 1)
        cagrw = (1 + ret) ** (1 / win_yrs) - 1
        ddw = max_drawdown(eqw)
        print(f"\nLAST {win_yrs} YEARS  ({eqw.index[0].date()} → {eqw.index[-1].date()})")
        print(f"  Return: {ret*100:>7.1f}%   CAGR: {cagrw*100:>6.1f}%   "
              f"Max DD: {ddw*100:>5.1f}%")


if __name__ == "__main__":
    run()
