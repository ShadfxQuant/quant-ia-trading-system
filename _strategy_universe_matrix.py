"""
Strategy × Universe edge matrix (Part 8.33).

User: "test multiple different strategies (new ones) or real quant strategies
to find the biggest edge over a large portfolio of stocks, futures, crypto,
and indices."

Tests ~13 strategies across ~35 symbols spanning 4 asset classes. Each
strategy returns a signed position series {-1,0,+1}; a unified vectorized
backtest computes bar returns and derives Sharpe / CAGR / MaxDD / PF.

Ranked by annualized Sharpe (cleanest cross-asset-class comparable since
it normalizes for each asset's vol).

Output:
  - per-asset-class leaderboard (which strategy wins on stocks vs futures
    vs crypto vs indices)
  - overall strategy ranking (mean Sharpe across all symbols)
  - the single biggest edge (strategy, symbol, Sharpe)
  - research/results/strategy_matrix_<ts>.csv
"""
from __future__ import annotations
import warnings, logging, os
from datetime import datetime
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd
from core.data_loader import load_symbol

RESULTS_DIR = os.path.join("research", "results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# ─────────────────────────── universe by asset class ───────────────────────────
UNIVERSE = {
    "INDEX":  ["SPY", "^NDX", "^GSPC", "DIA", "QQQ", "IWM", "MDY"],
    "FUTURE": ["ES=F", "NQ=F", "YM=F", "GC=F", "SI=F", "HG=F", "CL=F", "NG=F"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD"],
    "STOCK":  ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "TSLA", "AMZN", "AVGO"],
    "COMMOD": ["GLD", "SLV", "USO", "DBC", "UNG"],
}

# ─────────────────────────── helpers ───────────────────────────
def _rsi(close, period=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(alpha=1/period, adjust=False).mean()
    return (100 - 100/(1 + g/l.replace(0, np.nan))).fillna(50)

def _atr(df, n=14):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h-l, (h-c.shift()).abs(), (l-c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()

# ─────────────────────────── strategies (return signed position series) ──────────
def s_tsmom(df, lb=200):
    """Time-Series Momentum (Moskowitz-Ooi-Pedersen). Sign of trailing return. THE managed-futures workhorse."""
    return np.sign(df["Close"].pct_change(lb)).fillna(0)

def s_donchian_2010(df, e=20, x=10):
    """Turtle 20/10 channel — long/short flip."""
    hi = df["High"].rolling(e).max().shift(1); lo = df["Low"].rolling(x).min().shift(1)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] > hi] = 1; pos[df["Close"] < lo] = -1
    return pos.ffill().fillna(0)

def s_donchian_5520(df, e=55, x=20):
    """Slow Turtle 55/20."""
    return s_donchian_2010(df, e, x)

def s_golden_cross(df, f=50, s=200):
    """50/200 MA crossover — long/flat."""
    return (df["Close"].rolling(f).mean() > df["Close"].rolling(s).mean()).astype(float)

def s_macd(df, f=12, s=26, sig=9):
    """MACD line vs signal — long/short."""
    ef = df["Close"].ewm(span=f, adjust=False).mean(); es = df["Close"].ewm(span=s, adjust=False).mean()
    macd = ef - es; signal = macd.ewm(span=sig, adjust=False).mean()
    return np.sign(macd - signal).fillna(0)

def s_clenow_momo(df, w=100):
    """Clenow momentum — momo>0 and above MA, long/flat."""
    momo = df["Close"].pct_change(w); ma = df["Close"].rolling(w).mean()
    return ((momo > 0) & (df["Close"] > ma)).astype(float)

def s_dual_momo(df, lb=240):
    """Antonacci absolute momentum — long when 12mo (240 bar) momo>0."""
    return (df["Close"].pct_change(lb) > 0).astype(float)

def s_connors_rsi2(df):
    """Connors RSI(2) mean-reversion — long dips above 200MA."""
    rsi2 = _rsi(df["Close"], 2); ma200 = df["Close"].rolling(200).mean()
    pos = pd.Series(np.nan, index=df.index)
    pos[(rsi2 < 10) & (df["Close"] > ma200)] = 1
    pos[rsi2 > 70] = 0
    return pos.ffill().fillna(0)

def s_bollinger_mr(df, n=20, k=2):
    """Bollinger mean-reversion — buy lower band, exit mid. Long/flat."""
    ma = df["Close"].rolling(n).mean(); sd = df["Close"].rolling(n).std()
    lower = ma - k*sd
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] < lower] = 1
    pos[df["Close"] >= ma] = 0
    return pos.ffill().fillna(0)

def s_keltner_breakout(df, n=20, mult=2):
    """ATR/Keltner channel breakout — long above upper, short below lower."""
    ma = df["Close"].ewm(span=n, adjust=False).mean(); atr = _atr(df, n)
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] > ma + mult*atr] = 1
    pos[df["Close"] < ma - mult*atr] = -1
    return pos.ffill().fillna(0)

def s_rsi_pullback(df):
    """Trend-pullback — above 200MA, RSI dips <40 then enter, exit RSI>70 or below 200MA."""
    rsi = _rsi(df["Close"], 14); ma200 = df["Close"].rolling(200).mean()
    uptrend = df["Close"] > ma200
    pos = pd.Series(np.nan, index=df.index)
    pos[(rsi < 40) & uptrend] = 1
    pos[(rsi > 70) | ~uptrend] = 0
    return pos.ffill().fillna(0)

def s_voltarget_trend(df, target=0.15):
    """Golden-cross direction × vol-target sizing (Tier-1 maths from SOPHISTICATED_METHODS)."""
    direction = (df["Close"].rolling(50).mean() > df["Close"].rolling(200).mean()).astype(float)
    ret = df["Close"].pct_change()
    rvol = ret.ewm(span=20).std() * np.sqrt(1638)   # ~hourly annualization
    size = (target / rvol).clip(0, 2.0).fillna(0)
    return (direction * size).fillna(0)

def s_macd_trend_filter(df):
    """MACD long/short but only in direction of 200MA trend (filtered)."""
    macd_pos = s_macd(df)
    above = df["Close"] > df["Close"].rolling(200).mean()
    pos = pd.Series(0.0, index=df.index)
    pos[(macd_pos > 0) & above] = 1
    pos[(macd_pos < 0) & ~above] = -1
    return pos

STRATEGIES = {
    "TSMOM_200":        s_tsmom,
    "Donchian_20/10":   s_donchian_2010,
    "Donchian_55/20":   s_donchian_5520,
    "GoldenCross_50/200": s_golden_cross,
    "MACD":             s_macd,
    "MACD_trendfilt":   s_macd_trend_filter,
    "Clenow_Momo":      s_clenow_momo,
    "DualMomo_240":     s_dual_momo,
    "Connors_RSI2":     s_connors_rsi2,
    "Bollinger_MR":     s_bollinger_mr,
    "Keltner_Break":    s_keltner_breakout,
    "RSI_Pullback":     s_rsi_pullback,
    "VolTarget_Trend":  s_voltarget_trend,
}

# ─────────────────────────── unified backtest ───────────────────────────
def backtest(df, pos):
    ret = df["Close"].pct_change().fillna(0)
    strat_ret = (pos.shift(1).fillna(0) * ret).fillna(0)
    if strat_ret.abs().sum() == 0:
        return None
    # annualization from actual bar spacing
    days = max((df.index.max() - df.index.min()).days, 1)
    years = days / 365.25
    bars_per_year = len(df) / years
    ann = np.sqrt(bars_per_year)
    mu = strat_ret.mean(); sd = strat_ret.std()
    sharpe = (mu / sd * ann) if sd > 0 else 0.0
    eq = (1 + strat_ret).cumprod()
    total = float(eq.iloc[-1])
    cagr = total ** (1/years) - 1 if total > 0 else -1
    dd = float(((eq - eq.cummax()) / eq.cummax()).min())
    gross_win = strat_ret[strat_ret > 0].sum()
    gross_loss = -strat_ret[strat_ret < 0].sum()
    pf = gross_win / gross_loss if gross_loss > 0 else 999.0
    n_flips = int((pos.diff().abs() > 0).sum())
    return {"sharpe": sharpe, "cagr": cagr, "maxdd": dd, "pf": pf, "n_flips": n_flips, "total_ret": total - 1}

# ─────────────────────────── run matrix ───────────────────────────
def main():
    print("\n" + "="*100)
    print("  STRATEGY × UNIVERSE EDGE MATRIX (Part 8.33)")
    print(f"  {len(STRATEGIES)} strategies × {sum(len(v) for v in UNIVERSE.values())} symbols, "
          f"ranked by annualized Sharpe")
    print("="*100)

    rows = []
    # preload data once per symbol
    data = {}
    for cls, syms in UNIVERSE.items():
        for s in syms:
            try:
                df = load_symbol(s)
                if len(df) > 250:
                    data[s] = (cls, df)
            except Exception as e:
                print(f"  load fail {s}: {str(e)[:50]}")

    print(f"  loaded {len(data)} symbols\n")

    for sym, (cls, df) in data.items():
        for sname, sfn in STRATEGIES.items():
            try:
                pos = sfn(df)
                if not isinstance(pos, pd.Series):
                    pos = pd.Series(pos, index=df.index)
                m = backtest(df, pos)
                if m is None: continue
                rows.append({"symbol": sym, "asset_class": cls, "strategy": sname, **m})
            except Exception:
                continue

    R = pd.DataFrame(rows)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    R.to_csv(os.path.join(RESULTS_DIR, f"strategy_matrix_{ts}.csv"), index=False)
    R.to_csv(os.path.join(RESULTS_DIR, "strategy_matrix_latest.csv"), index=False)

    # ── overall strategy ranking (mean Sharpe across all symbols) ──
    print("="*100)
    print("  OVERALL STRATEGY RANKING (mean Sharpe across all symbols)")
    print("="*100)
    overall = R.groupby("strategy").agg(
        mean_sharpe=("sharpe", "mean"),
        median_sharpe=("sharpe", "median"),
        mean_cagr=("cagr", "mean"),
        mean_dd=("maxdd", "mean"),
        win_rate=("sharpe", lambda x: (x > 0).mean()),
    ).sort_values("mean_sharpe", ascending=False)
    print(f"  {'strategy':<22}{'meanSharpe':>11}{'medSharpe':>11}{'meanCAGR':>10}{'meanDD':>9}{'%symbols+':>11}")
    print("  " + "-"*82)
    for name, r in overall.iterrows():
        print(f"  {name:<22}{r['mean_sharpe']:>11.2f}{r['median_sharpe']:>11.2f}"
              f"{r['mean_cagr']*100:>+9.1f}%{r['mean_dd']*100:>+8.1f}%{r['win_rate']*100:>+10.0f}%")

    # ── per-asset-class winner ──
    print("\n" + "="*100)
    print("  BIGGEST EDGE PER ASSET CLASS (best mean-Sharpe strategy)")
    print("="*100)
    for cls in UNIVERSE:
        sub = R[R["asset_class"] == cls]
        if len(sub) == 0: continue
        byst = sub.groupby("strategy")["sharpe"].mean().sort_values(ascending=False)
        best = byst.index[0]
        print(f"\n  {cls}:  best = {best} (mean Sharpe {byst.iloc[0]:.2f})")
        top3 = byst.head(3)
        for st, sh in top3.items():
            cagr = sub[sub["strategy"]==st]["cagr"].mean()
            print(f"      {st:<22} Sharpe {sh:>5.2f}  CAGR {cagr*100:>+6.1f}%")

    # ── single biggest edge ──
    print("\n" + "="*100)
    print("  TOP 15 SINGLE STRATEGY×SYMBOL EDGES (by Sharpe)")
    print("="*100)
    top = R.sort_values("sharpe", ascending=False).head(15)
    print(f"  {'strategy':<22}{'symbol':<10}{'class':<9}{'Sharpe':>8}{'CAGR':>9}{'MaxDD':>9}{'PF':>7}")
    print("  " + "-"*82)
    for _, r in top.iterrows():
        print(f"  {r['strategy']:<22}{r['symbol']:<10}{r['asset_class']:<9}"
              f"{r['sharpe']:>8.2f}{r['cagr']*100:>+8.1f}%{r['maxdd']*100:>+8.1f}%{r['pf']:>7.2f}")

    print(f"\n  wrote → research/results/strategy_matrix_{ts}.csv")


if __name__ == "__main__":
    main()
