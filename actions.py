"""
Recovery actions. All return (bool, str) — (succeeded, detail).
"""

import time
import subprocess
import requests
from wakeonlan import send_magic_packet
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
    """SSH into TravelNet Pi and restart Docker containers."""
    result = subprocess.run(
        [
            "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
            f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
            "cd ~/services/TravelNet/server && docker compose restart",
        ],
        capture_output=True,
        timeout=60,
    )
    ok = result.returncode == 0
    detail = result.stdout.decode().strip() or result.stderr.decode().strip()
    return ok, detail


def ssh_reboot_travelnet() -> tuple[bool, str]:
    """SSH into TravelNet Pi and reboot it."""
    result = subprocess.run(
        [
            "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
            f"{TRAVELNET_SSH_USER}@{TRAVELNET_TAILSCALE_HOST}",
            "sudo reboot",
        ],
        capture_output=True,
        timeout=30,
    )
    # reboot closes the connection immediately, so non-zero exit is expected
    return True, "reboot command sent"


def shelly_power_cycle() -> tuple[bool, str]:
    """Cut power to TravelNet Pi via Shelly plug, then restore."""
    try:
        off = requests.get(f"http://{SHELLY_IP}/relay/0?turn=off", timeout=5)
        if off.status_code != 200:
            return False, f"failed to turn off: {off.status_code}"
        time.sleep(SHELLY_POWER_OFF_DELAY)
        on = requests.get(f"http://{SHELLY_IP}/relay/0?turn=on", timeout=5)
        if on.status_code != 200:
            return False, f"failed to turn on: {on.status_code}"
        return True, "power cycled successfully"
    except Exception as e:
        return False, str(e)


def wake_pc() -> tuple[bool, str]:
    """Send WoL magic packet to the PC."""
    try:
        send_magic_packet(PC_MAC)
        return True, f"magic packet sent to {PC_MAC}"
    except Exception as e:
        return False, str(e)


def shutdown_pc() -> tuple[bool, str]:
    """SSH into PC and shut it down."""
    result = subprocess.run(
        [
            "ssh", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=no",
            f"{PC_SSH_USER}@{PC_TAILSCALE_HOST}",
            "shutdown /s /t 0",  # Windows shutdown command
        ],
        capture_output=True,
        timeout=30,
    )
    ok = result.returncode == 0
    return ok, result.stdout.decode().strip() or result.stderr.decode().strip()
