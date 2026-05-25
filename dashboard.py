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
from datetime import datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from config.settings import PULLBACK, TRENDCARRY, DATA

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


@st.cache_data(ttl=600, show_spinner=True)
def _live_snapshot(symbol: str) -> dict:
    """Fallback: run prepare_dual in-process and build the same per-symbol dict
    the worker would produce."""
    from core.data_loader import load_symbol
    from main_portfolio import prepare_dual
    df = prepare_dual(load_symbol(symbol))
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
        "bars": bars,
    }


@st.cache_data(ttl=600, show_spinner=False)
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
    "Pullback engine + trend-carry sleeve · SPY/DIA · weak-gate / 2.5× lev · "
    "backtest #21: $221K from $100K (in-sample, ~147 wks). "
    "**Educational only — not investment advice. Past performance is not "
    "indicative of future results.**"
)

with st.sidebar:
    st.header("Settings")
    symbol = st.selectbox("Symbol", DATA.symbols, index=0)
    if st.button("🔄 Force refresh (clear cache)"):
        st.cache_data.clear()
        st.rerun()
    st.caption(f"Page load: {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")

snap, mv, jtail, mode = _get_state(symbol)
st.caption(f"Data source: **{mode}**")


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
# Latest signal
# ---------------------------------------------------------------------------
st.divider()
st.subheader(f"🔔 Latest signal — {symbol}")

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
    macro_warn = ""
    if (sig == 1 and mv["verdict"] == "RISK_OFF") or (sig == -1 and mv["verdict"] == "RISK_ON"):
        macro_warn = f" ⚠️ **MACRO MISMATCH ({mv['verdict']})**"
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
# Pyramid gates
# ---------------------------------------------------------------------------
st.divider()
st.subheader("🏛 Pyramid gates")
pp1, pp2 = st.columns(2)
pp1.metric("Pullback pyramid OK?",
           "✓ ALLOWED" if snap["pullback_pyramid_ok"] else "✗ blocked",
           f"cap = {snap['pullback_pyramid_cap']}")
pp2.metric("Trend-carry pyramid OK?",
           "✓ ALLOWED" if snap["trend_carry_pyramid_ok"] else "✗ blocked",
           f"cap = {snap['trend_carry_pyramid_cap']}")
st.caption("Gates: structure_ok · regime ∈ {growth, slowdown} · VWAP-confirmed "
           "(pullback only) · momentum gate OFF in production.")


# ---------------------------------------------------------------------------
# TradingView Advanced Chart (live, with VWAP + Volume studies)
# ---------------------------------------------------------------------------
st.divider()
st.subheader(f"📈 TradingView — {symbol} (interactive)")
TV_SYMBOL_MAP = {"SPY": "AMEX:SPY", "DIA": "AMEX:DIA", "QQQ": "NASDAQ:QQQ",
                 "IWM": "AMEX:IWM", "XLK": "AMEX:XLK", "XLF": "AMEX:XLF"}
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
           "Full Volume Profile (visible range) requires TV Pro; the chart below "
           "is the free Python-computed alternative.")


# ---------------------------------------------------------------------------
# Python-side Volume Profile (free alternative to TV Pro)
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📊 Volume profile — last 400 bars (Python-computed)")
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
    # Distribute each bar's volume across the bins its [low, high] range covers.
    for _, r in bdf.iterrows():
        lo, hi, v = float(r["l"]), float(r["h"]), float(r["v"])
        if hi <= lo or v <= 0:
            continue
        i0 = max(0, int((lo - price_min) / (price_max - price_min) * n_bins))
        i1 = min(n_bins - 1, int((hi - price_min) / (price_max - price_min) * n_bins))
        span = max(i1 - i0 + 1, 1)
        per = v / span
        vol_per_bin[i0:i1 + 1] += per

    # POC + Value Area (70%)
    poc_idx = int(np.argmax(vol_per_bin))
    poc_price = float(centers[poc_idx])
    total_v = vol_per_bin.sum()
    sorted_idx = np.argsort(-vol_per_bin)
    cum = 0.0
    va_set = set()
    for i in sorted_idx:
        cum += vol_per_bin[i]
        va_set.add(int(i))
        if cum >= 0.70 * total_v:
            break
    va_prices = [centers[i] for i in sorted(va_set)]
    va_low, va_high = (float(min(va_prices)), float(max(va_prices))) if va_prices else (poc_price, poc_price)

    cc1, cc2, cc3 = st.columns(3)
    cc1.metric("POC (point of control)", f"${poc_price:.2f}")
    cc2.metric("Value Area High (70%)", f"${va_high:.2f}")
    cc3.metric("Value Area Low (70%)", f"${va_low:.2f}")

    vp_df = pd.DataFrame({"Price": [f"${p:.2f}" for p in centers],
                          "Volume": vol_per_bin})
    # Horizontal bar via st.bar_chart with price-as-index.
    chart_df = pd.DataFrame({"volume": vol_per_bin}, index=[f"${p:.2f}" for p in centers])
    st.bar_chart(chart_df, horizontal=True, height=400)
    st.caption(f"POC = price bin with most traded volume. "
               f"Value Area = the {len(va_set)} bins capturing 70% of total volume.")


# ---------------------------------------------------------------------------
# Journal
# ---------------------------------------------------------------------------
st.divider()
st.subheader("📓 Trade journal — last 20")
if not jtail:
    st.info("No journal entries yet (run `python3 -m live_signal --watch --journal`).")
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


# ---------------------------------------------------------------------------
# Ask an AI about this signal
# ---------------------------------------------------------------------------
import urllib.parse

st.divider()
st.subheader("💬 Ask an AI about this signal")
st.caption("No login required, no API key needed. Type your question; the box "
           "below auto-includes the current signal context. Then click a button "
           "to open that prompt in Claude, ChatGPT, or Perplexity.")

_user_q = st.text_input("Your question",
                        placeholder="e.g. Is now a good time to enter? "
                                    "What's the worst-case loss here?")

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
    f"Pullback pyramid OK: {snap['pullback_pyramid_ok']} (cap={snap['pullback_pyramid_cap']})\n"
    f"Macro verdict: {mv['verdict']} "
    f"(risk_off={mv['risk_off_score']}, risk_on={mv['risk_on_score']}, "
    f"headlines={mv['n_headlines']})\n"
    f"Engine: deterministic pullback + trend-carry · ATR-normalized · "
    f"weak-gate pyramiding · 2.5x leverage backtest result $221K from $100K "
    f"in-sample.\n\n"
    f"Question: {_user_q or '(no question yet)'}"
)
with st.expander("Prompt that will be sent (you can also copy this)", expanded=False):
    st.code(_context, language="text")

_q_enc = urllib.parse.quote(_context)
b1, b2, b3 = st.columns(3)
b1.markdown(f"[💭 **Open in Claude**](https://claude.ai/new?q={_q_enc})")
b2.markdown(f"[🤖 **Open in ChatGPT**](https://chat.openai.com/?q={_q_enc})")
b3.markdown(f"[🔍 **Open in Perplexity**](https://www.perplexity.ai/search/new?q={_q_enc})")
st.caption("Each link opens a new chat in that AI with the signal context pre-filled. "
           "First-time users may need to sign in to the chosen AI.")


# ---------------------------------------------------------------------------
# Subscribe for notifications
# ---------------------------------------------------------------------------
DISCORD_INVITE = os.environ.get("DISCORD_INVITE_URL", "").strip()
try:
    if "DISCORD_INVITE_URL" in st.secrets:
        DISCORD_INVITE = str(st.secrets["DISCORD_INVITE_URL"])
except Exception:
    pass

# Public RSS feed lives at the raw GitHub URL of the worker's output.
RSS_URL = os.environ.get("RSS_URL", "").strip()
try:
    if "RSS_URL" in st.secrets:
        RSS_URL = str(st.secrets["RSS_URL"])
except Exception:
    pass

st.divider()
st.subheader("🔔 Subscribe for signal notifications")
sc1, sc2 = st.columns(2)
with sc1:
    st.markdown("**Discord (recommended)**")
    if DISCORD_INVITE:
        st.markdown(f"[Join the signals server →]({DISCORD_INVITE})")
        st.caption("Every fresh signal (across all tracked symbols) is posted "
                   "to a read-only channel within ~5 minutes of bar close.")
    else:
        st.info("Discord invite link not configured. Admin: set "
                "`DISCORD_INVITE_URL` env var or Streamlit secret.")
with sc2:
    st.markdown("**RSS feed**")
    if RSS_URL:
        st.markdown(f"[Subscribe via RSS →]({RSS_URL})")
        st.caption("Paste this URL into any RSS reader (Feedly, Inoreader, "
                   "NetNewsWire, etc.). Updated whenever the worker writes a "
                   "new signal.")
    else:
        st.info("RSS feed URL not configured. Admin: point `RSS_URL` at "
                "`https://raw.githubusercontent.com/<user>/<repo>/main/data/signals.rss`.")
st.caption("Both subscription channels are free and pull from the same worker that "
           "feeds this dashboard, so they fire at the same time.")


st.divider()
st.caption(
    "Quant IA — pullback + trend-carry · ATR-normalized thresholds · "
    "VWAP-gated pyramiding · HMM informational only · "
    "[SESSION_LOG #21–#23 production: $221K/12.9% DD in-sample]. "
    "**Not investment advice.**"
)
