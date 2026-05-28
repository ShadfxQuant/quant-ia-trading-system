"""
Background worker — keeps `data/state.json` fresh so the Streamlit dashboard
can load instantly without re-running the engine or hitting yfinance.

Runs forever. Every `--interval` seconds (default 600 = 10 min):
    1. Download fresh bars for every symbol in DATA.symbols.
    2. Run the full prepare_dual pipeline (indicators + HMM + signals).
    3. Fetch macro verdict (news_macro).
    4. Snapshot last-bar state + last 20 journal trades.
    5. Atomic write to data/state.json (write tmp, fsync, rename).

Usage:
    python3 -m worker                       # default 10-min loop
    python3 -m worker --interval 300        # 5-min loop
    python3 -m worker --once                # write one snapshot and exit
    python3 -m worker --symbols SPY,DIA     # override symbol list
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timezone

import pandas as pd

from config.settings import DATA, PULLBACK, TRENDCARRY, CRYPTO_CARRY
from core.data_loader import load_symbol
from main_portfolio import prepare_dual
from core.news_macro import macro_verdict, RISK_OFF_THEMES, RISK_ON_THEMES
from core.notifier import send_signal as _notify_signal, send_text as _notify_text
try:
    from core.crypto_carry import snap_all as _carry_snap_all
except ImportError:
    # crypto_carry was shelved (needs 2 accounts, Infinex is single-venue).
    # Stub it so the worker keeps running.
    def _carry_snap_all():
        return []

STATE_PATH = os.path.join("data", "state.json")
JOURNAL_PATH = os.path.join("data", "trade_journal.csv")
NOTIFIED_PATH = os.path.join("data", "last_notified.json")
RSS_PATH = os.path.join("data", "signals.rss")
RSS_HISTORY_PATH = os.path.join("data", "signals_rss_items.json")
RSS_MAX_ITEMS = 50
RSS_PUBLIC_URL_BASE = os.environ.get(
    "RSS_PUBLIC_URL_BASE",
    "https://github.com/ShadfxQuant/quant-ia-trading-system",
)


# ---------------------------------------------------------------------------
# Snapshot builders
# ---------------------------------------------------------------------------
def _snapshot_symbol(symbol: str) -> dict:
    df = prepare_dual(load_symbol(symbol, force_refresh=True))
    # Apply per-symbol regime filter (e.g. PAXGUSDT → ADX≥25 + NYSE hours).
    # See core/regime_filter.py — symbols without a filter pass through.
    try:
        from core.regime_filter import apply_regime_filter
        df = apply_regime_filter(df, symbol)
    except Exception as e:
        print(f"[worker] regime filter skipped for {symbol}: {e}")
    last = df.iloc[-1]
    ts = df.index[-1]
    pb_sig = int(last.get("pullback_Signal", 0) or 0)
    tc_sig = int(last.get("trend_carry_Signal", 0) or 0)

    # Tail of bars for chart + volume profile.
    tail = df.tail(400)
    bars = []
    for t, row in tail.iterrows():
        bars.append({
            "t": t.isoformat(),
            "o": float(row.get("Open", row["Close"])),
            "h": float(row.get("High", row["Close"])),
            "l": float(row.get("Low", row["Close"])),
            "c": float(row["Close"]),
            "v": float(row.get("Volume", 0.0)),
            "ema": float(row.get("EMA", float("nan"))),
            "sma": float(row.get("SMA", float("nan"))),
            "vwap": float(row.get("VWAP", float("nan"))) if "VWAP" in row else None,
        })

    # Insight Read — bias / strength / regime / macro tilt synthesized from
    # the same df. Always present (even when there's no signal) so the
    # dashboard + /read slash command always have something to render.
    try:
        from core.read import compute_read
        # Pass macro lazily via attribute so we don't recompute it per symbol.
        read = compute_read(df, symbol, macro=getattr(_snapshot_symbol, "_macro", None))
    except Exception as e:
        read = {"error": f"{type(e).__name__}: {e}"}

    return {
        "symbol": symbol,
        "bar_time_utc": ts.tz_convert("UTC").isoformat() if ts.tzinfo else ts.isoformat(),
        "close": float(last["Close"]),
        "ema": float(last.get("EMA", float("nan"))),
        "sma": float(last.get("SMA", float("nan"))),
        "vwap": float(last.get("VWAP", float("nan"))) if "VWAP" in df.columns else None,
        "pullback_signal": pb_sig,
        "trend_carry_signal": tc_sig,
        "pullback_pyramid_ok": bool(last.get("pullback_PyramidOK", False)),
        "pullback_pyramid_cap": int(last.get("pullback_PyramidCap", 0) or 0),
        "trend_carry_pyramid_ok": bool(last.get("trend_carry_PyramidOK", False)),
        "trend_carry_pyramid_cap": int(last.get("trend_carry_PyramidCap", 0) or 0),
        "read": read,
        "bars": bars,
    }


def _snapshot_macro() -> dict:
    v = macro_verdict(force_refresh=True)
    return {
        "verdict": v.verdict,
        "risk_off_score": v.risk_off_score,
        "risk_on_score": v.risk_on_score,
        "theme_hits": v.theme_hits,
        "sample_headlines": v.sample_headlines,
        "n_headlines": v.n_headlines,
        "sources_used": v.sources_used,
        "fetched_at": v.fetched_at,
        "risk_off_themes": sorted(RISK_OFF_THEMES),
        "risk_on_themes": sorted(RISK_ON_THEMES),
    }


def _snapshot_journal() -> list[dict]:
    if not os.path.exists(JOURNAL_PATH):
        return []
    try:
        j = pd.read_csv(JOURNAL_PATH)
    except Exception:
        return []
    if j.empty:
        return []
    return j.tail(20).iloc[::-1].to_dict(orient="records")


def build_state(symbols: list[str]) -> dict:
    """Full snapshot dict. Per-symbol failures don't kill the whole snapshot."""
    state = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "engine": {
            "pullback_base_pct": PULLBACK.base_size_pct,
            "pullback_cap_pct": PULLBACK.capital_cap_pct,
            "pullback_stop_pct": PULLBACK.stop_loss_pct,
            "pullback_tp1_pct": PULLBACK.partial_tp_pct,
            "pullback_tp2_pct": PULLBACK.final_tp_pct,
            "pullback_partial_size": PULLBACK.partial_tp_size,
            "pullback_final_size": PULLBACK.final_tp_size,
            "tc_base_pct": TRENDCARRY.base_size_pct,
            "tc_stop_pct": TRENDCARRY.stop_loss_pct,
            "tc_tp1_pct": TRENDCARRY.partial_tp_pct,
            "tc_tp2_pct": TRENDCARRY.final_tp_pct,
            "tc_partial_size": TRENDCARRY.partial_tp_size,
            "tc_final_size": TRENDCARRY.final_tp_size,
        },
        "symbols": {},
        "errors": {},
    }
    # Compute macro first so per-symbol reads can fold it into the Read card.
    try:
        state["macro"] = _snapshot_macro()
    except Exception as e:
        state["errors"]["macro"] = f"{type(e).__name__}: {e}"
        state["macro"] = None
    _snapshot_symbol._macro = state.get("macro")  # cheap closure for compute_read

    for sym in symbols:
        try:
            state["symbols"][sym] = _snapshot_symbol(sym)
        except Exception as e:
            state["errors"][sym] = f"{type(e).__name__}: {e}"
            traceback.print_exc()

    try:
        state["journal_tail"] = _snapshot_journal()
    except Exception as e:
        state["errors"]["journal"] = f"{type(e).__name__}: {e}"
        state["journal_tail"] = []

    if CRYPTO_CARRY.enabled:
        try:
            state["crypto_carry"] = _carry_snap_all()
        except Exception as e:
            state["errors"]["crypto_carry"] = f"{type(e).__name__}: {e}"
            state["crypto_carry"] = []
    else:
        state["crypto_carry"] = []

    # Paper trader — open/manage virtual positions on every signal so we
    # build a live track record without risking real money.
    try:
        from core.paper_trader import tick as _paper_tick
        state["paper"] = _paper_tick(state)
    except Exception as e:
        state["errors"]["paper"] = f"{type(e).__name__}: {e}"
        state["paper"] = None

    return state


# ---------------------------------------------------------------------------
# Atomic JSON write
# ---------------------------------------------------------------------------
def _json_default(o):
    if isinstance(o, (pd.Timestamp, datetime)):
        return o.isoformat()
    if isinstance(o, float) and (o != o):     # NaN
        return None
    return str(o)


def write_state(state: dict, path: str = STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, default=_json_default)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# Notification: fire Discord only when a NEW signal-bar appears.
# Persisted in data/last_notified.json so cron re-runs don't double-fire.
# Schema: { "<symbol>:<strategy>:<side>": "<bar_time_utc iso>" }
# ---------------------------------------------------------------------------
def _load_notified() -> dict:
    if not os.path.exists(NOTIFIED_PATH):
        return {}
    try:
        with open(NOTIFIED_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_notified(d: dict) -> None:
    os.makedirs(os.path.dirname(NOTIFIED_PATH), exist_ok=True)
    tmp = NOTIFIED_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, NOTIFIED_PATH)


# ---------------------------------------------------------------------------
# RSS feed — written every snapshot, holds the most recent N unique signals.
# ---------------------------------------------------------------------------
def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


def _load_rss_history() -> list[dict]:
    if not os.path.exists(RSS_HISTORY_PATH):
        return []
    try:
        with open(RSS_HISTORY_PATH) as f:
            return json.load(f)
    except Exception:
        return []


def _save_rss_history(items: list[dict]) -> None:
    tmp = RSS_HISTORY_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(items, f, indent=2, default=_json_default)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RSS_HISTORY_PATH)


def _write_rss(items: list[dict]) -> None:
    """Write the canonical RSS 2.0 XML to RSS_PATH."""
    now = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0">',
        '<channel>',
        '<title>Quant IA — Live Signals</title>',
        f'<link>{_xml_escape(RSS_PUBLIC_URL_BASE)}</link>',
        '<description>Deterministic pullback + trend-carry signals on SPY/DIA. '
        'Educational only — not investment advice.</description>',
        '<language>en-us</language>',
        f'<lastBuildDate>{now}</lastBuildDate>',
    ]
    for it in items[:RSS_MAX_ITEMS]:
        parts.append("<item>")
        parts.append(f"<title>{_xml_escape(it.get('title', ''))}</title>")
        parts.append(f"<description>{_xml_escape(it.get('description', ''))}</description>")
        parts.append(f"<guid isPermaLink=\"false\">{_xml_escape(it.get('guid', ''))}</guid>")
        parts.append(f"<pubDate>{_xml_escape(it.get('pubDate', now))}</pubDate>")
        parts.append("</item>")
    parts.append("</channel>")
    parts.append("</rss>")
    tmp = RSS_PATH + ".tmp"
    with open(tmp, "w") as f:
        f.write("\n".join(parts))
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, RSS_PATH)


def update_rss(state: dict) -> int:
    """Append any *new* (symbol, strategy, side, bar_time) signals to the RSS
    feed. Returns count appended."""
    items = _load_rss_history()
    existing_guids = {it.get("guid") for it in items}
    appended = 0
    now_rfc = datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
    engine = state.get("engine", {})
    macro = state.get("macro") or {}
    for sym, snap in state.get("symbols", {}).items():
        for strat, sig_key in (("pullback", "pullback_signal"),
                               ("trend_carry", "trend_carry_signal")):
            side = int(snap.get(sig_key, 0) or 0)
            if side == 0:
                continue
            bar = snap.get("bar_time_utc", "")
            guid = f"{sym}:{strat}:{side}:{bar}"
            if guid in existing_guids:
                continue
            side_word = "LONG" if side == 1 else "SHORT"
            close = snap.get("close", 0.0)
            base = (engine.get("pullback_base_pct", 0.75) if strat == "pullback"
                    else engine.get("tc_base_pct", 0.30))
            stop = (engine.get("pullback_stop_pct", 0.025) if strat == "pullback"
                    else engine.get("tc_stop_pct", 0.04))
            tp1 = (engine.get("pullback_tp1_pct", 0.04) if strat == "pullback"
                   else engine.get("tc_tp1_pct", 0.08))
            tp2 = (engine.get("pullback_tp2_pct", 0.15) if strat == "pullback"
                   else engine.get("tc_tp2_pct", 0.25))
            macro_line = (f"  Macro: {macro.get('verdict', '?')} "
                          f"(off={macro.get('risk_off_score', 0)}, "
                          f"on={macro.get('risk_on_score', 0)})") if macro else ""
            desc = (f"{strat.upper()} {side_word} on {sym} @ bar {bar} | "
                    f"Close=${close:.2f} | Size={base*100:.0f}% of acct | "
                    f"Stop=-{stop*100:.2f}% | TP1=+{tp1*100:.2f}% | "
                    f"TP2=+{tp2*100:.2f}%.{macro_line} "
                    f"Educational only — not investment advice.")
            items.insert(0, {
                "guid": guid,
                "title": f"{strat.upper()} {side_word} — {sym} @ ${close:.2f}",
                "description": desc,
                "pubDate": now_rfc,
            })
            appended += 1
    if appended:
        items = items[:RSS_MAX_ITEMS]
        _save_rss_history(items)
    _write_rss(items)
    return appended


def maybe_notify_carry(state: dict) -> int:
    """Fire Discord when a symbol's latest 8h funding rate crosses the
    alert threshold AND we haven't pinged for that exact funding event yet.
    Dedup key: `carry:<symbol>:<last_funding_ts>`. Returns count sent."""
    notified = _load_notified()
    sent = 0
    for c in state.get("crypto_carry", []) or []:
        if not c.get("alert_active"):
            continue
        key = f"carry:{c['symbol']}:{c['last_funding_ts']}"
        if notified.get(key):
            continue
        ann = c["annualized"] * 100
        ann_recent = c["recent_annualized"] * 100
        side_word = "🔔 HIGH FUNDING — short perp + long spot to harvest" \
                    if c["latest_8h"] > 0 else \
                    "🔔 NEGATIVE FUNDING — long perp + short spot to harvest"
        msg = (f"{side_word}\n"
               f"**{c['symbol']}** · 8h funding = `{c['latest_8h']*100:.4f}%` "
               f"→ annualised **{ann:.1f}%** (7d avg {ann_recent:.1f}%)\n"
               f"Delta-neutral cash-and-carry · backtest 2024-25 Sharpe 5–12, "
               f"DD <2%. _Educational only._")
        if _notify_text(msg):
            notified[key] = "1"
            sent += 1
            print(f"[worker] 🔔 carry alert: {c['symbol']} ann={ann:.1f}%")
    if sent:
        _save_notified(notified)
    return sent


def maybe_notify(state: dict) -> int:
    """For each per-symbol snapshot with a non-zero signal, fire Discord
    iff this exact (symbol, strategy, side, bar_time) hasn't fired before.
    Returns number of notifications sent.

    Also pings Discord on paper trader exits — even with no fresh entry,
    a closed leg (stop / TP1 / TP2) is interesting to see live."""
    sent = 0
    notified = _load_notified()
    macro = state.get("macro")
    engine = state.get("engine", {})
    paper = state.get("paper") or {}
    paper_actions = paper.get("actions", [])
    paper_index: dict[str, list[dict]] = {}
    for a in paper_actions:
        paper_index.setdefault(a.get("symbol", ""), []).append(a)

    for sym, snap in state.get("symbols", {}).items():
        for strat, sig_key in (("pullback", "pullback_signal"),
                               ("trend_carry", "trend_carry_signal")):
            side = int(snap.get(sig_key, 0) or 0)
            if side == 0:
                continue
            key = f"{sym}:{strat}:{side}"
            bar = snap.get("bar_time_utc", "")
            if notified.get(key) == bar:
                continue   # already notified for this exact bar
            # Attach paper-trade action context for the Discord card.
            paper_for_sym = [a for a in paper_index.get(sym, [])
                             if a.get("strategy") == strat]
            snap = {**snap, "_paper_actions": paper_for_sym,
                    "_paper_equity": paper.get("equity")}
            ok = _notify_signal(sym, side, snap, strategy=strat,
                                macro=macro, engine=engine)
            if ok:
                notified[key] = bar
                sent += 1
                print(f"[worker] 🔔 notified Discord: {sym} {strat} "
                      f"{'LONG' if side==1 else 'SHORT'} @ {bar}")

    # Paper exits get their own short ping (no dedupe — already deduped at the
    # paper_trader level since a leg only closes once).
    for a in paper_actions:
        if a.get("event") != "close":
            continue
        emoji = "✅" if a.get("pnl", 0) >= 0 else "❌"
        msg = (f"{emoji} **PAPER EXIT** · {a['symbol']} {a['strategy']} "
               f"{a['side']} closed via `{a['reason']}` @ "
               f"${a['exit_price']:,.2f}  ·  pnl `${a['pnl']:+,.2f}`  "
               f"·  equity → `${a['equity_after']:,.2f}`")
        _notify_text(msg)

    if sent:
        _save_notified(notified)
    return sent


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------
_STOP = False
def _on_signal(signum, frame):
    global _STOP
    _STOP = True
    print(f"\n[worker] received signal {signum}, shutting down after current iter.")


def loop(symbols: list[str], interval: int) -> None:
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    print(f"[worker] starting · symbols={symbols} · interval={interval}s · "
          f"writes={STATE_PATH}")
    while not _STOP:
        t0 = time.time()
        try:
            state = build_state(symbols)
            write_state(state)
            n_sent = maybe_notify(state)
            n_carry = maybe_notify_carry(state)
            n_rss = update_rss(state)
            print(f"[worker] {datetime.now(timezone.utc):%H:%M:%S} UTC · "
                  f"wrote snapshot · notified={n_sent} · carry={n_carry} · "
                  f"rss_new={n_rss} · "
                  f"errors={list(state['errors'].keys()) or 'none'} · "
                  f"elapsed={time.time()-t0:.1f}s")
        except Exception:
            print("[worker] FATAL error in iteration; staying alive.")
            traceback.print_exc()
        # Sleep in small chunks so Ctrl-C is responsive.
        slept = 0
        while not _STOP and slept < interval:
            time.sleep(min(2, interval - slept))
            slept += 2
    print("[worker] stopped.")


def main():
    p = argparse.ArgumentParser(description="Background worker — keeps data/state.json fresh.")
    p.add_argument("--interval", type=int, default=600,
                   help="seconds between snapshots (default 600 = 10 min)")
    p.add_argument("--once", action="store_true", help="write one snapshot and exit")
    p.add_argument("--symbols", default=None,
                   help="comma-separated override (default: DATA.symbols)")
    a = p.parse_args()
    syms = (a.symbols.split(",") if a.symbols else DATA.symbols)
    syms = [s.strip().upper() for s in syms if s.strip()]
    if a.once:
        state = build_state(syms)
        write_state(state)
        n_sent = maybe_notify(state)
        n_carry = maybe_notify_carry(state)
        n_rss = update_rss(state)
        print(f"[worker] wrote one snapshot to {STATE_PATH} · "
              f"notified={n_sent} · carry={n_carry} · rss_new={n_rss} · "
              f"errors={list(state['errors'].keys()) or 'none'}")
        return
    loop(syms, a.interval)


if __name__ == "__main__":
    main()
