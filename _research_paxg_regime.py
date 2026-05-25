"""
PAXG (tokenized gold) on Infinex — regime-gated trend engine.

Baseline: the proven SPY/GLD pullback engine on PAXGUSDT 1h gave PF 1.20
(weak). Hypothesis: the engine fires on chop bars between trends. Adding
a "tradable regime" gate so it ONLY fires when ADX shows real trend
strength (or ATR expansion confirms a directional move, or time-of-day
restricts to high-quality hours) should restore PF toward the validated
2.5–3 we see on GLD's NYSE-hours data.

Gate to ship: PF ≥ 1.8 AND n ≥ 30 with PAXG on Binance perp data,
respecting the constraint that Infinex executes perps only (single venue).

Filters tested:
    NONE       baseline (replicates PF 1.20 finding)
    ADX_20+    ADX(14) ≥ 20   (weak trend OK)
    ADX_25+    ADX(14) ≥ 25   (classic trend threshold)
    ADX_30+    ADX(14) ≥ 30   (strong trend only)
    ATR_EXP    ATR(14) ≥ rolling_mean(ATR, 96) × 1.2 (vol expanding)
    SLOPE_4    EMA slope positive ≥ 4 consecutive bars (regime persisting)
    NYSE_ONLY  trade only 13:30-20:00 UTC (NYSE session)
    EU_NYSE    trade 07:00-20:00 UTC (London open through NYSE close)
    NO_ASIA    skip 00:00-07:00 UTC (Asian session = lower-quality moves)
    COMBO_A    ADX_25 + ATR_EXP
    COMBO_B    ADX_25 + NO_ASIA
    COMBO_C    ADX_25 + ATR_EXP + NO_ASIA  (strictest)
"""
from __future__ import annotations
import warnings
import numpy as np
import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import (
    generate_signals as pullback_signals,
    exit_profile_for as pb_exit,
)
from strategies.trend_carry import (
    generate_signals as tc_signals,
    exit_profile_for as tc_exit,
)
from execution.portfolio import run_portfolio, StrategySpec
from backtest.metrics import sharpe_daily, max_drawdown


def _compute_adx(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Standard Wilder ADX. Returns a Series aligned with df.index."""
    high, low, close = df["High"], df["Low"], df["Close"].shift(1)
    plus_dm = high.diff()
    minus_dm = -low.diff()
    plus_dm[plus_dm < 0] = 0
    minus_dm[minus_dm < 0] = 0
    mask = plus_dm > minus_dm
    plus_dm = plus_dm.where(mask, 0)
    minus_dm = minus_dm.where(~mask, 0)
    tr = pd.concat([(high - low),
                    (high - close).abs(),
                    (low - close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / n, adjust=False).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    minus_di = 100 * minus_dm.ewm(alpha=1 / n, adjust=False).mean() / atr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / n, adjust=False).mean()
    return adx


def _slope_persistence(slope: pd.Series, k: int) -> pd.Series:
    """True at bar t if slope > 0 for k consecutive bars ending at t."""
    pos = slope > 0
    return pos.rolling(k).sum() >= k


def _make_filter(df: pd.DataFrame, kind: str) -> pd.Series:
    """Return a boolean Series; True = trade-eligible bars."""
    if kind == "NONE":
        return pd.Series(True, index=df.index)
    if kind == "ADX_20":
        return df["ADX"] >= 20
    if kind == "ADX_25":
        return df["ADX"] >= 25
    if kind == "ADX_30":
        return df["ADX"] >= 30
    if kind == "ATR_EXP":
        return df["ATR"] >= df["ATR_mean"] * 1.2
    if kind == "SLOPE_4":
        return _slope_persistence(df["EMA_slope"], 4)
    if kind == "NYSE_ONLY":
        h = pd.Series(df.index.hour, index=df.index)
        return (h >= 13) & (h < 20)
    if kind == "EU_NYSE":
        h = pd.Series(df.index.hour, index=df.index)
        return (h >= 7) & (h < 20)
    if kind == "NO_ASIA":
        h = pd.Series(df.index.hour, index=df.index)
        return ~((h >= 0) & (h < 7))
    if kind == "COMBO_A":
        return _make_filter(df, "ADX_25") & _make_filter(df, "ATR_EXP")
    if kind == "COMBO_B":
        return _make_filter(df, "ADX_25") & _make_filter(df, "NO_ASIA")
    if kind == "COMBO_C":
        return (_make_filter(df, "ADX_25")
                & _make_filter(df, "ATR_EXP")
                & _make_filter(df, "NO_ASIA"))
    if kind == "COMBO_D":   # ADX_30 + NO_ASIA  (stricter trend)
        return _make_filter(df, "ADX_30") & _make_filter(df, "NO_ASIA")
    if kind == "COMBO_E":   # ADX_25 + NO_ASIA + SLOPE_4  (regime AND persistence)
        return (_make_filter(df, "ADX_25")
                & _make_filter(df, "NO_ASIA")
                & _make_filter(df, "SLOPE_4"))
    if kind == "COMBO_F":   # NYSE_ONLY + ADX_25  (the canonical "gold hours")
        return _make_filter(df, "ADX_25") & _make_filter(df, "NYSE_ONLY")
    raise ValueError(f"unknown filter kind {kind!r}")


def run_with_filter(symbol: str, filter_kind: str) -> dict:
    df = prepare_dual(load_symbol(symbol))
    df["ADX"] = _compute_adx(df)
    df["ATR_mean"] = df["ATR"].rolling(96).mean() if "ATR" in df.columns else 0.0
    mask = _make_filter(df, filter_kind).fillna(False)
    # Zero out engine signals on non-tradable bars.
    df.loc[~mask, "pullback_Signal"] = 0
    df.loc[~mask, "trend_carry_Signal"] = 0
    bt = run_portfolio(df, [
        StrategySpec("pullback", PULLBACK, pb_exit()),
        StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
    ], symbol=symbol, initial_capital=100_000)
    eq = bt["equity_curve"]
    tr = bt["trades"]
    days = (eq.index[-1] - eq.index[0]).days
    cagr = (eq.iloc[-1] / 100_000) ** (365.25 / max(days, 1)) - 1
    dd = max_drawdown(eq)
    pf = (tr.loc[tr.pnl > 0, "pnl"].sum() /
          max(1e-9, -tr.loc[tr.pnl < 0, "pnl"].sum())) if (tr.pnl < 0).any() else float("inf")
    wr = float((tr.pnl > 0).mean()) if not tr.empty else 0.0
    eligible_pct = float(mask.mean())
    return dict(
        filter=filter_kind, final=float(eq.iloc[-1]), cagr=cagr, dd=dd,
        pf=pf, wr=wr, n=int(len(tr)), shday=sharpe_daily(eq),
        eligible_pct=eligible_pct,
    )


FILTERS = ["NONE",
           "ADX_20", "ADX_25", "ADX_30",
           "ATR_EXP", "SLOPE_4",
           "NYSE_ONLY", "EU_NYSE", "NO_ASIA",
           "COMBO_A", "COMBO_B", "COMBO_C",
           "COMBO_D", "COMBO_E", "COMBO_F"]


def main() -> None:
    warnings.filterwarnings("ignore")
    SYMBOL = "PAXGUSDT"
    print(f"PAXG regime-filter sweep · symbol={SYMBOL} · same 2.5x-lev engine\n")
    print(f"{'Filter':<12}{'Eligible%':>11}{'Final':>11}{'CAGR':>8}"
          f"{'DD':>8}{'PF':>6}{'WR':>5}{'n':>4}{'Sh_day':>8}")
    print("-" * 80)
    winners = []
    for f in FILTERS:
        try:
            r = run_with_filter(SYMBOL, f)
            tag = "✅" if (r["pf"] >= 1.8 and r["n"] >= 30) else "  "
            print(f"{tag}{r['filter']:<10}{r['eligible_pct']*100:>10.1f}%"
                  f"${r['final']:>9,.0f}{r['cagr']*100:>7.1f}%"
                  f"{r['dd']*100:>7.1f}%{r['pf']:>6.2f}{r['wr']*100:>4.0f}%"
                  f"{r['n']:>4d}{r['shday']:>8.2f}")
            if r["pf"] >= 1.8 and r["n"] >= 30:
                winners.append(r)
        except Exception as e:
            print(f"  {f:<10} FAILED — {type(e).__name__}: {e}")

    print("\n" + "=" * 80)
    if winners:
        winners.sort(key=lambda r: -r["pf"])
        print(f"✅ {len(winners)} filter(s) cleared PF ≥ 1.8 AND n ≥ 30 on PAXG.")
        print("\nRanked by PF:")
        for r in winners:
            print(f"   {r['filter']:<10}  PF {r['pf']:.2f}  ·  "
                  f"CAGR {r['cagr']*100:.1f}%  ·  DD {r['dd']*100:.1f}%  ·  "
                  f"n={r['n']}  ·  Sh_day {r['shday']:.2f}  ·  "
                  f"eligible {r['eligible_pct']*100:.0f}% of bars")
    else:
        print("❌ No filter combo cleared PF ≥ 1.8 with n ≥ 30. PAXG-native "
              "engine still doesn't beat the GLD→PAXG translation pattern.")


if __name__ == "__main__":
    main()
