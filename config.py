import os
from dotenv import load_dotenv

load_dotenv()

# --- TravelNet Pi ---
TRAVELNET_TAILSCALE_HOST = os.getenv("TRAVELNET_TAILSCALE_HOST", "pi-server.tail186ff8.ts.net")
TRAVELNET_API_URL = os.getenv("TRAVELNET_API_URL", f"https://{TRAVELNET_TAILSCALE_HOST}")
TRAVELNET_API_TOKEN = os.getenv("TRAVELNET_API_TOKEN", "")
TRAVELNET_SSH_USER = os.getenv("TRAVELNET_SSH_USER", "dan")

# --- Shelly Plug ---
SHELLY_IP = os.getenv("SHELLY_IP", "192.168.0.XX")  # set after Shelly is configured
SHELLY_POWER_OFF_DELAY = 5   # seconds between off and on during power cycle

# --- PC (Wake-on-LAN) ---
PC_MAC = os.getenv("PC_MAC", "XX:XX:XX:XX:XX:XX")
PC_TAILSCALE_HOST = os.getenv("PC_TAILSCALE_HOST", "dan-pc.tail186ff8.ts.net")
PC_SSH_USER = os.getenv("PC_SSH_USER", "dan")

# --- Internet check ---
INTERNET_CHECK_HOST = "8.8.8.8"

# --- Pushcut ---
PUSHCUT_WEBHOOK_URL = os.getenv("PUSHCUT_WEBHOOK_URL", "")

# --- Monitoring intervals ---
CHECK_INTERVAL_SECONDS = 60       # how often to run checks
FAILURE_THRESHOLD = 3             # consecutive failures before acting
RECOVERY_COOLDOWN_SECONDS = 300   # min time between recovery attempts
