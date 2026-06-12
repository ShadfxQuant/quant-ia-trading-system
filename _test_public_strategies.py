"""
Test public/known quant strategies on our universe (Part 8.30 Phase 5).

User asked: "see if any of the given quant trading strats works for this"

We implement and backtest:
  1. Turtle Trading (Donchian breakout): 20-day high entry, 10-day low exit
  2. Clenow Momentum: 12-month price momentum filter
  3. RSI(2) mean-reversion (Larry Connors style)
  4. Moving Average Crossover (50/200 golden cross)
  5. Dual Momentum (relative + absolute, Antonacci)

Quality bar (same as universe-expansion gate):
  PF >= 1.5, CAGR >= 8%, DD <= 22%, WR >= 50% (3 of 4)
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd

from core.data_loader import load_symbol

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0

# ────────── strategies ──────────
def turtle_signals(df: pd.DataFrame, entry_n=20, exit_n=10) -> pd.DataFrame:
    """Donchian breakout — buy 20-day high, sell on 10-day low."""
    out = df.copy()
    high_n = out["High"].rolling(entry_n).max().shift(1)
    low_n  = out["Low"].rolling(exit_n).min().shift(1)
    long_signal = out["Close"] > high_n
    exit_signal = out["Close"] < low_n
    state = pd.Series(0, index=out.index)
    in_pos = False
    for i in range(len(out)):
        if not in_pos and long_signal.iloc[i]:
            in_pos = True
        elif in_pos and exit_signal.iloc[i]:
            in_pos = False
        state.iloc[i] = 1 if in_pos else 0
    out["signal"] = state
    return out


def clenow_momentum_signals(df: pd.DataFrame, momo_window=100) -> pd.DataFrame:
    """Long when 100-bar momentum positive AND price above 100-bar MA."""
    out = df.copy()
    out["momo"] = out["Close"].pct_change(momo_window)
    out["ma"]   = out["Close"].rolling(momo_window).mean()
    signal = ((out["momo"] > 0) & (out["Close"] > out["ma"])).astype(int)
    out["signal"] = signal
    return out


def connors_rsi2_signals(df: pd.DataFrame) -> pd.DataFrame:
    """RSI(2) — buy when RSI<10 and Close > 200MA. Exit when RSI > 70."""
    delta = df["Close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1/2, adjust=False).mean()
    loss = (-delta).clip(lower=0).ewm(alpha=1/2, adjust=False).mean()
    rsi2 = 100 - 100/(1 + gain/loss.replace(0, np.nan))
    ma200 = df["Close"].rolling(200).mean()
    out = df.copy()
    entry = (rsi2 < 10) & (out["Close"] > ma200)
    exit_ = rsi2 > 70
    state = pd.Series(0, index=out.index)
    in_pos = False
    for i in range(len(out)):
        if not in_pos and entry.iloc[i]: in_pos = True
        elif in_pos and exit_.iloc[i]:    in_pos = False
        state.iloc[i] = 1 if in_pos else 0
    out["signal"] = state
    return out


def golden_cross_signals(df: pd.DataFrame, fast=50, slow=200) -> pd.DataFrame:
    """Trade long when fast MA > slow MA."""
    out = df.copy()
    out["fast"] = out["Close"].rolling(fast).mean()
    out["slow"] = out["Close"].rolling(slow).mean()
    out["signal"] = (out["fast"] > out["slow"]).astype(int)
    return out


def antonacci_dual_momentum(df: pd.DataFrame, lookback=12*20) -> pd.DataFrame:
    """Dual momentum — long only when absolute momentum positive (12mo on hourly)."""
    out = df.copy()
    out["momo"] = out["Close"].pct_change(lookback)
    out["signal"] = (out["momo"] > 0).astype(int)
    return out


# ────────── simple backtest ──────────
def bt_signal_series(df: pd.DataFrame) -> dict:
    """Take 1 = long full position, 0 = flat. Compute simple equity curve."""
    sig = df["signal"]
    ret = df["Close"].pct_change().fillna(0)
    pos = sig.shift(1).fillna(0)
    strat_ret = pos * ret
    # trade boundaries
    trades_pnl = []
    entry_idx = None
    for i in range(1, len(df)):
        if sig.iloc[i-1] == 0 and sig.iloc[i] == 1:
            entry_idx = i
        elif sig.iloc[i-1] == 1 and sig.iloc[i] == 0 and entry_idx is not None:
            entry_price = df["Close"].iloc[entry_idx]
            exit_price  = df["Close"].iloc[i]
            pnl_pct = (exit_price / entry_price - 1)
            trades_pnl.append(pnl_pct)
            entry_idx = None

    eq_curve = INITIAL * (1 + strat_ret).cumprod()
    eq = float(eq_curve.iloc[-1])
    peak = eq_curve.cummax()
    dd = float(((eq_curve - peak) / peak).min())
    days = (df.index.max() - df.index.min()).days
    years = max(days / 365.25, 0.1)
    cagr = (eq / INITIAL) ** (1/years) - 1
    if len(trades_pnl) == 0:
        return {"pf": 0, "cagr": 0, "dd": 0, "wr": 0, "n": 0, "eq": INITIAL}
    wins = [p for p in trades_pnl if p > 0]
    losses = [p for p in trades_pnl if p < 0]
    pf_w = sum(wins) if wins else 0
    pf_l = -sum(losses) if losses else 0.0001
    pf = pf_w / pf_l if pf_l > 0 else 999.0
    wr = len(wins) / len(trades_pnl)
    return {"pf": pf, "cagr": cagr, "dd": dd, "wr": wr, "n": len(trades_pnl), "eq": eq}


STRATEGIES = [
    ("Turtle (Donchian)",    turtle_signals),
    ("Clenow Momentum",      clenow_momentum_signals),
    ("Connors RSI(2)",       connors_rsi2_signals),
    ("Golden Cross 50/200",  golden_cross_signals),
    ("Antonacci Dual-Momo",  antonacci_dual_momentum),
]


def main():
    print("\n" + "="*100)
    print("  PUBLIC STRATEGIES ON OUR UNIVERSE (Part 8.30 Phase 5)")
    print("  Gate: PF>=1.5, CAGR>=8%, DD<=22%, WR>=50% (3 of 4)")
    print("="*100)

    for symbol in SYMBOLS:
        print(f"\n  ── {symbol} ──")
        df = load_symbol(symbol)
        print(f"    {'strategy':<25}{'PF':>6}{'CAGR':>9}{'DD':>9}{'WR':>7}"
              f"{'n':>5}{'eq $':>11}{'pass?':>7}")
        print("    " + "-"*78)
        for name, fn in STRATEGIES:
            try:
                df_sig = fn(df)
                r = bt_signal_series(df_sig)
                score = 0
                score += int(r["pf"]   >= 1.5)
                score += int(r["cagr"] >= 0.08)
                score += int(abs(r["dd"]) <= 0.22)
                score += int(r["wr"]   >= 0.50)
                verdict = "PASS" if score >= 3 else "    "
                print(f"    {name:<25}{r['pf']:>6.2f}{r['cagr']*100:>+8.1f}%"
                      f"{r['dd']*100:>+8.1f}%{r['wr']*100:>+6.1f}%"
                      f"{r['n']:>5,}${r['eq']:>9,.0f}{verdict:>7}")
            except Exception as e:
                print(f"    {name:<25} ERROR: {type(e).__name__}: {str(e)[:40]}")


if __name__ == "__main__":
    main()
