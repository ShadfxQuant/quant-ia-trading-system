"""
Hidden Markov Model regime overlay.

Probabilistic complement to the deterministic `regime_model` classifier.
The deterministic Regime column is preserved unchanged; this module ADDS
posterior probability columns:

    P_bull, P_bear, P_range, HMM_state

Training discipline (no lookahead):
  * Train on a trailing window (default 6 months on 1h bars).
  * Predict the next month forward only.
  * Refit at the end of each prediction window (default monthly).
  * Bars before the first prediction window emit P_* = NaN.

State labelling:
  * After each refit, states are renamed by the mean return of their
    Gaussian emission distribution: highest-mean -> bull, lowest -> bear,
    middle -> range.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pandas as pd

try:
    from hmmlearn.hmm import GaussianHMM
except ImportError:                          # pragma: no cover
    GaussianHMM = None


@dataclass
class HMMConfig:
    n_states: int = 3
    train_bars: int = 6 * 21 * 7             # ~6 months of 1h bars (21 trading days/mo, 7 hours/session)
    refit_every_bars: int = 21 * 7           # refit monthly (~21 trading days)
    min_train_bars: int = 200                # don't fit unless we have at least this many
    covariance_type: str = "full"
    n_iter: int = 50
    random_state: int = 42


def _build_features(close: pd.Series, vol_ratio: pd.Series) -> pd.DataFrame:
    """Two-column observation matrix: log returns + scaled vol-ratio."""
    log_ret = np.log(close).diff()
    vr = vol_ratio.fillna(1.0)
    feats = pd.concat([log_ret, vr], axis=1)
    feats.columns = ["log_ret", "vol_ratio"]
    return feats


def _fit_one(features: np.ndarray, cfg: HMMConfig) -> GaussianHMM | None:
    if GaussianHMM is None:
        raise RuntimeError("hmmlearn not installed. `pip install hmmlearn`.")
    if len(features) < cfg.min_train_bars:
        return None
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        model = GaussianHMM(
            n_components=cfg.n_states,
            covariance_type=cfg.covariance_type,
            n_iter=cfg.n_iter,
            random_state=cfg.random_state,
        )
        try:
            model.fit(features)
        except Exception:
            return None
    return model


def _label_states(model: GaussianHMM) -> dict[int, str]:
    """Map raw state index -> bull/bear/range based on Gaussian means of log_ret."""
    means = model.means_[:, 0]               # column 0 is log_ret
    order = np.argsort(means)                # ascending: bear, range, bull
    labels = {}
    if len(order) == 1:
        labels[order[0]] = "bull"
    elif len(order) == 2:
        labels[order[0]] = "bear"
        labels[order[1]] = "bull"
    else:
        labels[order[0]] = "bear"
        labels[order[-1]] = "bull"
        for k in order[1:-1]:
            labels[k] = "range"
    return labels


def attach_hmm_probabilities(df: pd.DataFrame, cfg: HMMConfig | None = None) -> pd.DataFrame:
    """
    Walk-forward HMM with rolling refits. Returns `df` with added columns:
        P_bull, P_bear, P_range, HMM_state.
    Bars before the first usable training window have NaN probabilities.
    """
    cfg = cfg or HMMConfig()
    if "Close" not in df.columns or "Vol_ratio" not in df.columns:
        raise ValueError("Need Close and Vol_ratio columns; run indicators first.")

    out = df.copy()
    feats = _build_features(out["Close"], out["Vol_ratio"]).dropna()
    aligned_index = feats.index

    p_bull = pd.Series(np.nan, index=out.index)
    p_bear = pd.Series(np.nan, index=out.index)
    p_range = pd.Series(np.nan, index=out.index)
    state_lbl = pd.Series(pd.NA, index=out.index, dtype=object)

    n = len(aligned_index)
    train_w = cfg.train_bars
    step = cfg.refit_every_bars

    if n < train_w + 1:
        out["P_bull"] = p_bull
        out["P_bear"] = p_bear
        out["P_range"] = p_range
        out["HMM_state"] = state_lbl
        return out

    # Walk-forward: fit on [start: t], score on (t : t+step], advance.
    t = train_w
    while t < n:
        train_X = feats.iloc[t - train_w : t].to_numpy()
        model = _fit_one(train_X, cfg)
        if model is None:
            t += step
            continue

        labels = _label_states(model)
        end = min(t + step, n)
        score_X = feats.iloc[t:end].to_numpy()
        if len(score_X) == 0:
            break
        try:
            posteriors = model.predict_proba(score_X)
        except Exception:
            t += step
            continue

        idx_slice = aligned_index[t:end]
        # Aggregate posteriors by labelled regime.
        for raw_state, lbl in labels.items():
            col = posteriors[:, raw_state]
            target = {"bull": p_bull, "bear": p_bear, "range": p_range}[lbl]
            existing = target.loc[idx_slice].values
            # If multiple raw states map to the same label, sum their probs.
            new_vals = np.where(np.isnan(existing), col, existing + col)
            target.loc[idx_slice] = new_vals

        # Top label per bar.
        argmax = np.argmax(posteriors, axis=1)
        state_lbl.loc[idx_slice] = [labels[s] for s in argmax]

        t += step

    out["P_bull"] = p_bull
    out["P_bear"] = p_bear
    out["P_range"] = p_range
    out["HMM_state"] = state_lbl
    return out
