"""
Discord webhook notifier.

Reads webhook URL from env DISCORD_WEBHOOK_URL. Sends a single embed per
fresh signal. Stays silent (returns None) if no webhook configured — never
raises on the caller.

Public API:
    send_signal(symbol, side, snap, macro=None, engine=None) -> bool
        side: +1 long, -1 short, 0 → no-op
    send_text(msg) -> bool
        for ad-hoc messages (heartbeat, errors)
"""
from __future__ import annotations

import json
import os
import urllib.error
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


def send_text(msg: str) -> bool:
    return _post({"content": msg[:1900]})


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

    fields = [
        {"name": "Symbol",    "value": f"`{symbol}`",                "inline": True},
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
            "title": f"🔔 {strategy.upper()} {side_word} — {symbol}",
            "description": "\n".join(desc_lines),
            "color": color,
            "fields": fields,
            "footer": {"text": "Quant IA · educational only · not investment advice"},
        }]
    }
    return _post(payload)


if __name__ == "__main__":
    # Smoke test: python -m core.notifier
    ok = send_text("✅ notifier smoke test — if you see this, the webhook works.")
    print("sent:", ok, "(set DISCORD_WEBHOOK_URL env var if False)")
