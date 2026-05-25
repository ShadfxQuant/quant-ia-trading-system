"""
Real-time terminal monitor.

Polls yfinance every N minutes, runs the production pipeline on a list of
symbols, prints a compact status line per symbol, and ALERTS audibly on
new entries / pyramid-allowance transitions.

Usage:
    python -m watch_live                          # default: SPY + DIA, 5-min poll
    python -m watch_live SPY DIA QQQ              # custom symbols
    python -m watch_live SPY DIA --interval 60    # poll every 60 seconds
    python -m watch_live SPY DIA --once           # single pass, no loop

The poll interval should be aligned with bar close — for 1h bars, every
5-10 minutes catches new bars within ~15 min of close (yfinance free-tier
latency). For aggressive intra-bar monitoring, drop to 60s.

State tracking: alerts fire only on TRANSITIONS (e.g. flat → LONG, or
pyramid blocked → pyramid allowed). Re-firing on every poll is silenced.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from config.settings import PULLBACK, TRENDCARRY, BACKTEST
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pullback_profile
from strategies.trend_carry import exit_profile_for as trend_carry_profile
from execution.portfolio import run_portfolio, StrategySpec


DEFAULT_SYMBOLS = ["SPY", "DIA"]
DEFAULT_INTERVAL_SEC = 300   # 5 minutes


# --- prior state for transition detection ---
_PRIOR: dict[str, dict] = {}


def _alert_terminal(msg: str) -> None:
    """Print bold+colour + ASCII bell for terminal alert."""
    # ANSI yellow bold + bell + reset
    print(f"\033[1;33m\a*** {msg} ***\033[0m", flush=True)


def _alert_macos(title: str, body: str) -> None:
    """Native macOS notification via osascript (silent if not on macOS)."""
    if sys.platform != "darwin":
        return
    safe_title = title.replace('"', '')
    safe_body = body.replace('"', '')
    os.system(f'osascript -e \'display notification "{safe_body}" with title "{safe_title}"\' >/dev/null 2>&1')


def _snapshot_symbol(symbol: str, refresh: bool) -> dict:
    """Run the pipeline on one symbol; return a compact status dict."""
    raw = load_symbol(symbol, force_refresh=refresh)
    df = prepare_dual(raw)

    # Replay backtest (cheap on cached data) just to find currently-open positions.
    # Apply the 2.5× leverage for production parity.
    pb_base, pb_cap = PULLBACK.base_size_pct, PULLBACK.capital_cap_pct
    tc_base, tc_cap = TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct
    PULLBACK.base_size_pct = pb_base * 2.5
    PULLBACK.capital_cap_pct = pb_cap * 2.5
    TRENDCARRY.base_size_pct = tc_base * 2.5
    TRENDCARRY.capital_cap_pct = tc_cap * 2.5
    try:
        bt = run_portfolio(
            df,
            [
                StrategySpec(name="pullback", cfg=PULLBACK, exit_profile=pullback_profile()),
                StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_profile()),
            ],
            symbol=symbol,
        )
    finally:
        PULLBACK.base_size_pct, PULLBACK.capital_cap_pct = pb_base, pb_cap
        TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct = tc_base, tc_cap

    trades = bt["trades"]
    open_now = {"pullback": 0, "trend_carry": 0}
    if not trades.empty:
        eod = trades[trades["exit_reason"] == "end_of_data"]
        for n in open_now:
            open_now[n] = int(eod.loc[eod.strategy == n, "entry_time"].nunique())

    last = df.iloc[-1]
    ts = df.index[-1]
    sig = int(last.get("pullback_Signal", 0)) if not pd.isna(last.get("pullback_Signal", 0)) else 0
    tc_sig = int(last.get("trend_carry_Signal", 0)) if not pd.isna(last.get("trend_carry_Signal", 0)) else 0
    pyramid_ok = bool(last.get("pullback_PyramidOK", False))
    close = float(last["Close"])
    vwap = float(last.get("VWAP", float("nan")))

    return {
        "symbol": symbol,
        "ts": ts,
        "Close": close,
        "VWAP": vwap,
        "regime": str(last.get("Regime", "?")),
        "structure": str(last.get("Structure", "?")),
        "RegimeScore": float(last.get("RegimeScore", 0.5)),
        "RVOL": float(last.get("RVOL", float("nan"))),
        "above_vwap": close > vwap if not pd.isna(vwap) else False,
        "pullback_signal": sig,
        "trend_carry_signal": tc_sig,
        "pyramid_ok": pyramid_ok,
        "pyramid_cap": int(last.get("pullback_PyramidCap", 0))
            if not pd.isna(last.get("pullback_PyramidCap", 0)) else 0,
        "open_pullback": open_now["pullback"],
        "open_trend_carry": open_now["trend_carry"],
    }


def _transitions(symbol: str, s: dict) -> list[str]:
    """Return human-readable alert strings for new transitions vs last poll."""
    prior = _PRIOR.get(symbol)
    alerts = []

    cur_sig = s["pullback_signal"]
    cur_tc = s["trend_carry_signal"]
    cur_pyr = s["pyramid_ok"]

    if prior is None:
        # First poll — only alert on currently live signals, not transitions
        if cur_sig == 1:
            alerts.append(f"{symbol} PULLBACK LONG ACTIVE @ {s['Close']:.2f}")
        elif cur_sig == -1:
            alerts.append(f"{symbol} PULLBACK SHORT ACTIVE @ {s['Close']:.2f}")
        if cur_tc == 1:
            alerts.append(f"{symbol} TREND_CARRY LONG ACTIVE @ {s['Close']:.2f}")
    else:
        # Transitions
        if cur_sig != 0 and cur_sig != prior["pullback_signal"]:
            side = "LONG" if cur_sig == 1 else "SHORT"
            alerts.append(f"{symbol} NEW PULLBACK {side} @ {s['Close']:.2f}")
        if cur_tc != 0 and cur_tc != prior["trend_carry_signal"]:
            alerts.append(f"{symbol} NEW TREND_CARRY LONG @ {s['Close']:.2f}")
        if cur_pyr and not prior["pyramid_ok"] and (s["open_pullback"] > 0):
            alerts.append(f"{symbol} PYRAMID UNLOCKED (open stack: {s['open_pullback']})")

    _PRIOR[symbol] = s
    return alerts


def _format_row(s: dict) -> str:
    sig_str = "LONG " if s["pullback_signal"] == 1 else ("SHORT" if s["pullback_signal"] == -1 else "flat ")
    tc_str = "  ↑" if s["trend_carry_signal"] == 1 else "   "
    vwap_str = "↑VWAP" if s["above_vwap"] else "↓VWAP"
    pyr_str = "OK " if s["pyramid_ok"] else "blk"
    return (
        f"  {s['symbol']:4s}  "
        f"px={s['Close']:.2f}  "
        f"{vwap_str}  "
        f"reg={s['regime']:<13s}  "
        f"RS={s['RegimeScore']:.2f}  "
        f"RVOL={s['RVOL']:.2f}  "
        f"sig={sig_str}{tc_str}  "
        f"pyr={pyr_str}(cap{s['pyramid_cap']})  "
        f"open: pb={s['open_pullback']} tc={s['open_trend_carry']}"
    )


def _poll_once(symbols: list[str], refresh: bool) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n[{now}]  polling {len(symbols)} symbols  refresh={refresh}")
    print("-" * 110)
    for sym in symbols:
        try:
            s = _snapshot_symbol(sym, refresh)
            print(_format_row(s))
            for alert in _transitions(sym, s):
                _alert_terminal(alert)
                _alert_macos(f"Trading signal: {sym}", alert)
        except Exception as e:
            print(f"  {sym}  ERROR: {e}")


def _install_signal_handler() -> None:
    def _bye(signum, frame):
        print("\nstopped.")
        sys.exit(0)
    signal.signal(signal.SIGINT, _bye)
    signal.signal(signal.SIGTERM, _bye)


def main() -> None:
    p = argparse.ArgumentParser(description="Real-time trading dashboard monitor.")
    p.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS,
                   help="symbols to monitor (default: SPY DIA)")
    p.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC,
                   help=f"poll interval in seconds (default: {DEFAULT_INTERVAL_SEC})")
    p.add_argument("--once", action="store_true",
                   help="single pass; no loop")
    p.add_argument("--no-refresh", action="store_true",
                   help="use cached data instead of forcing yfinance refresh each poll")
    args = p.parse_args()

    _install_signal_handler()
    refresh = not args.no_refresh

    print(f"=== watch_live | symbols={args.symbols} | interval={args.interval}s | "
          f"refresh={refresh} ===")
    print("Ctrl+C to exit\n")

    if args.once:
        _poll_once(args.symbols, refresh)
        return

    while True:
        _poll_once(args.symbols, refresh)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
