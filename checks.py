"""
Health check functions. Each returns (bool, str) — (is_healthy, detail).
"""

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


import os
import subprocess
import time
from datetime import datetime, UTC
import requests
from config import (
    INTERNET_CHECK_HOST,
    TRAVELNET_API_URL,
    TRAVELNET_API_TOKEN,
    TRAVELNET_TAILSCALE_HOST,
    TRAVELNET_LAN_HOST,
    TRAVELNET_API_URL_TAILSCALE,
    PREFECT_API_URL,
    TRAVELNET_SSH_USER,
    PREFECT_WORKER_CONTAINER,
    CERT_PATH,
    SHELLY_IP,
    CERT_CHECK_PATHS,
    CERT_WARN_DAYS,
    CERT_CRITICAL_DAYS,
)


def check_internet() -> tuple[bool, str]:
    """Ping 8.8.8.8 to confirm internet connectivity."""
    result = subprocess.run(
        ["ping", "-c", "2", "-W", "3", INTERNET_CHECK_HOST],
        capture_output=True,
    )
    ok = result.returncode == 0
    return ok, "ok" if ok else "no response from 8.8.8.8"


def check_shelly() -> tuple[bool, str]:
    """Check if the Shelly plug is reachable via its local API."""
    try:
        resp = requests.post(
            f"http://{SHELLY_IP}/rpc/Switch.GetStatus",
            json={"id": 0},
            timeout=5,
        )
        ok = resp.status_code == 200
        return ok, "ok" if ok else f"status {resp.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "unreachable"
    except requests.exceptions.Timeout:
        return False, "timed out"
    except Exception as e:
        return False, str(e)

def check_tailscale_ping() -> tuple[bool, str]:
    """Ping TravelNet Pi via Tailscale to confirm it is reachable."""
    ok, detail = _do_tailscale_ping()
    if not ok:
        # Before reporting failure, attempt to self-heal the local tailscaled daemon
        try:
            subprocess.run(["sudo", "systemctl", "restart", "tailscaled"], timeout=30, capture_output=True)
        except subprocess.TimeoutExpired:
            pass
        time.sleep(15)
        ok, detail = _do_tailscale_ping()  # authoritative result
    return ok, detail

def _do_tailscale_ping() -> tuple[bool, str]:
    result = subprocess.run(
        ["ping", "-c", "2", "-W", "3", TRAVELNET_TAILSCALE_HOST],
        capture_output=True,
    )
    ok = result.returncode == 0
    return ok, "ok" if ok else f"no ping response from {TRAVELNET_TAILSCALE_HOST}"

def check_api() -> tuple[bool, str]:
    """Hit the TravelNet API health endpoint via the Tailnet name and port \
    (travelnet:8000)"""
    try:
        resp = requests.get(
            f"{TRAVELNET_API_URL_TAILSCALE}/metadata/watchdog",
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

def check_cloudflare() -> tuple[bool, str]:
    """Hit the TravelNet API health endpoint via the Cloudflare tunnel (api.travelnet.dev)"""
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


def check_ssh() -> tuple[bool, bool, str]:
    """
    Check SSH reachability of the TravelNet Pi via both Tailscale and LAN.
    Both paths are attempted every cycle so lan_ok always reflects a real
    result rather than "not attempted". Tailscale remains the preferred path
    for detail-message ordering and for anything downstream that reads
    tailscale_ok first (e.g. ssh_ok = tailscale_ok or lan_ok).
    Returns (tailscale_ok, lan_ok, detail).
    """
    ssh_cmd = [
        "ssh", "-i", "/home/dan/.ssh/watchdog_id",
        "-o", "ConnectTimeout=5",
        "-o", "StrictHostKeyChecking=no",
        "-o", "BatchMode=yes",
    ]

    def _try(host: str) -> bool:
        try:
            result = subprocess.run(
                ssh_cmd + [f"{TRAVELNET_SSH_USER}@{host}", "echo ok"],
                capture_output=True,
                timeout=15,
            )
            return result.returncode == 0 and result.stdout.decode().strip() == "ok"
        except Exception:
            return False

    tailscale_ok = _try(TRAVELNET_TAILSCALE_HOST)
    lan_ok = _try(TRAVELNET_LAN_HOST)

    if tailscale_ok and lan_ok:
        detail = "tailscale ok, lan ok"
    elif tailscale_ok:
        detail = "tailscale ok, lan failed"
    elif lan_ok:
        detail = "tailscale failed, lan ok"
    else:
        detail = "both failed"

    return tailscale_ok, lan_ok, detail


def check_prefect_server() -> tuple[bool, str]:
    """Check Prefect server is up via its health endpoint."""
    try:
        resp = requests.get(f"{PREFECT_API_URL}/health", timeout=10)
        ok = resp.status_code == 200
        return ok, "ok" if ok else f"status {resp.status_code}"
    except requests.exceptions.ConnectionError:
        return False, "connection refused"
    except requests.exceptions.Timeout:
        return False, "timed out"
    except Exception as e:
        return False, str(e)


def check_prefect_serve() -> tuple[bool, str]:
    """
    Check the deployments.py serve() process is running inside the container.
    With Prefect serve() there is no worker — deployments.py IS the scheduler.
    If this process is dead, no scheduled flows will run.
    """
    try:
        result = subprocess.run(
            [
                "ssh", "-i", "/home/dan/.ssh/watchdog_id",
                "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
                f"docker exec {PREFECT_WORKER_CONTAINER} "
                "cat /proc/1/cmdline | tr '\\0' ' ' | grep -q 'deployments' "
                "&& echo ok || echo dead",
            ],
            capture_output=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return False, "SSH command timed out"
    if result.returncode != 0:
        return False, f"SSH failed: {result.stderr.decode().strip()}"
    output = result.stdout.decode().strip()
    return output == "ok", output


def check_prefect_recent_flow() -> tuple[bool, str]:
    """
    Check at least one flow run completed or failed in the last 25 hours.
    The daily_summary flow runs at 08:00 every day — silence beyond 25 hours
    means the serve() process is not executing flows even if it is running.
    """
    from datetime import timedelta
    cutoff = (datetime.now(UTC) - timedelta(hours=25)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    try:
        resp = requests.post(
            f"{PREFECT_API_URL}/flow_runs/filter",
            json={
                "flow_runs": {
                    "state": {"type": {"any_": ["COMPLETED", "FAILED", "CRASHED"]}},
                    "start_time": {"after_": cutoff},
                },
                "limit": 1,
                "sort": "START_TIME_DESC",
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return False, f"flow_runs endpoint status {resp.status_code}"
        runs = resp.json()
        if not runs:
            return False, "no completed/failed flow runs in the last 25 hours"
        run = runs[0]
        return True, f"last run: '{run.get('name')}' state={run.get('state_name')}"
    except requests.exceptions.ConnectionError:
        return False, "connection refused"
    except requests.exceptions.Timeout:
        return False, "timed out"
    except Exception as e:
        return False, str(e)


def _parse_cert_expiry(path: str) -> tuple[int | None, str | None]:
    """Parse a PEM cert and return (days_remaining, expiry_date_iso) or (None, None) on error."""
    try:
        result = subprocess.run(
            ["openssl", "x509", "-in", path, "-noout", "-enddate"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return None, None
        # output: "notAfter=Jul 29 14:42:54 2026 GMT"
        date_str = result.stdout.strip().split("=", 1)[1].strip()
        expiry = datetime.strptime(date_str, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
        days = (expiry - datetime.now(UTC)).days
        return days, expiry.strftime("%Y-%m-%d")
    except Exception:
        return None, None


def _cert_status(days_remaining: int | None) -> str:
    if days_remaining is None:
        return "error"
    if days_remaining <= 0:
        return "expired"
    if days_remaining <= CERT_CRITICAL_DAYS:
        return "critical"
    if days_remaining <= CERT_WARN_DAYS:
        return "warn"
    return "ok"


def check_cert_expiry() -> list[dict]:
    """
    Read monitored TLS cert files from local disk copies synced by the TravelNet Pi's
    scp cron. Returns one dict per cert:
      name: str           — display name
      path: str           — file path checked
      days_remaining: int | None
      expiry_date: str | None  — "YYYY-MM-DD"
      status: "ok" | "warn" | "critical" | "expired" | "missing" | "error"
    """
    results = []
    for name, path in CERT_CHECK_PATHS:
        if not os.path.exists(path):
            results.append({
                "name": name, "path": path,
                "days_remaining": None, "expiry_date": None,
                "status": "missing",
            })
            continue
        days, expiry_date = _parse_cert_expiry(path)
        results.append({
            "name": name, "path": path,
            "days_remaining": days, "expiry_date": expiry_date,
            "status": _cert_status(days),
        })
    return results