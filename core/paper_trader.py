"""
In-system paper trader. Live track record of what the strategy *would* be doing
on a virtual $100K account.

Design: each worker tick, this module:
  1. Inspects the latest bar for every symbol in state.
  2. If there's a fresh signal (deduped by bar_time) and no open position for
     that symbol-strategy, opens a virtual position sized as % of equity.
  3. For every open position, checks the bar's high/low against the exit
     ladder (stop, TP1 partial, TP2 final). Closes legs that triggered.
  4. Updates equity, saves data/paper_account.json.

State surfaces in:
  - data/paper_account.json — equity curve, open positions, closed trades
  - Dashboard "Paper Portfolio" tab
  - Discord signal cards (appended "paper account: opened LONG ...")

Limitations (v1):
  - Fills assumed at signal-bar close (no slippage modeled)
  - No partial-fill modeling — full size at signal bar
  - Exit checks use the bar's high/low against fixed pct levels (no
    intra-bar order-of-events; if both stop and TP1 hit in same bar, we
    assume stop wins — conservative)
  - No funding cost modeling (PAXG perp funding rates skipped for simplicity)
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any

from config.settings import PULLBACK, TRENDCARRY


PAPER_PATH = os.path.join("data", "paper_account.json")
INITIAL_CAPITAL = 100_000.0


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
@dataclass
class Position:
    symbol: str
    strategy: str               # "pullback" | "trend_carry"
    side: int                   # +1 long, -1 short
    entry_time: str             # iso UTC
    entry_price: float
    size: float                 # USD notional
    stop_pct: float
    tp1_pct: float
    tp2_pct: float
    tp1_size_frac: float        # fraction of size taken at TP1
    bar_time_at_entry: str      # dedupe key
    tp1_hit: bool = False
    remaining_size: float = 0.0  # set to size at open

    def __post_init__(self):
        if self.remaining_size == 0.0:
            self.remaining_size = self.size


@dataclass
class ClosedTrade:
    symbol: str
    strategy: str
    side: int
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    size: float
    pnl: float
    reason: str                 # "stop" | "tp1" | "tp2" | "manual"


@dataclass
class PaperAccount:
    equity: float = INITIAL_CAPITAL
    initial_capital: float = INITIAL_CAPITAL
    cash: float = INITIAL_CAPITAL
    open_positions: list[Position] = field(default_factory=list)
    closed_trades: list[ClosedTrade] = field(default_factory=list)
    last_updated_utc: str = ""

    def to_dict(self) -> dict:
        return {
            "equity": self.equity,
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "open_positions": [asdict(p) for p in self.open_positions],
            "closed_trades": [asdict(t) for t in self.closed_trades[-200:]],
            "n_trades_total": len(self.closed_trades),
            "last_updated_utc": self.last_updated_utc,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PaperAccount":
        return cls(
            equity=d.get("equity", INITIAL_CAPITAL),
            initial_capital=d.get("initial_capital", INITIAL_CAPITAL),
            cash=d.get("cash", INITIAL_CAPITAL),
            open_positions=[Position(**p) for p in d.get("open_positions", [])],
            closed_trades=[ClosedTrade(**t) for t in d.get("closed_trades", [])],
            last_updated_utc=d.get("last_updated_utc", ""),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def load_account() -> PaperAccount:
    if not os.path.exists(PAPER_PATH):
        return PaperAccount()
    try:
        with open(PAPER_PATH) as f:
            return PaperAccount.from_dict(json.load(f))
    except Exception as e:
        print(f"[paper_trader] failed to load {PAPER_PATH}: {e}; starting fresh")
        return PaperAccount()


def save_account(acct: PaperAccount) -> None:
    os.makedirs(os.path.dirname(PAPER_PATH), exist_ok=True)
    acct.last_updated_utc = datetime.now(timezone.utc).isoformat()
    with open(PAPER_PATH, "w") as f:
        json.dump(acct.to_dict(), f, indent=2)


# ---------------------------------------------------------------------------
# Sizing + strategy config lookup
# ---------------------------------------------------------------------------
def _strategy_cfg(strategy: str) -> dict[str, float]:
    """Return stop/TP/size config for the strategy. Canonical attribute
    names (base_size_pct, stop_loss_pct, *_tp_pct, *_tp_size) as stored on
    the PULLBACK / TRENDCARRY dataclasses — already in absolute decimals."""
    if strategy == "pullback":
        return {
            "size_frac": PULLBACK.base_size_pct,
            "stop_pct": PULLBACK.stop_loss_pct,
            "tp1_pct": PULLBACK.partial_tp_pct,
            "tp2_pct": PULLBACK.final_tp_pct,
            "tp1_size_frac": PULLBACK.partial_tp_size,
        }
    if strategy == "trend_carry":
        return {
            "size_frac": TRENDCARRY.base_size_pct,
            "stop_pct": TRENDCARRY.stop_loss_pct,
            "tp1_pct": TRENDCARRY.partial_tp_pct,
            "tp2_pct": TRENDCARRY.final_tp_pct,
            "tp1_size_frac": TRENDCARRY.partial_tp_size,
        }
    raise ValueError(f"unknown strategy {strategy!r}")


# ---------------------------------------------------------------------------
# Open / close logic
# ---------------------------------------------------------------------------
def _has_open(acct: PaperAccount, symbol: str, strategy: str) -> bool:
    return any(p.symbol == symbol and p.strategy == strategy for p in acct.open_positions)


def _open_position(acct: PaperAccount, symbol: str, strategy: str, side: int,
                   entry_price: float, bar_time: str) -> Position | None:
    if _has_open(acct, symbol, strategy):
        return None  # don't pyramid in v1
    cfg = _strategy_cfg(strategy)
    size = acct.equity * cfg["size_frac"]
    pos = Position(
        symbol=symbol, strategy=strategy, side=side,
        entry_time=datetime.now(timezone.utc).isoformat(),
        entry_price=entry_price, size=size,
        stop_pct=cfg["stop_pct"], tp1_pct=cfg["tp1_pct"], tp2_pct=cfg["tp2_pct"],
        tp1_size_frac=cfg["tp1_size_frac"],
        bar_time_at_entry=bar_time,
    )
    acct.open_positions.append(pos)
    return pos


def _close_leg(acct: PaperAccount, pos: Position, exit_price: float,
               close_size: float, reason: str) -> ClosedTrade:
    """Close a fraction of the position. Updates equity from the realized leg."""
    # Realized PnL on this leg
    pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * pos.side
    pnl = close_size * pnl_pct
    acct.equity += pnl
    pos.remaining_size -= close_size

    trade = ClosedTrade(
        symbol=pos.symbol, strategy=pos.strategy, side=pos.side,
        entry_time=pos.entry_time, entry_price=pos.entry_price,
        exit_time=datetime.now(timezone.utc).isoformat(),
        exit_price=exit_price, size=close_size,
        pnl=pnl, reason=reason,
    )
    acct.closed_trades.append(trade)
    return trade


def _manage_open(acct: PaperAccount, bar_high: float, bar_low: float,
                 symbol: str) -> list[ClosedTrade]:
    """Walk every open position on this symbol, fire exits if the bar
    hit stop / TP1 / TP2 levels. Returns list of closed-leg events."""
    events: list[ClosedTrade] = []
    still_open: list[Position] = []
    for pos in acct.open_positions:
        if pos.symbol != symbol:
            still_open.append(pos)
            continue
        # Compute trigger prices
        if pos.side == 1:
            stop_px = pos.entry_price * (1.0 - pos.stop_pct)
            tp1_px = pos.entry_price * (1.0 + pos.tp1_pct)
            tp2_px = pos.entry_price * (1.0 + pos.tp2_pct)
            stop_hit = bar_low <= stop_px
            tp1_hit = bar_high >= tp1_px
            tp2_hit = bar_high >= tp2_px
        else:
            stop_px = pos.entry_price * (1.0 + pos.stop_pct)
            tp1_px = pos.entry_price * (1.0 - pos.tp1_pct)
            tp2_px = pos.entry_price * (1.0 - pos.tp2_pct)
            stop_hit = bar_high >= stop_px
            tp1_hit = bar_low <= tp1_px
            tp2_hit = bar_low <= tp2_px

        # Conservative: if both stop and TP hit in same bar, stop wins
        if stop_hit:
            ev = _close_leg(acct, pos, stop_px, pos.remaining_size, "stop")
            events.append(ev)
            continue  # position closed entirely

        if not pos.tp1_hit and tp1_hit:
            tp1_size = pos.size * pos.tp1_size_frac
            ev = _close_leg(acct, pos, tp1_px, tp1_size, "tp1")
            events.append(ev)
            pos.tp1_hit = True

        if tp2_hit:
            ev = _close_leg(acct, pos, tp2_px, pos.remaining_size, "tp2")
            events.append(ev)
            continue  # position closed entirely

        still_open.append(pos)

    acct.open_positions = still_open
    return events


# ---------------------------------------------------------------------------
# Public tick entry point — called from worker.build_state
# ---------------------------------------------------------------------------
def tick(state: dict) -> dict[str, Any]:
    """Walk every symbol in state, manage open positions, open new ones on
    fresh signals. Returns a list of action strings for the worker to log
    and the Discord card to display."""
    acct = load_account()
    actions: list[dict] = []

    for sym, snap in state.get("symbols", {}).items():
        bars = snap.get("bars") or []
        if not bars:
            continue
        last_bar = bars[-1]
        bar_high = float(last_bar.get("h", last_bar.get("c", 0)))
        bar_low = float(last_bar.get("l", last_bar.get("c", 0)))
        bar_close = float(last_bar.get("c", 0))
        bar_time = snap.get("bar_time_utc", "")

        # 1. Manage existing positions against this bar
        for ev in _manage_open(acct, bar_high, bar_low, sym):
            actions.append({
                "event": "close",
                "symbol": sym,
                "strategy": ev.strategy,
                "side": "LONG" if ev.side == 1 else "SHORT",
                "reason": ev.reason,
                "exit_price": ev.exit_price,
                "pnl": round(ev.pnl, 2),
                "equity_after": round(acct.equity, 2),
            })

        # 2. Open new positions on fresh signals
        for strat, sig_key in (("pullback", "pullback_signal"),
                               ("trend_carry", "trend_carry_signal")):
            side = int(snap.get(sig_key, 0) or 0)
            if side == 0:
                continue
            # Dedupe: only open once per bar_time per symbol/strategy
            already = any(
                t.symbol == sym and t.strategy == strat
                and t.entry_time.startswith(bar_time[:13])  # hour-level
                for t in acct.closed_trades[-50:]
            ) or _has_open(acct, sym, strat)
            if already:
                continue
            pos = _open_position(acct, sym, strat, side, bar_close, bar_time)
            if pos:
                actions.append({
                    "event": "open",
                    "symbol": sym,
                    "strategy": strat,
                    "side": "LONG" if side == 1 else "SHORT",
                    "entry_price": pos.entry_price,
                    "size_usd": round(pos.size, 2),
                    "stop": round(pos.entry_price * (1 - pos.stop_pct * side), 2),
                    "tp1": round(pos.entry_price * (1 + pos.tp1_pct * side), 2),
                    "tp2": round(pos.entry_price * (1 + pos.tp2_pct * side), 2),
                    "equity_after": round(acct.equity, 2),
                })

    save_account(acct)
    return {
        "equity": round(acct.equity, 2),
        "initial_capital": acct.initial_capital,
        "return_pct": round((acct.equity / acct.initial_capital - 1) * 100, 2),
        "open_count": len(acct.open_positions),
        "closed_count": len(acct.closed_trades),
        "actions": actions,
    }
