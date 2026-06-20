"""
Notifier — Discord webhook + Telegram + Twilio SMS.

Channels (each independent, silent if not configured, never raises on caller):
  Discord   — set env DISCORD_WEBHOOK_URL
  Telegram  — set env TELEGRAM_BOT_TOKEN  (from @BotFather)
                      TELEGRAM_CHAT_ID    (your chat id; get it from @userinfobot)
  SMS       — set env TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
                      TWILIO_FROM_NUMBER (your Twilio number, +1...),
                      TWILIO_TO_NUMBER   (your phone, +1...)

A signal fans out to EVERY configured channel. To use Telegram only, just set
the two TELEGRAM_* vars and leave the others unset.

Telegram is the recommended channel: free, instant push, and its inline-button
support is what a future "tap to approve → execute" flow would build on.

Public API:
    send_signal(symbol, side, snap, macro=None, engine=None) -> bool
        side: +1 long, -1 short, 0 → no-op
    send_text(msg) -> bool       ad-hoc messages (heartbeat, errors)
    send_telegram(body) -> bool  raw Telegram message (used internally too)
    send_sms(body) -> bool       raw SMS (used internally too)
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


def _telegram_creds() -> tuple[str, str] | None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if token and chat:
        return token, chat
    return None


def send_telegram(body: str) -> bool:
    """Send a Telegram message via the Bot API. Silent if unconfigured."""
    creds = _telegram_creds()
    if not creds:
        return False
    token, chat = creds
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat,
        "text": body[:4000],
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded",
                 "User-Agent": "quant_ia_notifier/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"[notifier] telegram failed: {e}")
        return False


def send_text(msg: str) -> bool:
    """Ad-hoc message to every configured channel. True if any delivered."""
    ok_discord = _post({"content": msg[:1900]})
    ok_telegram = send_telegram(msg)
    ok_sms = send_sms(msg)
    return ok_discord or ok_telegram or ok_sms


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

    # Concise plain-text body — the actionable essentials only.
    bt = str(snap.get("bar_time_utc", ""))[:16]
    plain_lines = [
        f"{strategy.upper()} {side_word} {_trade_label}",
        f"entry ${close:.2f} · size {base*100:.0f}%",
        f"stop -{stop*100:.1f}% · TP1 +{tp1*100:.1f}% · TP2 +{tp2*100:.1f}%",
    ]
    if bt:
        plain_lines.append(f"{bt} UTC")
    ok_sms = send_sms("\n".join(plain_lines))

    # Telegram: same content, lightly Markdown-formatted.
    emoji = "🟢" if side == 1 else "🔴"
    tg = (f"{emoji} *{strategy.upper()} {side_word}* — `{_trade_label}`\n"
          f"entry *${close:.2f}* · size {base*100:.0f}%\n"
          f"stop -{stop*100:.1f}% · TP1 +{tp1*100:.1f}% · TP2 +{tp2*100:.1f}% · R:R {rr:.2f}×")
    if bt:
        tg += f"\n_{bt} UTC_"
    ok_telegram = send_telegram(tg)

    return ok_discord or ok_telegram or ok_sms


if __name__ == "__main__":
    # Smoke test:  python -m core.notifier          (all configured channels)
    #              python -m core.notifier --telegram (Telegram only)
    #              python -m core.notifier --sms      (SMS only)
    import sys
    msg = "Quant IA smoke test — if you got this, your channel works."
    if "--telegram" in sys.argv:
        ok = send_telegram(msg)
    elif "--sms" in sys.argv:
        ok = send_sms(msg)
    else:
        ok = send_text(msg)
    print(f"discord configured:  {_webhook_url() is not None}")
    print(f"telegram configured: {_telegram_creds() is not None}")
    print(f"twilio configured:   {_twilio_creds() is not None}")
    print("sent:", ok)
    if not _telegram_creds():
        print("  → set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID for Telegram")
