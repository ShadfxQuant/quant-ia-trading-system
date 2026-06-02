"""
Pre-ML diagnostic: are stops concentrated in chop or distributed across regimes?

If the answer is "concentrated in chop", a simple ADX/regime size multiplier
captures most of the value without any model overhead. If stops are
uniformly distributed, then the case for an ML classifier on
slope/divergence/VIX/breadth/yields gets much stronger.

Runs SPY + GLD with current production config (post-baseline-#0 deploy).
For each STOP exit, labels with the deterministic regime + HMM state +
ADX bucket at entry, plus the time-of-day. Cross-tabs everything.
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


def _compute_adx(df, n=14):
    high, low, close = df["High"], df["Low"], df["Close"].shift(1)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)
    tr = pd.concat([(high-low), (high-close).abs(), (low-close).abs()],
                   axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    pdi = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    mdi = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean()


def _adx_bucket(v):
    if pd.isna(v): return "?"
    if v < 20: return "chop (<20)"
    if v < 30: return "weak (20-30)"
    return "strong (>=30)"


def audit(symbol):
    print(f"\n{'='*92}\n  {symbol} — stops vs regime breakdown\n{'='*92}")
    df = prepare_dual(load_symbol(symbol))
    df["__ADX"] = _compute_adx(df)
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=100_000)
    tr = bt["trades"].copy()

    def at_entry(col, t):
        try: return df[col].asof(t)
        except KeyError: return None

    tr["adx"] = tr.entry_time.apply(lambda t: at_entry("__ADX", t))
    tr["adx_bucket"] = tr.adx.apply(_adx_bucket)
    tr["regime"] = tr.entry_time.apply(lambda t: at_entry("Regime", t))
    tr["hmm"] = tr.entry_time.apply(lambda t: at_entry("HMM_state", t))
    tr["is_stop"] = tr.exit_reason.str.contains("stop", na=False)
    tr["hour"] = tr.entry_time.dt.hour

    n_total = len(tr)
    n_stops = int(tr.is_stop.sum())
    stop_pnl = float(tr.loc[tr.is_stop, "pnl"].sum())
    print(f"  trades total: {n_total}  ·  stops: {n_stops}  ·  "
          f"stop $: ${stop_pnl:+,.0f}")

    if n_stops == 0:
        print("  (no stops — nothing to audit)")
        return tr

    # 1. Stops by ADX bucket
    print(f"\n  STOPS BY ADX BUCKET (entry-bar ADX):")
    print(f"  {'bucket':<16}{'stops':>7}{'% of stops':>12}{'stop $':>12}{'% of stop $':>14}")
    print("  " + "-"*70)
    stops = tr[tr.is_stop]
    for b, g in stops.groupby("adx_bucket"):
        pct_n = len(g) / n_stops * 100
        s = float(g.pnl.sum())
        pct_s = s / stop_pnl * 100 if stop_pnl else 0
        print(f"  {str(b):<16}{len(g):>7}{pct_n:>11.1f}%${s:>+10,.0f}{pct_s:>+12.1f}%")

    # 2. Stops by deterministic regime
    print(f"\n  STOPS BY DETERMINISTIC REGIME:")
    print(f"  {'regime':<16}{'stops':>7}{'% of stops':>12}{'stop $':>12}{'% of stop $':>14}")
    print("  " + "-"*70)
    for r, g in stops.groupby("regime"):
        pct_n = len(g) / n_stops * 100
        s = float(g.pnl.sum())
        pct_s = s / stop_pnl * 100 if stop_pnl else 0
        print(f"  {str(r):<16}{len(g):>7}{pct_n:>11.1f}%${s:>+10,.0f}{pct_s:>+12.1f}%")

    # 3. Stops by HMM state
    print(f"\n  STOPS BY HMM STATE:")
    print(f"  {'hmm':<16}{'stops':>7}{'% of stops':>12}{'stop $':>12}{'% of stop $':>14}")
    print("  " + "-"*70)
    for h, g in stops.groupby("hmm"):
        pct_n = len(g) / n_stops * 100
        s = float(g.pnl.sum())
        pct_s = s / stop_pnl * 100 if stop_pnl else 0
        print(f"  {str(h):<16}{len(g):>7}{pct_n:>11.1f}%${s:>+10,.0f}{pct_s:>+12.1f}%")

    # 4. Stops by entry hour (NYSE session vs off-hours)
    print(f"\n  STOPS BY ENTRY-BAR HOUR (UTC):")
    print(f"  {'hour':<16}{'stops':>7}{'% of stops':>12}{'stop $':>12}{'% of stop $':>14}")
    print("  " + "-"*70)
    for h, g in stops.groupby("hour"):
        pct_n = len(g) / n_stops * 100
        s = float(g.pnl.sum())
        pct_s = s / stop_pnl * 100 if stop_pnl else 0
        print(f"  hour {h:02d}        {len(g):>7}{pct_n:>11.1f}%${s:>+10,.0f}{pct_s:>+12.1f}%")

    # 5. The headline question — what fraction of stop pain comes from ADX<25 bars?
    chop_stops = stops[stops.adx < 25]
    chop_pnl = float(chop_stops.pnl.sum())
    print(f"\n  ── HEADLINE: are stops concentrated in chop (ADX<25)? ──")
    print(f"    chop-bar stops:  {len(chop_stops)}/{n_stops} ({len(chop_stops)/n_stops*100:.1f}% of stops)")
    print(f"    chop-bar $ pain: ${chop_pnl:+,.0f} / ${stop_pnl:+,.0f} "
          f"({chop_pnl/stop_pnl*100:.1f}% of stop $)")

    # Same for the 5-state regime "chop-like" buckets
    bad_regimes = ["crash", "slowdown"]   # weakest WR regimes from Task 3
    bad_reg_stops = stops[stops.regime.isin(bad_regimes)]
    bad_reg_pnl = float(bad_reg_stops.pnl.sum())
    print(f"    {bad_regimes} stops: {len(bad_reg_stops)}/{n_stops} ({len(bad_reg_stops)/n_stops*100:.1f}% of stops)")
    print(f"    {bad_regimes} $ pain: ${bad_reg_pnl:+,.0f} ({bad_reg_pnl/stop_pnl*100:.1f}% of stop $)")

    # 6. Simulated counterfactual — what if we'd skipped chop-bar entries entirely?
    not_stops = tr[~tr.is_stop]
    chop_winners_lost = not_stops[not_stops.adx < 25]
    win_pnl_in_chop = float(chop_winners_lost.pnl.sum())
    print(f"\n  COUNTERFACTUAL — if we'd skipped ALL ADX<25 entries:")
    print(f"    stop savings:    ${-chop_pnl:+,.0f}")
    print(f"    winner losses:   ${-win_pnl_in_chop:+,.0f} (winners we wouldn't have taken)")
    net = -chop_pnl - win_pnl_in_chop
    sign = "gain" if net > 0 else "loss"
    print(f"    NET {sign}:        ${net:+,.0f}  (positive = filter helps)")

    return tr


def main():
    print("\n" + "="*92)
    print("  STOP-LEG REGIME DIAGNOSTIC — pre-ML viability check")
    print("="*92)
    print("  Hypothesis: if stops concentrate in chop, a simple ADX/regime")
    print("  size-mult captures most of the alpha. If they're distributed,")
    print("  the ML route is the only path.")

    tr_spy = audit("SPY")
    tr_gld = audit("GLD")

    # Combined summary
    print(f"\n{'='*92}\n  COMBINED SPY + GLD\n{'='*92}")
    tr = pd.concat([tr_spy.assign(__sym="SPY"), tr_gld.assign(__sym="GLD")],
                   ignore_index=True)
    stops = tr[tr.is_stop]
    chop_stops = stops[stops.adx < 25]
    print(f"  total trades:    {len(tr)}")
    print(f"  total stops:     {len(stops)}")
    print(f"  total stop $:    ${stops.pnl.sum():+,.0f}")
    print(f"  chop stops:      {len(chop_stops)}  "
          f"({len(chop_stops)/max(1,len(stops))*100:.1f}% of stops)")
    print(f"  chop stop $:     ${chop_stops.pnl.sum():+,.0f}  "
          f"({chop_stops.pnl.sum()/max(1e-9, stops.pnl.sum())*100:.1f}% of stop $)")


if __name__ == "__main__":
    main()
