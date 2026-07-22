"""
Streamlit dashboard for the quant_ia_trading_system.

Read-only page showing the current live snapshot: macro verdict, pullback /
trend-carry signal, embedded TradingView chart (with VWAP + volume), a
Python-side volume profile, pyramid gate status, last 20 journal trades.

Two data modes (auto-detected):
    1. WORKER mode  — if data/state.json exists and is < STATE_MAX_AGE_SEC
                      old, read everything from it. Page loads in <50 ms.
    2. LIVE mode    — fall back to running prepare_dual + macro_verdict in
                      the web process (slower, but no worker needed).

Friend-only access:
    Set env var DASH_TOKEN (or st.secrets["DASH_TOKEN"]) to any string.
    Share URL with ?token=<string>. No token → lock screen.

Disclaimer: educational only — not investment advice.
"""
from __future__ import annotations

import json
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

try:
    import plotly.graph_objects as go
    _HAS_PLOTLY = True
except ImportError:
    _HAS_PLOTLY = False

from config.settings import PULLBACK, TRENDCARRY, DATA, trade_label

STATE_PATH = os.path.join("data", "state.json")
STATE_MAX_AGE_SEC = 1800   # 30 min — older = treated as stale

# ---------------------------------------------------------------------------
# Page setup
# ---------------------------------------------------------------------------
st.set_page_config(page_title="Quant IA — Live Signals",
                   page_icon="📈", layout="wide")


# ---------------------------------------------------------------------------
# Friend-only access (URL token gate)
# ---------------------------------------------------------------------------
def _expected_token() -> str | None:
    try:
        if "DASH_TOKEN" in st.secrets:
            return str(st.secrets["DASH_TOKEN"])
    except Exception:
        pass
    return os.environ.get("DASH_TOKEN")


def _gate() -> bool:
    expected = _expected_token()
    if not expected:
        return True
    qp = st.query_params
    given = qp.get("token", "")
    if isinstance(given, list):
        given = given[0] if given else ""
    if given == expected:
        return True
    st.title("🔒 Private dashboard")
    st.write("Add `?token=...` to the URL to view.")
    return False


if not _gate():
    st.stop()


# ---------------------------------------------------------------------------
# Worker-snapshot loader (preferred). Falls back to live compute when stale.
# ---------------------------------------------------------------------------
@st.cache_data(ttl=60, show_spinner=False)
def _load_state_file() -> dict | None:
    if not os.path.exists(STATE_PATH):
        return None
    try:
        mtime = os.path.getmtime(STATE_PATH)
        if (datetime.now().timestamp() - mtime) > STATE_MAX_AGE_SEC:
            return None
        with open(STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return None


@st.cache_data(ttl=600, show_spinner="Pulling fresh market data + running engine…")
def _live_snapshot(symbol: str) -> dict:
    """Fallback: run prepare_dual in-process and build the same per-symbol dict
    the worker would produce."""
    from core.data_loader import load_symbol
    from main_portfolio import prepare_dual
    df = prepare_dual(load_symbol(symbol))
    try:
        from core.regime_filter import apply_regime_filter
        df = apply_regime_filter(df, symbol)
    except Exception:
        pass
    last = df.iloc[-1]
    ts = df.index[-1]
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
    return {
        "symbol": symbol,
        "bar_time_utc": ts.isoformat(),
        "close": float(last["Close"]),
        "ema": float(last.get("EMA", float("nan"))),
        "sma": float(last.get("SMA", float("nan"))),
        "vwap": float(last.get("VWAP", float("nan"))) if "VWAP" in df.columns else None,
        "pullback_signal": int(last.get("pullback_Signal", 0) or 0),
        "trend_carry_signal": int(last.get("trend_carry_Signal", 0) or 0),
        "pullback_pyramid_ok": bool(last.get("pullback_PyramidOK", False)),
        "pullback_pyramid_cap": int(last.get("pullback_PyramidCap", 0) or 0),
        "trend_carry_pyramid_ok": bool(last.get("trend_carry_PyramidOK", False)),
        "trend_carry_pyramid_cap": int(last.get("trend_carry_PyramidCap", 0) or 0),
        "read": (lambda: __import__("core.read", fromlist=["compute_read"]).compute_read(df, symbol))(),
        "bars": bars,
    }


@st.cache_data(ttl=600, show_spinner="Fetching macro headlines…")
def _live_macro() -> dict:
    from core.news_macro import macro_verdict, RISK_OFF_THEMES, RISK_ON_THEMES
    v = macro_verdict()
    return {
        "verdict": v.verdict,
        "risk_off_score": v.risk_off_score,
        "risk_on_score": v.risk_on_score,
        "theme_hits": v.theme_hits,
        "sample_headlines": v.sample_headlines,
        "n_headlines": v.n_headlines,
        "sources_used": v.sources_used,
        "risk_off_themes": sorted(RISK_OFF_THEMES),
        "risk_on_themes": sorted(RISK_ON_THEMES),
    }


@st.cache_data(ttl=600, show_spinner=False)
def _live_journal() -> list[dict]:
    path = os.path.join("data", "trade_journal.csv")
    if not os.path.exists(path):
        return []
    try:
        return pd.read_csv(path).tail(20).iloc[::-1].to_dict(orient="records")
    except Exception:
        return []


def _get_state(symbol: str) -> tuple[dict, dict, list[dict], str]:
    """Return (symbol_snapshot, macro_snapshot, journal_tail, mode_label)."""
    state = _load_state_file()
    if state is not None and symbol in state.get("symbols", {}):
        return (state["symbols"][symbol],
                state.get("macro") or _live_macro(),
                state.get("journal_tail", []),
                f"WORKER (snapshot @ {state.get('generated_at_utc', '?')[:19]} UTC)")
    return (_live_snapshot(symbol), _live_macro(), _live_journal(),
            "LIVE (web process compute)")


# ---------------------------------------------------------------------------
# Header + disclaimer
# ---------------------------------------------------------------------------
st.title("📈 Quant IA — Live Signals")
st.caption(
    "Pullback engine + trend-carry sleeve · "
    + " · ".join(DATA.symbols)
    + " · 1× lev · proxy-signal architecture (SPY/^NDX/GLD signals → MT5 US500/US100/XAUUSD execution) · "
    "Kalman-smoothed HMM (informational) · "
    "**realized 2.83yr: $100K → $409,279 (+$309,279 / +64.5% CAGR / −9.1% DD / WR 71.0% / n=920)** · "
    "3yr MC: mean $452K, P(2×) 100%, P(5×) 23%, P(ruin) 0%. "
    "**Educational only — not investment advice. Past performance is not "
    "indicative of future results.**"
)

with st.sidebar:
    st.header("Settings")
    symbol = st.selectbox(
        "Symbol (signal source → MT5 label)",
        DATA.symbols, index=0,
        format_func=lambda s: f"{s} → {trade_label(s)}" if trade_label(s) != s else s,
    )
    if st.button("🔄 Force refresh (clear cache)"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Page load: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")

snap, mv, jtail, mode = _get_state(symbol)
st.caption(f"Data source: **{mode}**")


# ---------------------------------------------------------------------------
# Top KPI strip — at-a-glance state for the front page.
# ---------------------------------------------------------------------------
def _kpi_strip(snap: dict, mv: dict, jtail: list[dict]) -> None:
    k1, k2, k3, k4 = st.columns(4)
    pb = snap["pullback_signal"]
    tc = snap["trend_carry_signal"]
    if pb == 1:
        sig_label, sig_delta, sig_dc = "🟢 PULLBACK LONG", "live trigger", "normal"
    elif pb == -1:
        sig_label, sig_delta, sig_dc = "🔴 PULLBACK SHORT", "live trigger", "inverse"
    elif tc == 1:
        sig_label, sig_delta, sig_dc = "🟢 TREND-CARRY LONG", "live trigger", "normal"
    else:
        sig_label, sig_delta, sig_dc = "⚪ FLAT", "no signal", "off"
    k1.metric("Current signal", sig_label, sig_delta, delta_color=sig_dc)

    v_emoji = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "NEUTRAL": "⚪"}.get(mv["verdict"], "⚪")
    k2.metric("Macro mood", f"{v_emoji} {mv['verdict']}",
              f"off {mv['risk_off_score']} · on {mv['risk_on_score']}",
              delta_color="off")

    # Journal-derived KPIs (closed legs only).
    closed_n, win_rate, total_pnl = 0, 0.0, 0.0
    if jtail:
        jdf = pd.DataFrame(jtail)
        if "event" in jdf.columns and "pnl" in jdf.columns:
            exits = jdf[jdf["event"] == "EXIT"]
            if not exits.empty:
                closed_n = int(len(exits))
                pnl_series = pd.to_numeric(exits["pnl"], errors="coerce")
                win_rate = float((pnl_series > 0).mean())
                total_pnl = float(pnl_series.sum())
    k3.metric("Closed legs (last 20)", closed_n,
              f"WR {win_rate:.0%}" if closed_n else "—",
              delta_color="off")
    k4.metric("Realised PnL", f"${total_pnl:,.0f}" if closed_n else "—",
              "from last 20 legs" if closed_n else "no closed legs yet",
              delta_color=("normal" if total_pnl > 0
                           else "inverse" if total_pnl < 0
                           else "off"))


_kpi_strip(snap, mv, jtail)


# ---------------------------------------------------------------------------
# Live model state — what's actually running (refreshed 2026-06-02)
# ---------------------------------------------------------------------------
with st.expander("🧠 Live model state — what's actually running right now", expanded=False):
    cL, cR = st.columns([1, 1])
    with cL:
        st.markdown("""
**Shipped pipeline (2026-06-02)**

```
yfinance bars  →  indicators  →  5-state regime
                                    ↓
                                  HMM regime
                                    ↓
                              Kalman P_bull smoother      ← NEW 8.11
                                    ↓
                              HMM_state_kalman
                                    ↓
              ┌─────────────────────┴───────────────────┐
              ↓                                          ↓
       Pullback signal                          Regime-flip exit
              ↓                                          ↓
       Trend-carry runner                       (disabled — GC=F dropped)
              ↓                                          ↓
                 Execution engine (shared $100K pool)
                                    ↓
                             Trade records
                                    ↓
                         _montecarlo_final.py
                          (10K-path MC gate)
```
""")
    with cR:
        st.markdown("""
**Live config**

| Knob | Value |
|---|---|
| Signals computed on | SPY, ^NDX, GLD |
| Execute on MT5 as | US500, **US100**, XAUUSD |
| Watchlist | SLV, EURUSD=X (IWM + QQQ dropped — Part 8.22) |
| Pullback size | 0.30 of equity, cap 1.00 |
| Max pyramid | 8 legs |
| Stop / TP1 / TP2 | −2.5% / +4% / +15% |
| Time stop | 390 bars |
| RSI size mult | 1.3× / 0.7× |
| Regime-flip exit | OFF (GC=F dropped 2026-06-26) |
| Kalman P_bull | ON (q=1e-4, r=1e-2) |
| Leverage | 1× (paper window) |

**MC headline (10K paths, 3yr horizon, 1×)**

| Metric | Value |
|---|---|
| Mean wealth | $451,629 |
| p5 wealth | $344,812 |
| p50 wealth | $445,530 |
| p95 wealth | $579,452 |
| P(double 2×) | 100% |
| P(5×) | **23.6%** |
| P(any loss) | 0.0% |
| P(ruin −50%) | 0.00% |

Per-symbol realized — signal source → MT5 execution:
- SPY → **US500**: $170,758 (+20.9% CAGR, 75.7% WR, PF 3.18)
- **^NDX** → **US100**: $170,920 (+20.9% CAGR, 78.3% WR, PF **3.13**)
- GLD → **XAUUSD**: $233,533 (+34.9% CAGR, 80.1% WR, PF 3.40)
  _(GC=F gold-futures cross-confirm dropped 2026-06-26 — gold via GLD only)_
""")
    st.caption(
        "Documented in SYSTEM_LOG.md Parts 8.7 → 8.12. "
        "Methodology note: combined number uses a shared $100K pool, not "
        "per-symbol stacks. Source of truth: `_montecarlo_final.py`."
    )


# ---------------------------------------------------------------------------
# Macro verdict
# ---------------------------------------------------------------------------
st.subheader("🌍 Macro verdict")
v_color = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "NEUTRAL": "⚪"}.get(mv["verdict"], "⚪")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Verdict", f"{v_color} {mv['verdict']}")
c2.metric("Risk-off score", mv["risk_off_score"])
c3.metric("Risk-on score", mv["risk_on_score"])
c4.metric("Headlines scanned", mv["n_headlines"])
st.caption("Sources: " + (", ".join(mv["sources_used"]) or "none"))

with st.expander("Theme hits & sample headlines", expanded=mv["verdict"] != "NEUTRAL"):
    risk_off = set(mv.get("risk_off_themes", []))
    any_hits = False
    for theme, n in sorted(mv["theme_hits"].items(), key=lambda x: -x[1]):
        if n == 0:
            continue
        any_hits = True
        side_lbl = "🔴 risk-off" if theme in risk_off else "🟢 risk-on"
        st.markdown(f"**{side_lbl} · `{theme}` × {n}**")
        for h in mv["sample_headlines"].get(theme, [])[:3]:
            st.markdown(f"- {h}")
    if not any_hits:
        st.write("No keyword hits in current headline batch.")


# ---------------------------------------------------------------------------
# Insight Read — system's narrative view of the symbol right now
# ---------------------------------------------------------------------------
st.divider()
_label = trade_label(symbol)
_subheader_sym = f"{symbol} → **{_label}**" if _label != symbol else symbol
st.subheader(f"🧭 Read — {_subheader_sym}")
_read = snap.get("read") or {}
if _read and "error" not in _read:
    _bias = _read.get("bias", "neutral")
    _emoji = {"bullish": "🟢", "bearish": "🔴", "neutral": "⚪️"}.get(_bias, "⚪️")
    _tilt = _read.get("macro_tilt", "neutral")
    _tilt_emoji = {"supports": "✅", "conflicts": "⚠️", "neutral": "·", "n/a": "·"}.get(_tilt, "·")
    rc1, rc2, rc3, rc4 = st.columns(4)
    rc1.metric("Bias", f"{_emoji} {_bias.upper()}")
    rc2.metric("Strength", _read.get("strength", "—").upper(),
               help=f"ADX = {_read.get('adx', '—')}")
    rc3.metric("Regime eligibility 24h", f"{_read.get('regime_pct_24h', 0):.0f}%",
               help="% of last 24h bars that passed the regime filter.")
    rc4.metric("Macro tilt", f"{_tilt_emoji} {_tilt.upper()}")
    st.write(_read.get("narrative", ""))
    flips = _read.get("flip") or []
    if flips:
        with st.expander("What would flip this read?", expanded=False):
            for f in flips:
                st.write(f"• {f}")
elif "error" in _read:
    st.caption(f"Read unavailable: {_read['error']}")
else:
    st.caption("Read not yet computed — next worker tick will populate it.")

st.divider()
st.subheader(f"🔔 Latest signal — {_subheader_sym}")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Bar time (UTC)", snap["bar_time_utc"][:16].replace("T", " "))
c2.metric("Close", f"${snap['close']:.2f}")
c3.metric("EMA(50)", f"${snap['ema']:.2f}" if snap['ema'] == snap['ema'] else "—")
c4.metric("SMA(130)", f"${snap['sma']:.2f}" if snap['sma'] == snap['sma'] else "—")
vw = snap.get("vwap")
c5.metric("VWAP", f"${vw:.2f}" if vw is not None and vw == vw else "—")


def _signal_card(label, sig, base_pct, stop_pct, tp1_pct, tp2_pct,
                 partial_size, final_size):
    if sig == 0:
        st.info(f"No {label} signal on the latest bar.")
        return
    side_word = "LONG" if sig == 1 else "SHORT"
    # Symbol-aware macro check (gold is inverse polarity vs equities).
    from core.news_macro import macro_aligned, is_inverse_macro
    aligned, reason = macro_aligned(symbol, sig, mv["verdict"])
    macro_warn = "" if aligned else f" ⚠️ **{reason}**"
    if is_inverse_macro(symbol) and aligned and mv["verdict"] != "NEUTRAL":
        macro_warn = f" ✅ macro-aligned ({mv['verdict']} favours {symbol} long)"
    st.success(f"**{label.upper()} {side_word} TRIGGERED**{macro_warn}")
    notion_pct = base_pct
    loss = notion_pct * stop_pct
    p1 = notion_pct * tp1_pct * partial_size
    p2 = notion_pct * tp2_pct * final_size
    total = p1 + p2
    rr = total / loss if loss > 0 else float("inf")
    cc1, cc2, cc3, cc4 = st.columns(4)
    cc1.metric("Position % of acct", f"{notion_pct*100:.0f}%")
    cc2.metric("Stop", f"−{stop_pct*100:.2f}%", delta=f"−{loss*100:.2f}% of acct",
               delta_color="inverse")
    cc3.metric("TP1 (partial)", f"+{tp1_pct*100:.2f}%", delta=f"+{p1*100:.2f}%")
    cc4.metric("TP2 (runner)", f"+{tp2_pct*100:.2f}%", delta=f"+{p2*100:.2f}%")
    st.caption(f"R:R = **{rr:.2f}×**  ·  worst case −{loss*100:.2f}%  ·  "
               f"both TPs +{total*100:.2f}% (per unit of account)")


_signal_card("pullback", snap["pullback_signal"],
             PULLBACK.base_size_pct, PULLBACK.stop_loss_pct,
             PULLBACK.partial_tp_pct, PULLBACK.final_tp_pct,
             PULLBACK.partial_tp_size, PULLBACK.final_tp_size)

_signal_card("trend_carry", snap["trend_carry_signal"],
             TRENDCARRY.base_size_pct, TRENDCARRY.stop_loss_pct,
             TRENDCARRY.partial_tp_pct, TRENDCARRY.final_tp_pct,
             TRENDCARRY.partial_tp_size, TRENDCARRY.final_tp_size)


# ---------------------------------------------------------------------------
# Signal Explainer — plain-English breakdown of the latest bar
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📖 What does this signal mean?")


def _explain_state(snap: dict) -> tuple[str, list[str]]:
    """Return (headline, bullet-list explanation)."""
    close = snap["close"]
    ema = snap["ema"]
    sma = snap["sma"]
    vwap = snap.get("vwap")
    pb = snap["pullback_signal"]
    tc = snap["trend_carry_signal"]

    structure = "bullish" if (ema == ema and sma == sma and ema > sma) else (
                "bearish" if (ema == ema and sma == sma and ema < sma) else "neutral")
    above_vwap = (vwap is not None and vwap == vwap and close > vwap)
    deviation_pct = ((ema - sma) / sma * 100) if (sma and sma == sma) else None
    price_dev_pct = ((close - ema) / ema * 100) if (ema and ema == ema) else None

    bullets = []
    bullets.append(
        f"**Trend structure: {structure.upper()}** — EMA(50) = ${ema:.2f}, "
        f"SMA(130) = ${sma:.2f}. "
        + ("The faster average is above the slower one, meaning the medium-term "
           "trend is up." if structure == "bullish" else
           "The faster average is below the slower one — medium-term trend is down."
           if structure == "bearish" else
           "Averages are close together — no clear trend direction.")
    )
    if deviation_pct is not None:
        bullets.append(
            f"**Imbalance (EMA − SMA): {deviation_pct:+.2f}%** — how stretched the "
            "trend is. Bigger absolute number = stronger directional bias."
        )
    if price_dev_pct is not None:
        bullets.append(
            f"**Pullback proximity (Close − EMA): {price_dev_pct:+.2f}%** — price "
            "needs to be inside an ATR-scaled band around EMA for a fresh entry. "
            "Too far above = chase; too far below = breakdown risk."
        )
    if vwap is not None and vwap == vwap:
        bullets.append(
            f"**VWAP: ${vwap:.2f}** — institutional reference price for the day. "
            f"Current close is **{'ABOVE' if above_vwap else 'BELOW'}** VWAP. "
            "Pyramid adds require the close to be above VWAP."
        )

    if pb == 1:
        head = ("🟢 **Pullback LONG fired** — bullish structure + price dipped into "
                "the ATR-scaled pullback band + momentum just turned back up.")
        bullets.append(
            "**What it means in plain English:** the medium-term trend is up, "
            "price just pulled back a little inside that trend, and momentum is "
            "starting to re-accelerate. The engine wants to ride the continuation."
        )
        bullets.append(
            "**Action plan:** enter LONG at the suggested size, place a stop at "
            "the displayed −% (this is your max loss for the full position), and "
            "set two take-profits at TP1 (close 50%) and TP2 (close the runner). "
            "After TP1 fills, move the stop to your entry price (breakeven)."
        )
    elif pb == -1:
        head = ("🔴 **Pullback SHORT fired** — rare; the engine only takes shorts "
                "when structure AND regime both agree it's bearish.")
        bullets.append(
            "**What it means in plain English:** the medium-term trend has flipped "
            "down AND the regime model agrees. This is the engine's strictest entry."
        )
    elif tc == 1:
        head = ("🟢 **Trend-carry LONG fired** — wider-exit, longer-hold version of "
                "the pullback. Designed to ride bigger swings.")
        bullets.append(
            "**Action plan:** smaller size than the pullback engine, wider stop "
            "(survives normal volatility), and a much further TP2. Hold for weeks "
            "if needed — the time stop is ~9 months on 1h bars."
        )
    else:
        head = "⚪ **No signal on the latest bar.** The engine is waiting."
        if structure == "bullish":
            bullets.append(
                "Trend is up — the engine is waiting for a clean pullback into the "
                "EMA + a momentum re-acceleration before firing. It can take days."
            )
        elif structure == "bearish":
            bullets.append(
                "Trend is down. Long entries require bullish structure; the engine "
                "will sit out until the trend flips back up."
            )
        else:
            bullets.append(
                "No clear trend. The engine only enters in confirmed up- or down-trends."
            )

    return head, bullets


_head, _bullets = _explain_state(snap)
st.markdown(_head)
for b in _bullets:
    st.markdown(f"- {b}")

with st.expander("How the engine works (1-minute summary)"):
    st.markdown("""
- **Pullback engine** — needs all four: bullish trend structure, price inside an
  ATR-scaled pullback band around EMA, deviation between EMA and SMA, momentum
  re-accelerating. Exits at a fixed stop, TP1 (close half + move stop to BE),
  and TP2 (close the runner).
- **Trend-carry sleeve** — same alpha logic but wider stops and a much further
  TP2. Designed to capture multi-week swings instead of single pullbacks.
- **Pyramiding** — adds new size on top of an existing winner when (1) trend is
  still favourable and (2) close is above VWAP. Each strategy has its own cap.
- **Macro filter** — *informational only*. Tags signals that disagree with the
  current world headlines (e.g. LONG fired while news reads RISK_OFF). Never
  blocks the trade — you decide.
- All percentages are **scale-invariant** — the trade plan works on any size
  book and any instrument that mirrors SPY's percentage returns.
""")


# ---------------------------------------------------------------------------
# Secondary sections behind tabs — keeps the front page focused on the
# headline info (macro verdict + signal cards + explainer above).
# ---------------------------------------------------------------------------
st.divider()
(tab_chart, tab_pyramid, tab_paper, tab_carry, tab_journal,
 tab_ask, tab_subscribe) = st.tabs(
    ["📈 Chart", "🏛 Pyramid", "💼 Paper Portfolio", "💰 Crypto Carry",
     "📓 Journal", "💬 Ask AI", "🔔 Subscribe"]
)

# ---------- 📈 Chart tab: TradingView + Python volume profile ----------
with tab_chart:
    st.subheader(f"TradingView — {symbol} (interactive)")
    TV_SYMBOL_MAP = {
        # Live signal sources (Part 8.22)
        "SPY":   "AMEX:SPY",          # → MT5 US500
        "^NDX":  "NASDAQ:NDX",        # → MT5 US100 (cash index, replaced QQQ)
        "GLD":   "AMEX:GLD",          # → MT5 XAUUSD
        "GC=F":  "COMEX:GC1!",        # → MT5 XAUUSD cross-confirm
        # Watchlist
        "SLV":      "AMEX:SLV",
        "EURUSD=X": "FX:EURUSD",
        # Legacy mappings (in case of pivot)
        "QQQ": "NASDAQ:QQQ", "DIA": "AMEX:DIA", "IWM": "AMEX:IWM",
        "ES=F": "CME_MINI:ES1!", "NQ=F": "CME_MINI:NQ1!",
        "XLK": "AMEX:XLK", "XLF": "AMEX:XLF",
        "BTCUSDT": "BINANCE:BTCUSDT", "ETHUSDT": "BINANCE:ETHUSDT",
    }
    tv_symbol = TV_SYMBOL_MAP.get(symbol, f"AMEX:{symbol}")
    tv_html = f"""
<div class="tradingview-widget-container" style="height:560px;width:100%">
  <div id="tv_chart_{symbol}" style="height:560px;width:100%"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
    new TradingView.widget({{
      "container_id": "tv_chart_{symbol}",
      "autosize": true,
      "symbol": "{tv_symbol}",
      "interval": "60",
      "timezone": "Etc/UTC",
      "theme": "dark",
      "style": "1",
      "locale": "en",
      "toolbar_bg": "#1e1e1e",
      "enable_publishing": false,
      "withdateranges": true,
      "hide_side_toolbar": false,
      "allow_symbol_change": true,
      "studies": [
        "VWAP@tv-basicstudies",
        "Volume@tv-basicstudies",
        "MAExp@tv-basicstudies",
        "MASimple@tv-basicstudies"
      ]
    }});
  </script>
</div>
"""
    components.html(tv_html, height=580)
    st.caption("TradingView free widget — VWAP, Volume, EMA, SMA pre-loaded. "
               "Full Volume Profile (visible range) requires TV Pro; the chart "
               "below is the free Python-computed alternative.")

    st.divider()
    st.subheader("Volume profile — last 400 bars (Python-computed)")
    bars = snap.get("bars", [])
    if len(bars) < 20:
        st.info("Not enough bars in snapshot for volume profile.")
    else:
        bdf = pd.DataFrame(bars)
        n_bins = st.slider("Price bins", min_value=20, max_value=80, value=40, step=5)
        price_min, price_max = float(bdf["l"].min()), float(bdf["h"].max())
        edges = np.linspace(price_min, price_max, n_bins + 1)
        centers = (edges[:-1] + edges[1:]) / 2
        vol_per_bin = np.zeros(n_bins)
        for _, r in bdf.iterrows():
            lo, hi, v = float(r["l"]), float(r["h"]), float(r["v"])
            if hi <= lo or v <= 0:
                continue
            i0 = max(0, int((lo - price_min) / (price_max - price_min) * n_bins))
            i1 = min(n_bins - 1, int((hi - price_min) / (price_max - price_min) * n_bins))
            span = max(i1 - i0 + 1, 1)
            per = v / span
            vol_per_bin[i0:i1 + 1] += per

        poc_idx = int(np.argmax(vol_per_bin))
        poc_price = float(centers[poc_idx])
        total_v = vol_per_bin.sum()
        sorted_idx = np.argsort(-vol_per_bin)
        cum = 0.0
        va_set: set[int] = set()
        for i in sorted_idx:
            cum += vol_per_bin[i]
            va_set.add(int(i))
            if cum >= 0.70 * total_v:
                break
        va_prices = [centers[i] for i in sorted(va_set)]
        va_low, va_high = ((float(min(va_prices)), float(max(va_prices)))
                           if va_prices else (poc_price, poc_price))

        cc1, cc2, cc3 = st.columns(3)
        cc1.metric("POC (point of control)", f"${poc_price:.2f}")
        cc2.metric("Value Area High (70%)", f"${va_high:.2f}")
        cc3.metric("Value Area Low (70%)", f"${va_low:.2f}")

        if _HAS_PLOTLY:
            # Horizontal Plotly bars with shaded value-area band + POC line.
            in_va = [(i in va_set) for i in range(n_bins)]
            colors = ["#00D4AA" if v else "#3a4759" for v in in_va]
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=vol_per_bin, y=centers, orientation="h",
                marker=dict(color=colors, line=dict(width=0)),
                hovertemplate="Price ≈ $%{y:.2f}<br>Volume %{x:,.0f}<extra></extra>",
                name="Volume",
            ))
            # POC marker
            fig.add_hline(y=poc_price, line=dict(color="#F5A524", width=2, dash="dot"),
                          annotation_text=f"POC ${poc_price:.2f}",
                          annotation_position="right",
                          annotation_font_color="#F5A524")
            # Value-area band
            fig.add_hrect(y0=va_low, y1=va_high,
                          fillcolor="#00D4AA", opacity=0.07, line_width=0,
                          annotation_text=f"Value Area 70% (${va_low:.2f} – ${va_high:.2f})",
                          annotation_position="top left",
                          annotation_font_color="#00D4AA",
                          annotation_font_size=11)
            fig.update_layout(
                template="plotly_dark",
                paper_bgcolor="#0B0F17", plot_bgcolor="#0B0F17",
                height=460, margin=dict(l=10, r=10, t=20, b=10),
                xaxis=dict(title="Volume", gridcolor="#1f2632", zeroline=False),
                yaxis=dict(title="Price", gridcolor="#1f2632", zeroline=False),
                showlegend=False,
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            # Fallback if plotly isn't installed.
            chart_df = pd.DataFrame({"volume": vol_per_bin},
                                    index=[f"${p:.2f}" for p in centers])
            st.bar_chart(chart_df, horizontal=True, height=400)
        st.caption(f"POC = price bin with most traded volume. "
                   f"Value Area = the {len(va_set)} bins capturing 70% of total volume. "
                   "Hover any bar for exact volume.")


# ---------- 🏛 Pyramid tab ----------
with tab_pyramid:
    st.subheader("Pyramid gates")
    pp1, pp2 = st.columns(2)
    pp1.metric("Pullback pyramid OK?",
               "✓ ALLOWED" if snap["pullback_pyramid_ok"] else "✗ blocked",
               f"cap = {snap['pullback_pyramid_cap']}")
    pp2.metric("Trend-carry pyramid OK?",
               "✓ ALLOWED" if snap["trend_carry_pyramid_ok"] else "✗ blocked",
               f"cap = {snap['trend_carry_pyramid_cap']}")
    st.caption("Gates: structure_ok · regime ∈ {growth, slowdown} · "
               "VWAP-confirmed (pullback only) · momentum gate OFF in production.")
    st.markdown(
        "**What is a pyramid add?** When you already hold a position and the "
        "engine signals again in the same direction, it doesn't open a new "
        "trade — it *adds* to the winner. These gates control whether that "
        "add is allowed on the current bar. Above-VWAP + bullish trend "
        "= institutional confirmation that the trend is still alive."
    )


# ---------- 💼 Paper Portfolio tab ----------
with tab_paper:
    st.subheader("Paper portfolio — live track record")
    st.caption("Virtual $100K account. Every signal the worker fires opens a "
               "paper position; the same exit ladder (stop / TP1 / TP2) closes "
               "it on subsequent bars. Pure simulation — no real orders placed.")

    import os as _os, json as _json
    _paper_path = _os.path.join("data", "paper_account.json")
    if not _os.path.exists(_paper_path):
        st.info("No paper trades yet. The first signal after the worker runs "
                "with the paper trader enabled will populate this.")
    else:
        try:
            with open(_paper_path) as _f:
                _paper = _json.load(_f)
        except Exception as e:
            _paper = None
            st.error(f"Could not read paper_account.json: {e}")

        if _paper:
            _equity = _paper.get("equity", 100_000)
            _init = _paper.get("initial_capital", 100_000)
            _ret_pct = (_equity / _init - 1) * 100 if _init else 0.0
            _open = _paper.get("open_positions", [])
            _closed = _paper.get("closed_trades", [])
            _n_total = _paper.get("n_trades_total", len(_closed))

            pc1, pc2, pc3, pc4 = st.columns(4)
            pc1.metric("Equity", f"${_equity:,.0f}",
                       delta=f"{_ret_pct:+.2f}% vs start")
            pc2.metric("Open positions", str(len(_open)))
            pc3.metric("Closed trades", str(_n_total))
            _wins = [t for t in _closed if t.get("pnl", 0) > 0]
            _wr = (len(_wins) / len(_closed) * 100) if _closed else 0.0
            pc4.metric("Win rate", f"{_wr:.1f}%")

            # Open positions table
            if _open:
                st.markdown("### 🟢 Open positions")
                _open_rows = []
                for p in _open:
                    side_word = "LONG" if p["side"] == 1 else "SHORT"
                    _open_rows.append({
                        "Symbol": p["symbol"],
                        "Strategy": p["strategy"],
                        "Side": side_word,
                        "Entry": f"${p['entry_price']:,.2f}",
                        "Size $": f"${p['size']:,.0f}",
                        "TP1 hit?": "✓" if p.get("tp1_hit") else "—",
                        "Opened (UTC)": p["entry_time"][:16].replace("T", " "),
                    })
                st.dataframe(_open_rows, use_container_width=True, hide_index=True)
            else:
                st.caption("No open positions right now — the engine is waiting "
                           "for a fresh setup.")

            # Equity curve from closed trades
            if _closed:
                st.markdown("### 📈 Equity curve")
                import pandas as _pd
                _eq_rows = [{"time": _paper.get("last_updated_utc", ""),
                             "equity": _init}]
                _running = float(_init)
                for t in _closed:
                    _running += t.get("pnl", 0)
                    _eq_rows.append({"time": t.get("exit_time", ""),
                                     "equity": _running})
                _eq_df = _pd.DataFrame(_eq_rows)
                _eq_df["time"] = _pd.to_datetime(_eq_df["time"], errors="coerce", utc=True)
                _eq_df = _eq_df.dropna(subset=["time"]).set_index("time").sort_index()
                st.line_chart(_eq_df["equity"])

                # Recent trades
                st.markdown("### 🧾 Last 20 closed trades")
                _recent = list(reversed(_closed[-20:]))
                _trade_rows = []
                for t in _recent:
                    side_word = "LONG" if t["side"] == 1 else "SHORT"
                    pnl = t.get("pnl", 0)
                    _trade_rows.append({
                        "Symbol": t["symbol"],
                        "Strategy": t["strategy"],
                        "Side": side_word,
                        "Reason": t["reason"],
                        "Entry": f"${t['entry_price']:,.2f}",
                        "Exit": f"${t['exit_price']:,.2f}",
                        "PnL": f"${pnl:+,.0f}",
                        "Closed (UTC)": t["exit_time"][:16].replace("T", " "),
                    })
                st.dataframe(_trade_rows, use_container_width=True, hide_index=True)
            else:
                st.caption("No closed trades yet — equity curve will appear "
                           "after the first exit.")


# ---------- 💰 Crypto Carry tab ----------
with tab_carry:
    st.subheader("Delta-neutral funding carry — Binance perps")
    st.caption("Validated 2026-05-25 on BTCUSDT / ETHUSDT / SOLUSDT — "
               "Sharpe 5–12, max DD <2%, CAGR 5–7%. Updated every cron run "
               "from Binance public funding endpoint.")

    # Reuse the snapshot loader pattern.
    def _load_carry() -> list[dict]:
        st_file = _load_state_file()
        if st_file and "crypto_carry" in st_file:
            return st_file["crypto_carry"] or []
        # Fallback to live fetch if no worker snapshot available.
        try:
            from core.crypto_carry import snap_all
            return snap_all()
        except Exception:
            return []

    carry_rows = _load_carry()
    if not carry_rows:
        st.info("No carry data yet. The worker will populate this on its next "
                "cron run.")
    else:
        # Top-level alert banner if any symbol is in alert.
        alerts = [c for c in carry_rows if c.get("alert_active")]
        if alerts:
            st.warning(
                "🔔 **Extreme funding active on " +
                ", ".join(f"{c['symbol']} ({c['annualized']*100:+.1f}%/yr)" for c in alerts)
                + "** — large carry opportunity OR crowded positioning warning."
            )

        # Table.
        rows = []
        for c in carry_rows:
            rows.append({
                "Symbol": c["symbol"],
                "8h funding": f"{c['latest_8h']*100:+.4f}%",
                "Annualised (latest)": f"{c['annualized']*100:+.2f}%",
                "7d avg annualised": f"{c['recent_annualized']*100:+.2f}%",
                "% positive (7d)": f"{c['pct_positive_recent']*100:.0f}%",
                "Position to harvest": c["side"],
                "Alert?": "🔔" if c["alert_active"] else "",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        # KPI strip — current top carry yield + average.
        top = max(carry_rows, key=lambda c: c["recent_annualized"])
        avg_ann = sum(c["recent_annualized"] for c in carry_rows) / len(carry_rows)
        kk1, kk2, kk3 = st.columns(3)
        kk1.metric("Top symbol (7d annualised)",
                   top["symbol"],
                   f"{top['recent_annualized']*100:+.2f}%")
        kk2.metric("Avg across roster (7d ann.)",
                   f"{avg_ann*100:+.2f}%",
                   "delta-neutral, no directional risk")
        kk3.metric("Symbols tracked", len(carry_rows),
                   "Binance perp funding")

    with st.expander("How to execute a carry position", expanded=False):
        st.markdown("""
**Delta-neutral funding carry (cash-and-carry)** — earn the funding rate
without taking directional risk:

1. Pick the symbol with the highest 7-day annualised yield (positive → short
   perp side; negative → long perp side).
2. **Long spot** and **short perp** in equal notional (e.g. $1,000 spot BTC +
   $1,000 short BTCUSDT perp). Net delta = 0 — you don't care if price moves.
3. Every 8 hours (00:00, 08:00, 16:00 UTC) you receive (or pay) the funding
   rate × notional. Positive funding pays the short perp side.
4. Hold until the funding rate normalises or flips sign, then close both legs.
5. Net of fees on majors ≈ **5–7%/yr** annualised, max DD historically <2%.

**Risk:** the only meaningful risk is **basis blowout** — a sudden divergence
between perp and spot price beyond your margin buffer. On BTC/ETH this is rare
and small; on smaller symbols it happens. Always use isolated margin and keep
extra collateral.

**Backtest reference (Dec 2023 → May 2026):**
- BTCUSDT: 7.15% CAGR · 0.35% max DD · Sharpe 11.77
- ETHUSDT: 7.45% CAGR · 0.50% max DD · Sharpe 11.79
- SOLUSDT: 5.53% CAGR · 1.98% max DD · Sharpe 5.53

_Educational only. Not financial advice. Verify funding rates and execute on
your own venue._
""")


# ---------- 📓 Journal tab ----------
with tab_journal:
    st.subheader("Trade journal — last 20")
    if not jtail:
        st.info("No journal entries yet "
                "(run `python3 -m live_signal --watch --journal`).")
    else:
        jdf = pd.DataFrame(jtail)
        show_cols = [c for c in ("trade_id", "timestamp", "event", "side",
                                 "symbol", "price", "qty", "pnl", "notes")
                     if c in jdf.columns]
        st.dataframe(jdf[show_cols], use_container_width=True, hide_index=True)
        if "event" in jdf.columns and "pnl" in jdf.columns:
            exits = jdf[jdf["event"] == "EXIT"]
            if not exits.empty:
                total = pd.to_numeric(exits["pnl"], errors="coerce").sum()
                wins = (pd.to_numeric(exits["pnl"], errors="coerce") > 0).sum()
                m1, m2, m3 = st.columns(3)
                m1.metric("Closed legs (last 20)", len(exits))
                m2.metric("Win rate", f"{wins / max(len(exits), 1):.0%}")
                m3.metric("Total realised", f"${total:,.2f}")


# ---------- 💬 Ask AI tab ----------
with tab_ask:
    st.subheader("Ask an AI about this signal")
    st.caption("No login required, no API key needed. Type your question; the "
               "prompt below auto-includes the current signal context. Click a "
               "button to open that prompt in Claude, ChatGPT, or Perplexity.")

    _user_q = st.text_input(
        "Your question",
        placeholder="e.g. Is now a good time to enter? "
                    "What's the worst-case loss here?",
    )

    _pb_word = {0: "no signal", 1: "LONG", -1: "SHORT"}[snap["pullback_signal"]]
    _tc_word = {0: "no signal", 1: "LONG"}.get(snap["trend_carry_signal"], "no signal")
    _context = (
        f"Quant IA live signal — {symbol} 1h\n"
        f"Bar time (UTC): {snap['bar_time_utc'][:19]}\n"
        f"Close: ${snap['close']:.2f}\n"
        f"EMA(50): ${snap['ema']:.2f}   SMA(130): ${snap['sma']:.2f}"
        + (f"   VWAP: ${snap['vwap']:.2f}" if snap.get('vwap') == snap.get('vwap') else "")
        + f"\nPullback signal: {_pb_word}\n"
        f"Trend-carry signal: {_tc_word}\n"
        f"Pullback pyramid OK: {snap['pullback_pyramid_ok']} "
        f"(cap={snap['pullback_pyramid_cap']})\n"
        f"Macro verdict: {mv['verdict']} "
        f"(risk_off={mv['risk_off_score']}, risk_on={mv['risk_on_score']}, "
        f"headlines={mv['n_headlines']})\n"
        f"Engine: deterministic pullback + trend-carry · ATR-normalized · "
        f"weak-gate pyramiding · 2.5x leverage backtest result $221K from $100K "
        f"in-sample.\n\n"
        f"Question: {_user_q or '(no question yet)'}"
    )
    with st.expander("Prompt that will be sent (you can also copy this)",
                     expanded=False):
        st.code(_context, language="text")

    _q_enc = urllib.parse.quote(_context)
    b1, b2, b3 = st.columns(3)
    b1.markdown(f"[💭 **Open in Claude**](https://claude.ai/new?q={_q_enc})")
    b2.markdown(f"[🤖 **Open in ChatGPT**](https://chat.openai.com/?q={_q_enc})")
    b3.markdown(f"[🔍 **Open in Perplexity**](https://www.perplexity.ai/search/new?q={_q_enc})")
    st.caption("Each link opens a new chat in that AI with the signal context "
               "pre-filled. First-time users may need to sign in to the AI.")


# ---------- 🔔 Subscribe tab ----------
with tab_subscribe:
    DISCORD_INVITE = os.environ.get("DISCORD_INVITE_URL", "").strip()
    try:
        if "DISCORD_INVITE_URL" in st.secrets:
            DISCORD_INVITE = str(st.secrets["DISCORD_INVITE_URL"])
    except Exception:
        pass
    RSS_URL = os.environ.get("RSS_URL", "").strip()
    try:
        if "RSS_URL" in st.secrets:
            RSS_URL = str(st.secrets["RSS_URL"])
    except Exception:
        pass

    st.subheader("Subscribe for signal notifications")
    sc1, sc2 = st.columns(2)
    with sc1:
        st.markdown("**Discord (recommended)**")
        if DISCORD_INVITE:
            st.markdown(f"[Join the signals server →]({DISCORD_INVITE})")
            st.caption("Every fresh signal is posted to a read-only channel "
                       "within ~5 minutes of bar close.")
        else:
            st.info("Discord invite link not configured. Admin: set "
                    "`DISCORD_INVITE_URL` env var or Streamlit secret.")
    with sc2:
        st.markdown("**RSS feed**")
        if RSS_URL:
            st.markdown(f"[Subscribe via RSS →]({RSS_URL})")
            st.caption("Paste this URL into any RSS reader (Feedly, Inoreader, "
                       "NetNewsWire, etc.). Updated whenever the worker writes "
                       "a new signal.")
        else:
            st.info("RSS feed URL not configured. Admin: point `RSS_URL` at "
                    "`https://raw.githubusercontent.com/<user>/<repo>/main/data/signals.rss`.")
    st.caption("Both subscription channels are free and pull from the same "
               "worker that feeds this dashboard, so they fire at the same time.")


st.divider()
st.caption(
    "Quant IA — pullback + trend-carry · ATR-normalized thresholds · "
    "VWAP-gated pyramiding · HMM informational only · "
    "[SESSION_LOG #21–#23 production: $221K/12.9% DD in-sample]. "
    "**Not investment advice.**"
)
