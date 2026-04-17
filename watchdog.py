"""
Watchdog main loop.

Monitors the TravelNet Pi and takes escalating recovery actions on failure.

Escalation ladder:
  1. 3 consecutive failures  → alert
  2. 5 consecutive failures + internet up  → SSH docker restart
  3. 10 consecutive failures + internet up  → SSH reboot
  4. 15 consecutive failures + internet up  → Shelly power cycle
  5. internet down  → alert only, no recovery actions
"""

import time
import logging
from datetime import datetime, UTC
import requests

from checks import check_internet, check_tailscale_ping, check_api
from actions import ssh_restart_docker, ssh_rebuild_docker, ssh_reboot_travelnet, shelly_power_cycle
from notify import notify
from config import CHECK_INTERVAL_SECONDS, RECOVERY_COOLDOWN_SECONDS, TRAVELNET_HEARTBEAT_URL, WATCHDOG_TOKEN
from server import start_server

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("/home/dan/watchdog/watchdog.log"),
    ],
)
log = logging.getLogger(__name__)


def push_heartbeat(internet_ok, tailscale_ok, api_ok, consecutive_failures):
    try:
        requests.post(
            TRAVELNET_HEARTBEAT_URL,
            json={
                "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "internet_ok": internet_ok,
                "tailscale_ok": tailscale_ok,
                "api_ok": api_ok,
                "consecutive_failures": consecutive_failures,
            },
            headers={"Authorization": f"Bearer {WATCHDOG_TOKEN}"},
            timeout=10,
            verify=False,
        )
    except Exception as e:
        log.warning(f"Failed to push heartbeat: {e}")


def run():
    start_server()
    log.info("Watchdog starting.")
    notify("🐕 Watchdog", "Watchdog started.")

    consecutive_failures = 0
    last_recovery_at = 0.0

    while True:
        now = time.time()

        internet_ok, internet_detail = check_internet()
        tailscale_ok, tailscale_detail = check_tailscale_ping()
        api_ok, api_detail = check_api()

        travelnet_healthy = tailscale_ok and api_ok

        log.info(
            f"internet={internet_ok}({internet_detail}) "
            f"tailscale={tailscale_ok}({tailscale_detail}) "
            f"api={api_ok}({api_detail})"
        )

        if travelnet_healthy:
            if consecutive_failures > 0:
                log.info("TravelNet recovered.")
                notify("✅ Watchdog", "TravelNet Pi is back online.")
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            log.warning(f"TravelNet unhealthy — consecutive failures: {consecutive_failures}")

            cooldown_elapsed = (now - last_recovery_at) > RECOVERY_COOLDOWN_SECONDS

            if consecutive_failures == 3:
                log.warning("Threshold reached — alerting.")
                notify("⚠️ Watchdog", f"TravelNet Pi is not responding. ({consecutive_failures} failures)")

            elif consecutive_failures == 5 and internet_ok and cooldown_elapsed:
                log.warning("Attempting Docker up via SSH.")
                notify("🔄 Watchdog", "Attempting Docker up.")
                ok, detail = ssh_restart_docker()
                log.info(f"Docker up: {ok} — {detail}")
                last_recovery_at = now

            elif consecutive_failures == 7 and internet_ok and cooldown_elapsed:
                log.warning("Attempting Docker rebuild via SSH.")
                notify("🔄 Watchdog", "Attempting Docker rebuild.")
                ok, detail = ssh_rebuild_docker()
                log.info(f"Docker rebuild: {ok} — {detail}")
                last_recovery_at = now

            elif consecutive_failures == 10 and internet_ok and cooldown_elapsed:
                log.warning("Attempting reboot via SSH.")
                notify("🔄 Watchdog", "Attempting Pi reboot via SSH.")
                ok, detail = ssh_reboot_travelnet()
                log.info(f"Reboot: {ok} — {detail}")
                last_recovery_at = now

            elif consecutive_failures == 15 and internet_ok and cooldown_elapsed:
                log.warning("Attempting Shelly power cycle.")
                notify("🔌 Watchdog", "Attempting power cycle via Shelly.")
                ok, detail = shelly_power_cycle()
                log.info(f"Power cycle: {ok} — {detail}")
                last_recovery_at = now

            elif not internet_ok:
                log.warning("Internet is down — skipping recovery actions.")

        push_heartbeat(internet_ok, tailscale_ok, api_ok, consecutive_failures)
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
