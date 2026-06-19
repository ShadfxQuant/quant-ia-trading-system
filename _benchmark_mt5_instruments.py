"""
Benchmark vs buy-&-hold for the user's ACTUAL traded instruments (Part 8.38).

User trades on TradingView/MT5:
  - S&P 500  (US500 / SPX)  → proxy ^GSPC (TradingView's SP500 cash index)
  - XAUUSD   (spot gold)    → proxy GLD (ETF tracks spot gold; % moves match
                              XAUUSD, unlike GC=F futures which carry roll)

For each: production engine vs buy-&-hold, plus the leverage/risk-matched
view (they trade CFDs with leverage) and a realistic-friction haircut.
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
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

INITIAL = 100_000.0
# (proxy, label the user sees)
INSTRUMENTS = [("^GSPC", "US500 / S&P 500"), ("GLD", "XAUUSD / spot gold")]
FRICTION_BP = 10   # typical MT5 CFD round-trip


def daily_equity(trades, idx, friction_bp=0):
    if len(trades) == 0:
        return pd.Series(INITIAL, index=idx)
    tr = trades.copy()
    pnl = tr["pnl"].astype(float)
    if friction_bp:
        pnl = pnl - (friction_bp / 10000.0) * INITIAL * 0.30  # ~30% notional per trade
    by_day = pnl.groupby(pd.to_datetime(tr["exit_time"]).dt.normalize()).sum()
    cum = INITIAL + by_day.cumsum()
    return cum.reindex(idx, method="ffill").fillna(INITIAL)


def m(eq):
    eq = eq.dropna()
    total = eq.iloc[-1] / INITIAL - 1
    yrs = max((eq.index[-1] - eq.index[0]).days / 365.25, 0.1)
    cagr = (eq.iloc[-1] / INITIAL) ** (1 / yrs) - 1
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    r = eq.pct_change().dropna()
    sharpe = r.mean() / r.std() * np.sqrt(252) if r.std() > 0 else 0
    return {"total": total, "cagr": cagr, "maxdd": dd, "sharpe": sharpe,
            "calmar": cagr / abs(dd) if dd else 0}


def main():
    print("="*92)
    print("  YOUR INSTRUMENTS — engine vs buy-&-hold  (Part 8.38)")
    print("="*92)
    out = {"instruments": {}}
    for sym, label in INSTRUMENTS:
        raw = load_symbol(sym)
        df = prepare_dual(raw)
        cfg = get_pullback_cfg(sym)
        trades = run_portfolio(df, [
            StrategySpec("pullback", cfg, pb_exit(cfg)),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=sym, initial_capital=INITIAL)["trades"]

        t0 = pd.to_datetime(trades["entry_time"]).min().normalize()
        t1 = pd.to_datetime(trades["exit_time"]).max().normalize()
        idx = pd.date_range(t0, t1, freq="D")

        eq = daily_equity(trades, idx)
        eq_fr = daily_equity(trades, idx, FRICTION_BP)
        price = raw["Close"].resample("1D").last().reindex(idx, method="ffill")
        bh = INITIAL * price / price.dropna().iloc[0]

        me, mf, mb = m(eq), m(eq_fr), m(bh)
        # leverage to match buy-hold drawdown
        lev = abs(mb["maxdd"]) / abs(me["maxdd"]) if me["maxdd"] else 1
        lev_return = me["total"] * lev

        def ds(s, n=90):
            s = s.dropna()
            if len(s) <= n: return [round(x) for x in s.tolist()]
            st = len(s)/n
            return [round(s.iloc[min(int(i*st), len(s)-1)]) for i in range(n)]

        out["instruments"][label] = {
            "sym": sym, "window": [str(t0.date()), str(t1.date())],
            "eng": me, "eng_fr": mf, "bh": mb, "lev": lev, "lev_return": lev_return,
            "eng_curve": ds(eq), "bh_curve": ds(bh),
        }

        print(f"\n  ── {label}  ({sym},  {t0.date()} → {t1.date()}) ──")
        print(f"  {'':<22}{'TotalRet':>10}{'CAGR':>9}{'MaxDD':>9}{'Sharpe':>8}{'Calmar':>8}")
        print("  " + "-"*66)
        print(f"  {'Buy & hold':<22}{mb['total']*100:>+9.1f}%{mb['cagr']*100:>+8.1f}%{mb['maxdd']*100:>+8.1f}%{mb['sharpe']:>8.2f}{mb['calmar']:>8.2f}")
        print(f"  {'Engine (ideal)':<22}{me['total']*100:>+9.1f}%{me['cagr']*100:>+8.1f}%{me['maxdd']*100:>+8.1f}%{me['sharpe']:>8.2f}{me['calmar']:>8.2f}")
        print(f"  {'Engine (10bp friction)':<22}{mf['total']*100:>+9.1f}%{mf['cagr']*100:>+8.1f}%{mf['maxdd']*100:>+8.1f}%{mf['sharpe']:>8.2f}{mf['calmar']:>8.2f}")
        print(f"  → Engine beats hold on raw return by {(me['total']-mb['total'])*100:+.1f}pp "
              f"with {abs(mb['maxdd']/me['maxdd']):.1f}× shallower drawdown")
        print(f"  → Risk-matched (lever {lev:.1f}× to hold's DD): engine ~{lev_return*100:+.0f}%  vs hold {mb['total']*100:+.0f}%")

    os.makedirs("research/results", exist_ok=True)
    json.dump(out, open("research/results/mt5_bench.json", "w"), indent=2, default=str)
    print("\n  wrote → research/results/mt5_bench.json")


if __name__ == "__main__":
    main()
