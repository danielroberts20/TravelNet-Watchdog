"""
Watchdog main loop.

Monitors the TravelNet Pi and takes escalating recovery actions on failure.

Escalation ladder:
  1. 3 consecutive failures               → alert
  2. 5 failures + internet up             → Docker up via SSH
  3. 7 failures + internet up             → Docker rebuild via SSH
  4. 10 failures + internet up            → Pi reboot via SSH
  5. 15 failures + internet up            → Shelly power cycle
  6. internet down                        → alert only, no recovery actions
  7. maintenance mode active              → suppress all recovery actions
"""

import time
import logging
from datetime import datetime, UTC
import requests

from checks import (
    check_internet, 
    check_tailscale_ping, 
    check_api, 
    check_shelly, 
    check_cloudflare,
    check_prefect_server,
    check_prefect_serve,
    check_prefect_recent_flow,
    )
from actions import ssh_restart_docker, ssh_rebuild_docker, ssh_reboot_travelnet, shelly_power_cycle
from notify import notify
from config import (
    CHECK_INTERVAL_SECONDS, 
    RECOVERY_COOLDOWN_SECONDS, 
    TRAVELNET_HEARTBEAT_URL, 
    WATCHDOG_TOKEN, 
    CERT_PATH, 
    FAILURE_THRESHOLD_LADDER, 
    PREFECT_ALERT_THRESHOLD, 
    MIRROR_INTERVAL_CYCLES
)
from server import start_server, is_maintenance_mode, clear_maintenance_mode, _push_to_pico
from logging.handlers import RotatingFileHandler
from log_mirror import mirror_travelnet_logs

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "/home/dan/watchdog/watchdog.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=5,
        ),
    ],
)
log = logging.getLogger(__name__)


def push_heartbeat(internet_ok, tailscale_ok, api_ok, prefect_ok, consecutive_failures):
    try:
        requests.post(
            TRAVELNET_HEARTBEAT_URL,
            json={
                "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "internet_ok": internet_ok,
                "tailscale_ok": tailscale_ok,
                "api_ok": api_ok,
                "prefect_ok": prefect_ok,
                "consecutive_failures": consecutive_failures,
            },
            headers={"Authorization": f"Bearer {WATCHDOG_TOKEN}"},
            timeout=10,
            verify=CERT_PATH,
        )
    except Exception as e:
        log.warning(f"Failed to push heartbeat: {e}")


def run():
    start_server()
    log.info("Watchdog starting.")
    notify("🐕 Watchdog", "Watchdog started.")

    consecutive_failures = 0
    last_recovery_at = 0.0
    shelly_failures = 0
    cloudflare_failures = 0
    prefect_failures = 0
    cycle_count = 0

    while True:
        now = time.time()

        internet_ok, internet_detail = check_internet()
        tailscale_ok, tailscale_detail = check_tailscale_ping()
        api_ok, api_detail = check_api()
        shelly_ok, shelly_detail = check_shelly()
        cloudflare_ok, cloudflare_detail = check_cloudflare()

        # Prefect checks — only if Tailscale is up
        if tailscale_ok:
            prefect_server_ok, prefect_server_detail = check_prefect_server()
            prefect_serve_ok, prefect_serve_detail = check_prefect_serve()
            prefect_flow_ok, prefect_flow_detail = check_prefect_recent_flow()
        else:
            prefect_server_ok = prefect_serve_ok = prefect_flow_ok = False
            prefect_server_detail = prefect_serve_detail = prefect_flow_detail = "tailscale down"

        prefect_healthy = prefect_server_ok and prefect_serve_ok and prefect_flow_ok

        # log.info(
        #     f"prefect_server={prefect_server_ok}({prefect_server_detail}) "
        #     f"prefect_serve={prefect_serve_ok}({prefect_serve_detail}) "
        #     f"prefect_flow={prefect_flow_ok}({prefect_flow_detail})"
        # )

        if prefect_healthy:
            if prefect_failures >= PREFECT_ALERT_THRESHOLD:
                notify("✅ Prefect", "Prefect scheduler is healthy again.")
            prefect_failures = 0
        elif tailscale_ok:
            prefect_failures += 1
            log.warning(
                f"Prefect unhealthy — failures: {prefect_failures} | "
                f"server={prefect_server_ok} serve={prefect_serve_ok} "
                f"flow={prefect_flow_ok}"
            )
            if prefect_failures == PREFECT_ALERT_THRESHOLD:
                reasons = []
                if not prefect_server_ok:
                    reasons.append(f"server: {prefect_server_detail}")
                if not prefect_serve_ok:
                    reasons.append(f"serve process: {prefect_serve_detail}")
                if not prefect_flow_ok:
                    reasons.append(f"recent flows: {prefect_flow_detail}")
                notify(
                    "⚠️ Prefect",
                    "Prefect scheduler is unhealthy.\n" + "\n".join(reasons)
                )

        travelnet_healthy = tailscale_ok and api_ok and cloudflare_ok

        log.info(
            f"internet={internet_ok}({internet_detail}) "
            f"tailscale={tailscale_ok}({tailscale_detail}) "
            f"api={api_ok}({api_detail}) "
            f"shelly={shelly_ok}({shelly_detail}) "
            f"cloudflare={cloudflare_ok}({cloudflare_detail}) "
            f"prefect={prefect_healthy}({prefect_flow_detail}) "
        )

        if not shelly_ok:
            shelly_failures += 1
            if shelly_failures == FAILURE_THRESHOLD_LADDER[0]:
                notify("⚠️ Watchdog", f"Shelly plug is not responding ({shelly_detail})")
        else:
            if shelly_failures > 0:
                notify("✅ Watchdog", "Shelly plug is back online.")
            shelly_failures = 0
        
        if not cloudflare_ok:
            if is_maintenance_mode():
                continue
            cloudflare_failures += 1
            if cloudflare_failures == FAILURE_THRESHOLD_LADDER[0]:
                notify("⚠️ Watchdog", f"Cloudflare Tunnel unresponsive ({shelly_detail}).\nYou should manually fallback to Tailnet address.")
        else:
            if cloudflare_failures > 0:
                notify("✅ Watchdog", "Cloudflare Tunnel is back online.")
            cloudflare_failures = 0

        if travelnet_healthy:
            if consecutive_failures > 0:
                log.info("TravelNet recovered.")
                _push_to_pico("recovered", "System recovered")
                notify("✅ TravelNet", "TravelNet Pi is back online.")
            consecutive_failures = 0
            if is_maintenance_mode():
                clear_maintenance_mode()
                log.info("Maintenance mode cleared — TravelNet is back.")
        else:
            if is_maintenance_mode():
                log.info("Maintenance mode active — suppressing recovery actions.")
                time.sleep(CHECK_INTERVAL_SECONDS)
                continue
                
            consecutive_failures += 1
            log.warning(f"TravelNet unhealthy — consecutive failures: {consecutive_failures}")

            cooldown_elapsed = (now - last_recovery_at) > RECOVERY_COOLDOWN_SECONDS

            if consecutive_failures == FAILURE_THRESHOLD_LADDER[0]:
                log.warning("Threshold reached — alerting.")
                _push_to_pico("alert", "API unreachable — Watchdog is monitoring")
                notify("⚠️ Watchdog", f"TravelNet Pi is not responding. ({consecutive_failures} failures)")

            elif consecutive_failures == FAILURE_THRESHOLD_LADDER[1] and internet_ok and cooldown_elapsed:
                log.warning("Attempting Docker up via SSH.")
                _push_to_pico("docker_start", "Self-healing — restarting containers")
                notify("🔄 Watchdog", "Attempting Docker up.")
                ok, detail = ssh_restart_docker()
                log.info(f"Docker up: {ok} — {detail}")
                last_recovery_at = now

            elif consecutive_failures == FAILURE_THRESHOLD_LADDER[2] and internet_ok and cooldown_elapsed:
                log.warning("Attempting Docker rebuild via SSH.")
                _push_to_pico("docker_rebuild", "Containers failed — rebuilding (few mins)")
                notify("🔄 Watchdog", "Attempting Docker rebuild.")
                ok, detail = ssh_rebuild_docker()
                log.info(f"Docker rebuild: {ok} — {detail}")
                last_recovery_at = now

            elif consecutive_failures == FAILURE_THRESHOLD_LADDER[3] and internet_ok and cooldown_elapsed:
                log.warning("Attempting reboot via SSH.")
                _push_to_pico("reboot", "Rebuild failed — rebooting the Pi")
                notify("🔄 Watchdog", "Attempting Pi reboot via SSH.")
                ok, detail = ssh_reboot_travelnet()
                log.info(f"Reboot: {ok} — {detail}")
                last_recovery_at = now

            elif consecutive_failures == FAILURE_THRESHOLD_LADDER[4] and internet_ok and cooldown_elapsed:
                log.warning("Attempting Shelly power cycle.")
                _push_to_pico("power_cycle", "Rebooting failed — power cycling the Pi")
                notify("🔌 Watchdog", "Attempting power cycle via Shelly.")
                ok, detail = shelly_power_cycle()
                log.info(f"Power cycle: {ok} — {detail}")
                last_recovery_at = now

            elif not internet_ok:
                log.warning("Internet is down — skipping recovery actions.")

        
        cycle_count += 1
        if cycle_count % MIRROR_INTERVAL_CYCLES == 0:
            try:
                mirror_travelnet_logs()
            except Exception as e:
                log.warning(f"Log mirror error: {e}")

        push_heartbeat(internet_ok, tailscale_ok, api_ok, prefect_healthy, consecutive_failures)
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
