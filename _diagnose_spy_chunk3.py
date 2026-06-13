"""
Diagnose the SPY chunk-3 degradation (Part 8.30 finding):
  Chunk 1 (2023-08 → 2024-07): PF 3.43, CAGR +23.4%, WR 74.1%
  Chunk 2 (2024-07 → 2025-06): PF 3.82, CAGR +17.6%, WR 78.7%
  Chunk 3 (2025-06 → 2026-06): PF 1.28, CAGR +4.2%,  WR 69.5%

Three things to check:
  1. Trade composition: did avg win shrink, avg loss grow, or both?
  2. Regime distribution: which HMM regime saw the engine fire most?
  3. Time-of-day: did entries cluster in different sessions?
  4. Hold duration: are trades exiting faster (time-stop) or slower?
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import pandas as pd
from config.settings import PULLBACK, TRENDCARRY, get_pullback_cfg
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

INITIAL = 100_000.0


def asof(s, t):
    try: return s.asof(pd.Timestamp(t))
    except Exception: return None


def main():
    print("\n" + "="*100)
    print("  SPY CHUNK-3 DEGRADATION DIAGNOSTIC (Part 8.31)")
    print("="*100)

    df = prepare_dual(load_symbol("SPY"))
    cfg = get_pullback_cfg("SPY")
    bt = run_portfolio(df, [
        StrategySpec("pullback", cfg, pb_exit(cfg)),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol="SPY", initial_capital=INITIAL)
    tr = bt["trades"].copy()
    tr["entry_time"] = pd.to_datetime(tr["entry_time"])
    tr = tr.sort_values("entry_time").reset_index(drop=True)

    # Split into 3 chunks chronologically
    n = len(tr)
    cuts = [int(n * x) for x in [0, 0.333, 0.667, 1.0]]
    chunks = []
    for i in range(3):
        chunk = tr.iloc[cuts[i]:cuts[i+1]].copy()
        chunks.append(chunk)

    print(f"\n  Total SPY trades: {n}\n")
    print(f"  {'metric':<28}{'chunk 1':>12}{'chunk 2':>12}{'chunk 3':>12}")
    print("  " + "-"*64)

    metrics = []
    for ck in chunks:
        if len(ck) == 0:
            metrics.append({})
            continue
        wins = ck[ck["pnl"] > 0]; losses = ck[ck["pnl"] < 0]
        # hold duration
        hold = (pd.to_datetime(ck["exit_time"]) -
                pd.to_datetime(ck["entry_time"])).dt.total_seconds() / 3600
        # regime at entry
        hmm = ck["entry_time"].apply(lambda t: asof(df["HMM_state_kalman"], t)) \
              if "HMM_state_kalman" in df.columns else pd.Series([None]*len(ck))
        # hour of day
        hour = ck["entry_time"].dt.hour
        # side
        side_long = ck["side"].apply(lambda s: 1 if s == 1 else 0).sum()
        side_short = ck["side"].apply(lambda s: 1 if s == -1 else 0).sum()
        # reason
        reasons = ck["exit_reason"].value_counts().to_dict()

        m = {
            "n_trades":   len(ck),
            "wr":         (ck["pnl"] > 0).mean() * 100,
            "avg_win":    float(wins["pnl"].mean()) if len(wins) else 0,
            "avg_loss":   float(losses["pnl"].mean()) if len(losses) else 0,
            "max_win":    float(wins["pnl"].max()) if len(wins) else 0,
            "max_loss":   float(losses["pnl"].min()) if len(losses) else 0,
            "pnl_total":  float(ck["pnl"].sum()),
            "avg_hold_h": float(hold.mean()),
            "max_hold_h": float(hold.max()),
            "longs":      int(side_long),
            "shorts":     int(side_short),
            "hmm_bull":   int(hmm.astype(str).str.contains("bull", na=False).sum()),
            "hmm_bear":   int(hmm.astype(str).str.contains("bear", na=False).sum()),
            "hmm_range":  int(hmm.astype(str).str.contains("range", na=False).sum()),
            "hour_avg":   float(hour.mean()),
            "stop_n":     int(reasons.get("stop", 0)),
            "tp1_n":      int(reasons.get("tp1_partial", reasons.get("tp1", 0))),
            "tp2_n":      int(reasons.get("tp2_final", reasons.get("tp2", 0))),
            "time_n":     int(reasons.get("time", 0)),
        }
        metrics.append(m)

    def line(label, fmt, key):
        vals = [m.get(key, 0) if m else 0 for m in metrics]
        print(f"  {label:<28}{fmt.format(vals[0]):>12}{fmt.format(vals[1]):>12}{fmt.format(vals[2]):>12}")

    line("n trades",       "{:.0f}", "n_trades")
    line("win rate %",     "{:.1f}", "wr")
    line("avg win $",      "{:+,.0f}", "avg_win")
    line("avg loss $",     "{:+,.0f}", "avg_loss")
    line("max win $",      "{:+,.0f}", "max_win")
    line("max loss $",     "{:+,.0f}", "max_loss")
    line("total PnL $",    "{:+,.0f}", "pnl_total")
    line("avg hold (hrs)", "{:.1f}", "avg_hold_h")
    line("max hold (hrs)", "{:.0f}", "max_hold_h")
    print("  " + "-"*64)
    line("longs (count)",  "{:.0f}", "longs")
    line("shorts (count)", "{:.0f}", "shorts")
    print("  " + "-"*64)
    line("HMM bull entries",  "{:.0f}", "hmm_bull")
    line("HMM bear entries",  "{:.0f}", "hmm_bear")
    line("HMM range entries", "{:.0f}", "hmm_range")
    print("  " + "-"*64)
    line("hour avg (UTC)", "{:.1f}", "hour_avg")
    line("stopped",        "{:.0f}", "stop_n")
    line("tp1 partial",    "{:.0f}", "tp1_n")
    line("tp2 final",      "{:.0f}", "tp2_n")
    line("time stop",      "{:.0f}", "time_n")

    # Headline interpretation
    print("\n  ── INTERPRETATION ──")
    m1, m2, m3 = metrics
    wr_drop = m1["wr"] - m3["wr"]
    avg_win_change = (m3["avg_win"] - m1["avg_win"]) / m1["avg_win"] * 100 if m1["avg_win"] else 0
    avg_loss_change = (m3["avg_loss"] - m1["avg_loss"]) / abs(m1["avg_loss"]) * 100 if m1["avg_loss"] else 0
    print(f"  WR change ch1→ch3:        {wr_drop:+.1f}pp")
    print(f"  Avg win change ch1→ch3:   {avg_win_change:+.1f}%")
    print(f"  Avg loss change ch1→ch3:  {avg_loss_change:+.1f}% (more negative = worse losses)")

    # Regime shift
    bull_ratio_1 = m1["hmm_bull"] / max(1, m1["n_trades"]) * 100
    bull_ratio_3 = m3["hmm_bull"] / max(1, m3["n_trades"]) * 100
    print(f"  HMM bull entry % ch1→ch3: {bull_ratio_1:.1f}% → {bull_ratio_3:.1f}% (regime shift?)")

    # Time-stop frequency
    ts_pct_1 = m1["time_n"] / max(1, m1["n_trades"]) * 100
    ts_pct_3 = m3["time_n"] / max(1, m3["n_trades"]) * 100
    print(f"  Time-stop % ch1→ch3:      {ts_pct_1:.1f}% → {ts_pct_3:.1f}% (trades ran out of time?)")


if __name__ == "__main__":
    main()
