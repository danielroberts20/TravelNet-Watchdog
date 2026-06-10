# ── WiFi ──────────────────────────────────────────────────────────────────────
WIFI_SSID     = "SKY3JTEY"
WIFI_PASSWORD = "yqNtfnKHxqkm"

# ── API ───────────────────────────────────────────────────────────────────────
API_URL         = "http://192.168.0.61:8000/metadata/status"
POLL_INTERVAL_S = 60
IDLE_TIMEOUT_S  = 30   # seconds before returning to status page

# ── Burn-in prevention ────────────────────────────────────────────────────────
# Times are UK local time (BST/GMT auto-detected on device).
NIGHT_DIM_START  = 23   # 23:00 UK local
NIGHT_DIM_END    = 7    # 07:00 UK local
BLANK_TIMEOUT_S  = 30 * 60   # blank screen after 30 min of no interaction

# ── UDP push receiver ─────────────────────────────────────────────────────────
PICO_UDP_PORT = 9002

# ── Staleness thresholds ──────────────────────────────────────────────────────
LOCATION_STALE_S = 24 * 3600
HEALTH_STALE_S   = 36 * 3600
WATCHDOG_STALE_S =  5 * 60

# ── Runbook step numbers ──────────────────────────────────────────────────────
STEP_API_DOWN      = 1
STEP_WATCHDOG_DOWN = 3
STEP_ALL_DOWN      = 4

# ── API field names (verified 2026-05-28) ─────────────────────────────────────
KEY_UPTIME        = "uptime"
KEY_UPTIME_PI     = "pi"
KEY_UPTIME_APP    = "app"
KEY_DB            = "db"
KEY_DB_SIZE_MB    = "size_mb"
KEY_DB_FREE_MB    = "free_space_mb"
KEY_DB_LATENCY_MS = "query_latency_ms"
KEY_LAST_UPLOAD       = "last_upload"
KEY_LAST_OVERLAND     = "location_overland"
KEY_LAST_SHORTCUTS    = "location_shortcuts"
KEY_LAST_HEALTH       = "health"
KEY_LAST_TRANSACTIONS = "transactions"
KEY_LAST_WORKOUTS     = "workouts"
KEY_LAST_WATCHDOG     = "last_watchdog_heartbeat"
KEY_ROW_COUNTS        = "row_counts"
KEY_ROW_OVERLAND      = "location_overland"
KEY_ROW_HEALTH        = "health_quantity"
KEY_ROW_TRANSACTION   = "transactions"
KEY_FX_USAGE          = "fx_api_usage"
KEY_FX_USAGE_COUNT    = "count"
KEY_FX_USAGE_MONTH    = "month"
KEY_PENDING_DIGEST    = "pending_digest_records"