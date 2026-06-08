"""
FINAL SIMULATION — definitive MC stamp on the production model shipped 2026-06-02.

10,000 bootstrap paths per symbol + combined portfolio. Projects each path
across three horizons (1-year, 3-year, 5-year) by drawing trades at the
realized per-year rate and reapplying compound growth. Reports expected
wealth, distribution tails, and probability of hitting key milestones.

Also runs leverage-sensitivity (1×, 1.5×, 2× notional scaling) on the
combined portfolio to map P(ruin) vs CAGR uplift.

This is the file to re-run any time the live config changes. It is the
authoritative number for "what is the model expected to deliver."
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

N_PATHS = 10_000
INITIAL = 100_000.0
SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
# Proxy-signal architecture (Part 8.22 - IWM dropped, QQQ→^NDX swap):
# signals computed on these tickers, executed on MT5 as:
#   SPY→US500 · ^NDX→US100 · GLD→XAUUSD · GC=F→XAUUSD (cross)
HORIZONS_YEARS = [1, 3, 5]
LEVERAGES = [1.0, 1.5, 2.0, 2.5]
RNG = np.random.default_rng(2026)


def trades_for(symbol):
    df = prepare_dual(load_symbol(symbol))
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    tr = bt["trades"].copy()
    eq = INITIAL; rets = []
    for p in tr["pnl"]:
        rets.append(p / eq); eq += p
    tr["ret"] = rets
    tr["symbol"] = symbol
    return tr


def mc_horizon(rets, days_realized, target_years, n_paths=N_PATHS,
               leverage=1.0):
    """Bootstrap to a target horizon by drawing trades at the realized rate."""
    rets = np.asarray(rets, dtype=float) * leverage  # naive leverage scaling
    n_realized = len(rets)
    trades_per_year = n_realized / max(days_realized / 365.25, 0.1)
    n_target = max(1, int(round(trades_per_year * target_years)))
    idx = RNG.integers(0, n_realized, size=(n_paths, n_target))
    sampled = rets[idx]
    # cap per-trade return floor at -0.95 to avoid mathematical ruin from leverage
    sampled = np.clip(sampled, -0.95, None)
    eq = INITIAL * np.cumprod(1.0 + sampled, axis=1)
    final = eq[:, -1]
    rmax = np.maximum.accumulate(eq, axis=1)
    dd = ((eq - rmax) / rmax).min(axis=1)
    cagr = (final / INITIAL) ** (1.0 / target_years) - 1.0
    return {
        "n_trades": n_target,
        "final_mean": float(final.mean()),
        "final_p5":   float(np.quantile(final, 0.05)),
        "final_p25":  float(np.quantile(final, 0.25)),
        "final_p50":  float(np.quantile(final, 0.50)),
        "final_p75":  float(np.quantile(final, 0.75)),
        "final_p95":  float(np.quantile(final, 0.95)),
        "cagr_p5":   float(np.quantile(cagr, 0.05)),
        "cagr_p50":  float(np.quantile(cagr, 0.50)),
        "cagr_p95":  float(np.quantile(cagr, 0.95)),
        "dd_p5":     float(np.quantile(dd, 0.05)),
        "dd_p50":    float(np.quantile(dd, 0.50)),
        "p_loss":    float((final < INITIAL).mean()),
        "p_double":  float((final > 2*INITIAL).mean()),
        "p_5x":      float((final > 5*INITIAL).mean()),
        "p_10x":     float((final > 10*INITIAL).mean()),
        "p_ruin":    float((final < 0.5*INITIAL).mean()),
    }


def realized(tr):
    eq = INITIAL; peak = INITIAL; dd_min = 0.0
    for r in tr["ret"]:
        eq *= (1.0 + r)
        peak = max(peak, eq); dd_min = min(dd_min, (eq-peak)/peak)
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    return eq, dd_min, days


def main():
    print("\n" + "="*100)
    print("  FINAL SIMULATION — production model 2026-06-02")
    print(f"  {N_PATHS:,} bootstrap paths/horizon, 3 horizons × {len(LEVERAGES)} leverage levels")
    print("="*100)

    # Collect baseline trades
    per_sym = {}
    for s in SYMBOLS:
        tr = trades_for(s)
        per_sym[s] = tr
    combined = pd.concat(per_sym.values(), ignore_index=True).sort_values("entry_time")
    eq = INITIAL; rets = []
    for p in combined["pnl"]:
        rets.append(p / eq); eq += p
    combined["ret"] = rets

    # ---- Realized window numbers ----
    print(f"\n  ── REALIZED (in-sample window) ──")
    print(f"  {'symbol':<10}{'final $':>15}{'profit $':>15}{'CAGR':>10}"
          f"{'max DD':>10}{'WR':>8}{'n trades':>10}")
    print("  " + "-"*80)
    for s, tr in per_sym.items():
        f, dd, d = realized(tr)
        wr = (tr["pnl"] > 0).mean() * 100
        years = d/365.25
        cagr = (f/INITIAL)**(1/max(years,0.1))-1
        print(f"  {s:<10}{f:>14,.0f}{f-INITIAL:>+14,.0f}{cagr*100:>+9.1f}%"
              f"{dd*100:>+9.1f}%{wr:>7.1f}%{len(tr):>10}")
    f, dd, d = realized(combined)
    wr = (combined["pnl"] > 0).mean() * 100
    years = d/365.25
    cagr = (f/INITIAL)**(1/max(years,0.1))-1
    print(f"  {'COMBINED':<10}{f:>14,.0f}{f-INITIAL:>+14,.0f}{cagr*100:>+9.1f}%"
          f"{dd*100:>+9.1f}%{wr:>7.1f}%{len(combined):>10}")
    print(f"  Window: {d} days ({years:.2f} years)  ·  Final PROFIT: ${f-INITIAL:+,.0f}")

    # ---- Forward projection at 1×, 3 horizons ----
    print(f"\n  ── FORWARD MC (1× leverage, combined portfolio) ──")
    print(f"  {'horizon':>10}{'mean $':>14}{'p5 $':>12}{'p50 $':>12}{'p95 $':>14}"
          f"{'p5 CAGR':>11}{'p50 CAGR':>11}{'P(2×)':>9}{'P(5×)':>9}{'P(ruin)':>10}")
    print("  " + "-"*120)
    days_r = (combined["exit_time"].max() - combined["entry_time"].min()).days
    for h in HORIZONS_YEARS:
        m = mc_horizon(combined["ret"].values, days_r, h, leverage=1.0)
        print(f"  {h:>9}y{m['final_mean']:>+13,.0f}{m['final_p5']:>+11,.0f}"
              f"{m['final_p50']:>+11,.0f}{m['final_p95']:>+13,.0f}"
              f"{m['cagr_p5']*100:>+10.1f}%{m['cagr_p50']*100:>+10.1f}%"
              f"{m['p_double']*100:>8.1f}%{m['p_5x']*100:>8.1f}%"
              f"{m['p_ruin']*100:>9.2f}%")

    # ---- Per-symbol 3-year MC ----
    print(f"\n  ── 3-YEAR FORWARD MC, per symbol (1× leverage) ──")
    print(f"  {'symbol':<10}{'p5 CAGR':>11}{'p50 CAGR':>11}{'p95 CAGR':>11}"
          f"{'p5 DD':>10}{'P(loss)':>10}{'P(2×)':>9}{'P(5×)':>9}")
    print("  " + "-"*88)
    for s, tr in per_sym.items():
        d = (tr["exit_time"].max() - tr["entry_time"].min()).days
        m = mc_horizon(tr["ret"].values, d, 3, leverage=1.0)
        print(f"  {s:<10}{m['cagr_p5']*100:>+10.1f}%{m['cagr_p50']*100:>+10.1f}%"
              f"{m['cagr_p95']*100:>+10.1f}%{m['dd_p5']*100:>+9.1f}%"
              f"{m['p_loss']*100:>9.1f}%{m['p_double']*100:>8.1f}%{m['p_5x']*100:>8.1f}%")

    # ---- Leverage sensitivity (3-year horizon, combined) ----
    print(f"\n  ── LEVERAGE SENSITIVITY (3-year horizon, combined portfolio) ──")
    print(f"  {'lev':>6}{'p5 CAGR':>11}{'p50 CAGR':>11}{'p95 CAGR':>11}"
          f"{'p5 DD':>10}{'P(loss)':>10}{'P(2×)':>9}{'P(5×)':>9}{'P(ruin)':>10}")
    print("  " + "-"*98)
    for lev in LEVERAGES:
        m = mc_horizon(combined["ret"].values, days_r, 3, leverage=lev)
        print(f"  {lev:>5.1f}×{m['cagr_p5']*100:>+10.1f}%{m['cagr_p50']*100:>+10.1f}%"
              f"{m['cagr_p95']*100:>+10.1f}%{m['dd_p5']*100:>+9.1f}%"
              f"{m['p_loss']*100:>9.1f}%{m['p_double']*100:>8.1f}%"
              f"{m['p_5x']*100:>8.1f}%{m['p_ruin']*100:>9.2f}%")

    # ---- Headline summary ----
    print(f"\n{'='*100}")
    print(f"  HEADLINE NUMBERS")
    print("="*100)
    m_3y = mc_horizon(combined["ret"].values, days_r, 3, leverage=1.0)
    print(f"  Realized in-sample profit: ${f-INITIAL:+,.0f} on $100K → ${f:,.0f}")
    print(f"  3-year forward expected wealth (mean): ${m_3y['final_mean']:,.0f}")
    print(f"  3-year p50 wealth:                     ${m_3y['final_p50']:,.0f}")
    print(f"  3-year p5  wealth:                     ${m_3y['final_p5']:,.0f}")
    print(f"  3-year p95 wealth:                     ${m_3y['final_p95']:,.0f}")
    print(f"  3-year P(double 2×):                   {m_3y['p_double']*100:.1f}%")
    print(f"  3-year P(5×):                          {m_3y['p_5x']*100:.1f}%")
    print(f"  3-year P(10×):                         {m_3y['p_10x']*100:.1f}%")
    print(f"  3-year P(any loss):                    {m_3y['p_loss']*100:.1f}%")
    print(f"  3-year P(ruin -50%):                   {m_3y['p_ruin']*100:.2f}%")


if __name__ == "__main__":
    main()
