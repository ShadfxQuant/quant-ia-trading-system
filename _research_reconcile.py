"""
SESSION_LOG #22 — Reconcile the #21 production config with the session's
validated findings: re-bind the HMM meta-layer (#6/#7) and re-activate
mean_reversion_extremes on IWM (#8) on top of the #21 engine.

Configs
  A  #21 baseline replicated         (pullback HMM OFF, SPY+DIA)
  B  #21 + HMM meta-layer            (pullback HMM ON,  SPY+DIA)
  C  #21 + IWM meanrev, no HMM       (pullback HMM OFF, SPY+DIA, IWM meanrev HMM OFF)
  D  #21 + HMM meta + IWM meanrev    (the stack)

Window: SPY/DIA/IWM 1h, yfinance max (~147.7 wks). Sharpe-weighted book,
weights = intraday-bar Sharpe (same convention #21 was verified under, so A
must reproduce ≈ $221,244 / 12.9% DD).
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY, MEANREV
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import generate_signals as pullback_signals, exit_profile_for as pb_exit
from strategies.trend_carry import generate_signals as tc_signals, exit_profile_for as tc_exit
from strategies.mean_reversion_extremes import generate_signals as mr_signals, exit_profile_for as mr_exit
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_intraday_bar, sharpe_daily, max_drawdown

START = 100_000.0


def _prep(symbol):
    df = prepare_dual(load_symbol(symbol))      # attaches HMM P_bull etc.
    return df


def _run(symbol, df, specs):
    bt = run_portfolio(df, specs, symbol=symbol)
    return bt


def _book(per):
    """Sharpe-weight per-symbol equity curves (weights = intraday-bar Sharpe)."""
    w = {s: max(sharpe_intraday_bar(p["equity"]), 0.05) for s, p in per.items()}
    tot = sum(w.values())
    w = {k: v / tot for k, v in w.items()}
    idx = sorted(set().union(*[p["equity"].index for p in per.values()]))
    a = pd.DataFrame(index=pd.DatetimeIndex(idx))
    for s, p in per.items():
        eqn = p["equity"] / p["equity"].iloc[0]
        a[s] = eqn.reindex(a.index).ffill().bfill()
    port = sum(a[s] * w[s] * START for s in a.columns)
    port.iloc[0] = START
    days = (port.index[-1] - port.index[0]).days
    cagr = (port.iloc[-1] / START) ** (365.25 / max(days, 1)) - 1
    dd = max_drawdown(port)
    return {
        "Final$": float(port.iloc[-1]),
        "PnL$": float(port.iloc[-1] - START),
        "CAGR_pct": cagr * 100,
        "DD_pct": dd * 100,
        "Sharpe_intraday": sharpe_intraday_bar(port),
        "Sharpe_daily": sharpe_daily(port),
        "MAR": (cagr / dd) if dd > 0 else float("inf"),
        "weights": w,
    }


def _pf(trades, strat=None):
    t = trades if strat is None else trades[trades["strategy"] == strat]
    if t.empty or not (t.pnl < 0).any():
        return float("inf")
    return float(t.loc[t.pnl > 0, "pnl"].sum() / -t.loc[t.pnl < 0, "pnl"].sum())


def _daily_pnl(curve):
    """Daily PnL increments from a cumulative (realized+mtm) strategy curve."""
    return curve.diff().resample("1D").sum().dropna()


def run_config(label, hmm_pullback, with_iwm, hmm_meanrev, prepped, diag=False):
    PULLBACK.use_hmm_meta = hmm_pullback
    MEANREV.use_hmm_meta = hmm_meanrev

    specs_eq = lambda: [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ]
    per = {}
    strat_curves = {}
    pf_rows = {}
    for sym in ("SPY", "DIA"):
        df = prepped[sym].copy()
        df = pullback_signals(df, PULLBACK)
        df = tc_signals(df, TRENDCARRY)
        bt = _run(sym, df, specs_eq())
        per[sym] = {"equity": bt["equity_curve"]}
        strat_curves[sym] = bt["strategy_curves"]
        pf_rows[sym] = _pf(bt["trades"])
        if diag and sym == "SPY":
            sig = df[df["pullback_Signal"] != 0]
            n = len(sig)
            b = sig["pullback_HmmBucket"].value_counts().to_dict()
            print(f"   [diag SPY] signal bars={n}  "
                  f"low(0.5x)={b.get(-1,0)} ({b.get(-1,0)/max(n,1):.0%})  "
                  f"normal(1.0x)={b.get(0,0)} ({b.get(0,0)/max(n,1):.0%})  "
                  f"high(2.0x)={b.get(1,0)} ({b.get(1,0)/max(n,1):.0%})")

    if with_iwm:
        df = prepped["IWM"].copy()
        df = mr_signals(df, MEANREV)
        bt = _run("IWM", df, [StrategySpec("meanrev", MEANREV, mr_exit())])
        per["IWM"] = {"equity": bt["equity_curve"]}
        strat_curves["IWM"] = bt["strategy_curves"]
        pf_rows["IWM(mr)"] = _pf(bt["trades"])

    m = _book(per)

    # Inter-strategy correlation: pullback (SPY+DIA) vs meanrev (IWM).
    rho = float("nan")
    if with_iwm:
        pb = (_daily_pnl(strat_curves["SPY"]["pullback"]) +
              _daily_pnl(strat_curves["DIA"]["pullback"]).reindex(
                  _daily_pnl(strat_curves["SPY"]["pullback"]).index).fillna(0.0))
        mr = _daily_pnl(strat_curves["IWM"]["meanrev"])
        j = pd.concat([pb, mr], axis=1).dropna()
        if len(j) > 5 and j.iloc[:, 0].std() and j.iloc[:, 1].std():
            rho = float(j.iloc[:, 0].corr(j.iloc[:, 1]))

    print(f"\n=== {label} ===")
    print(f"  Final ${m['Final$']:,.0f}  PnL ${m['PnL$']:,.0f}  CAGR {m['CAGR_pct']:.1f}%  "
          f"DD {m['DD_pct']:.2f}%  MAR {m['MAR']:.2f}")
    print(f"  Sharpe intraday={m['Sharpe_intraday']:.3f}  daily={m['Sharpe_daily']:.3f}")
    print(f"  Weights: " + ", ".join(f"{k}={v:.1%}" for k, v in m['weights'].items()))
    print(f"  PF: " + ", ".join(f"{k}={v:.2f}" for k, v in pf_rows.items()))
    if with_iwm:
        print(f"  inter-strategy rho (pullback vs meanrev, daily PnL): {rho:.3f}")
    m["rho"] = rho
    m["pf"] = pf_rows
    return m


def main():
    warnings.filterwarnings("ignore")
    print("Preparing data (SPY, DIA, IWM)...")
    prepped = {s: _prep(s) for s in ("SPY", "DIA", "IWM")}

    results = {}
    results["A"] = run_config("A  #21 baseline (pullback HMM OFF)",
                              False, False, False, prepped)
    results["B"] = run_config("B  #21 + HMM meta-layer (pullback HMM ON)",
                              True, False, False, prepped, diag=True)
    results["C"] = run_config("C  #21 + IWM meanrev, no HMM",
                              False, True, False, prepped)
    results["D"] = run_config("D  #21 + HMM meta + IWM meanrev (the stack)",
                              True, True, True, prepped, diag=True)

    print("\n================ SUMMARY ================")
    print(f"{'cfg':<4}{'Final$':>12}{'CAGR%':>8}{'DD%':>8}"
          f"{'Sh_intr':>9}{'Sh_day':>8}{'MAR':>6}{'rho':>7}")
    for k, m in results.items():
        print(f"{k:<4}{m['Final$']:>12,.0f}{m['CAGR_pct']:>8.1f}{m['DD_pct']:>8.2f}"
              f"{m['Sharpe_intraday']:>9.3f}{m['Sharpe_daily']:>8.3f}"
              f"{m['MAR']:>6.2f}{m.get('rho', float('nan')):>7.3f}")

    print("\n--- Success criteria (config D) ---")
    d = results["D"]
    ok_eq = d["Final$"] >= 215_000
    ok_dd = d["DD_pct"] <= 11.0
    ok_sh = d["Sharpe_daily"] >= 0.60
    ok_rho = (not np.isnan(d["rho"])) and d["rho"] <= 0.15
    print(f"  Final ≥ $215K     : {d['Final$']:,.0f}   {'PASS' if ok_eq else 'FAIL'}")
    print(f"  DD ≤ 11.0%        : {d['DD_pct']:.2f}%   {'PASS' if ok_dd else 'FAIL'}")
    print(f"  Sharpe_daily ≥0.60: {d['Sharpe_daily']:.3f}   {'PASS' if ok_sh else 'FAIL'}")
    print(f"  rho ≤ 0.15        : {d['rho']:.3f}   {'PASS' if ok_rho else 'FAIL'}")
    print(f"  >>> config D {'MEETS ALL' if all([ok_eq,ok_dd,ok_sh,ok_rho]) else 'DOES NOT meet all'}")


if __name__ == "__main__":
    main()
