"""
Pullback / Golden-Cross / VWAP strategy suite (Part 8.35).

User: try more strategies, look into pullback golden-cross using EMA, SMA,
and VWAP.

Tests ~10 strategies built from EMA/SMA/VWAP confluence on HOURLY bars
(VWAP needs intraday granularity — session-anchored, resets each day).
Ranked by annualized Sharpe across a multi-asset subset, best ones then
run on the full universe.

Strategies:
  1. GC_Pullback_EMA   — golden cross (EMA50>SMA200) + buy pullback to EMA50
  2. GC_Pullback_SMA   — golden cross + buy pullback to SMA50
  3. VWAP_Reclaim      — uptrend + price reclaims session VWAP from below
  4. VWAP_Pullback     — golden cross + pullback to VWAP + bounce
  5. Triple_Confluence — long only when Close>EMA>SMA & pullback to EMA, RSI filter
  6. VWAP_MeanRev      — fade extreme z-score deviation from VWAP
  7. EMA_SMA_Ribbon    — stacked EMA20>EMA50>SMA200, buy EMA20 pullback
  8. GC_VWAP_Combo     — golden cross AND above VWAP, pullback to EMA50
  9. AnchoredVWAP_Trend— price above rolling 200-bar VWAP = trend, pullback entry
 10. VWAP_Band_Break   — break above VWAP+1σ band in uptrend (breakout flavor)

Unlevered signal backtests, no friction (ranks edge; friction gate before ship).
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd
from core.data_loader import load_symbol

SUBSET = ["SPY", "^NDX", "GLD", "GC=F", "QQQ", "AAPL", "NVDA", "BTC-USD", "CL=F", "SLV"]
FULL = {
    "INDEX":  ["SPY", "^NDX", "^GSPC", "DIA", "QQQ", "IWM"],
    "FUTURE": ["ES=F", "NQ=F", "GC=F", "SI=F", "HG=F", "CL=F", "NG=F"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD"],
    "STOCK":  ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "TSLA"],
    "COMMOD": ["GLD", "SLV", "USO", "DBC"],
}

# ───────────────── indicators ─────────────────
def _rsi(c, p=14):
    d = c.diff(); g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    return (100 - 100/(1 + g/l.replace(0, np.nan))).fillna(50)

def session_vwap(df):
    """Session-anchored VWAP — resets each calendar day. The real trader VWAP."""
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    pv = tp * df["Volume"]
    day = df.index.normalize()
    cum_pv = pv.groupby(day).cumsum()
    cum_v = df["Volume"].groupby(day).cumsum().replace(0, np.nan)
    return (cum_pv / cum_v).ffill()

def rolling_vwap(df, n=200):
    tp = (df["High"] + df["Low"] + df["Close"]) / 3
    return (tp * df["Volume"]).rolling(n).sum() / df["Volume"].rolling(n).sum().replace(0, np.nan)

def vwap_bands(df, n=None, k=1.0):
    """Session VWAP ± k·stdev of price-from-vwap."""
    vw = session_vwap(df)
    dev = (df["Close"] - vw)
    sd = dev.groupby(df.index.normalize()).transform(lambda x: x.expanding().std())
    return vw, vw + k*sd, vw - k*sd

# ───────────────── strategies (signed position) ─────────────────
def s1_gc_pullback_ema(df):
    """Golden cross (EMA50>SMA200) + buy when price dips to/below EMA50 then closes above."""
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    sma200 = df["Close"].rolling(200).mean()
    gc = ema50 > sma200
    dipped = (df["Low"] <= ema50) & (df["Close"] > ema50)   # touched EMA, closed above
    pos = pd.Series(np.nan, index=df.index)
    pos[gc & dipped] = 1
    pos[(~gc) | (df["Close"] < sma200)] = 0
    return pos.ffill().fillna(0)

def s2_gc_pullback_sma(df):
    sma50 = df["Close"].rolling(50).mean()
    sma200 = df["Close"].rolling(200).mean()
    gc = sma50 > sma200
    dipped = (df["Low"] <= sma50) & (df["Close"] > sma50)
    pos = pd.Series(np.nan, index=df.index)
    pos[gc & dipped] = 1
    pos[(~gc) | (df["Close"] < sma200)] = 0
    return pos.ffill().fillna(0)

def s3_vwap_reclaim(df):
    """Uptrend (above 200MA) + price reclaims session VWAP from below."""
    vw = session_vwap(df); ma200 = df["Close"].rolling(200).mean()
    reclaim = (df["Close"] > vw) & (df["Close"].shift(1) <= vw.shift(1))
    pos = pd.Series(np.nan, index=df.index)
    pos[(df["Close"] > ma200) & reclaim] = 1
    pos[df["Close"] < vw] = 0
    return pos.ffill().fillna(0)

def s4_vwap_pullback(df):
    """Golden cross + pullback to VWAP + bounce (low touches VWAP, close above)."""
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    sma200 = df["Close"].rolling(200).mean()
    vw = session_vwap(df); gc = ema50 > sma200
    bounce = (df["Low"] <= vw) & (df["Close"] > vw)
    pos = pd.Series(np.nan, index=df.index)
    pos[gc & bounce] = 1
    pos[(~gc) | (df["Close"] < ema50)] = 0
    return pos.ffill().fillna(0)

def s5_triple_confluence(df):
    """Close>EMA20>SMA50 stacked, buy pullback to EMA20, RSI<60 filter (not overbought)."""
    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    sma50 = df["Close"].rolling(50).mean()
    sma200 = df["Close"].rolling(200).mean()
    rsi = _rsi(df["Close"], 14)
    stacked = (ema20 > sma50) & (sma50 > sma200)
    pull = (df["Low"] <= ema20) & (df["Close"] > ema20) & (rsi < 60)
    pos = pd.Series(np.nan, index=df.index)
    pos[stacked & pull] = 1
    pos[df["Close"] < sma50] = 0
    return pos.ffill().fillna(0)

def s6_vwap_meanrev(df):
    """Fade extreme deviation below VWAP (z<-2) in an uptrend; exit at VWAP."""
    vw, up, lo = vwap_bands(df, k=2.0)
    ma200 = df["Close"].rolling(200).mean()
    pos = pd.Series(np.nan, index=df.index)
    pos[(df["Close"] < lo) & (df["Close"] > ma200)] = 1
    pos[df["Close"] >= vw] = 0
    return pos.ffill().fillna(0)

def s7_ema_sma_ribbon(df):
    """EMA20>EMA50>SMA200 ribbon, buy EMA20 pullback."""
    ema20 = df["Close"].ewm(span=20, adjust=False).mean()
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    sma200 = df["Close"].rolling(200).mean()
    ribbon = (ema20 > ema50) & (ema50 > sma200)
    pull = (df["Low"] <= ema20) & (df["Close"] > ema20)
    pos = pd.Series(np.nan, index=df.index)
    pos[ribbon & pull] = 1
    pos[ema20 < ema50] = 0
    return pos.ffill().fillna(0)

def s8_gc_vwap_combo(df):
    """Golden cross AND above session VWAP, buy pullback to EMA50."""
    ema50 = df["Close"].ewm(span=50, adjust=False).mean()
    sma200 = df["Close"].rolling(200).mean()
    vw = session_vwap(df)
    cond = (ema50 > sma200) & (df["Close"] > vw)
    pull = (df["Low"] <= ema50) & (df["Close"] > ema50)
    pos = pd.Series(np.nan, index=df.index)
    pos[cond & pull] = 1
    pos[(df["Close"] < sma200) | (df["Close"] < vw)] = 0
    return pos.ffill().fillna(0)

def s9_anchored_vwap_trend(df):
    """Price above rolling-200 VWAP = trend; buy pullback to that VWAP."""
    rvw = rolling_vwap(df, 200)
    above = df["Close"] > rvw
    pull = (df["Low"] <= rvw) & (df["Close"] > rvw)
    pos = pd.Series(np.nan, index=df.index)
    pos[above & pull] = 1
    pos[df["Close"] < rvw] = 0
    return pos.ffill().fillna(0)

def s10_vwap_band_break(df):
    """Break above VWAP+1σ in uptrend (breakout flavor)."""
    vw, up, lo = vwap_bands(df, k=1.0)
    ma200 = df["Close"].rolling(200).mean()
    brk = (df["Close"] > up) & (df["Close"].shift(1) <= up.shift(1))
    pos = pd.Series(np.nan, index=df.index)
    pos[brk & (df["Close"] > ma200)] = 1
    pos[df["Close"] < vw] = 0
    return pos.ffill().fillna(0)

STRATS = {
    "GC_Pullback_EMA": s1_gc_pullback_ema,
    "GC_Pullback_SMA": s2_gc_pullback_sma,
    "VWAP_Reclaim":    s3_vwap_reclaim,
    "VWAP_Pullback":   s4_vwap_pullback,
    "Triple_Confluence": s5_triple_confluence,
    "VWAP_MeanRev":    s6_vwap_meanrev,
    "EMA_SMA_Ribbon":  s7_ema_sma_ribbon,
    "GC_VWAP_Combo":   s8_gc_vwap_combo,
    "AnchoredVWAP_Trend": s9_anchored_vwap_trend,
    "VWAP_Band_Break": s10_vwap_band_break,
}

# ───────────────── backtest ─────────────────
def backtest(df, pos):
    ret = df["Close"].pct_change().fillna(0)
    sr = (pos.shift(1).fillna(0) * ret).fillna(0)
    if sr.abs().sum() == 0: return None
    days = max((df.index.max() - df.index.min()).days, 1)
    years = days/365.25; bpy = len(df)/years; ann = np.sqrt(bpy)
    mu, sd = sr.mean(), sr.std()
    sharpe = mu/sd*ann if sd > 0 else 0
    eq = (1+sr).cumprod(); total = float(eq.iloc[-1])
    cagr = total**(1/years)-1 if total > 0 else -1
    dd = float(((eq-eq.cummax())/eq.cummax()).min())
    # per-trade win rate (count entry→exit blocks)
    chg = pos.diff().fillna(0); entries = (pos.shift(1).fillna(0) == 0) & (pos != 0)
    n_trades = int(entries.sum())
    active = sr[pos.shift(1).fillna(0) != 0]
    wr = float((active > 0).mean()) if len(active) else 0
    gw = sr[sr > 0].sum(); gl = -sr[sr < 0].sum()
    pf = gw/gl if gl > 0 else 999
    time_in = float((pos != 0).mean())
    return {"sharpe": sharpe, "cagr": cagr, "maxdd": dd, "wr": wr, "pf": pf,
            "n": n_trades, "time_in": time_in}

def main():
    print("="*108)
    print("  PULLBACK / GOLDEN-CROSS / VWAP STRATEGY SUITE (Part 8.35)  — hourly bars")
    print("="*108)
    data = {}
    for s in SUBSET:
        try:
            df = load_symbol(s)
            if "Volume" in df and df["Volume"].sum() > 0 and len(df) > 300:
                data[s] = df
        except Exception: pass
    print(f"  loaded {len(data)} subset symbols\n")

    # strategy × symbol on subset
    agg = {name: [] for name in STRATS}
    for sym, df in data.items():
        for name, fn in STRATS.items():
            try:
                m = backtest(df, fn(df))
                if m: agg[name].append((sym, m))
            except Exception:
                pass

    print(f"  {'strategy':<22}{'meanSharpe':>11}{'medSharpe':>11}{'meanCAGR':>10}{'meanDD':>9}{'meanWR':>8}{'meanPF':>8}{'%+':>6}")
    print("  " + "-"*84)
    ranking = []
    for name, lst in agg.items():
        if not lst: continue
        sh = [m["sharpe"] for _, m in lst]
        cg = [m["cagr"] for _, m in lst]
        dd = [m["maxdd"] for _, m in lst]
        wr = [m["wr"] for _, m in lst]
        pf = [min(m["pf"], 10) for _, m in lst]
        pos_rate = np.mean([1 if x > 0 else 0 for x in sh])
        ranking.append((name, np.mean(sh), np.median(sh), np.mean(cg), np.mean(dd), np.mean(wr), np.mean(pf), pos_rate))
    ranking.sort(key=lambda x: x[1], reverse=True)
    for name, ms, mds, mc, md, mw, mpf, pr in ranking:
        print(f"  {name:<22}{ms:>11.2f}{mds:>11.2f}{mc*100:>+9.1f}%{md*100:>+8.1f}%"
              f"{mw*100:>+7.1f}%{mpf:>8.2f}{pr*100:>+5.0f}%")

    # best 3 → full universe
    best3 = [r[0] for r in ranking[:3]]
    print(f"\n  ── TOP 3 ON FULL UNIVERSE: {best3} ──")
    fdata = {}
    for cls, syms in FULL.items():
        for s in syms:
            try:
                df = load_symbol(s)
                if "Volume" in df and df["Volume"].sum() > 0 and len(df) > 300:
                    fdata[s] = (cls, df)
            except Exception: pass
    for name in best3:
        fn = STRATS[name]; rows = []
        for sym, (cls, df) in fdata.items():
            m = backtest(df, fn(df))
            if m: rows.append((sym, cls, m))
        if not rows: continue
        sh = np.mean([m["sharpe"] for _,_,m in rows])
        cg = np.mean([m["cagr"] for _,_,m in rows])
        dd = np.mean([m["maxdd"] for _,_,m in rows])
        wr = np.mean([m["wr"] for _,_,m in rows])
        print(f"\n  {name}: meanSharpe {sh:.2f}  meanCAGR {cg*100:+.1f}%  meanDD {dd*100:+.1f}%  meanWR {wr*100:.1f}%")
        top = sorted(rows, key=lambda x: x[2]["sharpe"], reverse=True)[:5]
        for sym, cls, m in top:
            print(f"      {sym:<9}{cls:<8} Sharpe {m['sharpe']:>5.2f}  CAGR {m['cagr']*100:>+6.1f}%  "
                  f"DD {m['maxdd']*100:>+6.1f}%  WR {m['wr']*100:>4.1f}%  PF {m['pf']:>4.2f}  n={m['n']}")


if __name__ == "__main__":
    main()
