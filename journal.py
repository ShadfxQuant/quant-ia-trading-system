"""
Trade journal — reconcile system signals with actual crypto-account fills.

Backed by a single CSV at data/trade_journal.csv. One row per event.
Three event types:

    SIGNAL  — system fired a signal (auto-logged from live_signal --journal)
    FILL    — you manually entered a position on your exchange
    EXIT    — you manually closed a position (full or partial)

Each FILL gets a sequential trade_id (T0001, T0002, ...). Each EXIT
references the FILL's trade_id so PnL can be computed pair-wise.

Commands:
    python -m journal show                  # full journal
    python -m journal show --last 20        # last 20 rows
    python -m journal fill                  # interactive: log a fill
    python -m journal exit                  # interactive: close a position
    python -m journal positions             # show open (un-exited) fills
    python -m journal summary               # WR / PF / total PnL / slippage stats
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import datetime, timezone

import pandas as pd


JOURNAL_PATH = "data/trade_journal.csv"

COLUMNS = [
    "timestamp_utc",
    "trade_id",
    "event_type",
    "bar_time",
    "symbol",
    "side",
    "strategy",
    "system_price",
    "system_stop_pct",
    "system_tp1_pct",
    "system_tp2_pct",
    "system_position_usd",
    "actual_symbol",
    "actual_price",
    "actual_qty",
    "actual_fees_usd",
    "ref_trade_id",
    "pnl_usd",
    "notes",
]


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _ensure_journal() -> None:
    os.makedirs(os.path.dirname(JOURNAL_PATH), exist_ok=True)
    if not os.path.exists(JOURNAL_PATH):
        with open(JOURNAL_PATH, "w", newline="") as f:
            csv.DictWriter(f, fieldnames=COLUMNS).writeheader()


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _read_journal() -> pd.DataFrame:
    _ensure_journal()
    if os.path.getsize(JOURNAL_PATH) <= 0:
        return pd.DataFrame(columns=COLUMNS)
    return pd.read_csv(JOURNAL_PATH, dtype=str).fillna("")


def _append_row(row: dict) -> None:
    _ensure_journal()
    full = {c: "" for c in COLUMNS}
    full.update({k: str(v) if v is not None else "" for k, v in row.items()})
    full["timestamp_utc"] = full.get("timestamp_utc") or _now_utc()
    with open(JOURNAL_PATH, "a", newline="") as f:
        csv.DictWriter(f, fieldnames=COLUMNS).writerow(full)


def _next_trade_id() -> str:
    df = _read_journal()
    fills = df[df["event_type"] == "FILL"]
    if fills.empty:
        return "T0001"
    ids = fills["trade_id"].dropna()
    ids = ids[ids.str.match(r"^T\d{4,}$", na=False)]
    if ids.empty:
        return "T0001"
    nums = ids.str[1:].astype(int)
    return f"T{nums.max() + 1:04d}"


def _open_positions(df: pd.DataFrame) -> pd.DataFrame:
    """Return FILL rows whose remaining qty (after partial exits) is > 0.

    A trade_id appears in the result if (sum of EXIT qty for ref_trade_id) <
    (FILL qty). Partial exits leave the position open with reduced size.
    """
    fills = df[df["event_type"] == "FILL"].copy()
    if fills.empty:
        return fills
    exits = df[df["event_type"] == "EXIT"]
    exited_by_tid = {}
    for _, e in exits.iterrows():
        tid = e.get("ref_trade_id", "")
        if not tid:
            continue
        try:
            exited_by_tid[tid] = exited_by_tid.get(tid, 0.0) + float(e["actual_qty"])
        except (ValueError, TypeError):
            continue
    keep = []
    for _, f in fills.iterrows():
        try:
            orig = float(f["actual_qty"])
        except (ValueError, TypeError):
            continue
        exited = exited_by_tid.get(f["trade_id"], 0.0)
        if orig - exited > 1e-9:
            keep.append(f.name)
    return fills.loc[keep]


# ---------------------------------------------------------------------------
# Public API (called from live_signal.py)
# ---------------------------------------------------------------------------

def log_signal(*, symbol: str, side: str, strategy: str, bar_time: str,
               system_price: float, stop_pct: float, tp1_pct: float,
               tp2_pct: float, position_usd: float, notes: str = "") -> None:
    """Programmatic SIGNAL-event logger called from live_signal.py."""
    _append_row({
        "event_type": "SIGNAL",
        "symbol": symbol,
        "side": side,
        "strategy": strategy,
        "bar_time": bar_time,
        "system_price": round(system_price, 4),
        "system_stop_pct": round(stop_pct, 6),
        "system_tp1_pct": round(tp1_pct, 6),
        "system_tp2_pct": round(tp2_pct, 6),
        "system_position_usd": round(position_usd, 2),
        "notes": notes,
    })


# ---------------------------------------------------------------------------
# CLI commands
# ---------------------------------------------------------------------------

def _input(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default is not None else ""
    val = input(f"  {prompt}{suffix}: ").strip()
    return val or (default or "")


def cmd_show(args) -> None:
    df = _read_journal()
    if df.empty:
        print("Journal is empty.")
        return
    if args.event:
        df = df[df["event_type"] == args.event.upper()]
    if args.last:
        df = df.tail(args.last)
    if df.empty:
        print("No matching rows.")
        return
    # Show only the most useful columns by event type
    summary_cols = ["timestamp_utc", "trade_id", "event_type", "symbol", "side",
                    "strategy", "system_price", "actual_price", "actual_qty",
                    "pnl_usd", "notes"]
    print(df[summary_cols].to_string(index=False))


def cmd_fill(args) -> None:
    print("=== Log a FILL (you just entered a position) ===")
    tid = _next_trade_id()
    print(f"  trade_id assigned: {tid}")

    side = _input("Side [LONG/SHORT]", "LONG").upper()
    if side not in ("LONG", "SHORT"):
        print(f"  invalid side: {side}"); return
    strategy = _input("Strategy [pullback/trend_carry]", "pullback")
    actual_symbol = _input("Crypto symbol (e.g. sSPY, SPXUSDT)")
    try:
        actual_price = float(_input("Fill price (USD)"))
        actual_qty = float(_input("Quantity (units)"))
        fees = float(_input("Fees (USD)", "0"))
    except ValueError:
        print("  numeric parse failed; aborting."); return
    notes = _input("Notes (optional)")

    # Auto-link to the most recent matching SIGNAL row, if any.
    df = _read_journal()
    sigs = df[(df["event_type"] == "SIGNAL") &
              (df["side"] == side) &
              (df["strategy"] == strategy)]
    linked_sig_price = ""
    if not sigs.empty:
        last_sig = sigs.iloc[-1]
        linked_sig_price = last_sig["system_price"]
        notes = (notes + f" | linked to signal @ {last_sig['bar_time']}").strip(" |")

    _append_row({
        "event_type": "FILL",
        "trade_id": tid,
        "side": side,
        "strategy": strategy,
        "system_price": linked_sig_price,
        "actual_symbol": actual_symbol,
        "actual_price": round(actual_price, 6),
        "actual_qty": round(actual_qty, 6),
        "actual_fees_usd": round(fees, 4),
        "notes": notes,
    })
    print(f"  ✓ FILL logged as {tid}")


def cmd_exit(args) -> None:
    df = _read_journal()
    opens = _open_positions(df)
    if opens.empty:
        print("No open positions to close.")
        return

    print("=== Open positions ===")
    for _, row in opens.iterrows():
        print(f"  {row['trade_id']}  {row['side']:<5}  "
              f"{row['actual_symbol']:<10}  "
              f"entry=${float(row['actual_price']):.4f}  "
              f"qty={float(row['actual_qty']):.4f}")

    tid = _input("Which trade_id to close? (or 'all' for full list)").upper()
    if tid not in opens["trade_id"].tolist():
        print(f"  invalid trade_id: {tid}"); return
    fill = opens[opens["trade_id"] == tid].iloc[0]

    try:
        exit_price = float(_input(
            f"Exit price (entry was ${float(fill['actual_price']):.4f})"))
        full_qty = float(fill["actual_qty"])
        qty_str = _input(f"Quantity exited", f"{full_qty}")
        exit_qty = float(qty_str)
        fees = float(_input("Fees (USD)", "0"))
    except ValueError:
        print("  numeric parse failed; aborting."); return
    notes = _input("Notes (e.g. 'TP1 hit', 'stopped out')")

    side_mult = 1 if fill["side"] == "LONG" else -1
    entry_price = float(fill["actual_price"])
    entry_fees = float(fill["actual_fees_usd"] or 0)
    gross = (exit_price - entry_price) * exit_qty * side_mult
    pnl = gross - fees - entry_fees * (exit_qty / full_qty)   # pro-rata entry fees

    _append_row({
        "event_type": "EXIT",
        "trade_id": f"X-{tid}",
        "ref_trade_id": tid,
        "side": fill["side"],
        "strategy": fill["strategy"],
        "actual_symbol": fill["actual_symbol"],
        "actual_price": round(exit_price, 6),
        "actual_qty": round(exit_qty, 6),
        "actual_fees_usd": round(fees, 4),
        "pnl_usd": round(pnl, 4),
        "notes": notes,
    })
    # Compute remaining-open qty AFTER this exit (sum prior exits + this one).
    prior_exits = df[(df["event_type"] == "EXIT") & (df["ref_trade_id"] == tid)]
    prior_qty = pd.to_numeric(prior_exits["actual_qty"], errors="coerce").fillna(0).sum() \
        if not prior_exits.empty else 0.0
    remaining_after = full_qty - prior_qty - exit_qty

    print(f"  ✓ EXIT logged. Gross PnL ${gross:+.2f}, net PnL ${pnl:+.2f}")
    if remaining_after > 1e-9:
        print(f"  ⚠ partial exit — {remaining_after:.4f} units still open under {tid}")
    else:
        print(f"  ✓ position {tid} fully closed.")


def cmd_positions(args) -> None:
    df = _read_journal()
    opens = _open_positions(df)
    if opens.empty:
        print("No open positions.")
        return
    print("=== Open positions ===")
    for _, row in opens.iterrows():
        # Compute how much of the original qty remains after any partial exits
        exits = df[(df["event_type"] == "EXIT") & (df["ref_trade_id"] == row["trade_id"])]
        exited_qty = exits["actual_qty"].astype(float).sum() if not exits.empty else 0
        remaining = float(row["actual_qty"]) - exited_qty
        print(f"  {row['trade_id']}  {row['side']:<5}  "
              f"{row['actual_symbol']:<10}  "
              f"entry=${float(row['actual_price']):.4f}  "
              f"qty_open={remaining:.4f}  "
              f"strategy={row['strategy']}")


def cmd_summary(args) -> None:
    df = _read_journal()
    if df.empty:
        print("Journal is empty."); return

    closed = df[df["event_type"] == "EXIT"].copy()
    if closed.empty:
        print("No closed trades yet — fills only.")
    else:
        closed["pnl_num"] = pd.to_numeric(closed["pnl_usd"], errors="coerce")
        wins = closed[closed["pnl_num"] > 0]
        losses = closed[closed["pnl_num"] < 0]
        total_pnl = closed["pnl_num"].sum()
        wr = len(wins) / len(closed) if len(closed) else 0
        if not losses.empty:
            pf = wins["pnl_num"].sum() / max(1e-9, -losses["pnl_num"].sum())
        else:
            pf = float("inf") if not wins.empty else 0.0

        print("=== Closed-trade summary ===")
        print(f"  Total closed   : {len(closed)}")
        print(f"  Total PnL      : ${total_pnl:+.2f}")
        print(f"  Win rate       : {wr:.1%}  ({len(wins)}W / {len(losses)}L)")
        print(f"  Profit factor  : {pf:.2f}")
        if not wins.empty:
            print(f"  Avg win        : ${wins['pnl_num'].mean():+.2f}")
            print(f"  Largest win    : ${wins['pnl_num'].max():+.2f}")
        if not losses.empty:
            print(f"  Avg loss       : ${losses['pnl_num'].mean():+.2f}")
            print(f"  Largest loss   : ${losses['pnl_num'].min():+.2f}")

    # Signal-vs-fill slippage analysis
    fills = df[df["event_type"] == "FILL"]
    sigs = df[df["event_type"] == "SIGNAL"]
    pairs = []
    for _, fill in fills.iterrows():
        if not fill["system_price"]:
            continue
        try:
            sp = float(fill["system_price"])
            ap = float(fill["actual_price"])
        except (ValueError, TypeError):
            continue
        slip_pct = (ap - sp) / sp * 100 * (1 if fill["side"] == "LONG" else -1)
        pairs.append(slip_pct)
    if pairs:
        ser = pd.Series(pairs)
        print("\n=== Signal → fill slippage (signed against position direction) ===")
        print(f"  Trades matched : {len(ser)}")
        print(f"  Avg slippage   : {ser.mean():+.3f}%  (positive = worse fill)")
        print(f"  Median         : {ser.median():+.3f}%")
        print(f"  Worst fill     : {ser.max():+.3f}%")

    print(f"\n  Total SIGNAL rows: {len(sigs)}")
    print(f"  Total FILL rows  : {len(fills)}")
    print(f"  Fill rate        : {(len(fills) / max(1, len(sigs))):.1%}  "
          f"(how often you act on signals)")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Trade journal CLI.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_show = sub.add_parser("show", help="Display journal entries")
    p_show.add_argument("--last", type=int, default=None)
    p_show.add_argument("--event", choices=["signal", "fill", "exit"], default=None)
    p_show.set_defaults(func=cmd_show)

    p_fill = sub.add_parser("fill", help="Interactive: log a manual fill")
    p_fill.set_defaults(func=cmd_fill)

    p_exit = sub.add_parser("exit", help="Interactive: log a manual exit")
    p_exit.set_defaults(func=cmd_exit)

    p_pos = sub.add_parser("positions", help="Show open (un-exited) positions")
    p_pos.set_defaults(func=cmd_positions)

    p_sum = sub.add_parser("summary", help="WR / PF / PnL / slippage")
    p_sum.set_defaults(func=cmd_summary)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
