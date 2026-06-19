"""
Edge maximizer (Part 8.34).

User: keep iterating strategies/ideas until we reach EXTREMELY HIGH relative
numbers — high WR, Sharpe, CAGR with low DD — introducing new mathematics
where needed.

Core thesis (proven numerically below): no single strategy×symbol gives a
robust "extremely high" edge. The free lunch is DIVERSIFICATION across
uncorrelated return streams + VOL TARGETING. We build the portfolio in
iterations and watch Sharpe climb, DD fall.

Math concepts introduced:
  - Inverse-vol (naive risk parity) weighting:  w_i ∝ 1/σ_i
  - Portfolio vol targeting:  scale = target_vol / realized_portfolio_vol
  - Diversification ratio:  portfolio_vol / weighted-avg asset vol
  - Correlation-aware sleeve combination (trend ⟂ mean-reversion)
  - Cross-sectional alignment on a common DAILY index (clean multi-asset)

All return streams are unlevered signal backtests aligned to DAILY bars,
no friction (ranks construction methods; friction gate applies before ship).
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import numpy as np
import pandas as pd
from core.data_loader import load_symbol

UNIVERSE = {
    "INDEX":  ["SPY", "^NDX", "^GSPC", "DIA", "QQQ", "IWM", "MDY"],
    "FUTURE": ["ES=F", "NQ=F", "YM=F", "GC=F", "SI=F", "HG=F", "CL=F", "NG=F"],
    "CRYPTO": ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD"],
    "STOCK":  ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "TSLA", "AMZN", "AVGO"],
    "COMMOD": ["GLD", "SLV", "USO", "DBC", "UNG"],
}
ALL_SYMBOLS = [s for v in UNIVERSE.values() for s in v]
TRADING_DAYS = 252

# ───────────────── indicators ─────────────────
def _rsi(c, p=14):
    d = c.diff(); g = d.clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    l = (-d).clip(lower=0).ewm(alpha=1/p, adjust=False).mean()
    return (100 - 100/(1 + g/l.replace(0, np.nan))).fillna(50)

# ───────────────── strategies (position series on a daily df) ─────────────────
def st_tsmom(df, lb=100):       return np.sign(df["Close"].pct_change(lb)).fillna(0)
def st_golden(df, f=50, s=200): return (df["Close"].rolling(f).mean() > df["Close"].rolling(s).mean()).astype(float)
def st_dualmomo(df, lb=120):    return (df["Close"].pct_change(lb) > 0).astype(float)
def st_clenow(df, w=90):
    return ((df["Close"].pct_change(w) > 0) & (df["Close"] > df["Close"].rolling(w).mean())).astype(float)
def st_connors(df):
    rsi2 = _rsi(df["Close"], 2); ma = df["Close"].rolling(200).mean()
    pos = pd.Series(np.nan, index=df.index)
    pos[(rsi2 < 10) & (df["Close"] > ma)] = 1; pos[rsi2 > 70] = 0
    return pos.ffill().fillna(0)
def st_bollinger(df, n=20, k=2):
    ma = df["Close"].rolling(n).mean(); sd = df["Close"].rolling(n).std()
    pos = pd.Series(np.nan, index=df.index)
    pos[df["Close"] < ma - k*sd] = 1; pos[df["Close"] >= ma] = 0
    return pos.ffill().fillna(0)

TREND_STRATS = {"TSMOM": st_tsmom, "Golden": st_golden, "DualMomo": st_dualmomo, "Clenow": st_clenow}
MR_STRATS    = {"Connors": st_connors, "Bollinger": st_bollinger}

# ───────────────── load daily data ─────────────────
def load_daily():
    daily = {}
    for s in ALL_SYMBOLS:
        try:
            df = load_symbol(s)
            d = df.resample("1D").agg({"Open":"first","High":"max","Low":"min","Close":"last","Volume":"sum"}).dropna()
            if len(d) > 260: daily[s] = d
        except Exception:
            pass
    return daily

# ───────────────── metrics on a daily return series ─────────────────
def metrics(r):
    r = r.fillna(0)
    if r.std() == 0 or r.abs().sum() == 0:
        return None
    sharpe = r.mean()/r.std()*np.sqrt(TRADING_DAYS)
    eq = (1+r).cumprod(); total = float(eq.iloc[-1])
    years = len(r)/TRADING_DAYS
    cagr = total**(1/years) - 1 if total > 0 else -1
    dd = float(((eq-eq.cummax())/eq.cummax()).min())
    # daily win rate among active days
    active = r[r != 0]
    wr = float((active > 0).mean()) if len(active) else 0
    sortino = r.mean()/r[r<0].std()*np.sqrt(TRADING_DAYS) if (r<0).any() else np.inf
    calmar = cagr/abs(dd) if dd != 0 else np.inf
    return {"sharpe": sharpe, "sortino": sortino, "cagr": cagr, "maxdd": dd,
            "wr": wr, "calmar": calmar, "total": total-1}

def show(label, m):
    if m is None:
        print(f"  {label:<46} (no trades)"); return
    print(f"  {label:<46} Sharpe {m['sharpe']:>5.2f}  Sortino {m['sortino']:>5.2f}  "
          f"CAGR {m['cagr']*100:>+6.1f}%  DD {m['maxdd']*100:>+6.1f}%  "
          f"WR {m['wr']*100:>4.1f}%  Calmar {m['calmar']:>4.2f}")

# ───────────────── portfolio builders ─────────────────
def strat_returns_matrix(daily, strats):
    """Return DataFrame of daily strategy returns, one column per symbol×strategy."""
    cols = {}
    for sym, df in daily.items():
        dret = df["Close"].pct_change()
        for sname, fn in strats.items():
            pos = fn(df)
            if not isinstance(pos, pd.Series): pos = pd.Series(pos, index=df.index)
            cols[f"{sname}:{sym}"] = (pos.shift(1) * dret).fillna(0)
    return pd.DataFrame(cols)

def inverse_vol_weights(R):
    vol = R.std().replace(0, np.nan)
    w = (1/vol); w = w/w.sum()
    return w.fillna(0)

def vol_target(port_ret, target=0.12):
    rvol = port_ret.ewm(span=30).std()*np.sqrt(TRADING_DAYS)
    scale = (target/rvol).clip(0, 3).shift(1).fillna(0)
    return port_ret*scale

# ───────────────── main iteration loop ─────────────────
def main():
    print("="*108)
    print("  EDGE MAXIMIZER — climbing the diversification ladder (Part 8.34)")
    print("="*108)
    daily = load_daily()
    print(f"  loaded {len(daily)} symbols on daily bars\n")

    # ITERATION 1 — best single strategy×symbol (the matrix ceiling)
    print("── ITERATION 1: best single strategy×symbol (unstable ceiling) ──")
    R_trend = strat_returns_matrix(daily, TREND_STRATS)
    best_sharpe, best_col = -9, None
    per = {}
    for col in R_trend.columns:
        m = metrics(R_trend[col]); per[col] = m
        if m and m["sharpe"] > best_sharpe: best_sharpe, best_col = m["sharpe"], col
    show(f"best single: {best_col}", per[best_col])
    avg_single = np.mean([m["sharpe"] for m in per.values() if m])
    print(f"  (mean single-stream Sharpe across {len(per)} streams: {avg_single:.2f})\n")

    # ITERATION 2 — diversified TREND book, equal-weight all symbols
    print("── ITERATION 2: diversified trend book (all symbols, equal weight) ──")
    ew = R_trend.mean(axis=1)
    show("equal-weight trend book", metrics(ew))

    # ITERATION 3 — inverse-vol (naive risk parity) weighting
    print("\n── ITERATION 3: + inverse-vol (risk parity) weighting ──")
    w = inverse_vol_weights(R_trend)
    rp = (R_trend * w).sum(axis=1)
    show("risk-parity trend book", metrics(rp))
    # diversification ratio
    wavg_vol = (w * R_trend.std()).sum()*np.sqrt(TRADING_DAYS)
    port_vol = rp.std()*np.sqrt(TRADING_DAYS)
    print(f"  diversification ratio: {port_vol/wavg_vol:.2f}  "
          f"(lower = more diversification benefit)")

    # ITERATION 4 — + portfolio vol targeting
    print("\n── ITERATION 4: + portfolio vol targeting (12% annual) ──")
    vt = vol_target(rp, 0.12)
    show("vol-targeted risk-parity trend", metrics(vt))

    # ITERATION 5 — + uncorrelated MEAN-REVERSION sleeve
    print("\n── ITERATION 5: + uncorrelated mean-reversion sleeve ──")
    R_mr = strat_returns_matrix(daily, MR_STRATS)
    w_mr = inverse_vol_weights(R_mr)
    rp_mr = (R_mr * w_mr).sum(axis=1)
    show("  mean-reversion sleeve alone", metrics(rp_mr))
    # correlation between sleeves
    aligned = pd.concat([rp.rename("trend"), rp_mr.rename("mr")], axis=1).fillna(0)
    corr = aligned["trend"].corr(aligned["mr"])
    print(f"  trend ⟂ mean-reversion correlation: {corr:+.2f}")
    # combine 60/40 trend/mr then vol-target
    combined = 0.6*aligned["trend"] + 0.4*aligned["mr"]
    show("  combined trend+MR (60/40)", metrics(combined))
    combined_vt = vol_target(combined, 0.12)
    show("  combined + vol-targeted", metrics(combined_vt))

    # ITERATION 6 — risk-parity BETWEEN the two sleeves (equal risk contribution)
    print("\n── ITERATION 6: equal-risk-contribution between sleeves + vol target ──")
    sv_t, sv_m = aligned["trend"].std(), aligned["mr"].std()
    wt = (1/sv_t)/((1/sv_t)+(1/sv_m)); wm = 1-wt
    erc = wt*aligned["trend"] + wm*aligned["mr"]
    show("ERC trend+MR", metrics(erc))
    erc_vt = vol_target(erc, 0.12)
    show("ERC + vol-targeted (12%)", metrics(erc_vt))
    erc_vt15 = vol_target(erc, 0.15)
    show("ERC + vol-targeted (15%)", metrics(erc_vt15))

    print("\n" + "="*108)
    print("  SUMMARY — the diversification ladder")
    print("="*108)
    ladder = [
        ("1. best single strat×symbol", per[best_col]),
        ("2. equal-weight trend book", metrics(ew)),
        ("3. risk-parity trend book", metrics(rp)),
        ("4. + vol targeting", metrics(vt)),
        ("5. + MR sleeve (60/40, vol-tgt)", metrics(combined_vt)),
        ("6. ERC + vol-targeted (15%)", metrics(erc_vt15)),
    ]
    print(f"  {'stage':<38}{'Sharpe':>8}{'CAGR':>9}{'MaxDD':>9}{'WR':>7}{'Calmar':>8}")
    print("  " + "-"*78)
    for label, m in ladder:
        if m is None: continue
        print(f"  {label:<38}{m['sharpe']:>8.2f}{m['cagr']*100:>+8.1f}%"
              f"{m['maxdd']*100:>+8.1f}%{m['wr']*100:>+6.1f}%{m['calmar']:>8.2f}")


if __name__ == "__main__":
    main()
