"""
Event-driven backtester with pyramiding and two-target partial exits.

Sizing
------
Notional sizing: each new entry deploys `position_size_pct` of current equity
(default 15%). With pyramiding (up to `max_pyramid_positions`), total notional
exposure can reach roughly position_size_pct * max_pyramid_positions of equity.

Pyramiding rule
---------------
* When flat, any signal opens one position.
* While the regime is `growth`, additional same-direction signals open extra
  positions, up to `max_pyramid_positions` total.
* In any other regime only a single position is allowed at a time.
* Counter-direction signals are ignored while positions are open.

Two-target exit
---------------
Each position carries:
    * a hard stop                   (`stop_loss_pct`)
    * a partial target (TP1)        (`take_profit_partial_pct`, scales out
                                     `take_profit_partial_size` of original qty,
                                     stop trails to breakeven for the runner)
    * a runner target (TP2)         (`take_profit_runner_pct`, closes remainder)
    * a max-bars-held time exit
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional

import pandas as pd

from config.settings import BACKTEST, STRATEGY


@dataclass
class _Position:
    side: int                     # +1 long, -1 short
    entry_time: pd.Timestamp
    entry_price: float
    qty_initial: float
    qty_open: float
    stop_price: float
    partial_target: float
    runner_target: float
    partial_taken: bool = False
    bars_held: int = 0


@dataclass
class TradeRecord:
    symbol: str
    side: int
    entry_time: pd.Timestamp
    entry_price: float
    exit_time: pd.Timestamp
    exit_price: float
    qty: float
    pnl: float
    return_pct: float
    bars_held: int
    exit_reason: str
    leg: str                      # "partial" | "runner" | "stop" | "time"


def _apply_costs(price: float, side: int, cfg=BACKTEST) -> float:
    """Adjust fill price for slippage + fee (always worse for the trader)."""
    slip = cfg.slippage_pct * side
    return price * (1 + slip) * (1 + cfg.fee_pct)


def _open_position(
    side: int,
    ts: pd.Timestamp,
    raw_price: float,
    equity: float,
    bcfg,
    scfg,
) -> Optional[_Position]:
    entry_fill = _apply_costs(raw_price, side, bcfg)
    notional = equity * bcfg.position_size_pct
    if notional <= 0 or entry_fill <= 0:
        return None
    qty = notional / entry_fill
    if side == 1:
        stop = entry_fill * (1 - scfg.stop_loss_pct)
        tp1 = entry_fill * (1 + scfg.take_profit_partial_pct)
        tp2 = entry_fill * (1 + scfg.take_profit_runner_pct)
    else:
        stop = entry_fill * (1 + scfg.stop_loss_pct)
        tp1 = entry_fill * (1 - scfg.take_profit_partial_pct)
        tp2 = entry_fill * (1 - scfg.take_profit_runner_pct)
    return _Position(
        side=side,
        entry_time=ts,
        entry_price=entry_fill,
        qty_initial=qty,
        qty_open=qty,
        stop_price=stop,
        partial_target=tp1,
        runner_target=tp2,
    )


def _close_leg(
    pos: _Position,
    qty_close: float,
    raw_exit: float,
    ts: pd.Timestamp,
    leg: str,
    symbol: str,
    bcfg,
) -> tuple[TradeRecord, float]:
    """Close `qty_close` units of `pos`. Returns the trade record and realised PnL."""
    fill = _apply_costs(raw_exit, -pos.side, bcfg)
    pnl = (fill - pos.entry_price) * qty_close * pos.side
    ret = pnl / (pos.entry_price * qty_close) if qty_close > 0 else 0.0
    rec = TradeRecord(
        symbol=symbol,
        side=pos.side,
        entry_time=pos.entry_time,
        entry_price=pos.entry_price,
        exit_time=ts,
        exit_price=fill,
        qty=qty_close,
        pnl=pnl,
        return_pct=ret,
        bars_held=pos.bars_held,
        exit_reason=leg if leg in ("stop", "time") else f"target_{leg}",
        leg=leg,
    )
    pos.qty_open -= qty_close
    return rec, pnl


def run_backtest(
    df: pd.DataFrame,
    symbol: str = "ASSET",
    bcfg=BACKTEST,
    scfg=STRATEGY,
) -> dict:
    """
    Execute the strategy on a fully-prepared DataFrame.

    Required columns: Close, Signal, Regime (and ideally High, Low for intrabar fills).
    """
    required = {"Close", "Signal", "Regime"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    cash = bcfg.initial_capital
    positions: list[_Position] = []
    closed: list[TradeRecord] = []
    equity_history: list[tuple[pd.Timestamp, float]] = []

    for ts, row in df.iterrows():
        price = float(row["Close"])
        high = float(row.get("High", price))
        low = float(row.get("Low", price))
        signal = int(row["Signal"]) if not pd.isna(row["Signal"]) else 0
        regime = row["Regime"]
        # HMM probabilities (NaN-safe; absent if HMM is off).
        p_bull = float(row.get("P_bull", float("nan"))) if "P_bull" in df.columns else float("nan")
        p_bear = float(row.get("P_bear", float("nan"))) if "P_bear" in df.columns else float("nan")

        # ---------- 1. Manage open positions (intrabar) ----------
        for pos in list(positions):
            pos.bars_held += 1

            # Stop check first (most conservative).
            stop_hit = (pos.side == 1 and low <= pos.stop_price) or (
                pos.side == -1 and high >= pos.stop_price
            )
            if stop_hit:
                rec, pnl = _close_leg(
                    pos, pos.qty_open, pos.stop_price, ts, "stop", symbol, bcfg
                )
                cash += rec.pnl
                closed.append(rec)
                positions.remove(pos)
                continue

            # Partial target (TP1) — only fires once per position.
            if not pos.partial_taken:
                tp1_hit = (pos.side == 1 and high >= pos.partial_target) or (
                    pos.side == -1 and low <= pos.partial_target
                )
                if tp1_hit:
                    qty_close = pos.qty_initial * scfg.take_profit_partial_size
                    qty_close = min(qty_close, pos.qty_open)
                    rec, pnl = _close_leg(
                        pos, qty_close, pos.partial_target, ts, "partial", symbol, bcfg
                    )
                    cash += rec.pnl
                    closed.append(rec)
                    pos.partial_taken = True
                    # Trail stop to breakeven for the runner.
                    pos.stop_price = pos.entry_price
                    if pos.qty_open <= 0:
                        positions.remove(pos)
                        continue

            # Runner target (TP2).
            tp2_hit = (pos.side == 1 and high >= pos.runner_target) or (
                pos.side == -1 and low <= pos.runner_target
            )
            if tp2_hit and pos.qty_open > 0:
                rec, pnl = _close_leg(
                    pos, pos.qty_open, pos.runner_target, ts, "runner", symbol, bcfg
                )
                cash += rec.pnl
                closed.append(rec)
                positions.remove(pos)
                continue

            # Time-based exit.
            if pos.bars_held >= scfg.max_holding_bars and pos.qty_open > 0:
                rec, pnl = _close_leg(
                    pos, pos.qty_open, price, ts, "time", symbol, bcfg
                )
                cash += rec.pnl
                closed.append(rec)
                positions.remove(pos)
                continue

        # ---------- 2. Equity = cash + MTM of open positions ----------
        mtm = sum((price - p.entry_price) * p.qty_open * p.side for p in positions)
        equity = cash + mtm
        equity_history.append((ts, equity))

        # ---------- 3. New entries ----------
        if signal == 0:
            continue

        # Don't open against existing positions.
        if positions and any(p.side != signal for p in positions):
            continue

        if not positions:
            new_pos = _open_position(signal, ts, price, equity, bcfg, scfg)
            if new_pos is not None:
                positions.append(new_pos)
        else:
            # Pyramid only when the regime confirms the signal direction.
            #   long pyramiding  -> growth or slowdown (both bullish-leaning)
            #   short pyramiding -> crash or distribution (both bearish-leaning)
            allow_pyramid = (
                (signal == 1 and regime in ("growth", "slowdown"))
                or (signal == -1 and regime in ("crash", "distribution"))
            )
            # HMM pyramiding boost: tighten the soft cap to a smaller fraction of
            # the hard cap unless HMM is highly confident in the same direction.
            soft_cap = bcfg.max_pyramid_positions
            if scfg.use_hmm_filter and "P_bull" in df.columns:
                soft_cap = max(3, bcfg.max_pyramid_positions // 2)
                if signal == 1 and p_bull == p_bull and p_bull >= scfg.hmm_pyramid_boost_threshold:
                    soft_cap = bcfg.max_pyramid_positions
                if signal == -1 and p_bear == p_bear and p_bear >= scfg.hmm_pyramid_boost_threshold:
                    soft_cap = bcfg.max_pyramid_positions
            if allow_pyramid and len(positions) < soft_cap:
                new_pos = _open_position(signal, ts, price, equity, bcfg, scfg)
                if new_pos is not None:
                    positions.append(new_pos)

    # ---------- 4. Force-close any leftovers at the end ----------
    if positions:
        last_ts = df.index[-1]
        last_price = float(df["Close"].iloc[-1])
        for pos in list(positions):
            rec, pnl = _close_leg(
                pos, pos.qty_open, last_price, last_ts, "end_of_data", symbol, bcfg
            )
            cash += rec.pnl
            closed.append(rec)
            positions.remove(pos)
        equity_history[-1] = (last_ts, cash)

    trades_df = pd.DataFrame([asdict(t) for t in closed])
    equity_curve = pd.Series(
        data=[v for _, v in equity_history],
        index=pd.DatetimeIndex([t for t, _ in equity_history]),
        name="equity",
    )
    return {"trades": trades_df, "equity_curve": equity_curve}
