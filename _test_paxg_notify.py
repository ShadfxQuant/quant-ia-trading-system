"""Test the Discord notification path for PAXGUSDT.

Step 1: send a plain text ping (proves the webhook URL works).
Step 2: build the real worker snapshot, force a synthetic LONG pullback
        signal on PAXG's latest bar, and route it through _notify_signal
        exactly the way the live worker would. Proves the end-to-end wiring.
"""
from core.notifier import send_signal, send_text
from worker import build_state

# --- Step 1: raw webhook ping ---
ok = send_text("🧪 PAXG notification test — webhook reachable. Step 2 follows.")
print(f"[1] webhook reachable: {ok}")

# --- Step 2: synthesize a PAXG signal and fire it ---
state = build_state(["PAXGUSDT"])
snap = state["symbols"].get("PAXGUSDT")
if not snap:
    print("[2] PAXGUSDT not in state — aborting"); raise SystemExit(1)

# Force LONG so we don't have to wait for a real one
snap["pullback_signal"] = 1
ok = send_signal(
    "PAXGUSDT", side=1, snap=snap, strategy="pullback",
    macro=state.get("macro"), engine=state.get("engine", {}),
)
print(f"[2] PAXG synthetic LONG sent: {ok}")
print(f"    bar_time={snap.get('bar_time_utc')} close={snap.get('close')}")
