"""
Kalman utilities for our trend engine.

Currently provides:
  - smooth_series(): univariate scalar Kalman filter (level-only state)
    for denoising HMM P_bull or any 0-1 probability series.

Background (Part 8.9 / 8.10):
  Raw HMM_state flips frequently on transient regime noise. A flip-on-state
  exit primitive (V1) destroys winners along with losers because the engine
  can't distinguish "noise flip" from "real regime turn". Kalman smoothing
  the underlying P_bull continuum lifts the precision of any downstream
  threshold or flip detection.

Model (level-only):
  x_t = x_{t-1} + w_t,  w_t ~ N(0, q)         (process / state noise)
  z_t = x_t + v_t,      v_t ~ N(0, r)         (observation noise)

Tuning:
  q controls how fast the smoothed value adapts to new evidence (large q
  → tracks raw quickly; small q → heavy smoothing).
  r controls how much we trust each observation.
  The signal-to-noise ratio q/r is what matters; absolute scale only sets
  the warmup transient. Default q=1e-4, r=1e-2 → ratio 0.01, which
  smooths the high-frequency HMM jitter without losing real regime turns.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def smooth_series(
    z: pd.Series | np.ndarray,
    process_var: float = 1e-4,
    obs_var: float = 1e-2,
    init_var: float = 1.0,
) -> pd.Series:
    """Apply a univariate Kalman filter to a noisy probability series.

    Args:
        z: input observations (e.g. HMM P_bull). NaNs are passed through
           (no measurement update on NaN bars).
        process_var: q. Larger → faster adaptation.
        obs_var: r. Larger → heavier smoothing.
        init_var: P_0. Initial state uncertainty; high → first observation
                  is trusted strongly.

    Returns:
        pd.Series of posterior means, same index as input.
    """
    is_series = isinstance(z, pd.Series)
    arr = np.asarray(z, dtype=float).ravel()
    n = len(arr)
    if n == 0:
        return z if is_series else pd.Series(arr)

    x = np.empty(n)
    P = np.empty(n)
    # init at first finite observation
    first_obs = next((arr[i] for i in range(n) if np.isfinite(arr[i])), 0.5)
    x_prev = float(first_obs)
    P_prev = init_var

    for t in range(n):
        # predict (random-walk dynamics)
        x_pred = x_prev
        P_pred = P_prev + process_var

        # update only on finite observations
        zt = arr[t]
        if np.isfinite(zt):
            K = P_pred / (P_pred + obs_var)
            x_new = x_pred + K * (zt - x_pred)
            P_new = (1.0 - K) * P_pred
        else:
            x_new = x_pred
            P_new = P_pred

        x[t] = x_new
        P[t] = P_new
        x_prev = x_new
        P_prev = P_new

    if is_series:
        return pd.Series(x, index=z.index, name=f"{z.name}_kalman" if z.name else None)
    return pd.Series(x)


def innovations(
    z: pd.Series | np.ndarray,
    process_var: float = 1e-4,
    obs_var: float = 1e-2,
    init_var: float = 1.0,
) -> pd.Series:
    """Return the per-bar innovation (observation − prediction).

    Useful as a feature for the ML loser-prob classifier (Part 8.10 item C):
    large positive innovations = regime accelerating bullish; large negative
    = regime accelerating bearish. Sign + magnitude both carry information.
    """
    is_series = isinstance(z, pd.Series)
    arr = np.asarray(z, dtype=float).ravel()
    n = len(arr)
    inn = np.empty(n)
    x_prev = float(next((arr[i] for i in range(n) if np.isfinite(arr[i])), 0.5))
    P_prev = init_var
    for t in range(n):
        x_pred = x_prev
        P_pred = P_prev + process_var
        zt = arr[t]
        if np.isfinite(zt):
            inn[t] = zt - x_pred
            K = P_pred / (P_pred + obs_var)
            x_prev = x_pred + K * inn[t]
            P_prev = (1.0 - K) * P_pred
        else:
            inn[t] = 0.0
            x_prev = x_pred
            P_prev = P_pred
    if is_series:
        return pd.Series(inn, index=z.index)
    return pd.Series(inn)
