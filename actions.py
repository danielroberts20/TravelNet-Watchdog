"""
Recovery actions. All return (bool, str) — (succeeded, detail).
"""

import time
import subprocess
import requests
from wakeonlan import send_magic_packet
from datetime import datetime, timezone
from config import (
    TRAVELNET_SSH_USER,
    TRAVELNET_TAILSCALE_HOST,
    SHELLY_IP,
    SHELLY_POWER_OFF_DELAY,
    PC_MAC,
    PC_TAILSCALE_HOST,
    PC_SSH_USER,
)


def ssh_restart_docker() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "ssh", "-i", "/home/dan/.ssh/watchdog_id",
                "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
                "cd ~/services/TravelNet/server && docker compose up -d",
            ],
            capture_output=True,
            timeout=60,
        )
        ok = result.returncode == 0
        detail = result.stdout.decode().strip() or result.stderr.decode().strip()
        return ok, detail
    except subprocess.TimeoutExpired:
        return False, "SSH command timed out"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def ssh_rebuild_docker() -> tuple[bool, str]:
    """SSH into TravelNet Pi and rebuild Docker containers."""
    try:
        result = subprocess.run(
            [
                "ssh", "-i", "/home/dan/.ssh/watchdog_id", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
                f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
                "cd ~/services/TravelNet/server && ./build.sh",
            ],
            capture_output=True,
            timeout=180,
        )
        ok = result.returncode == 0
        detail = result.stdout.decode().strip() or result.stderr.decode().strip()
        return ok, detail
    except subprocess.TimeoutExpired:
        return False, "SSH command timed out"
    except Exception as e:
        return False, f"Unexpected error: {e}"

def ssh_reboot_travelnet() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            [
                "ssh", "-i", "/home/dan/.ssh/watchdog_id",
                "-o", "ConnectTimeout=10",
                "-o", "StrictHostKeyChecking=no",
                f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
                "bash /home/dan/services/TravelNet/server/scripts/graceful_reboot.sh watchdog",
            ],
            capture_output=True,
            timeout=60,
            text=True,
        )

        if result.returncode == 0:
            return True, "reboot command sent"

        # Non-zero exit → something failed
        return False, f"SSH failed (code {result.returncode}): {result.stderr.strip()}"

    except subprocess.TimeoutExpired:
        return False, "SSH command timed out"

    except Exception as e:
        return False, f"Unexpected error: {e}"


def shelly_power_cycle() -> tuple[bool, str]:
    """Cut power to TravelNet Pi via Shelly plug, then restore."""
    try:
        off = requests.post(f"http://{SHELLY_IP}/rpc/Switch.Set", json={"id": 0, "on": False}, timeout=5)
        if off.status_code != 200:
            return False, f"failed to turn off: {off.status_code}"
        time.sleep(SHELLY_POWER_OFF_DELAY)
        on = requests.post(f"http://{SHELLY_IP}/rpc/Switch.Set", json={"id": 0, "on": True}, timeout=5)
        if on.status_code != 200:
            return False, f"failed to turn on: {on.status_code}"
        return True, "power cycled successfully"
    except Exception as e:
        return False, str(e)


def wake_pc() -> tuple[bool, str]:
    """Send WoL magic packet to the PC and record timestamp."""
    try:
        send_magic_packet(PC_MAC)
        with open("/tmp/last_wol_sent", "w") as f:
            f.write(datetime.now(timezone.utc).isoformat())
        return True, f"magic packet sent to {PC_MAC}"
    except Exception as e:
        return False, str(e)


def shutdown_pc() -> tuple[bool, str]:
    result = subprocess.run(
        [
            "ssh", "-i", "/home/dan/.ssh/watchdog_id","-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
            "-p", "2222",
            f"{PC_SSH_USER}@{PC_TAILSCALE_HOST}",
            "docker ps -q | xargs -r docker stop; /mnt/c/Windows/System32/shutdown.exe /s /f /t 0",
        ],
        capture_output=True,
        timeout=30,
    )
    ok = result.returncode == 0
    return ok, result.stdout.decode().strip() or result.stderr.decode().strip()
