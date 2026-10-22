"""
CareLoop - Demo Script (scripts/demo.py)

Sends the 3 conversational demonstration messages to the CareLoop /api/chat endpoint:
  1. "I took my BP tablet"
  2. "I had my Metformin"
  3. "I feel dizzy"

Prints the replies received from the system.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Ensure project root is in sys.path
import os

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEMO_MESSAGES = [
    "I took my BP tablet",
    "I had my Metformin",
    "I feel dizzy",
]

PORT = int(os.environ.get("PORT", 8000))
API_URL = f"http://127.0.0.1:{PORT}/api/chat"
DASHBOARD_URL = f"http://127.0.0.1:{PORT}/api/dashboard"


def send_via_http(message: str) -> dict:
    """Send message via HTTP POST to CareLoop server."""
    payload = json.dumps({"message": message}).encode("utf-8")
    req = urllib.request.Request(
        API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> None:
    print("=" * 65)
    print(" CareLoop Conversational Demo (scripts/demo.py)")
    print("=" * 65)

    use_live_http = True
    # Check if server is running
    try:
        urllib.request.urlopen(DASHBOARD_URL, timeout=2)
        print(f"Connected to live CareLoop server at http://127.0.0.1:{PORT}\n")
    except Exception:
        use_live_http = False
        print(f"CareLoop server is not running at http://127.0.0.1:{PORT}.")
        print("Executing demo messages using local intent handler (in-process)...\n")

    for idx, msg in enumerate(DEMO_MESSAGES, start=1):
        print(f"[{idx}] User: \"{msg}\"")
        try:
            if use_live_http:
                res = send_via_http(msg)
            else:
                from intents import handle_message
                res = handle_message(msg)

            print(f"    Tool : {res.get('tool')}")
            print(f"    Reply: \"{res.get('reply')}\"\n")
        except Exception as exc:
            print(f"    Error: {exc}\n")

    print("=" * 65)
    print(" Demo complete.")
    print("=" * 65)


if __name__ == "__main__":
    main()
