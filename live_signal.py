"""
LIVE SIGNAL TERMINAL — production signal output.

This is NOT a backtest. It pulls the latest SPY/DIA 1H data from yfinance,
runs the production strategy, and prints a clean trade card with directional,
sizing and exit-ladder information expressed as percentages of account.

Output shows:
    * direction (LONG / SHORT / flat)
    * position size as a percentage of account (and in dollars for the
      configured account size)
    * stop / TP percentages — apply directly to any instrument that tracks
      SPY's percentage moves (ETF, futures, perp, tokenised SPX, etc.)
    * pyramid permission (whether to ADD to an existing position)
    * NYSE market-hours flag (signals are only fresh while NYSE trades)

Percent moves are scale-invariant — the exit ladder works on any size book
and on any instrument that mirrors SPY's percentage returns.

Educational only — not investment advice.

Usage:
    python -m live_signal                          # SPY, default settings
    python -m live_signal --account 10000          # show dollar sizes for $10K
    python -m live_signal --symbol DIA             # signal from DIA instead
    python -m live_signal --refresh                # force fresh yfinance pull
    python -m live_signal --watch --interval 600   # 10-min poll loop
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta

import pandas as pd

from config.settings import PULLBACK, TRENDCARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from strategies.pullback import exit_profile_for as pullback_profile
from strategies.trend_carry import exit_profile_for as trend_carry_profile
from execution.portfolio import run_portfolio, StrategySpec
import journal
try:
    from core.news_macro import print_news_warning
except Exception:                                                  # pragma: no cover
    def print_news_warning(side: int, symbol: str = "SPY") -> None:  # graceful no-op fallback
        return


# ---------------------------------------------------------------------------
# NYSE hours (no holidays; that's fine for live use — you'll see it's flat)
# ---------------------------------------------------------------------------

NYSE_OPEN_UTC_HOUR = 13   # 09:30 ET = 13:30 UTC (during EDT; 14:30 UTC during EST)
NYSE_OPEN_UTC_MIN = 30
NYSE_CLOSE_UTC_HOUR = 20  # 16:00 ET = 20:00 UTC (EDT)


def _nyse_status(now_utc: datetime | None = None) -> tuple[bool, str]:
    """Returns (is_open, human_string). Crude — DST may shift ±1h; close enough."""
    now = now_utc or datetime.now(timezone.utc)
    if now.weekday() >= 5:
        return False, "WEEKEND — NYSE closed (signals are stale until market reopens)"
    open_t = now.replace(hour=NYSE_OPEN_UTC_HOUR, minute=NYSE_OPEN_UTC_MIN, second=0, microsecond=0)
    close_t = now.replace(hour=NYSE_CLOSE_UTC_HOUR, minute=0, second=0, microsecond=0)
    if open_t <= now <= close_t:
        return True, f"NYSE OPEN — signals are fresh"
    if now < open_t:
        delta = open_t - now
        return False, f"NYSE PRE-MARKET — opens in {delta}"
    delta = (open_t + timedelta(days=1)) - now
    return False, f"NYSE CLOSED — opens in {delta}"


# ---------------------------------------------------------------------------
# Run pipeline & extract latest snapshot
# ---------------------------------------------------------------------------

def _snapshot(symbol: str, refresh: bool, account: float, leverage: float) -> dict:
    """Run the production pipeline; extract latest-bar snapshot with sizing."""
    pb_base, pb_cap = PULLBACK.base_size_pct, PULLBACK.capital_cap_pct
    tc_base, tc_cap = TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct
    PULLBACK.base_size_pct = pb_base * leverage
    PULLBACK.capital_cap_pct = pb_cap * leverage
    TRENDCARRY.base_size_pct = tc_base * leverage
    TRENDCARRY.capital_cap_pct = tc_cap * leverage

    try:
        raw = load_symbol(symbol, force_refresh=refresh)
        df = prepare_dual(raw)
        bt = run_portfolio(
            df,
            [
                StrategySpec(name="pullback",    cfg=PULLBACK,   exit_profile=pullback_profile()),
                StrategySpec(name="trend_carry", cfg=TRENDCARRY, exit_profile=trend_carry_profile()),
            ],
            symbol=symbol,
            initial_capital=account,
        )
    finally:
        PULLBACK.base_size_pct, PULLBACK.capital_cap_pct = pb_base, pb_cap
        TRENDCARRY.base_size_pct, TRENDCARRY.capital_cap_pct = tc_base, tc_cap

    last = df.iloc[-1]
    ts = df.index[-1]

    trades = bt["trades"]
    sim_open = {"pullback": 0, "trend_carry": 0}
    if not trades.empty:
        eod = trades[trades["exit_reason"] == "end_of_data"]
        for n in sim_open:
            sim_open[n] = int(eod.loc[eod.strategy == n, "entry_time"].nunique())

    # Effective thresholds and stop produced by the strategy (ATR-normalized).
    stop_pct = float(last.get("pullback_StopPctOverride", PULLBACK.stop_loss_pct))
    pullback_band_eff = float(last.get("pullback_PullbackBandEff", PULLBACK.pullback_band))

    pb_sig = int(last.get("pullback_Signal", 0)) if not pd.isna(last.get("pullback_Signal", 0)) else 0
    tc_sig = int(last.get("trend_carry_Signal", 0)) if not pd.isna(last.get("trend_carry_Signal", 0)) else 0

    return {
        "symbol": symbol,
        "ts": ts,
        "Close": float(last["Close"]),
        "VWAP": float(last.get("VWAP", float("nan"))),
        "EMA": float(last.get("EMA", float("nan"))),
        "SMA": float(last.get("SMA", float("nan"))),
        "ATR": float(last.get("ATR", float("nan"))),
        "Regime": str(last.get("Regime", "?")),
        "Structure": str(last.get("Structure", "?")),
        "RegimeScore": float(last.get("RegimeScore", 0.5)),
        "RVOL": float(last.get("RVOL", float("nan"))),
        "PullbackBandEff": pullback_band_eff,
        "StopPct": stop_pct,
        "pullback_signal": pb_sig,
        "trend_carry_signal": tc_sig,
        "pyramid_ok": bool(last.get("pullback_PyramidOK", False)),
        "pyramid_cap": int(last.get("pullback_PyramidCap", 0))
            if not pd.isna(last.get("pullback_PyramidCap", 0)) else 0,
        "sim_open_pullback": sim_open["pullback"],
        "sim_open_trend_carry": sim_open["trend_carry"],
    }


# ---------------------------------------------------------------------------
# Trade card rendering
# ---------------------------------------------------------------------------

def _render(snap: dict, account: float, leverage: float) -> None:
    is_open, mkt_status = _nyse_status()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

    base_size_pct = PULLBACK.base_size_pct * leverage
    cap_pct = PULLBACK.capital_cap_pct * leverage
    max_notional = account * cap_pct
    base_notional = account * base_size_pct

    # Exit-ladder percentages — universal, apply to any SPY-correlated instrument.
    stop_pct = snap["StopPct"]
    tp1_pct = PULLBACK.partial_tp_pct
    tp2_pct = PULLBACK.final_tp_pct
    tc_stop_pct = TRENDCARRY.stop_loss_pct
    tc_tp1_pct = TRENDCARRY.partial_tp_pct
    tc_tp2_pct = TRENDCARRY.final_tp_pct
    tc_base_notional = account * TRENDCARRY.base_size_pct * leverage

    print(f"\n{'═' * 78}")
    print(f"  LIVE SIGNAL · {now}")
    print(f"  Account ${account:.2f}  ·  Leverage {leverage}x  ·  Max notional ${max_notional:.2f}")
    print(f"  Signal source: {snap['symbol']} 1H  ·  Bar close: {snap['ts']}")
    print(f"{'═' * 78}")
    print(f"  Market status     : {'🟢 ' if is_open else '🔴 '}{mkt_status}")
    print(f"  Signal-source px  : ${snap['Close']:.2f}")
    print(f"  EMA(50) / SMA(130): {snap['EMA']:.2f} / {snap['SMA']:.2f}")
    print(f"  VWAP              : {snap['VWAP']:.2f}  "
          f"({'price ABOVE' if snap['Close'] > snap['VWAP'] else 'price BELOW'})")
    print(f"  Structure         : {snap['Structure']}")
    print(f"  Regime            : {snap['Regime']}    "
          f"RegimeScore: {snap['RegimeScore']:.2f}")
    print(f"  RVOL (diagnostic) : {snap['RVOL']:.2f}")

    # --- Decision section ---
    pb = snap["pullback_signal"]
    tc = snap["trend_carry_signal"]
    print(f"\n  {'─' * 76}")
    if pb == 1:
        side = "LONG"
        sign = +1
    elif pb == -1:
        side = "SHORT"
        sign = -1
    else:
        side = None
        sign = 0

    if side is not None:
        # Dollar P&L math at each exit level (50% closed at TP1, 50% at TP2).
        loss_at_stop   = base_notional * stop_pct          # full position stops out
        profit_at_tp1  = base_notional * tp1_pct * PULLBACK.partial_tp_size   # 50% × +4%
        profit_at_tp2  = base_notional * tp2_pct * PULLBACK.final_tp_size     # 50% × +15%
        total_potential = profit_at_tp1 + profit_at_tp2
        rr = total_potential / loss_at_stop if loss_at_stop > 0 else float("inf")

        print(f"  🔔 PULLBACK {side} TRIGGERED")
        print(f"  {'─' * 76}")
        print(f"     Position size : ${base_notional:.2f}  "
              f"({base_size_pct:.0%} of account)")
        print(f"     Stop          : {-stop_pct * sign * 100:+.2f}%  →  "
              f"loss if hit: −${loss_at_stop:.2f}")
        print(f"     TP1 (close 50%): {tp1_pct * sign * 100:+.2f}%  →  "
              f"profit booked: +${profit_at_tp1:.2f}  (then trail stop to BE)")
        print(f"     TP2 (close 50%): {tp2_pct * sign * 100:+.2f}%  →  "
              f"profit booked: +${profit_at_tp2:.2f}")
        print(f"     ───────────────────────────────────────────")
        print(f"     💰 If both TPs hit: +${total_potential:.2f}  ·  "
              f"Worst case: −${loss_at_stop:.2f}  ·  R:R = {rr:.2f}×")
        print(f"     Time stop     : 390 bars (~3 months on 1h)")
        # Macro-sanity check: warn only on mismatch, never blocks the trade.
        # Symbol-aware so gold (inverse polarity) doesn't false-flag.
        print_news_warning(sign, symbol=snap.get("symbol", "SPY"))
        print(f"")
        print(f"  ▶ ACTION:")
        print(f"     1. Check your instrument's CURRENT price.")
        print(f"     2. Enter {side} for ${base_notional:.2f} at market.")
        print(f"     3. Bracket: stop = (current × {1 - stop_pct * sign:.4f}),")
        print(f"                 TP1  = (current × {1 + tp1_pct * sign:.4f}),")
        print(f"                 TP2  = (current × {1 + tp2_pct * sign:.4f}).")
        print(f"     4. After TP1 fills, move stop to your original entry price.")
    else:
        # Even when no signal, show what TPs WOULD look like if you took a long
        # at the current price — useful for late-entry decisions / dry-runs.
        hyp_loss = base_notional * stop_pct
        hyp_tp1  = base_notional * tp1_pct * PULLBACK.partial_tp_size
        hyp_tp2  = base_notional * tp2_pct * PULLBACK.final_tp_size
        hyp_total = hyp_tp1 + hyp_tp2
        print(f"  No pullback signal on the latest bar.")
        print(f"  (If you entered a long now at ${snap['Close']:.2f} with ${base_notional:.2f}:")
        print(f"     stop −{stop_pct*100:.2f}% = −${hyp_loss:.2f}  ·  "
              f"TP1 +{tp1_pct*100:.0f}% = +${hyp_tp1:.2f}  ·  "
              f"TP2 +{tp2_pct*100:.0f}% = +${hyp_tp2:.2f}  ·  "
              f"both hit = +${hyp_total:.2f})")

    if tc == 1:
        tc_loss = tc_base_notional * tc_stop_pct
        tc_tp1_profit = tc_base_notional * tc_tp1_pct * TRENDCARRY.partial_tp_size
        tc_tp2_profit = tc_base_notional * tc_tp2_pct * TRENDCARRY.final_tp_size
        tc_total = tc_tp1_profit + tc_tp2_profit
        tc_rr = tc_total / tc_loss if tc_loss > 0 else float("inf")
        print(f"\n  🔔 TREND-CARRY LONG also triggered (separate sleeve)")
        print(f"     Position size : ${tc_base_notional:.2f}  "
              f"({TRENDCARRY.base_size_pct * leverage:.0%} of account)")
        print(f"     Stop          : -{tc_stop_pct * 100:.2f}%  →  loss: −${tc_loss:.2f}")
        print(f"     TP1 (close 30%): +{tc_tp1_pct * 100:.2f}%  →  profit: +${tc_tp1_profit:.2f}")
        print(f"     TP2 (close 70%): +{tc_tp2_pct * 100:.2f}%  →  profit: +${tc_tp2_profit:.2f}")
        print(f"     ───────────────────────────────────────────")
        print(f"     💰 If both TPs hit: +${tc_total:.2f}  ·  R:R = {tc_rr:.2f}×")
        print(f"     Trailing stop : ATR×3.0 after TP1  ·  Max hold: 1500 bars (~9mo)")
        # Macro-sanity check (trend-carry is long-only).
        print_news_warning(+1, symbol=snap.get("symbol", "SPY"))

    # --- Pyramid status ---
    print(f"\n  {'─' * 76}")
    print(f"  PYRAMID STATUS")
    if snap["pyramid_ok"]:
        print(f"     ✓ Pyramiding ALLOWED — all gates clear")
        print(f"       (bullish structure + regime ∈ {{growth, slowdown}} + above VWAP + momentum > 0)")
        print(f"     Stack cap: {snap['pyramid_cap']}")
        if snap["sim_open_pullback"] > 0:
            print(f"     If you're currently long, you may ADD ${base_notional:.2f} "
                  f"more (use same stop / TPs as fresh entry).")
    else:
        print(f"     ✗ Pyramiding BLOCKED — at least one gate failed.")
        if snap["Structure"] != "bullish":
            print(f"       · structure is {snap['Structure']!r}, not bullish")
        if snap["Regime"] not in ("growth", "slowdown"):
            print(f"       · regime is {snap['Regime']!r}, not growth/slowdown")
        if snap["Close"] <= snap["VWAP"]:
            print(f"       · price is below VWAP")

    # --- Simulated context (the backtest's open positions, for reference) ---
    print(f"\n  {'─' * 76}")
    print(f"  Sim context (backtest replay — not your real positions):")
    print(f"     pullback open stacks   : {snap['sim_open_pullback']}")
    print(f"     trend_carry open stacks: {snap['sim_open_trend_carry']}")
    print(f"  (Track your actual positions yourself; this is just a reference)")
    print(f"{'═' * 78}")


# ---------------------------------------------------------------------------
# Journal hook — auto-record SIGNAL rows on transitions
# ---------------------------------------------------------------------------

def _maybe_journal_signal(snap: dict, account: float, leverage: float,
                           last_bar_logged: dict[str, str]) -> None:
    """Append a SIGNAL row IF (a) signal is non-flat AND (b) this bar wasn't
    already journaled. Dedupe key is the bar timestamp — re-polls within the
    same hour don't double-log.
    """
    bar_key = f"{snap['symbol']}|{snap['ts']}"
    if last_bar_logged.get(snap["symbol"]) == str(snap["ts"]):
        return  # already logged this bar

    base_notional_pullback = account * PULLBACK.base_size_pct * leverage
    base_notional_trend = account * TRENDCARRY.base_size_pct * leverage

    pb = snap["pullback_signal"]
    tc = snap["trend_carry_signal"]

    if pb != 0:
        journal.log_signal(
            symbol=snap["symbol"],
            side=("LONG" if pb == 1 else "SHORT"),
            strategy="pullback",
            bar_time=str(snap["ts"]),
            system_price=snap["Close"],
            stop_pct=snap["StopPct"],
            tp1_pct=PULLBACK.partial_tp_pct,
            tp2_pct=PULLBACK.final_tp_pct,
            position_usd=base_notional_pullback,
            notes=f"regime={snap['Regime']} score={snap['RegimeScore']:.2f} pyramid_ok={snap['pyramid_ok']}",
        )
        print(f"  📓 journal: SIGNAL row written (pullback {('LONG' if pb == 1 else 'SHORT')})")

    if tc == 1:
        journal.log_signal(
            symbol=snap["symbol"],
            side="LONG",
            strategy="trend_carry",
            bar_time=str(snap["ts"]),
            system_price=snap["Close"],
            stop_pct=TRENDCARRY.stop_loss_pct,
            tp1_pct=TRENDCARRY.partial_tp_pct,
            tp2_pct=TRENDCARRY.final_tp_pct,
            position_usd=base_notional_trend,
            notes=f"regime={snap['Regime']} score={snap['RegimeScore']:.2f}",
        )
        print(f"  📓 journal: SIGNAL row written (trend_carry LONG)")

    last_bar_logged[snap["symbol"]] = str(snap["ts"])


# ---------------------------------------------------------------------------
# Watch loop
# ---------------------------------------------------------------------------

def _install_signal_handler() -> None:
    def _bye(signum, frame):
        print("\nstopped.")
        sys.exit(0)
    signal.signal(signal.SIGINT, _bye)
    signal.signal(signal.SIGTERM, _bye)


def main() -> None:
    p = argparse.ArgumentParser(description="Live trade-signal terminal.")
    p.add_argument("--symbol", default="SPY",
                   help="Signal source (yfinance ticker). SPY default; DIA also good.")
    p.add_argument("--account", type=float, default=10000.0,
                   help="Account size in USD for the dollar display (default: 10000). "
                        "Percentages in the trade card are scale-invariant.")
    p.add_argument("--leverage", type=float, default=2.5,
                   help="Leverage multiplier (default: 2.5)")
    p.add_argument("--refresh", action="store_true", default=True,
                   help="Force fresh yfinance pull (default on)")
    p.add_argument("--no-refresh", dest="refresh", action="store_false",
                   help="Use cached data instead of re-downloading")
    p.add_argument("--watch", action="store_true",
                   help="Keep polling on an interval; alert on transitions")
    p.add_argument("--interval", type=int, default=600,
                   help="Poll interval in seconds (default: 600 = 10 min)")
    p.add_argument("--journal", action="store_true",
                   help="Auto-log SIGNAL rows on signal transitions "
                        "(into data/trade_journal.csv)")
    args = p.parse_args()

    _install_signal_handler()

    if args.watch:
        print(f"=== live_signal watch · {args.symbol} · ${args.account} @ {args.leverage}x · "
              f"poll {args.interval}s ===")
        if args.journal:
            print(f"    journal: ON → data/trade_journal.csv (signal rows on transitions)")
        print("Ctrl+C to exit\n")
        last_signal = None
        last_bar_logged: dict[str, str] = {}
        while True:
            try:
                snap = _snapshot(args.symbol, args.refresh, args.account, args.leverage)
                _render(snap, args.account, args.leverage)
                cur = (snap["pullback_signal"], snap["trend_carry_signal"], snap["pyramid_ok"])
                if last_signal is not None and cur != last_signal:
                    # Audible alert + macOS notification
                    print("\033[1;33m\a*** STATE CHANGE — review trade card above ***\033[0m", flush=True)
                    if sys.platform == "darwin":
                        os.system('osascript -e \'display notification "Signal state changed" '
                                  'with title "Live Signal"\' >/dev/null 2>&1')
                last_signal = cur
                if args.journal:
                    _maybe_journal_signal(snap, args.account, args.leverage, last_bar_logged)
            except Exception as e:
                print(f"[error] {e}")
            time.sleep(args.interval)
    else:
        snap = _snapshot(args.symbol, args.refresh, args.account, args.leverage)
        _render(snap, args.account, args.leverage)
        if args.journal:
            _maybe_journal_signal(snap, args.account, args.leverage, {})


if __name__ == "__main__":
    main()
