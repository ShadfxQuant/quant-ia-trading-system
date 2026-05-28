"""
GLD backtest — same engine, but exclude 2025 entirely.

2025 was a +152% / 95% WR year on GLD because gold ran on Russia/banking/
Fed-pivot tailwinds. Including it makes any strategy look brilliant. Real
question: does the engine survive the ordinary years (2023 partial, 2024,
2026 YTD) without the boom?

Method: run the full backtest, then walk the trade log chronologically
EXCLUDING any trade whose entry was in 2025. Compound from $100K through
the remaining trades. Result is the honest non-boom equity curve.
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


SYMBOL = "PAXGUSDT"
START_CAP = 100_000
EXCLUDE_YEAR = 2025


def main() -> None:
    from core.regime_filter import apply_regime_filter
    df = apply_regime_filter(prepare_dual(load_symbol(SYMBOL)), SYMBOL)
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=SYMBOL, initial_capital=START_CAP)
    tr = bt["trades"].copy().sort_values("entry_time")
    tr["year"] = tr.entry_time.dt.year

    # ---- Simulate same trades, but skip the excluded year ----
    # The actual portfolio backtester does position sizing % of equity, so the
    # cleanest approximation: compute per-trade return-on-equity from the real
    # backtest (pnl / equity_at_entry) and re-apply chronologically.
    eq = bt["equity_curve"]
    tr["eq_at_entry"] = tr.entry_time.map(lambda t: eq.asof(t))
    tr["ret"] = tr.pnl / tr.eq_at_entry
    tr_kept = tr[tr.year != EXCLUDE_YEAR].copy()

    cap = START_CAP
    eq_kept = [(tr_kept.entry_time.min() if len(tr_kept) else eq.index[0], cap)]
    for _, row in tr_kept.iterrows():
        cap = cap * (1.0 + row.ret)
        eq_kept.append((row.exit_time, cap))
    eq_kept = pd.Series(dict(eq_kept)).sort_index()

    final = float(eq_kept.iloc[-1])
    total_ret = final / START_CAP - 1
    n_total = len(tr)
    n_kept = len(tr_kept)
    days = (eq_kept.index[-1] - eq_kept.index[0]).days
    yrs = max(days, 1) / 365.25
    cagr = (final / START_CAP) ** (1 / yrs) - 1 if final > 0 else -1
    dd = ((eq_kept / eq_kept.cummax()) - 1).min()
    pf = (tr_kept[tr_kept.pnl > 0].pnl.sum() /
          max(1e-9, -tr_kept[tr_kept.pnl < 0].pnl.sum())) if (tr_kept.pnl < 0).any() else float("inf")
    wr = float((tr_kept.pnl > 0).mean()) if len(tr_kept) else 0.0

    print(f"\n{'='*72}")
    print(f"GLD backtest — EXCLUDING {EXCLUDE_YEAR}  (no-boom-year honest read)")
    print(f"{'='*72}")
    print(f"Original window:    {bt['equity_curve'].index[0].date()} → {bt['equity_curve'].index[-1].date()}")
    print(f"Trades original:    {n_total}")
    print(f"Trades after skip:  {n_kept}  ({n_total - n_kept} from {EXCLUDE_YEAR} dropped)")
    print(f"Effective span:     {days} days ({yrs:.2f} years)")
    print()
    print(f"  Starting capital:  ${START_CAP:>10,}")
    print(f"  Ending capital:    ${final:>10,.0f}")
    print(f"  Total return:      {total_ret*100:>10.1f}%")
    print(f"  CAGR:              {cagr*100:>10.1f}%")
    print(f"  Max drawdown:      {abs(dd)*100:>10.1f}%")
    print(f"  Profit factor:     {pf:>10.2f}")
    print(f"  Win rate:          {wr*100:>10.1f}%")
    print(f"  Worst trade:       ${tr_kept.pnl.min() if n_kept else 0:>10,.0f}")
    print(f"  Best trade:        ${tr_kept.pnl.max() if n_kept else 0:>10,.0f}")
    print(f"  Avg trade PnL:     ${tr_kept.pnl.mean() if n_kept else 0:>10,.0f}")

    print(f"\n{'─'*72}\nYear-by-year (excluding {EXCLUDE_YEAR})\n{'─'*72}")
    print(f"{'Year':<8}{'Trades':>8}{'WR':>8}{'PF':>8}{'Σpnl':>14}")
    print("-" * 50)
    for yr in sorted(tr_kept.year.unique()):
        sub = tr_kept[tr_kept.year == yr]
        n = len(sub)
        wr_y = sub.win.mean() if "win" in sub.columns else (sub.pnl > 0).mean()
        pnl_y = sub.pnl.sum()
        pf_y = (sub[sub.pnl>0].pnl.sum() /
                max(1e-9, -sub[sub.pnl<0].pnl.sum())) if (sub.pnl<0).any() else float("inf")
        print(f"{yr:<8}{n:>8d}{wr_y*100:>7.1f}%{pf_y:>8.2f}{pnl_y:>+13,.0f}")


if __name__ == "__main__":
    main()
