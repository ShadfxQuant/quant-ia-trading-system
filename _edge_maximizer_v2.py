"""
Edge maximizer v2 — advanced mathematics (Part 8.34b).

Iteration 1-6 (v1) capped at Sharpe ~1.2 with plain long/flat trend+MR.
Per user instruction, introduce genuinely new mathematics to break higher:

  7. CONTINUOUS vol-scaled TSMOM  — positions ∝ z-scored momentum / vol,
     not binary. Smoother, higher Sharpe (Moskowitz-Ooi-Pedersen style).
  8. CROSS-SECTIONAL momentum     — vol-normalize every asset, rank daily,
     long top tercile / short bottom tercile = MARKET-NEUTRAL. Strips the
     common factor; classic AQR cross-sectional momentum premium.
  9. TANGENCY (max-Sharpe) sleeve weighting — rolling out-of-sample
     mean-variance optimal weights across the alpha sleeves (Markowitz):
        w* ∝ Σ⁻¹ μ      (long-only-clipped, normalized, lagged → no lookahead)
 10. Full stack: cross-sectional + continuous-TS + MR, tangency-weighted,
     vol-targeted.

All daily, unlevered-signal, no friction (ranks construction; friction gate
applies before any ship). No-lookahead: all weights use rolling/lagged stats.
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
ALL = [s for v in UNIVERSE.values() for s in v]
TD = 252

def metrics(r):
    r = r.fillna(0)
    if r.std() == 0 or r.abs().sum() == 0: return None
    sh = r.mean()/r.std()*np.sqrt(TD)
    eq = (1+r).cumprod(); total = float(eq.iloc[-1]); years = len(r)/TD
    cagr = total**(1/years)-1 if total > 0 else -1
    dd = float(((eq-eq.cummax())/eq.cummax()).min())
    active = r[r != 0]; wr = float((active > 0).mean()) if len(active) else 0
    sortino = r.mean()/r[r<0].std()*np.sqrt(TD) if (r<0).any() else np.inf
    calmar = cagr/abs(dd) if dd != 0 else np.inf
    return {"sharpe": sh, "sortino": sortino, "cagr": cagr, "maxdd": dd, "wr": wr, "calmar": calmar}

def show(lbl, m):
    if m is None: print(f"  {lbl:<46} (none)"); return
    print(f"  {lbl:<46} Sharpe {m['sharpe']:>5.2f}  Sortino {m['sortino']:>5.2f}  "
          f"CAGR {m['cagr']*100:>+6.1f}%  DD {m['maxdd']*100:>+6.1f}%  WR {m['wr']*100:>4.1f}%  Calmar {m['calmar']:>4.2f}")

def vol_target(r, target=0.12, span=30):
    rvol = r.ewm(span=span).std()*np.sqrt(TD)
    return r*(target/rvol).clip(0,3).shift(1).fillna(0)

def main():
    print("="*108)
    print("  EDGE MAXIMIZER v2 — advanced mathematics (Part 8.34b)")
    print("="*108)

    # build aligned daily close panel
    closes = {}
    for s in ALL:
        try:
            df = load_symbol(s)
            d = df["Close"].resample("1D").last().dropna()
            if len(d) > 260: closes[s] = d
        except Exception: pass
    P = pd.DataFrame(closes).sort_index()
    P = P.dropna(how="all").ffill(limit=3)
    ret = P.pct_change()
    print(f"  panel: {P.shape[1]} symbols × {P.shape[0]} days "
          f"({P.index[0].date()} → {P.index[-1].date()})\n")

    # rolling vol for normalization (lagged, no lookahead)
    vol = ret.rolling(30).std().shift(1)
    vol_ann = vol*np.sqrt(TD)

    # ── 7. CONTINUOUS vol-scaled TSMOM ──
    print("── ITERATION 7: continuous vol-scaled TSMOM (z-scored momentum / vol) ──")
    mom = P.pct_change(90)
    # z-score momentum cross-sectionally each day, scale by inverse vol, clip
    z = mom.sub(mom.mean(axis=1), axis=0).div(mom.std(axis=1).replace(0,np.nan), axis=0)
    pos = (z / (vol_ann.replace(0,np.nan))).clip(-3,3)
    pos = pos.div(pos.abs().sum(axis=1), axis=0).shift(1)   # normalize gross, lag
    ts_cont = (pos*ret).sum(axis=1)
    show("continuous vol-scaled TSMOM", metrics(ts_cont))
    show("  + vol-targeted (12%)", metrics(vol_target(ts_cont, 0.12)))

    # ── 8. CROSS-SECTIONAL momentum (market-neutral) ──
    print("\n── ITERATION 8: cross-sectional momentum (long top / short bottom tercile, mkt-neutral) ──")
    for lb in (60, 90, 120):
        m = P.pct_change(lb)
        # vol-normalize so assets are comparable
        msig = m / vol_ann.replace(0,np.nan)
        rank = msig.rank(axis=1, pct=True)
        long_leg  = (rank >= 0.667).astype(float)
        short_leg = (rank <= 0.333).astype(float)
        nL = long_leg.sum(axis=1).replace(0,np.nan); nS = short_leg.sum(axis=1).replace(0,np.nan)
        w = long_leg.div(nL, axis=0) - short_leg.div(nS, axis=0)
        w = w.shift(1)   # lag — trade next day
        xs = (w*ret).sum(axis=1)
        show(f"cross-sectional momo (lb={lb})", metrics(xs))
    # keep lb=90 as the canonical
    m = P.pct_change(90); msig = m/vol_ann.replace(0,np.nan); rank = msig.rank(axis=1, pct=True)
    long_leg=(rank>=0.667).astype(float); short_leg=(rank<=0.333).astype(float)
    nL=long_leg.sum(axis=1).replace(0,np.nan); nS=short_leg.sum(axis=1).replace(0,np.nan)
    w_xs=(long_leg.div(nL,axis=0)-short_leg.div(nS,axis=0)).shift(1)
    xs90=(w_xs*ret).sum(axis=1)
    show("  cross-sectional + vol-targeted (12%)", metrics(vol_target(xs90, 0.12)))

    # ── 9. within-asset-class cross-sectional (homogeneous → cleaner) ──
    print("\n── ITERATION 9: cross-sectional momentum WITHIN each asset class ──")
    legs = []
    for cls, syms in UNIVERSE.items():
        cols = [s for s in syms if s in P.columns]
        if len(cols) < 3: continue
        Pc = P[cols]; rc = Pc.pct_change(); volc = rc.rolling(30).std().shift(1)*np.sqrt(TD)
        mc = Pc.pct_change(90)/volc.replace(0,np.nan)
        rk = mc.rank(axis=1, pct=True)
        ll=(rk>=0.5).astype(float); sl=(rk<0.5).astype(float)
        nl=ll.sum(axis=1).replace(0,np.nan); ns=sl.sum(axis=1).replace(0,np.nan)
        wc=(ll.div(nl,axis=0)-sl.div(ns,axis=0)).shift(1)
        clsret=(wc*rc).sum(axis=1)
        m_cls = metrics(clsret)
        show(f"  XS within {cls}", m_cls)
        legs.append(clsret.rename(cls))
    xs_within = pd.concat(legs, axis=1).fillna(0).mean(axis=1)
    show("XS-within-class composite", metrics(xs_within))
    show("  + vol-targeted (12%)", metrics(vol_target(xs_within, 0.12)))

    # ── 10. FULL STACK: tangency-weighted blend of the alpha sleeves ──
    print("\n── ITERATION 10: tangency (max-Sharpe) blend of all alpha sleeves + vol target ──")
    sleeves = pd.concat([
        ts_cont.rename("TS_cont"),
        xs90.rename("XS_all"),
        xs_within.rename("XS_within"),
    ], axis=1).fillna(0)
    # correlation matrix of sleeves
    print("  sleeve correlations:")
    print("   ", sleeves.corr().round(2).to_string().replace("\n","\n    "))
    # rolling tangency weights: w ∝ Σ⁻¹ μ on trailing 252d, lagged
    win = 252
    W = pd.DataFrame(index=sleeves.index, columns=sleeves.columns, dtype=float)
    for i in range(win, len(sleeves)):
        sub = sleeves.iloc[i-win:i]
        mu = sub.mean().values
        cov = sub.cov().values + np.eye(len(mu))*1e-9
        try:
            raw = np.linalg.solve(cov, mu)
        except Exception:
            raw = np.ones(len(mu))
        raw = np.clip(raw, 0, None)          # long-only sleeves
        if raw.sum() <= 0: raw = np.ones(len(mu))
        W.iloc[i] = raw/raw.sum()
    W = W.shift(1).fillna(0)
    tangency = (W*sleeves).sum(axis=1)
    show("tangency-weighted blend", metrics(tangency))
    show("  + vol-targeted (12%)", metrics(vol_target(tangency, 0.12)))
    show("  + vol-targeted (15%)", metrics(vol_target(tangency, 0.15)))
    show("  + vol-targeted (20%)", metrics(vol_target(tangency, 0.20)))

    # ── SUMMARY ──
    print("\n" + "="*108)
    print("  SUMMARY — advanced-math ladder (best of each)")
    print("="*108)
    rows = [
        ("7. continuous vol-scaled TSMOM (vt12)", metrics(vol_target(ts_cont,0.12))),
        ("8. cross-sectional momo all (vt12)", metrics(vol_target(xs90,0.12))),
        ("9. XS-within-class composite (vt12)", metrics(vol_target(xs_within,0.12))),
        ("10. tangency blend (vt12)", metrics(vol_target(tangency,0.12))),
        ("10. tangency blend (vt15)", metrics(vol_target(tangency,0.15))),
        ("10. tangency blend (vt20)", metrics(vol_target(tangency,0.20))),
    ]
    print(f"  {'stage':<40}{'Sharpe':>8}{'CAGR':>9}{'MaxDD':>9}{'WR':>7}{'Calmar':>8}")
    print("  " + "-"*80)
    for lbl, m in rows:
        if m is None: continue
        print(f"  {lbl:<40}{m['sharpe']:>8.2f}{m['cagr']*100:>+8.1f}%"
              f"{m['maxdd']*100:>+8.1f}%{m['wr']*100:>+6.1f}%{m['calmar']:>8.2f}")


if __name__ == "__main__":
    main()
