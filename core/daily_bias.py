"""
Daily directional bias — BULL / BEAR / RANGING per symbol.

Combines the Kalman-smoothed HMM regime (P_bull_kalman) with the deterministic
EMA50/SMA130 structure:

    BULL     HMM bullish (P_bull_kalman > 0.60) AND structure bull (EMA > SMA)
    BEAR     HMM bearish (P_bull_kalman < 0.40) AND structure bear (EMA < SMA)
    RANGING  the two disagree, OR the HMM sits in the undecided 0.40-0.60 band

The "agreement" test is what produces a meaningful RANGING state: when the
regime model and the price structure point opposite ways (a transition or a
chop), there is no clean directional edge, so the bias is RANGING.

IMPORTANT — this is an INFORMATIONAL context layer, not a trade gate. Gating
entries on the HMM regressed the engine (SYSTEM_LOG Parts #6/#7, regime-flip
exit, regime suppression — all rejected). This surfaces a daily bias for the
dashboard / the user's discretion; it does NOT block the pullback/trend_carry
signals.

Writes data/daily_bias.json. Run standalone:  python -m core.daily_bias
"""
from __future__ import annotations

import contextlib
import io
import json
import os
from datetime import datetime, timezone

BIAS_PATH = os.path.join("data", "daily_bias.json")

BULL_TH = 0.60
BEAR_TH = 0.40


def classify(p_bull_kalman: float, structure_bull: bool) -> str:
    """Return BULL / BEAR / RANGING from the HMM prob + structure agreement."""
    hmm_bull = p_bull_kalman > BULL_TH
    hmm_bear = p_bull_kalman < BEAR_TH
    if hmm_bull and structure_bull:
        return "BULL"
    if hmm_bear and not structure_bull:
        return "BEAR"
    return "RANGING"


def compute_daily_bias(symbols=None, write: bool = True) -> dict:
    """Compute the daily bias for each symbol and (optionally) write the JSON."""
    from config.settings import DATA
    from core.data_loader import load_symbol
    from main_portfolio import prepare_dual

    symbols = symbols or list(DATA.symbols)
    bias = {}
    for s in symbols:
        with contextlib.redirect_stdout(io.StringIO()):
            df = prepare_dual(load_symbol(s, force_refresh=True))
        last = df.iloc[-1]
        pbk = float(last.get("P_bull_kalman", float("nan")))
        structure_bull = float(last["EMA"]) > float(last["SMA"])
        bias[s] = {
            "bias": classify(pbk, structure_bull),
            "p_bull_kalman": round(pbk, 3),
            "structure": "BULL" if structure_bull else "BEAR",
            "rsi": round(float(last.get("RSI_14", float("nan"))), 1),
            "close": round(float(last["Close"]), 2),
        }
    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "method": "HMM(P_bull_kalman) x EMA50/SMA130 structure; RANGING on disagreement",
        "informational_only": True,
        "bias": bias,
    }
    if write:
        os.makedirs("data", exist_ok=True)
        with open(BIAS_PATH, "w") as f:
            json.dump(payload, f, indent=2)
    return payload


if __name__ == "__main__":
    p = compute_daily_bias()
    print(f"daily bias @ {p['generated_at_utc'][:19]} UTC")
    for sym, b in p["bias"].items():
        print(f"  {sym:5} {b['bias']:8}  (P_bull_k={b['p_bull_kalman']:.2f} "
              f"struct={b['structure']} RSI={b['rsi']})")
    print(f"written to {BIAS_PATH}")
