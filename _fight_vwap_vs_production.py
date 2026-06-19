"""
Head-to-head: VWAP-enhanced pullback vs production, per symbol (Part 8.36).

Both engines run through the IDENTICAL exit ladder / sizing / trend_carry
machinery. Only difference: the challenger adds VWAP-pullback entries.

Emits research/results/vwap_fight.json with per-symbol metrics + equity curves
for the dashboard.
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


def equity_and_metrics(trades):
    if len(trades) == 0:
        return None
    tr = trades.sort_values("exit_time").copy()
    eq = INITIAL; curve = []; peak = INITIAL; dd_min = 0.0
    for _, t in tr.iterrows():
        eq += float(t["pnl"]); peak = max(peak, eq)
        dd_min = min(dd_min, (eq - peak) / peak)
        curve.append({"t": str(t["exit_time"])[:10], "eq": round(eq, 0)})
    days = (tr["exit_time"].max() - tr["entry_time"].min()).days
    years = max(days / 365.25, 0.1)
    wins = tr[tr["pnl"] > 0]; losses = tr[tr["pnl"] < 0]
    pf_w = float(wins["pnl"].sum()); pf_l = float(-losses["pnl"].sum())
    pf = pf_w / pf_l if pf_l > 0 else 999.0
    cagr = (eq / INITIAL) ** (1 / years) - 1
    wr = float((tr["pnl"] > 0).mean())
    # daily-ish sharpe proxy from per-trade pnl
    rets = tr["pnl"] / INITIAL
    sharpe = (rets.mean() / rets.std() * np.sqrt(len(rets) / years)) if rets.std() > 0 else 0
    avg_win = float(wins["pnl"].mean()) if len(wins) else 0
    avg_loss = float(losses["pnl"].mean()) if len(losses) else 0
    return {
        "final_eq": round(eq, 0), "profit": round(eq - INITIAL, 0),
        "cagr": round(cagr * 100, 1), "maxdd": round(dd_min * 100, 1),
        "wr": round(wr * 100, 1), "pf": round(pf, 2), "sharpe": round(sharpe, 2),
        "n": int(len(tr)), "avg_win": round(avg_win, 0), "avg_loss": round(avg_loss, 0),
        "curve": curve,
    }


def run_production(symbol, df):
    cfg = get_pullback_cfg(symbol)
    res = run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    return res["trades"]


def run_challenger(symbol, df):
    cfg = get_pullback_cfg(symbol)
    df2 = vwap_generate(df, cfg)   # adds pullback_vwap_* columns
    res = run_portfolio(df2, [
        StrategySpec("pullback_vwap", cfg, vwap_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=INITIAL)
    return res["trades"], df2


def main():
    print("="*100)
    print("  FIGHT: VWAP-enhanced pullback  vs  PRODUCTION  (per symbol, identical exits)")
    print("="*100)

    out = {"symbols": {}, "generated_utc": pd.Timestamp.utcnow().isoformat()}
    agg_prod = {"profit": 0, "n": 0}; agg_chal = {"profit": 0, "n": 0}

    for sym in SYMBOLS:
        df = prepare_dual(load_symbol(sym))
        prod_tr = run_production(sym, df)
        chal_tr, df2 = run_challenger(sym, df)
        mp = equity_and_metrics(prod_tr)
        mc = equity_and_metrics(chal_tr)
        vwap_entries = int(df2["pullback_vwap_FromVWAP"].sum()) if "pullback_vwap_FromVWAP" in df2 else 0
        out["symbols"][sym] = {"production": mp, "challenger": mc, "vwap_extra_entries": vwap_entries}

        def winner(a, b, key, higher=True):
            if a is None or b is None: return "?"
            return "C" if ((b[key] > a[key]) == higher) else "P"

        print(f"\n  ── {sym} ──  (VWAP added {vwap_entries} entry signals)")
        print(f"  {'metric':<12}{'PRODUCTION':>14}{'CHALLENGER':>14}{'winner':>9}")
        print("  " + "-"*49)
        for label, key, hi in [("CAGR %","cagr",True),("MaxDD %","maxdd",True),
                               ("WinRate %","wr",True),("PF","pf",True),
                               ("Sharpe","sharpe",True),("Profit $","profit",True),
                               ("Trades","n",True)]:
            pv = mp[key] if mp else 0; cv = mc[key] if mc else 0
            w = winner(mp, mc, key, hi)
            print(f"  {label:<12}{pv:>14,.1f}{cv:>14,.1f}{w:>9}")
        if mp: agg_prod["profit"] += mp["profit"]; agg_prod["n"] += mp["n"]
        if mc: agg_chal["profit"] += mc["profit"]; agg_chal["n"] += mc["n"]

    out["aggregate"] = {
        "production_total_profit": round(agg_prod["profit"], 0),
        "challenger_total_profit": round(agg_chal["profit"], 0),
        "production_total_trades": agg_prod["n"],
        "challenger_total_trades": agg_chal["n"],
    }

    print("\n" + "="*100)
    print(f"  AGGREGATE PROFIT — production ${agg_prod['profit']:+,.0f}  vs  "
          f"challenger ${agg_chal['profit']:+,.0f}  "
          f"(Δ ${agg_chal['profit']-agg_prod['profit']:+,.0f})")
    print("="*100)

    os.makedirs("research/results", exist_ok=True)
    with open("research/results/vwap_fight.json", "w") as f:
        json.dump(out, f, indent=2)
    print("  wrote → research/results/vwap_fight.json")


if __name__ == "__main__":
    main()
