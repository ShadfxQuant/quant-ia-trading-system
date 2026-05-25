"""
Portfolio backtester for the dual-strategy SPY 1H system.

Each `StrategySpec` ships:
    * `name`              — column prefix (e.g. "pullback")
    * `cfg`               — strategy config (Pullback/BreakoutStratConfig)
    * `exit_profile`      — shared ExitProfile contract

Per-bar execution order:
    1. Manage open positions (stop / TPs / trailing / time stop).
    2. Mark equity (cash + total MTM).
    3. For each strategy, evaluate `<name>_Signal` and `<name>_PyramidOK` and
       open a new position if its capital cap and pyramid cap allow.

Per-symbol strategy assignment: `run_portfolio` takes its `strategies` list
per call, so each symbol's book can run a different StrategySpec set (e.g.
SPY/DIA → pullback+trend_carry, IWM → meanrev). Callers Sharpe-weight the
per-symbol equity curves into the combined book (see _research_reconcile.py).

Per-strategy attribution tracked:
    * realized cumulative PnL curve
    * per-strategy MTM contribution to combined equity
    * trade list with `strategy` column
    * per-strategy max drawdown
    * inter-strategy return correlation
    * average holding period (in bars)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, List, Optional, Sequence

import numpy as np
import pandas as pd

from config.settings import BACKTEST
from strategies.exit_profile import ExitProfile


@dataclass
class StrategySpec:
    name: str
    cfg: Any
    exit_profile: ExitProfile


@dataclass
class _Position:
    strategy: str
    side: int
    entry_time: pd.Timestamp
    entry_price: float
    qty_initial: float
    qty_open: float
    stop_price: float
    partial_target: float
    final_target: float
    bars_held: int = 0
    partial_taken: bool = False
    final_taken: bool = False
    trailing_active: bool = False
    # Per-position ATR-trail state — ratchets monotonically in favour of the
    # position. NaN until the first valid ATR value is observed.
    trail_level: float = float("nan")
    # Entry-time context for trade attribution.
    stack_idx: int = 0
    rvol_at_entry: float = float("nan")
    vwap_alignment_at_entry: int = 0


@dataclass
class TradeRecord:
    strategy: str
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
    # Entry context (snapshot at the moment the position was opened).
    stack_idx: int = 0           # 0 = initial entry, 1+ = pyramid stack
    rvol_at_entry: float = float("nan")
    vwap_alignment_at_entry: int = 0   # +1 above, -1 below, 0 unknown


def _apply_costs(price: float, side: int) -> float:
    return price * (1 + BACKTEST.slippage_pct * side) * (1 + BACKTEST.fee_pct)


def _open_position(
    strategy_name: str,
    side: int,
    ts: pd.Timestamp,
    raw_price: float,
    notional: float,
    profile: ExitProfile,
    *,
    stack_idx: int = 0,
    rvol_at_entry: float = float("nan"),
    vwap_alignment_at_entry: int = 0,
    stop_pct_override: float | None = None,
) -> Optional[_Position]:
    if notional <= 0:
        return None
    entry_fill = _apply_costs(raw_price, side)
    qty = notional / entry_fill
    if qty <= 0:
        return None

    # Phase 4 P2: per-entry stop override (ATR-normalized). Falls back to the
    # ExitProfile's fixed stop_loss_pct when no override provided.
    stop_pct = stop_pct_override if (
        stop_pct_override is not None and stop_pct_override > 0
    ) else profile.stop_loss_pct

    if side == 1:
        stop = entry_fill * (1 - stop_pct)
        partial = entry_fill * (1 + profile.partial_tp_pct)
        final = entry_fill * (1 + profile.final_tp_pct)
    else:
        stop = entry_fill * (1 + stop_pct)
        partial = entry_fill * (1 - profile.partial_tp_pct)
        final = entry_fill * (1 - profile.final_tp_pct)

    pos = _Position(
        strategy=strategy_name,
        side=side,
        entry_time=ts,
        entry_price=entry_fill,
        qty_initial=qty,
        qty_open=qty,
        stop_price=stop,
        partial_target=partial,
        final_target=final,
        trailing_active=(profile.trailing_starts_at == "immediately"
                         and profile.trailing_stop_enabled),
        stack_idx=stack_idx,
        rvol_at_entry=rvol_at_entry,
        vwap_alignment_at_entry=vwap_alignment_at_entry,
    )
    return pos


def _close_leg(
    pos: _Position,
    qty_close: float,
    raw_exit: float,
    ts: pd.Timestamp,
    leg: str,
    symbol: str,
) -> TradeRecord:
    fill = _apply_costs(raw_exit, -pos.side)
    pnl = (fill - pos.entry_price) * qty_close * pos.side
    ret = pnl / (pos.entry_price * qty_close) if qty_close > 0 else 0.0
    rec = TradeRecord(
        strategy=pos.strategy,
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
        exit_reason=leg,
        stack_idx=pos.stack_idx,
        rvol_at_entry=pos.rvol_at_entry,
        vwap_alignment_at_entry=pos.vwap_alignment_at_entry,
    )
    pos.qty_open -= qty_close
    return rec


def _trail_value(row: pd.Series, profile: ExitProfile) -> float:
    """Return the trailing-stop level from the configured indicator."""
    if profile.trailing_logic_type == "ema_50":
        return float(row.get("EMA", float("nan")))
    if profile.trailing_logic_type == "atr":
        # If ATR is not in the dataframe, fall back to EMA.
        return float(row.get("ATR", row.get("EMA", float("nan"))))
    return float("nan")


def _strategy_exposure(positions: list[_Position], strategy_name: str) -> float:
    return sum(p.qty_open * p.entry_price for p in positions if p.strategy == strategy_name)


def _strategy_mtm(positions: list[_Position], price: float, strategy_name: str) -> float:
    return sum(
        (price - p.entry_price) * p.qty_open * p.side
        for p in positions if p.strategy == strategy_name
    )


def run_portfolio(
    df: pd.DataFrame,
    strategies: Sequence[StrategySpec],
    symbol: str = "ASSET",
    initial_capital: float | None = None,
) -> dict:
    cap0 = initial_capital if initial_capital is not None else BACKTEST.initial_capital
    cash = cap0
    positions: list[_Position] = []
    closed: list[TradeRecord] = []
    equity_history: list[tuple[pd.Timestamp, float]] = []
    # exposure_history: list of (ts, gross_notional_exposure, equity)
    exposure_history: list[tuple[pd.Timestamp, float, float]] = []

    # Per-strategy cumulative realised PnL & MTM tracking.
    per_strat_realized = {s.name: [] for s in strategies}    # list of (ts, cum_pnl)
    per_strat_mtm_track = {s.name: [] for s in strategies}   # list of (ts, mtm)

    spec_by_name = {s.name: s for s in strategies}
    cumulative_pnl_running = {s.name: 0.0 for s in strategies}

    for ts, row in df.iterrows():
        price = float(row["Close"])
        high  = float(row.get("High", price))
        low   = float(row.get("Low",  price))

        # ---------- 1. Manage positions ----------
        for pos in list(positions):
            spec = spec_by_name[pos.strategy]
            profile = spec.exit_profile
            pos.bars_held += 1

            # Hard stop.
            stop_hit = (pos.side == 1 and low <= pos.stop_price) or (
                pos.side == -1 and high >= pos.stop_price
            )
            if stop_hit:
                rec = _close_leg(pos, pos.qty_open, pos.stop_price, ts, "stop", symbol)
                cash += rec.pnl
                cumulative_pnl_running[pos.strategy] += rec.pnl
                closed.append(rec)
                positions.remove(pos)
                continue

            # Partial take-profit.
            if not pos.partial_taken and profile.partial_tp_size > 0:
                tp_hit = (pos.side == 1 and high >= pos.partial_target) or (
                    pos.side == -1 and low <= pos.partial_target
                )
                if tp_hit:
                    qty_close = min(pos.qty_initial * profile.partial_tp_size, pos.qty_open)
                    rec = _close_leg(pos, qty_close, pos.partial_target, ts, "tp1_partial", symbol)
                    cash += rec.pnl
                    cumulative_pnl_running[pos.strategy] += rec.pnl
                    closed.append(rec)
                    pos.partial_taken = True
                    if profile.move_stop_to_be_after_partial:
                        pos.stop_price = pos.entry_price
                    if profile.trailing_stop_enabled and profile.trailing_starts_at == "after_partial":
                        pos.trailing_active = True
                    if pos.qty_open <= 1e-9:
                        positions.remove(pos)
                        continue

            # Final take-profit.
            if not pos.final_taken and profile.final_tp_size > 0 and pos.qty_open > 1e-9:
                tp_hit = (pos.side == 1 and high >= pos.final_target) or (
                    pos.side == -1 and low <= pos.final_target
                )
                if tp_hit:
                    qty_close = min(pos.qty_initial * profile.final_tp_size, pos.qty_open)
                    rec = _close_leg(pos, qty_close, pos.final_target, ts, "tp2_final", symbol)
                    cash += rec.pnl
                    cumulative_pnl_running[pos.strategy] += rec.pnl
                    closed.append(rec)
                    pos.final_taken = True
                    if profile.trailing_stop_enabled and profile.trailing_starts_at == "after_final":
                        pos.trailing_active = True
                    if pos.qty_open <= 1e-9:
                        positions.remove(pos)
                        continue

            # Trailing stop on the runner.
            if pos.trailing_active and pos.qty_open > 0:
                if profile.trailing_logic_type == "atr":
                    # Per-position ratchet: trail = max(prev_trail, price - k*ATR)
                    atr_val = float(row.get("ATR", float("nan")))
                    if not np.isnan(atr_val):
                        offset = profile.atr_multiplier * atr_val
                        if pos.side == 1:
                            candidate = price - offset
                            pos.trail_level = (candidate if np.isnan(pos.trail_level)
                                               else max(pos.trail_level, candidate))
                            trail_hit = price < pos.trail_level
                        else:
                            candidate = price + offset
                            pos.trail_level = (candidate if np.isnan(pos.trail_level)
                                               else min(pos.trail_level, candidate))
                            trail_hit = price > pos.trail_level
                    else:
                        trail_hit = False
                else:
                    # Indicator-based trailing (e.g. EMA(50)) — global level.
                    trail_val = _trail_value(row, profile)
                    if not np.isnan(trail_val):
                        trail_hit = (pos.side == 1 and price < trail_val) or (
                            pos.side == -1 and price > trail_val
                        )
                    else:
                        trail_hit = False

                if trail_hit:
                    rec = _close_leg(pos, pos.qty_open, price, ts, "trail", symbol)
                    cash += rec.pnl
                    cumulative_pnl_running[pos.strategy] += rec.pnl
                    closed.append(rec)
                    positions.remove(pos)
                    continue

            # Time stop.
            if pos.bars_held >= profile.max_hold_bars and pos.qty_open > 0:
                rec = _close_leg(pos, pos.qty_open, price, ts, "time", symbol)
                cash += rec.pnl
                cumulative_pnl_running[pos.strategy] += rec.pnl
                closed.append(rec)
                positions.remove(pos)
                continue

        # ---------- 2. Equity / per-strategy attribution ----------
        mtm = sum((price - p.entry_price) * p.qty_open * p.side for p in positions)
        equity = cash + mtm
        equity_history.append((ts, equity))
        # Track gross notional exposure (sum of |qty * entry_price|) for
        # exposure-utilisation diagnostics.
        gross_exposure = sum(abs(p.qty_open * p.entry_price) for p in positions)
        exposure_history.append((ts, gross_exposure, equity))

        for s in strategies:
            per_strat_realized[s.name].append((ts, cumulative_pnl_running[s.name]))
            per_strat_mtm_track[s.name].append((ts, _strategy_mtm(positions, price, s.name)))

        # ---------- 3. Entries ----------
        for spec in strategies:
            cfg = spec.cfg
            sig_col = f"{spec.name}_Signal"
            mult_col = f"{spec.name}_SizeMult"
            pyr_col = f"{spec.name}_PyramidOK"

            sig_val = row.get(sig_col, 0)
            sig = int(sig_val) if not pd.isna(sig_val) else 0
            if sig == 0:
                continue

            same_strat = [p for p in positions if p.strategy == spec.name]
            if same_strat and any(p.side != sig for p in same_strat):
                continue

            current_exposure = _strategy_exposure(positions, spec.name)
            cap_dollars = equity * cfg.capital_cap_pct
            if current_exposure >= cap_dollars:
                continue

            # Pyramiding gate (only checked when we already have a position).
            if same_strat:
                pyramid_ok = bool(row.get(pyr_col, False))
                if not pyramid_ok:
                    continue
                # Per-bar dynamic cap (HMM aggressiveness scaling); falls back
                # to the strategy's static `max_pyramid_positions` if absent.
                cap_col = f"{spec.name}_PyramidCap"
                dyn_cap_val = row.get(cap_col, cfg.max_pyramid_positions)
                try:
                    dyn_cap = int(dyn_cap_val) if not pd.isna(dyn_cap_val) else cfg.max_pyramid_positions
                except (TypeError, ValueError):
                    dyn_cap = cfg.max_pyramid_positions
                effective_cap = min(cfg.max_pyramid_positions, dyn_cap)
                if len(same_strat) >= effective_cap:
                    continue

            size_mult = float(row.get(mult_col, 1.0))
            if size_mult <= 0:
                continue

            notional = equity * cfg.base_size_pct * size_mult
            notional = min(notional, cap_dollars - current_exposure)
            if notional <= 0:
                continue

            # Capture entry context (stack depth, RVOL, VWAP alignment) for
            # per-trade attribution and downstream analytics.
            stack_idx = len(same_strat)
            rvol_at_entry = float(row.get("RVOL", float("nan")))
            close_v = price
            vwap_v = row.get("VWAP", float("nan"))
            if not pd.isna(vwap_v):
                vwap_align = 1 if close_v > vwap_v else (-1 if close_v < vwap_v else 0)
            else:
                vwap_align = 0

            # Phase 4 P2 — read per-bar stop override if the strategy emitted one.
            stop_override_col = f"{spec.name}_StopPctOverride"
            stop_override_val = None
            if stop_override_col in df.columns:
                raw = row.get(stop_override_col)
                if raw is not None and not pd.isna(raw) and float(raw) > 0:
                    stop_override_val = float(raw)

            new_pos = _open_position(
                spec.name, sig, ts, price, notional, spec.exit_profile,
                stack_idx=stack_idx,
                rvol_at_entry=rvol_at_entry,
                vwap_alignment_at_entry=vwap_align,
                stop_pct_override=stop_override_val,
            )
            if new_pos is not None:
                positions.append(new_pos)

    # ---------- 4. Force-close on last bar ----------
    if positions:
        last_ts = df.index[-1]
        last_price = float(df["Close"].iloc[-1])
        for pos in list(positions):
            rec = _close_leg(pos, pos.qty_open, last_price, last_ts, "end_of_data", symbol)
            cash += rec.pnl
            cumulative_pnl_running[pos.strategy] += rec.pnl
            closed.append(rec)
            positions.remove(pos)
        equity_history[-1] = (last_ts, cash)

    trades_df = pd.DataFrame([asdict(t) for t in closed])
    equity_curve = pd.Series(
        [v for _, v in equity_history],
        index=pd.DatetimeIndex([t for t, _ in equity_history]),
        name="equity",
    )

    # Per-strategy curves: realized + MTM, indexed identically to combined equity.
    strategy_curves: dict[str, pd.Series] = {}
    for s in strategies:
        realised_idx = [t for t, _ in per_strat_realized[s.name]]
        realised_vals = [v for _, v in per_strat_realized[s.name]]
        mtm_vals = [v for _, v in per_strat_mtm_track[s.name]]
        strategy_curves[s.name] = pd.Series(
            np.array(realised_vals) + np.array(mtm_vals),
            index=pd.DatetimeIndex(realised_idx),
            name=f"{s.name}_pnl",
        )

    # Exposure curve (gross notional / equity per bar) — used for utilisation
    # and average-pyramid-depth diagnostics in main_portfolio.py.
    if exposure_history:
        ex_idx = pd.DatetimeIndex([t for t, _, _ in exposure_history])
        gross_arr = np.array([g for _, g, _ in exposure_history], dtype=float)
        eq_arr = np.array([e for _, _, e in exposure_history], dtype=float)
        utilisation = pd.Series(
            np.where(eq_arr > 0, gross_arr / eq_arr, 0.0),
            index=ex_idx, name="exposure_utilisation",
        )
    else:
        utilisation = pd.Series(dtype=float, name="exposure_utilisation")

    return {
        "trades": trades_df,
        "equity_curve": equity_curve,
        "strategy_curves": strategy_curves,
        "exposure_curve": utilisation,
        "initial_capital": cap0,
    }
