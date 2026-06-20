"""
Telegram button listener — catches taps on signal buttons and records your
decision (took / skipped) to a journal.

Run it as a long-lived process (e.g. on your Mac while trading):
    python3 telegram_listener.py

What it does on each tap:
  • answers the callback (button stops spinning)
  • edits the message to show your decision
  • appends the decision to data/trade_decisions.jsonl (your real-vs-signal log)

IMPORTANT — what "✅ Took it" does and does NOT do:
  It RECORDS that you took the trade. It does NOT auto-execute on Infinex.
  Auto-execution needs an Infinex order bridge (wallet-signing) which is not
  built and is security-sensitive — see SYSTEM notes. This listener is the
  approval/journal layer; a future bridge would plug in where TODO marks below.
"""
from __future__ import annotations
import json, os, time
import urllib.error, urllib.parse, urllib.request

from core.env import load_env
load_env()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
JOURNAL = os.path.join("data", "trade_decisions.jsonl")


def api(method: str, params: dict, timeout=35) -> dict:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=timeout) as r:
            return json.loads(r.read().decode())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError) as e:
        print(f"[listener] {method} failed: {e}")
        return {"ok": False}


def record(decision: str, payload: str):
    os.makedirs("data", exist_ok=True)
    row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "decision": decision, "signal": payload}
    with open(JOURNAL, "a") as f:
        f.write(json.dumps(row) + "\n")


def handle_callback(cb: dict):
    data = cb.get("data", "")            # e.g. "took:US500:LONG"
    cb_id = cb["id"]
    msg = cb.get("message", {})
    chat_id = msg.get("chat", {}).get("id")
    msg_id = msg.get("message_id")
    parts = data.split(":")
    action = parts[0] if parts else ""
    payload = ":".join(parts[1:]) if len(parts) > 1 else ""

    if action == "took":
        toast = "✅ Logged: you took this trade"
        tag = "\n\n✅ *TAKEN* — logged to your journal"
        record("took", payload)
        # TODO(infinex-bridge): place the order here once an Infinex execution
        # bridge exists. Until then this only records the decision.
    elif action == "skip":
        toast = "❌ Logged: skipped"
        tag = "\n\n❌ *SKIPPED*"
        record("skip", payload)
    else:
        toast = "unknown action"
        tag = ""

    api("answerCallbackQuery", {"callback_query_id": cb_id, "text": toast})
    if chat_id and msg_id and tag:
        new_text = (msg.get("text", "") + tag)
        api("editMessageText", {"chat_id": chat_id, "message_id": msg_id,
                                "text": new_text, "parse_mode": "Markdown"})
    print(f"[listener] {action} → {payload}")


def main():
    if not (TOKEN and CHAT):
        print("✗ TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set (run setup_telegram.py)")
        return
    print("[listener] running — tap a signal button in Telegram. Ctrl-C to stop.")
    offset = None
    while True:
        params = {"timeout": 30}
        if offset is not None:
            params["offset"] = offset
        res = api("getUpdates", params, timeout=40)
        for u in res.get("result", []):
            offset = u["update_id"] + 1
            if "callback_query" in u:
                try:
                    handle_callback(u["callback_query"])
                except Exception as e:
                    print(f"[listener] handler error: {e}")
        time.sleep(1)


if __name__ == "__main__":
    main()
