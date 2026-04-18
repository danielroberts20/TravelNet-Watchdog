"""
Health check functions. Each returns (bool, str) — (is_healthy, detail).
"""

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


import subprocess
import requests
from config import (
    INTERNET_CHECK_HOST,
    TRAVELNET_API_URL,
    TRAVELNET_API_TOKEN,
    TRAVELNET_TAILSCALE_HOST,
    CERT_PATH,
)


def check_internet() -> tuple[bool, str]:
    """Ping 8.8.8.8 to confirm internet connectivity."""
    result = subprocess.run(
        ["ping", "-c", "2", "-W", "3", INTERNET_CHECK_HOST],
        capture_output=True,
    )
    ok = result.returncode == 0
    return ok, "ok" if ok else "no response from 8.8.8.8"


def check_tailscale_ping() -> tuple[bool, str]:
    """Ping TravelNet Pi via Tailscale to confirm it is reachable."""
    result = subprocess.run(
        ["ping", "-c", "2", "-W", "3", TRAVELNET_TAILSCALE_HOST],
        capture_output=True,
    )
    ok = result.returncode == 0
    return ok, "ok" if ok else f"no ping response from {TRAVELNET_TAILSCALE_HOST}"


def check_api() -> tuple[bool, str]:
    """Hit the TravelNet API health endpoint."""
    try:
        resp = requests.get(
            f"{TRAVELNET_API_URL}/metadata/status",
            headers={"Authorization": f"Bearer {TRAVELNET_API_TOKEN}"},
            timeout=10,
            verify=CERT_PATH,
        )
        ok = resp.status_code == 200
        return ok, f"status {resp.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "connection refused"
    except requests.exceptions.Timeout:
        return False, "timed out"
    except Exception as e:
        return False, str(e)
