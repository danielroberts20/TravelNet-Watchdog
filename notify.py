"""
Pushcut notification helpers.
"""

import requests
from config import PUSHCUT_WEBHOOK_URL


def notify(title: str, body: str) -> None:
    """Fire a Pushcut notification. Fails silently — never block the watchdog loop."""
    if not PUSHCUT_WEBHOOK_URL:
        print(f"[notify] no webhook configured — would have sent: {title}: {body}")
        return
    try:
        requests.post(
            PUSHCUT_WEBHOOK_URL,
            json={"title": title, "text": body},
            timeout=10,
        )
    except Exception as e:
        print(f"[notify] failed to send notification: {e}")
