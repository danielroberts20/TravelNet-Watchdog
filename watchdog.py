"""
Watchdog main loop.

Monitors the TravelNet Pi and takes escalating recovery actions on failure.

Escalation ladder:
  1. 3 consecutive failures               → alert
  2. 5 failures + internet up             → Docker up via SSH
  3. 7 failures + internet up             → Docker rebuild via SSH
  4. 10 failures + internet up            → Pi reboot via SSH
  5. 15 failures + internet up            → Shelly power cycle
  6. 25 failures + internet up            → second Shelly power cycle
  7. 35 failures                          → final alert, go silent
  8. internet down                        → alert only, no recovery actions
  9. maintenance mode active              → suppress all recovery actions
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
    check_ssh,
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
from server import (
    start_server,
    is_maintenance_mode,
    clear_maintenance_mode,
    _push_to_pico,
    _push_maintenance_to_pico,
    is_maintenance_forced,
    update_watchdog_state,
)
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
notified_prefect = False
notified_watchdog = False
notified_shelly = False
notified_cloudflare = False
notified_ssh = False
notified_final = False
notified_maintenance = False

def push_heartbeat(internet_ok, tailscale_ok, api_ok, prefect_ok, ssh_ok, consecutive_failures):
    try:
        requests.post(
            TRAVELNET_HEARTBEAT_URL,
            json={
                "timestamp": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "internet_ok": internet_ok,
                "tailscale_ok": tailscale_ok,
                "api_ok": api_ok,
                "prefect_ok": prefect_ok,
                "ssh_ok": ssh_ok,
                "consecutive_failures": consecutive_failures,
            },
            headers={"Authorization": f"Bearer {WATCHDOG_TOKEN}"},
            timeout=10,
            verify=CERT_PATH,
        )
    except Exception as e:
        log.warning(f"Failed to push heartbeat: {e}")


def run():
    global notified_prefect, notified_watchdog, notified_shelly, notified_cloudflare, notified_ssh, notified_final, notified_maintenance
    start_server()
    log.info("Watchdog starting.")
    notify("🐕 Watchdog", "Watchdog started.")

    consecutive_failures = 0
    last_recovery_at = 0.0
    shelly_failures = 0
    cloudflare_failures = 0
    ssh_failures = 0
    prefect_failures = 0
    cycle_count = 0

    while True:
        now = time.time()

        # --- Run all checks ---
        internet_ok, internet_detail = check_internet()
        tailscale_ok, tailscale_detail = check_tailscale_ping()
        api_ok, api_detail = check_api()
        shelly_ok, shelly_detail = check_shelly()
        cloudflare_ok, cloudflare_detail = check_cloudflare()
        ssh_tailscale_ok, ssh_lan_ok, ssh_detail = check_ssh()
        ssh_ok = ssh_tailscale_ok or ssh_lan_ok

        # Prefect checks — only if Tailscale is up
        if tailscale_ok:
            prefect_server_ok, prefect_server_detail = check_prefect_server()
            prefect_serve_ok, prefect_serve_detail = check_prefect_serve()
            prefect_flow_ok, prefect_flow_detail = check_prefect_recent_flow()
        else:
            prefect_server_ok = prefect_serve_ok = prefect_flow_ok = False
            prefect_server_detail = prefect_serve_detail = prefect_flow_detail = "tailscale down"

        prefect_healthy = prefect_server_ok and prefect_serve_ok and prefect_flow_ok
        travelnet_healthy = tailscale_ok and api_ok

        log.info(
            f"internet={internet_ok}({internet_detail}) "
            f"tailscale={tailscale_ok}({tailscale_detail}) "
            f"api={api_ok}({api_detail}) "
            f"shelly={shelly_ok}({shelly_detail}) "
            f"cloudflare={cloudflare_ok}({cloudflare_detail}) "
            f"ssh={ssh_ok}({ssh_detail}) "
            f"prefect={prefect_healthy}({prefect_flow_detail}) "
        )

        # --- Secondary monitor: Prefect ---
        if prefect_healthy:
            if prefect_failures >= PREFECT_ALERT_THRESHOLD:
                if notified_prefect:
                    notify("✅ Prefect", "Prefect scheduler is healthy again.")
                    notified_prefect = False
            prefect_failures = 0
        elif tailscale_ok:
            prefect_failures += 1
            log.warning(
                f"Prefect unhealthy — failures: {prefect_failures} | "
                f"server={prefect_server_ok} serve={prefect_serve_ok} "
                f"flow={prefect_flow_ok}"
            )
            if prefect_failures == PREFECT_ALERT_THRESHOLD:
                if travelnet_healthy:
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
                    notified_prefect = True

        # --- Secondary monitor: Shelly ---
        if not shelly_ok:
            shelly_failures += 1
            if shelly_failures == FAILURE_THRESHOLD_LADDER[0]:
                notify("⚠️ Watchdog", f"Shelly plug is not responding ({shelly_detail})")
                notified_shelly = True
        else:
            if shelly_failures > 0 and notified_shelly:
                notify("✅ Watchdog", "Shelly plug is back online.")
                notified_shelly = False
            shelly_failures = 0

        # --- Secondary monitor: Cloudflare ---
        # Suppress counter and alert when maintenance mode is active.
        if not cloudflare_ok:
            if not is_maintenance_mode():
                cloudflare_failures += 1
                if cloudflare_failures == FAILURE_THRESHOLD_LADDER[0]:
                    if travelnet_healthy:
                        notify("⚠️ Watchdog", f"Cloudflare Tunnel unresponsive ({cloudflare_detail}).\nYou should manually fallback to Tailnet address.")
                        notified_cloudflare = True
        else:
            if cloudflare_failures > 0 and notified_cloudflare:
                notify("✅ Watchdog", "Cloudflare Tunnel is back online.")
                notified_cloudflare = False
            cloudflare_failures = 0

        # --- Secondary monitor: SSH ---
        if not ssh_ok:
            ssh_failures += 1
            if ssh_failures == FAILURE_THRESHOLD_LADDER[0]:
                _push_to_pico("ssh_alert", "SSH unreachable — Pi may be severely down")
                notified_ssh = True
        else:
            if ssh_failures > 0 and notified_ssh:
                _push_to_pico("ssh_recovered", "SSH reachable again")
                notified_ssh = False
            ssh_failures = 0

        # --- Primary: TravelNet health ---
        if travelnet_healthy:
            if is_maintenance_mode() and not notified_maintenance:
                log.info("Maintenance mode active — TravelNet is healthy.")
                _push_maintenance_to_pico(True)
                notified_maintenance = True
            elif not is_maintenance_mode() and notified_maintenance:
                _push_maintenance_to_pico(False)
                notified_maintenance = False

            if consecutive_failures > 0 and notified_watchdog:
                log.info("TravelNet recovered.")
                _push_to_pico("recovered", "System recovered")
                notify("✅ TravelNet", "TravelNet is back online.")
                notified_watchdog = False
                notified_final = False
            consecutive_failures = 0
            if is_maintenance_mode() and not is_maintenance_forced():
                clear_maintenance_mode()
                log.info("Maintenance mode cleared — TravelNet is back.")
                _push_maintenance_to_pico(False)
                notified_maintenance = False
        else:
            if not is_maintenance_mode():
                _push_maintenance_to_pico(False)
                notified_maintenance = False

            if is_maintenance_mode():
                log.info(
                    f"Maintenance mode active — suppressing recovery. "
                    f"consecutive_failures={consecutive_failures} "
                    f"internet={internet_ok} tailscale={tailscale_ok} api={api_ok}"
                )
                if not notified_maintenance:
                    _push_maintenance_to_pico(True)
                    notified_maintenance = True
            else:
                consecutive_failures += 1
                log.warning(f"TravelNet unhealthy — consecutive failures: {consecutive_failures}")

                cooldown_elapsed = (now - last_recovery_at) > RECOVERY_COOLDOWN_SECONDS

                if consecutive_failures == FAILURE_THRESHOLD_LADDER[0]:
                    log.warning("Threshold reached — alerting.")
                    _push_to_pico("alert", "API unreachable — Watchdog is monitoring")
                    notify("⚠️ Watchdog", f"TravelNet is not responding. ({consecutive_failures} failures)")
                    notified_watchdog = True

                elif consecutive_failures == FAILURE_THRESHOLD_LADDER[1] and internet_ok and cooldown_elapsed:
                    log.warning("Attempting Docker up via SSH.")
                    _push_to_pico("docker_start", "Self-healing — restarting containers")
                    notify("🔄 Watchdog", "Attempting Docker restart.")
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
                    notify("🔄 Watchdog", "Attempting reboot via SSH.")
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

                elif consecutive_failures == FAILURE_THRESHOLD_LADDER[5] and internet_ok and cooldown_elapsed:
                    log.warning("Attempting second Shelly power cycle.")
                    _push_to_pico("power_cycle_2", "Second power cycle — still unresponsive")
                    notify("🔌 Watchdog", "Power cycle attempt 2 — TravelNet still unresponsive.")
                    ok, detail = shelly_power_cycle()
                    log.info(f"Power cycle 2: {ok} — {detail}")
                    last_recovery_at = now

                elif consecutive_failures == FAILURE_THRESHOLD_LADDER[6] and not notified_final:
                    log.warning("TravelNet unresponsive after two power cycles — sending final alert.")
                    notify("🆘 Watchdog", "TravelNet is unresponsive after two power cycles. Manual intervention required. Watchdog will continue monitoring silently.")
                    notified_final = True

                elif not internet_ok:
                    log.warning("Internet is down — skipping recovery actions.")

        # --- Log mirror ---
        cycle_count += 1
        if cycle_count % MIRROR_INTERVAL_CYCLES == 0:
            try:
                mirror_travelnet_logs()
            except Exception as e:
                log.warning(f"Log mirror error: {e}")

        # --- Heartbeat and sleep (unconditional) ---
        push_heartbeat(internet_ok, tailscale_ok, api_ok, prefect_healthy, ssh_ok, consecutive_failures)
        update_watchdog_state(
            consecutive_failures=consecutive_failures,
            shelly_failures=shelly_failures,
            last_recovery_at=last_recovery_at,
            internet_ok=internet_ok,
            tailscale_ok=tailscale_ok,
            api_ok=api_ok,
            shelly_ok=shelly_ok,
            last_check_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            internet_detail=internet_detail,
            tailscale_detail=tailscale_detail,
            api_detail=api_detail,
            shelly_detail=shelly_detail,
        )
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run()
