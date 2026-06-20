"""
One-command Telegram setup.

Prereqs (2 min, on your phone):
  1. Open Telegram, message @BotFather, send /newbot, follow prompts.
     It gives you a BOT TOKEN like 123456789:ABC-DEF...
  2. Open a chat with YOUR new bot and send it any message (e.g. "hi").
     (The bot can't message you until you've messaged it first.)

Then run:
    python3 setup_telegram.py <BOT_TOKEN>

This auto-discovers your chat id from the message you sent, writes both values
to .env (gitignored), and sends you a confirmation message so you know it works.
"""
from __future__ import annotations
import json
import sys
import urllib.error
import urllib.parse
import urllib.request


def _api(token: str, method: str, params: dict | None = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def main():
    if len(sys.argv) < 2:
        print("usage: python3 setup_telegram.py <BOT_TOKEN>")
        print("  (get the token from @BotFather, then message your bot once)")
        sys.exit(1)
    token = sys.argv[1].strip()

    # 1. validate token
    try:
        me = _api(token, "getMe")
    except (urllib.error.HTTPError, urllib.error.URLError, json.JSONDecodeError) as e:
        print(f"✗ couldn't reach Telegram with that token: {e}")
        sys.exit(1)
    if not me.get("ok"):
        print(f"✗ invalid token: {me.get('description')}")
        sys.exit(1)
    botname = me["result"].get("username", "?")
    print(f"✓ token valid — bot @{botname}")

    # 2. find chat id from recent messages
    updates = _api(token, "getUpdates")
    chat_id = None
    for u in reversed(updates.get("result", [])):
        msg = u.get("message") or u.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            chat_id = chat["id"]
            who = chat.get("first_name") or chat.get("username") or "you"
            break
    if not chat_id:
        print("✗ no messages found. Open Telegram, send your bot any message "
              f"(@{botname}), then re-run this command.")
        sys.exit(1)
    print(f"✓ found chat id {chat_id} ({who})")

    # 3. write .env (preserve any existing keys)
    import os
    root = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(root, ".env")
    existing = {}
    if os.path.isfile(env_path):
        for line in open(env_path):
            if "=" in line and not line.strip().startswith("#"):
                k, _, v = line.partition("=")
                existing[k.strip()] = v.strip()
    existing["TELEGRAM_BOT_TOKEN"] = token
    existing["TELEGRAM_CHAT_ID"] = str(chat_id)
    with open(env_path, "w") as f:
        for k, v in existing.items():
            f.write(f"{k}={v}\n")
    print(f"✓ wrote TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID to .env")

    # 4. send confirmation
    res = _api(token, "sendMessage", {
        "chat_id": chat_id,
        "text": "✅ Quant IA connected. You'll get trade signals here.",
    })
    if res.get("ok"):
        print("✓ sent you a confirmation message — check Telegram!")
        print("\n  Done. Live signals will now reach your phone via Telegram.")
    else:
        print(f"✗ test message failed: {res.get('description')}")


if __name__ == "__main__":
    main()
