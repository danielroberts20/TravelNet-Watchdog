import os
from dotenv import load_dotenv

load_dotenv()

# --- TravelNet Pi ---
TRAVELNET_TAILSCALE_HOST = os.getenv("TRAVELNET_TAILSCALE_HOST", "travelnet.tail186ff8.ts.net")
TRAVELNET_HEARTBEAT_URL = os.getenv(
    "TRAVELNET_HEARTBEAT_URL",
    f"https://{TRAVELNET_TAILSCALE_HOST}/upload/watchdog/heartbeat"
)
TRAVELNET_API_URL = os.getenv("TRAVELNET_API_URL", f"https://{TRAVELNET_TAILSCALE_HOST}")
TRAVELNET_API_URL_TAILSCALE = os.getenv("TRAVELNET_API_URL_TAILSCALE", f"http://{TRAVELNET_TAILSCALE_HOST}:8000")
PREFECT_API_URL = os.getenv("PREFECT_API_URL", f"http://{TRAVELNET_TAILSCALE_HOST}:4200/api")
PREFECT_WORKER_CONTAINER = os.getenv("PREFECT_WORKER_CONTAINER", "prefect-server")
TRAVELNET_API_TOKEN = os.getenv("TRAVELNET_API_TOKEN", "")
TRAVELNET_SSH_USER = os.getenv("TRAVELNET_SSH_USER", "dan")
WATCHDOG_TOKEN = os.getenv("WATCHDOG_TOKEN", "")  # for health checks and notifications

# --- Shelly Plug ---
SHELLY_IP = os.getenv("SHELLY_IP", "192.168.0.XX")  # set after Shelly is configured
SHELLY_POWER_OFF_DELAY = 15   # seconds between off and on during power cycle

CERT_PATH = os.getenv("CERT_PATH", "")

# --- PC (Wake-on-LAN) ---
PC_MAC = os.getenv("PC_MAC", "XX:XX:XX:XX:XX:XX")
PC_TAILSCALE_HOST = os.getenv("PC_TAILSCALE_HOST", "dan-pc.tail186ff8.ts.net")
PC_SSH_USER = os.getenv("PC_SSH_USER", "dan")

# --- Internet check ---
INTERNET_CHECK_HOST = "8.8.8.8"

# --- Pushcut ---
PUSHCUT_WEBHOOK_URL = os.getenv("PUSHCUT_WEBHOOK_URL", "")

# --- Monitoring intervals ---
CHECK_INTERVAL_SECONDS = 58       # how often to run checks
FAILURE_THRESHOLD_LADDER = [3, 5, 7, 10, 15] # Escalation failures to trigger each new action
RECOVERY_COOLDOWN_SECONDS = 300   # min time between recovery attempts
PREFECT_ALERT_THRESHOLD = 3   # consecutive failures before alert
