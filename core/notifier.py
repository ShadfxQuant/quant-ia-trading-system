"""
Notifier — Discord webhook + Twilio SMS.

Channels (each independent, silent if not configured, never raises on caller):
  Discord  — set env DISCORD_WEBHOOK_URL
  SMS      — set env TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
             TWILIO_FROM_NUMBER (your Twilio number, +1...),
             TWILIO_TO_NUMBER   (your phone, +1...)

A signal goes to BOTH channels that are configured. To use SMS only, just
leave DISCORD_WEBHOOK_URL unset.

Public API:
    send_signal(symbol, side, snap, macro=None, engine=None) -> bool
        side: +1 long, -1 short, 0 → no-op
    send_text(msg) -> bool      ad-hoc messages (heartbeat, errors)
    send_sms(body) -> bool      raw SMS (used internally too)
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


def _webhook_url() -> str | None:
    url = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
    return url or None


def _post(payload: dict) -> bool:
    url = _webhook_url()
    if not url:
        return False
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json",
                 "User-Agent": "quant_ia_notifier/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=8) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"[notifier] webhook failed: {e}")
        return False


def _twilio_creds() -> tuple[str, str, str, str] | None:
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    tok = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    frm = os.environ.get("TWILIO_FROM_NUMBER", "").strip()
    to = os.environ.get("TWILIO_TO_NUMBER", "").strip()
    if sid and tok and frm and to:
        return sid, tok, frm, to
    return None


def send_sms(body: str) -> bool:
    """Send an SMS via Twilio's REST API (no SDK needed). Silent if unconfigured."""
    creds = _twilio_creds()
    if not creds:
        return False
    sid, tok, frm, to = creds
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode({"From": frm, "To": to, "Body": body[:1500]}).encode("utf-8")
    auth = base64.b64encode(f"{sid}:{tok}".encode()).decode()
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Authorization": f"Basic {auth}",
                 "Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "quant_ia_notifier/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"[notifier] sms failed: {e}")
        return False


def send_text(msg: str) -> bool:
    """Ad-hoc message to every configured channel. True if any delivered."""
    ok_discord = _post({"content": msg[:1900]})
    ok_sms = send_sms(msg)
    return ok_discord or ok_sms


def send_signal(symbol: str, side: int, snap: dict,
                strategy: str = "pullback",
                macro: dict | None = None,
                engine: dict | None = None) -> bool:
    """Post a formatted Discord embed for a fresh signal."""
    if side == 0:
        return False
    side_word = "LONG" if side == 1 else "SHORT"
    color = 0x2ecc71 if side == 1 else 0xe74c3c

    # Pull pcts from engine block (worker emits these) with safe fallbacks.
    e = engine or {}
    if strategy == "pullback":
        base = e.get("pullback_base_pct", 0.75)
        stop = e.get("pullback_stop_pct", 0.025)
        tp1 = e.get("pullback_tp1_pct", 0.04)
        tp2 = e.get("pullback_tp2_pct", 0.15)
        psize = e.get("pullback_partial_size", 0.5)
        fsize = e.get("pullback_final_size", 0.5)
    else:
        base = e.get("tc_base_pct", 0.30)
        stop = e.get("tc_stop_pct", 0.04)
        tp1 = e.get("tc_tp1_pct", 0.08)
        tp2 = e.get("tc_tp2_pct", 0.25)
        psize = e.get("tc_partial_size", 0.30)
        fsize = e.get("tc_final_size", 0.70)

    loss = base * stop
    p1 = base * tp1 * psize
    p2 = base * tp2 * fsize
    total = p1 + p2
    rr = total / loss if loss > 0 else 0.0
    close = snap.get("close", 0.0)

    # Surface MT5 trading label so the user knows the exact instrument to enter
    try:
        from config.settings import trade_label
        _trade_label = trade_label(symbol)
    except Exception:
        _trade_label = symbol
    symbol_field_value = (f"`{symbol}` → **{_trade_label}**"
                           if _trade_label != symbol else f"`{symbol}`")

    fields = [
        {"name": "Symbol",    "value": symbol_field_value,             "inline": True},
        {"name": "Side",      "value": f"**{side_word}**",            "inline": True},
        {"name": "Strategy",  "value": strategy,                       "inline": True},
        {"name": "Close",     "value": f"${close:.2f}",                "inline": True},
        {"name": "Size",      "value": f"{base*100:.0f}% of acct",     "inline": True},
        {"name": "R:R",       "value": f"{rr:.2f}×",                   "inline": True},
        {"name": "Stop",      "value": f"−{stop*100:.2f}% (−{loss*100:.2f}% acct)", "inline": True},
        {"name": "TP1",       "value": f"+{tp1*100:.2f}% (+{p1*100:.2f}%)",         "inline": True},
        {"name": "TP2",       "value": f"+{tp2*100:.2f}% (+{p2*100:.2f}%)",         "inline": True},
    ]

    desc_lines = [f"Bar time (UTC): `{snap.get('bar_time_utc', '?')[:19]}`"]
    if macro and macro.get("verdict"):
        v = macro["verdict"]
        emoji = {"RISK_ON": "🟢", "RISK_OFF": "🔴", "NEUTRAL": "⚪"}.get(v, "⚪")
        # Symbol-aware: gold (inverse polarity) is aligned with risk-off longs.
        try:
            from core.news_macro import macro_aligned, is_inverse_macro
            aligned, _reason = macro_aligned(symbol, side, v)
            mismatch = not aligned
            inverse = is_inverse_macro(symbol)
        except Exception:
            mismatch = (side == 1 and v == "RISK_OFF") or (side == -1 and v == "RISK_ON")
            inverse = False
        if mismatch:
            warn = " ⚠️ **MACRO MISMATCH**"
        elif inverse and v != "NEUTRAL":
            warn = f" ✅ macro-aligned (inverse polarity — {v} favours {symbol} long)"
        else:
            warn = ""
        desc_lines.append(f"Macro: {emoji} **{v}** "
                          f"(off={macro.get('risk_off_score', 0)} on={macro.get('risk_on_score', 0)}){warn}")
        if mismatch and macro.get("sample_headlines"):
            for theme, headlines in macro["sample_headlines"].items():
                if not headlines:
                    continue
                for h in headlines[:1]:
                    desc_lines.append(f"   • _{theme}_: {h[:140]}")
                break

    payload = {
        "embeds": [{
            "title": (f"🔔 {strategy.upper()} {side_word} — {_trade_label}"
                       if _trade_label != symbol
                       else f"🔔 {strategy.upper()} {side_word} — {symbol}"),
            "description": "\n".join(desc_lines),
            "color": color,
            "fields": fields,
            "footer": {"text": "Quant IA · educational only · not investment advice"},
        }]
    }
    ok_discord = _post(payload)

    # Concise SMS — the actionable essentials only (instrument, side, levels)
    sms_lines = [
        f"{strategy.upper()} {side_word} {_trade_label}",
        f"entry ${close:.2f} · size {base*100:.0f}%",
        f"stop -{stop*100:.1f}% · TP1 +{tp1*100:.1f}% · TP2 +{tp2*100:.1f}%",
    ]
    bt = snap.get("bar_time_utc", "")
    if bt:
        sms_lines.append(f"{str(bt)[:16]} UTC")
    ok_sms = send_sms("\n".join(sms_lines))

    return ok_discord or ok_sms


if __name__ == "__main__":
    # Smoke test: python -m core.notifier
    import sys
    sms_only = "--sms" in sys.argv
    msg = "Quant IA SMS smoke test — if you got this text, SMS works."
    ok = send_sms(msg) if sms_only else send_text(msg)
    print(f"discord configured: {_webhook_url() is not None}")
    print(f"twilio configured:  {_twilio_creds() is not None}")
    print("sent:", ok)
    if not _twilio_creds():
        print("  → set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, "
              "TWILIO_FROM_NUMBER, TWILIO_TO_NUMBER for SMS")
