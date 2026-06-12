"""
Loser-probability classifier (Part 8.30).

Trains a gradient-boosted tree to predict whether a trade will lose,
given the feature vector at the entry bar. Goal: filter high-loser-prob
entries to improve combined expectancy.

Features at entry bar (extracted from the live data pipeline):
  - P_bull_kalman (smoothed HMM regime score)
  - RSI(14)
  - ATR pct (atr/close)
  - EMA50 slope (3-bar)
  - Distance to EMA50 (% deviation)
  - Distance to SMA130 (% deviation)
  - Bar range z-score
  - Close position in bar (0-1)
  - Hour of day
  - Day of week
  - 5-bar return
  - 20-bar return
  - Realized vol (20bar)
  - Bull regime flag (EMA50 > SMA130)

Label: 1 if trade pnl < 0 (loser), else 0.

Output:
  - Trained model saved to data/loser_classifier.pkl
  - Feature importance ranking
  - Train / OOS AUC, accuracy, precision/recall at threshold 0.5
  - Counterfactual: "if we'd filtered entries with loser_prob > 0.7,
    what's the lift?"
"""
from __future__ import annotations
import warnings, logging
warnings.filterwarnings("ignore")
logging.getLogger("statsmodels").setLevel(logging.ERROR)

import pickle
import numpy as np
import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score, precision_recall_fscore_support

from config.settings import PULLBACK, TRENDCARRY, get_pullback_cfg
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pb_exit
from strategies.trend_carry import exit_profile_for as tc_exit
from execution.portfolio import run_portfolio, StrategySpec

SYMBOLS = ["SPY", "^NDX", "GLD", "GC=F"]
INITIAL = 100_000.0
FEATURE_COLS = [
    "p_bull_kalman", "rsi", "atr_pct", "ema_slope_pct",
    "ema_dist_pct", "sma_dist_pct", "range_z", "close_pos",
    "hour", "dow", "ret_5", "ret_20", "rvol", "bull_regime",
]


def asof(s, t):
    try: return s.asof(pd.Timestamp(t))
    except Exception: return None


def extract_features(df, t):
    """Pull feature vector at bar timestamp t."""
    try:
        i = df.index.get_indexer([pd.Timestamp(t)], method="bfill")[0]
        if i < 0 or i >= len(df): return None
        row = df.iloc[i]
        if i < 20: return None    # need history for rolling features
        c = row["Close"]
        atr = row.get("ATR", np.nan)
        rsi = row.get("RSI_14", row.get("RSI", 50.0))
        if pd.isna(rsi):
            from research.proxies import rsi as _rsi
            rsi = _rsi(df.iloc[max(0, i-30):i+1]).iloc[-1]
        ema = row.get("EMA", c)
        sma = row.get("SMA", c)
        slope_3 = (ema - df["EMA"].iloc[i-3]) / 3 if "EMA" in df else 0
        ret_5 = (c / df["Close"].iloc[i-5] - 1) if i >= 5 else 0
        ret_20 = (c / df["Close"].iloc[i-20] - 1) if i >= 20 else 0
        rng = row["High"] - row["Low"]
        rng_window = df.iloc[max(0,i-50):i+1]
        rng_mu = (rng_window["High"] - rng_window["Low"]).mean()
        rng_sd = (rng_window["High"] - rng_window["Low"]).std()
        range_z = (rng - rng_mu) / rng_sd if rng_sd > 0 else 0
        close_pos = (c - row["Low"]) / max(rng, 1e-9)
        log_ret = np.log(df["Close"].iloc[max(0,i-19):i+1] / df["Close"].iloc[max(0,i-19):i+1].shift(1))
        rvol = log_ret.std() if len(log_ret) > 5 else 0
        return {
            "p_bull_kalman": float(row.get("P_bull_kalman", 0.5)),
            "rsi":           float(rsi),
            "atr_pct":       float(atr / c) if c > 0 and not pd.isna(atr) else 0.02,
            "ema_slope_pct": float(slope_3 / c) if c > 0 else 0,
            "ema_dist_pct":  float((c - ema) / ema) if ema > 0 else 0,
            "sma_dist_pct":  float((c - sma) / sma) if sma > 0 else 0,
            "range_z":       float(range_z),
            "close_pos":     float(close_pos),
            "hour":          float(row.name.hour if hasattr(row.name, "hour") else 0),
            "dow":           float(row.name.dayofweek if hasattr(row.name, "dayofweek") else 0),
            "ret_5":         float(ret_5),
            "ret_20":        float(ret_20),
            "rvol":          float(rvol),
            "bull_regime":   float(1 if ema > sma else 0),
        }
    except Exception:
        return None


def collect_training_set():
    rows = []
    print("  Collecting trades + entry features from each symbol...")
    for s in SYMBOLS:
        df = prepare_dual(load_symbol(s))
        cfg = get_pullback_cfg(s)
        bt = run_portfolio(df, [
            StrategySpec("pullback", cfg, pb_exit(cfg)),
            StrategySpec("trend_carry", TRENDCARRY, tc_exit()),
        ], symbol=s, initial_capital=INITIAL)
        tr = bt["trades"]
        for _, t in tr.iterrows():
            feats = extract_features(df, t["entry_time"])
            if feats is None: continue
            feats["symbol"] = s
            feats["entry_time"] = t["entry_time"]
            feats["pnl"] = float(t["pnl"])
            feats["is_loser"] = int(t["pnl"] < 0)
            rows.append(feats)
        print(f"    {s:<8} → {len(tr)} trades")
    return pd.DataFrame(rows)


def main():
    print("\n" + "="*100)
    print("  LIGHTGBM-STYLE LOSER-PROB CLASSIFIER (Part 8.30 Phase 4)")
    print("="*100)

    df = collect_training_set()
    print(f"\n  Total trades collected: {len(df)}")
    print(f"  Loser rate (baseline):  {df['is_loser'].mean()*100:.1f}%")

    # Chronological split (walk-forward style): first 70% train, last 30% test
    df = df.sort_values("entry_time").reset_index(drop=True)
    split = int(len(df) * 0.70)
    train, test = df.iloc[:split], df.iloc[split:]
    print(f"  Train: {len(train)}  ·  Test (OOS): {len(test)}  "
          f"({train['entry_time'].iloc[0].strftime('%Y-%m')} → "
          f"{train['entry_time'].iloc[-1].strftime('%Y-%m')} train, "
          f"{test['entry_time'].iloc[0].strftime('%Y-%m')} → "
          f"{test['entry_time'].iloc[-1].strftime('%Y-%m')} test)")

    X_train = train[FEATURE_COLS].fillna(0).values
    y_train = train["is_loser"].values
    X_test  = test[FEATURE_COLS].fillna(0).values
    y_test  = test["is_loser"].values

    # ─── Train ───
    print(f"\n  Training GradientBoostingClassifier (200 estimators, depth 3)...")
    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05,
        min_samples_split=20, random_state=42,
    )
    model.fit(X_train, y_train)

    # ─── Evaluate ───
    p_train = model.predict_proba(X_train)[:, 1]
    p_test  = model.predict_proba(X_test)[:, 1]

    print(f"\n  AUC train: {roc_auc_score(y_train, p_train):.3f}")
    print(f"  AUC OOS:   {roc_auc_score(y_test, p_test):.3f}")
    print(f"  Accuracy OOS @ thr=0.5: {accuracy_score(y_test, p_test > 0.5):.3f}")

    # ─── Feature importance ───
    print(f"\n  Feature importance (top 10):")
    imp = sorted(zip(FEATURE_COLS, model.feature_importances_),
                 key=lambda x: x[1], reverse=True)
    for name, val in imp[:10]:
        print(f"    {name:<20} {val:.4f}")

    # ─── Counterfactual: filter high-loser-prob entries ───
    print(f"\n  ── COUNTERFACTUAL FILTERING ──")
    print(f"  Baseline OOS PnL:           ${test['pnl'].sum():+,.0f}  (n={len(test)})")
    for thr in (0.3, 0.4, 0.5, 0.6, 0.7):
        kept = test[p_test < thr]
        kept_pnl = kept["pnl"].sum()
        kept_n = len(kept)
        kept_wr = (kept["pnl"] > 0).mean() * 100 if kept_n else 0
        delta = kept_pnl - test["pnl"].sum()
        print(f"  Filter loser_prob > {thr:.1f}:   "
              f"${kept_pnl:+,.0f}  (n={kept_n:>3}, wr={kept_wr:.1f}%)  "
              f"Δ vs baseline: ${delta:+,.0f}")

    # ─── Save model ───
    import os
    os.makedirs("data", exist_ok=True)
    with open("data/loser_classifier.pkl", "wb") as f:
        pickle.dump({
            "model": model,
            "features": FEATURE_COLS,
            "trained_at_utc": pd.Timestamp.utcnow().isoformat(),
            "n_train": len(train),
            "n_test": len(test),
            "auc_train": float(roc_auc_score(y_train, p_train)),
            "auc_test": float(roc_auc_score(y_test, p_test)),
        }, f)
    print(f"\n  Saved → data/loser_classifier.pkl")


if __name__ == "__main__":
    main()
