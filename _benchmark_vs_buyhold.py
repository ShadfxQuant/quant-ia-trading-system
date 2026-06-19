"""
Benchmark both engines vs buy-&-hold the underlying (Part 8.37).

User: compare growth of both models to the actual movement of the stock to
see if we're underperforming.

For each symbol, on a common DAILY calendar axis over the strategy's active
window: production equity, VWAP-challenger equity, and buy-&-hold $100k in the
underlying. Reports total return, CAGR, MaxDD, and — crucially — exposure-
adjusted context (the strategies are NOT 100% invested, so raw return
understates their capital efficiency).
"""
from __future__ import annotations
import warnings, logging, json, os
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd

from config.settings import TRENDCARRY, get_pullback_cfg
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.pullback_vwap import generate_signals as vwap_generate, exit_profile_for as vwap_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0


def daily_equity_from_trades(trades, idx):
    """Step equity at each trade exit, forward-fill onto daily index `idx`."""
    eq = pd.Series(INITIAL, index=idx, dtype=float)
    if len(trades) == 0:
        return eq
    tr = trades.sort_values("exit_time")
    steps = pd.Series(0.0, index=idx)
    running = INITIAL
    # build a step series keyed by exit date
    by_day = tr.groupby(pd.to_datetime(tr["exit_time"]).dt.normalize())["pnl"].sum()
    cum = INITIAL + by_day.cumsum()
    cum = cum.reindex(idx, method="ffill").fillna(INITIAL)
    return cum


def curve_metrics(eq):
    eq = eq.dropna()
    total = eq.iloc[-1] / INITIAL - 1
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    cagr = (eq.iloc[-1] / INITIAL) ** (1 / max(years, 0.1)) - 1
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    # daily sharpe
    r = eq.pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    calmar = cagr / abs(dd) if dd != 0 else 0
    return {"total": total, "cagr": cagr, "maxdd": dd, "sharpe": sharpe, "calmar": calmar}


def main():
    print("="*100)
    print("  BOTH ENGINES vs BUY-&-HOLD THE UNDERLYING (Part 8.37)")
    print("="*100)

    out = {"symbols": {}}
    for sym in SYMBOLS:
        raw = load_symbol(sym)
        df = prepare_dual(raw)
        pcfg = get_pullback_cfg(sym)

        prod = run_portfolio(df, [
            StrategySpec("pullback", pcfg, pb_exit(pcfg)),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=sym, initial_capital=INITIAL)["trades"]

        df2 = vwap_generate(df, pcfg)
        chal = run_portfolio(df2, [
            StrategySpec("pullback_vwap", pcfg, vwap_exit(pcfg)),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=sym, initial_capital=INITIAL)["trades"]

        # common daily window = first entry to last exit across both
        starts = [pd.to_datetime(prod["entry_time"]).min(), pd.to_datetime(chal["entry_time"]).min()]
        ends   = [pd.to_datetime(prod["exit_time"]).max(),  pd.to_datetime(chal["exit_time"]).max()]
        t0, t1 = min(starts), max(ends)
        idx = pd.date_range(t0.normalize(), t1.normalize(), freq="D")

        eq_prod = daily_equity_from_trades(prod, idx)
        eq_chal = daily_equity_from_trades(chal, idx)

        # buy & hold the underlying over same window
        price = raw["Close"].resample("1D").last().reindex(idx, method="ffill")
        price = price.loc[idx]
        bh = INITIAL * price / price.dropna().iloc[0]

        mp = curve_metrics(eq_prod); mc = curve_metrics(eq_chal); mb = curve_metrics(bh)

        # downsample curves for the dashboard
        def ds(s, n=90):
            s = s.dropna()
            if len(s) <= n: return [round(x) for x in s.tolist()]
            step = len(s) / n
            return [round(s.iloc[min(int(i*step), len(s)-1)]) for i in range(n)]

        out["symbols"][sym] = {
            "prod": mp, "chal": mc, "bh": mb,
            "prod_curve": ds(eq_prod), "chal_curve": ds(eq_chal), "bh_curve": ds(bh),
            "window": [str(t0.date()), str(t1.date())],
        }

        print(f"\n  ── {sym}  ({t0.date()} → {t1.date()}) ──")
        print(f"  {'':<16}{'TotalRet':>10}{'CAGR':>9}{'MaxDD':>9}{'Sharpe':>8}{'Calmar':>8}")
        print("  " + "-"*60)
        for name, m in [("Buy & Hold", mb), ("Production", mp), ("VWAP", mc)]:
            print(f"  {name:<16}{m['total']*100:>+9.1f}%{m['cagr']*100:>+8.1f}%"
                  f"{m['maxdd']*100:>+8.1f}%{m['sharpe']:>8.2f}{m['calmar']:>8.2f}")
        # verdict on raw return
        beats_bh = mp["total"] > mb["total"]
        print(f"  → Production {'BEATS' if beats_bh else 'LAGS'} buy-&-hold on raw return "
              f"({(mp['total']-mb['total'])*100:+.1f}pp), "
              f"DD {abs(mb['maxdd'])/max(abs(mp['maxdd']),1e-9):.1f}× {'shallower' if abs(mp['maxdd'])<abs(mb['maxdd']) else 'deeper'}")

    os.makedirs("research/results", exist_ok=True)
    json.dump(out, open("research/results/buyhold_bench.json", "w"), indent=2, default=str)
    print("\n  wrote → research/results/buyhold_bench.json")


if __name__ == "__main__":
    main()
