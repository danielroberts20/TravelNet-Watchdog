"""
TravelNet Status Display — with push notifications
Waveshare Pico LCD 1.3 (240×240, ST7789)

Prerequisites — copy to Pico via Thonny:
  1. main.py (this file)
  2. config.py
  3. LCD_1inch3.py  ← Pico_code.7z → python/Pico-LCD-1.3/

Pages:
  0 — Family status  (default; idle timeout returns here)
  1 — System health  (IP, RSSI, uptime, source ages)
  2 — Data & storage (row counts, DB size)
  3 — Alerts         (FX usage, log queue)
  4 — Messages       (push messages from Dan)

Buttons:
  A            — Force immediate API poll
  B            — Page 0: toggle plain-English detail in banner
  Y            — Jump to messages page from anywhere
  Joystick ←/→ — Navigate pages
  Joystick press — Toggle backlight

Push (UDP port 9002, non-blocking):
  {"type": "message",     "text": "..."}
  {"type": "watchdog",    "step": "docker_start|docker_rebuild|reboot|power_cycle|alert|recovered", "text": "..."}
  {"type": "maintenance", "active": true|false}
  {"type": "pc",          "state": "checking|remote|clear|off"}
"""

import network
import urequests
import utime
import sys
import usocket
import ujson
from machine import Pin, PWM
import LCD_1inch3

import config

# ── Colours (RGB565, BRG channel order) ──────────────────────────────────────
# Formula: ((B>>3)<<11) | ((R>>2)<<5) | (G>>3)
BLACK     = 0x0000
WHITE     = 0xFFFF
RED       = 0x07E0   # R=255, G=0,   B=0
GREEN     = 0x001F   # R=0,   G=255, B=0
AMBER     = 0x07F4   # R=255, G=160, B=0
BLUE      = 0xF800   # R=0,   G=0,   B=255  (self-healing)
DARK_GREY = 0x4208   # R=G=B=64
MID_GREY  = 0x8410   # R=G=B=128
YELLOW    = 0x07FF

# ── Display ───────────────────────────────────────────────────────────────────
LCD = LCD_1inch3.LCD_1inch3()
LCD.fill(BLACK)
LCD.show()

BL = PWM(Pin(13))
BL.freq(1000)
BL_BRIGHT = 32768
BL_DIM    = 4096
BL_NIGHT  = 1500    # barely visible — enough to read if you walk up to it
BL_OFF    = 0
BL.duty_u16(BL_BRIGHT)
_bl_on      = True    # user toggle (joystick press)
_bl_blanked = False   # auto screen-blank state

# ── Buttons and joystick (all active-LOW) ─────────────────────────────────────
BTN_A     = Pin(15, Pin.IN, Pin.PULL_UP)
BTN_B     = Pin(17, Pin.IN, Pin.PULL_UP)
BTN_Y     = Pin(21, Pin.IN, Pin.PULL_UP)   # jump to messages
JOY_LEFT  = Pin(16, Pin.IN, Pin.PULL_UP)
JOY_RIGHT = Pin(20, Pin.IN, Pin.PULL_UP)
JOY_CTR   = Pin( 3, Pin.IN, Pin.PULL_UP)

_btn_prev = {'A': 1, 'B': 1, 'Y': 1, 'LEFT': 1, 'RIGHT': 1, 'CTR': 1}

# ── UDP push socket ───────────────────────────────────────────────────────────
_udp_sock = usocket.socket(usocket.AF_INET, usocket.SOCK_DGRAM)
_udp_sock.bind(('0.0.0.0', config.PICO_UDP_PORT))
_udp_sock.setblocking(False)

# ── App state ─────────────────────────────────────────────────────────────────
state = {
    # Derived poll status
    'status':         'unknown',  # 'ok' | 'warn' | 'error' | 'unknown'
    'step':           None,
    'detail':         'Starting up...',
    # Per-source ages
    'gps_age_s':      None,
    'health_age_s':   None,
    'watchdog_age_s': None,
    # Uptime strings (pre-formatted by server)
    'uptime_pi':      None,
    'uptime_app':     None,
    # DB stats
    'db_size_mb':     None,
    'db_free_mb':     None,
    'db_lat_ms':      None,
    # Row counts
    'row_overland':   None,
    'row_health':     None,
    'row_tx':         None,
    # Other upload ages
    'tx_age_s':       None,
    'workout_age_s':  None,
    # Alerts
    'fx_count':       None,
    'fx_month':       None,
    'pending_digest': None,
    # Poll metadata
    'last_poll_time': 'Never',
    'last_poll_ok':   False,
    # Network diagnostics
    'wifi_rssi':      None,
    'wifi_ip':        None,
    'last_error':     None,
    # Push state
    'push_msg_text':      None,   # latest message from Dan
    'push_msg_time':      None,   # HH:MM when received
    'push_msg_unread':    False,
    'push_wd_text':       None,   # watchdog escalation text
    'push_wd_step':       None,   # watchdog step name
    'push_wd_healing':    False,  # True when actively healing (step 5+)
    'push_maintenance':   False,
    'push_pc_state':      None,   # None | 'checking' | 'remote'
    'push_pc_started':    0,      # utime.time() when checking state was set
}

_current_page       = 0
_prev_page          = 0   # page to return to when Y is pressed on messages
_last_input_time    = utime.time()   # for idle page-return timeout
_last_activity_time = utime.time()   # for screen blank timeout
_show_detail        = False
_last_flash         = -1   # for PC checking banner flash
TOTAL_PAGES         = 5

# Watchdog steps that mean "actively healing — don't intervene"
HEALING_STEPS = {'docker_start', 'docker_rebuild', 'reboot', 'power_cycle'}

# ── Utility ───────────────────────────────────────────────────────────────────

def _connect_wifi():
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    print(f"[wifi] connecting to '{config.WIFI_SSID}'...")
    if not wlan.isconnected():
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        deadline = utime.time() + 20
        while not wlan.isconnected() and utime.time() < deadline:
            utime.sleep_ms(300)
    if wlan.isconnected():
        ip, mask, gateway, dns = wlan.ifconfig()
        rssi = wlan.status('rssi')
        state['wifi_ip']   = ip
        state['wifi_rssi'] = rssi
        print(f"[wifi] connected")
        print(f"[wifi]   IP:      {ip}")
        print(f"[wifi]   mask:    {mask}")
        print(f"[wifi]   gateway: {gateway}")
        print(f"[wifi]   DNS:     {dns}")
        print(f"[wifi]   RSSI:    {rssi}dBm")
    else:
        print(f"[wifi] FAILED — status={wlan.status()}")
    return wlan.isconnected()


def _sync_time():
    print("[ntp] syncing time...")
    import ntptime as _ntp
    _ntp.timeout = 5
    for host in ("pool.ntp.org", "216.239.35.0"):
        try:
            _ntp.host = host
            _ntp.settime()
            t = utime.localtime()
            print(f"[ntp] OK ({host}) — {t[0]}-{t[1]:02d}-{t[2]:02d} "
                  f"{t[3]:02d}:{t[4]:02d}:{t[5]:02d} UTC")
            return True
        except Exception as e:
            print(f"[ntp] FAILED ({host}):")
            sys.print_exception(e)
    print("[ntp] all sources failed — continuing without time sync")
    return False


def _parse_iso_s(ts):
    if not ts or 'T' not in ts:
        return None
    try:
        ts = ts[:19]
        d, t = ts.split('T')
        yr = int(d[0:4]); mo = int(d[5:7]); dy = int(d[8:10])
        hr = int(t[0:2]); mn = int(t[3:5]); sc = int(t[6:8])
        return utime.mktime((yr, mo, dy, hr, mn, sc, 0, 0))
    except:
        return None


def _day_of_week(year, month, day):
    """Tomohiko Sakamoto's algorithm. Returns 0=Sun, 1=Mon ... 6=Sat."""
    t = (0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4)
    if month < 3:
        year -= 1
    return (year + year // 4 - year // 100 + year // 400 + t[month - 1] + day) % 7


def _last_sunday(year, month):
    """Day-of-month of the last Sunday in the given month."""
    days_in = (0, 31, 28 + (1 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 0),
               31, 30, 31, 30, 31, 31, 30, 31, 30, 31)
    last = days_in[month]
    return last - _day_of_week(year, month, last)


def _uk_offset():
    """Returns 1 during BST (last Sun Mar → last Sun Oct at 01:00 UTC), else 0."""
    t = utime.localtime()
    year, month, day, hour = t[0], t[1], t[2], t[3]
    if month < 3 or month > 10:
        return 0
    if 3 < month < 10:
        return 1
    ls = _last_sunday(year, month)
    if month == 3:
        if day < ls:  return 0
        if day > ls:  return 1
        return 1 if hour >= 1 else 0
    # month == 10
    if day < ls:  return 1
    if day > ls:  return 0
    return 0 if hour >= 1 else 1


def _local_hhmm():
    """Current time as HH:MM in UK local time (GMT or BST)."""
    t = utime.localtime()
    h = (t[3] + _uk_offset()) % 24
    return f"{h:02d}:{t[4]:02d}"


def _age_s(ts):
    """Seconds since a timestamp string. Returns 999_999 if unparseable."""
    epoch = _parse_iso_s(ts)
    if epoch is None:
        return 999_999
    return max(0, utime.time() - epoch)


def _fmt_age(seconds):
    if seconds is None or seconds >= 999_999:
        return "unknown"
    if seconds < 120:
        return f"{seconds}s ago"
    if seconds < 7200:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h ago"


def _fmt_mb(mb):
    if mb is None:
        return "---"
    if mb >= 1_000_000:
        return f"{mb / 1_000_000:.1f}TB"
    if mb >= 1_000:
        return f"{mb / 1_000:.0f}GB"
    return f"{mb:.0f}MB"


def _fmt_count(n):
    if n is None:
        return "---"
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n // 1_000}k"
    return str(n)


def _cx(text, cw=8, sw=240):
    return max(0, (sw - len(text) * cw) // 2)


def _bold(text, x, y, color):
    LCD.text(text, x,   y,   color)
    LCD.text(text, x+1, y,   color)
    LCD.text(text, x,   y+1, color)
    LCD.text(text, x+1, y+1, color)


def _wrap(text, width=26):
    """Word-wrap text to a list of lines."""
    words = text.split()
    lines, line = [], ""
    for w in words:
        if len(line) + len(w) + 1 > width:
            if line:
                lines.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        lines.append(line)
    return lines


def _age_tag(age_s_val, threshold_s):
    if age_s_val is None or age_s_val >= 999_999:
        return " [?]"
    return " [!]" if age_s_val > threshold_s else " [OK]"


def _status_color():
    s = state['status']
    if s == 'ok':    return GREEN
    if s == 'warn':  return AMBER
    if s == 'error': return RED
    return MID_GREY


def _check_backlight():
    """Auto-dim at night and blank after extended inactivity. Call every main loop tick."""
    global _bl_blanked, _bl_on

    # PC state active — never fully blank (family needs to see warning),
    # but still allow night dim. Unblank immediately if currently off.
    if state['push_pc_state'] is not None:
        if _bl_blanked:
            _bl_blanked = False
            _render()
        # Dim after inactivity but never fully blank — family needs to see warning
        idle_s = utime.time() - _last_activity_time
        if idle_s >= config.BLANK_TIMEOUT_S:
            BL.duty_u16(BL_DIM)
        else:
            _apply_backlight()
        return

    idle_s = utime.time() - _last_activity_time

    if idle_s >= config.BLANK_TIMEOUT_S:
        if not _bl_blanked:
            BL.duty_u16(BL_OFF)
            _bl_blanked = True
        return

    if _bl_blanked:
        _bl_blanked = False
        _apply_backlight()
        _render()
        return

    _apply_backlight()


def _apply_backlight():
    """Set backlight based on time of day and user toggle, without touching blank state."""
    hour = (utime.localtime()[3] + _uk_offset()) % 24
    is_night = hour >= config.NIGHT_DIM_START or hour < config.NIGHT_DIM_END
    if is_night:
        BL.duty_u16(BL_NIGHT)
    elif _bl_on:
        BL.duty_u16(BL_BRIGHT)
    else:
        BL.duty_u16(BL_DIM)


def _startup(msg):
    LCD.fill(BLACK)
    _bold("TravelNet", _cx("TravelNet"), 88, WHITE)
    LCD.text(msg, _cx(msg), 116, MID_GREY)
    LCD.show()

# ── UDP push handler ──────────────────────────────────────────────────────────

def _check_udp():
    global _current_page, _last_input_time
    try:
        data, _ = _udp_sock.recvfrom(512)
        msg = ujson.loads(data.decode())
        _handle_push(msg)
    except OSError:
        pass  # No data waiting — expected


def _handle_push(msg):
    global _current_page, _prev_page, _last_input_time, _last_activity_time, _bl_blanked
    _last_activity_time = utime.time()
    ts = _local_hhmm()
    msg_type = msg.get('type')
    print(f"[push] received type='{msg_type}': {msg}")

    if msg_type == 'message':
        state['push_msg_text']   = msg.get('text', '')
        state['push_msg_time']   = ts
        state['push_msg_unread'] = True
        _prev_page       = _current_page
        _current_page    = 4
        _last_input_time = utime.time()
        _render()

    elif msg_type == 'watchdog':
        step = msg.get('step', '')
        if step == 'recovered':
            state['push_wd_text']    = None
            state['push_wd_step']    = None
            state['push_wd_healing'] = False
            print("[push] watchdog recovered — clearing healing state")
        else:
            state['push_wd_text']    = msg.get('text', '')
            state['push_wd_step']    = step
            state['push_wd_healing'] = step in HEALING_STEPS
        _render()

    elif msg_type == 'pc':
        ps = msg.get('state', '')
        state['push_pc_state'] = None if ps in ('clear', 'off') else (ps or None)
        if ps == 'checking':
            state['push_pc_started'] = utime.time()
        if state['push_pc_state'] is not None:
            # Wake screen and force status page so banner is visible
            _current_page       = 0
            _last_input_time    = utime.time()
            _last_activity_time = utime.time()
            if _bl_blanked:
                _bl_blanked = False
                _apply_backlight()
        _render()

    elif msg_type == 'maintenance':
        state['push_maintenance'] = bool(msg.get('active', False))
        _render()

# ── Poll ──────────────────────────────────────────────────────────────────────

def _poll():
    try:
        wlan = network.WLAN(network.STA_IF)
        if wlan.isconnected():
            state['wifi_rssi'] = wlan.status('rssi')
            state['wifi_ip']   = wlan.ifconfig()[0]
        else:
            print("[poll] WARNING — WiFi not connected")
    except Exception as e:
        print("[poll] could not read WiFi status:")
        sys.print_exception(e)

    print(f"[poll] GET {config.API_URL}")
    data = None
    for attempt in range(1, 4):
        try:
            r = urequests.get(config.API_URL, timeout=20)
            print(f"[poll] HTTP {r.status_code} (attempt {attempt})")
            data = r.json()
            r.close()
            break
        except Exception as e:
            print(f"[poll] attempt {attempt} FAILED:")
            sys.print_exception(e)
            if attempt < 3:
                utime.sleep(3)

    if data is None:
        err = "Connection failed after 3 attempts"
        state['last_poll_ok'] = False
        state['detail']       = err
        state['last_error']   = err
        _derive_status()
        return

    try:
        uptime = data.get(config.KEY_UPTIME) or {}
        state['uptime_pi']  = uptime.get(config.KEY_UPTIME_PI)
        state['uptime_app'] = uptime.get(config.KEY_UPTIME_APP)

        db = data.get(config.KEY_DB) or {}
        state['db_size_mb'] = db.get(config.KEY_DB_SIZE_MB)
        state['db_free_mb'] = db.get(config.KEY_DB_FREE_MB)
        state['db_lat_ms']  = db.get(config.KEY_DB_LATENCY_MS)

        uploads = data.get(config.KEY_LAST_UPLOAD) or {}
        state['gps_age_s']     = _age_s(uploads.get(config.KEY_LAST_OVERLAND))
        state['health_age_s']  = _age_s(uploads.get(config.KEY_LAST_HEALTH))
        state['tx_age_s']      = _age_s(uploads.get(config.KEY_LAST_TRANSACTIONS))
        state['workout_age_s'] = _age_s(uploads.get(config.KEY_LAST_WORKOUTS))

        state['watchdog_age_s'] = _age_s(data.get(config.KEY_LAST_WATCHDOG))

        rows = data.get(config.KEY_ROW_COUNTS) or {}
        state['row_overland'] = rows.get(config.KEY_ROW_OVERLAND)
        state['row_health']   = rows.get(config.KEY_ROW_HEALTH)
        state['row_tx']       = rows.get(config.KEY_ROW_TRANSACTION)

        fx = data.get(config.KEY_FX_USAGE) or {}
        state['fx_count'] = fx.get(config.KEY_FX_USAGE_COUNT)
        state['fx_month'] = fx.get(config.KEY_FX_USAGE_MONTH)

        state['pending_digest'] = data.get(config.KEY_PENDING_DIGEST)

        state['last_poll_ok'] = True
        state['last_error']   = None
        state['last_poll_time'] = _local_hhmm()
        print(f"[poll] OK — GPS {_fmt_age(state['gps_age_s'])}  "
              f"health {_fmt_age(state['health_age_s'])}  "
              f"watchdog {_fmt_age(state['watchdog_age_s'])}")

    except Exception as e:
        err = f"Parse error: {str(e)}"
        state['last_poll_ok'] = False
        state['detail']       = err[:40]
        state['last_error']   = err
        print("[poll] parse FAILED:")
        sys.print_exception(e)

    _derive_status()


def _derive_status():
    wd = state['watchdog_age_s']
    wd_stale = wd is not None and wd > config.WATCHDOG_STALE_S

    if not state['last_poll_ok']:
        if wd_stale:
            state.update(status='error', step=config.STEP_ALL_DOWN,
                detail="API down + Watchdog missing. Check both Pi power cables.")
        else:
            state.update(status='error', step=config.STEP_API_DOWN,
                detail="API unreachable. Unplug + replug the Pi power cable, wait 2 mins.")
        return

    if wd_stale:
        state.update(status='error', step=config.STEP_WATCHDOG_DOWN,
            detail="Watchdog Pi not responding. Check its power cable.")
        return

    gps_stale    = state['gps_age_s']    is not None and state['gps_age_s']    > config.LOCATION_STALE_S
    health_stale = state['health_age_s'] is not None and state['health_age_s'] > config.HEALTH_STALE_S

    if gps_stale or health_stale:
        parts = []
        if gps_stale:    parts.append("GPS quiet")
        if health_stale: parts.append("health quiet")
        state.update(status='warn', step=None,
            detail=f"{', '.join(parts)}. System is fine - consider texting Dan.")
        return

    state.update(status='ok', step=None, detail="All systems normal.")
    # Clear any watchdog healing state — poll confirms recovery regardless of push
    if state['push_wd_healing']:
        print("[status] API healthy — clearing watchdog healing state")
        state['push_wd_healing'] = False
        state['push_wd_text']    = None
        state['push_wd_step']    = None

# ── Rendering ─────────────────────────────────────────────────────────────────

def _render():
    LCD.fill(BLACK)
    if   _current_page == 0: _page_status()
    elif _current_page == 1: _page_health()
    elif _current_page == 2: _page_counts()
    elif _current_page == 3: _page_alerts()
    elif _current_page == 4: _page_messages()
    LCD.show()


def _page_status():
    """Page 0 — family-facing status with push overrides."""

    # ── Determine banner content ──────────────────────────────────────────────
    if state['push_pc_state'] == 'checking':
        elapsed = utime.time() - state['push_pc_started']
        if elapsed < 5:
            flash_on = utime.ticks_ms() // 500 % 2 == 0
            banner_color = YELLOW if flash_on else BLACK
        else:
            banner_color = YELLOW
        banner_lines = ["/!\\ DO NOT TOUCH", "Checking for", "remote access..."]

    elif state['push_pc_state'] == 'remote':
        banner_color = AMBER
        banner_lines = ["/!\\ DO NOT TOUCH", "Dan working remotely", "Do not power off PC"]

    elif state['push_maintenance']:
        banner_color = YELLOW
        banner_lines = ["MAINTENANCE", "Dan is working", "on the system"]

    elif state['push_wd_healing']:
        banner_color = BLUE
        banner_lines = ["SELF HEALING"]
        if state['push_wd_text']:
            banner_lines += _wrap(state['push_wd_text'], 22)[:2]

    elif state['push_wd_step'] == 'alert' and state['push_wd_text']:
        banner_color = _status_color()
        banner_lines = None

    else:
        banner_color = _status_color()
        banner_lines = None

    # ── Draw banner ───────────────────────────────────────────────────────────
    LCD.fill_rect(0, 0, 240, 120, banner_color)

    if banner_lines:
        y = max(12, 60 - len(banner_lines) * 14)
        for ln in banner_lines[:4]:
            _bold(ln, _cx(ln), y, BLACK); y += 22
    elif _show_detail:
        lines = _wrap(state['detail'], 26)
        y = max(8, 60 - len(lines) * 10)
        for ln in lines[:5]:
            LCD.text(ln, _cx(ln), y, BLACK); y += 20
    else:
        s = state['status']
        if s == 'ok':
            _bold("ALL GOOD",       _cx("ALL GOOD"),       36, BLACK)
            _bold("System healthy", _cx("System healthy"), 60, BLACK)
        elif s == 'warn':
            _bold("CHECK IN",       _cx("CHECK IN"),       28, BLACK)
            _bold("WITH DAN",       _cx("WITH DAN"),       50, BLACK)
            LCD.text("System is fine", _cx("System is fine"), 76, BLACK)
        elif s == 'error':
            _bold("ACTION NEEDED",  _cx("ACTION NEEDED"),  24, BLACK)
            if state['step']:
                st = f"See STEP {state['step']}"
                _bold(st, _cx(st), 52, BLACK)
            _bold("in the runbook", _cx("in the runbook"), 80, BLACK)
        else:
            _bold("STARTING UP",    _cx("STARTING UP"),    44, BLACK)
            LCD.text("Please wait...", _cx("Please wait..."), 68, BLACK)

    # ── Source rows ───────────────────────────────────────────────────────────
    LCD.hline(0, 124, 240, MID_GREY)
    y = 132

    def _row(label, age, threshold):
        tag = _age_tag(age, threshold)
        col = RED if (age and age > threshold) else WHITE
        LCD.text(f"{label:<10}{_fmt_age(age):<12}{tag}", 4, y, col)

    _row("GPS:",     state['gps_age_s'],      config.LOCATION_STALE_S)
    y += 16
    _row("Health:",  state['health_age_s'],   config.HEALTH_STALE_S)
    y += 16
    _row("Watch:",   state['watchdog_age_s'], config.WATCHDOG_STALE_S)

    # ── Footer ────────────────────────────────────────────────────────────────
    LCD.hline(0, 196, 240, WHITE)

    if state['push_wd_step'] == 'alert' and not state['push_wd_healing']:
        LCD.text("Watchdog monitoring...", 4, 202, AMBER)
    else:
        LCD.text(f"Updated: {state['last_poll_time']}", 4, 202, WHITE)

    if state['push_msg_unread']:
        LCD.text("* MSG         A:Recheck", 0, 214, AMBER)
        LCD.text("B:Detail      Y:Msg", 0, 226, AMBER)
    else:
        LCD.text("A:Recheck     B:Detail", 4, 214, WHITE)
        LCD.text("Y:Msg         </>", 4, 226, WHITE)


def _page_health():
    """Page 1 — system health detail."""
    LCD.text("SYSTEM HEALTH", _cx("SYSTEM HEALTH"), 4, WHITE)
    LCD.hline(0, 16, 240, MID_GREY)

    api_col  = GREEN if state['last_poll_ok'] else RED
    rssi     = state['wifi_rssi']
    rssi_str = f"{rssi}dBm" if rssi is not None else "---"
    rssi_col = (GREEN if rssi > -60 else AMBER if rssi > -75 else RED) if rssi else WHITE

    rows = [
        ("API",      "UP" if state['last_poll_ok'] else "DOWN", api_col),
        ("Pi up",    state['uptime_pi']  or "---",              WHITE),
        ("App up",   state['uptime_app'] or "---",              WHITE),
        ("GPS",      _fmt_age(state['gps_age_s']),              WHITE),
        ("Health",   _fmt_age(state['health_age_s']),           WHITE),
        ("Watchdog", _fmt_age(state['watchdog_age_s']),         WHITE),
        ("WiFi",     rssi_str,                                  rssi_col),
        ("IP",       state['wifi_ip'] or "---",                 WHITE),
    ]
    y = 24
    for label, value, col in rows:
        LCD.text(f"{label:<12}{value}", 4, y, col)
        y += 18

    _page_footer(1)


def _page_counts():
    """Page 2 — row counts and storage."""
    LCD.text("DATA & STORAGE", _cx("DATA & STORAGE"), 4, WHITE)
    LCD.hline(0, 16, 240, MID_GREY)

    lat = f"{state['db_lat_ms']:.1f}ms" if state['db_lat_ms'] is not None else "---"
    rows = [
        ("GPS points",    _fmt_count(state['row_overland'])),
        ("Health records",_fmt_count(state['row_health'])),
        ("Transactions",  _fmt_count(state['row_tx'])),
        ("DB size",       _fmt_mb(state['db_size_mb'])),
        ("Free space",    _fmt_mb(state['db_free_mb'])),
        ("Latency",       lat),
    ]
    y = 28
    for label, value in rows:
        LCD.text(f"{label:<18}{value:>6}", 4, y, WHITE)
        y += 18

    _page_footer(2)


def _page_alerts():
    """Page 3 — FX usage and log queue."""
    LCD.text("ALERTS", _cx("ALERTS"), 4, WHITE)
    LCD.hline(0, 16, 240, MID_GREY)

    fx  = state['fx_count']
    mth = state['fx_month'] or "---"
    pd  = state['pending_digest']

    fx_str = f"{fx}/100 ({mth})" if fx is not None else "---"
    fx_col = RED if (fx and fx > 80) else AMBER if (fx and fx > 60) else WHITE
    pd_str = str(pd) if pd is not None else "---"
    pd_col = AMBER if (pd and pd > 10) else WHITE

    y = 28
    LCD.text(f"FX calls:  {fx_str}", 4, y, fx_col);  y += 20
    LCD.text(f"Log queue: {pd_str}", 4, y, pd_col);  y += 20
    LCD.text(f"API:       {'OK' if state['last_poll_ok'] else 'FAIL'}",
             4, y, GREEN if state['last_poll_ok'] else RED)

    _page_footer(3)


def _page_messages():
    """Page 4 — push messages from Dan."""
    state['push_msg_unread'] = False

    has_msg = bool(state['push_msg_text'])
    LCD.text("FROM DAN", _cx("FROM DAN"), 4, AMBER)
    LCD.hline(0, 16, 240, MID_GREY)

    if has_msg:
        lines = _wrap(state['push_msg_text'], 26)
        y = max(28, 110 - len(lines) * 12)
        for ln in lines[:8]:
            LCD.text(ln, _cx(ln), y, WHITE); y += 20
        LCD.hline(0, 210, 240, MID_GREY)
        received = state['push_msg_time'] or "unknown"
        LCD.text(f"Received: {received}", 4, 216, MID_GREY)
    else:
        LCD.text("No messages yet.", _cx("No messages yet."), 110, MID_GREY)

    _page_footer(4)


def _page_footer(page_num):
    LCD.hline(0, 228, 240, MID_GREY)
    dots = "  " + "  ".join("*" if i == page_num else "." for i in range(TOTAL_PAGES))
    LCD.text(dots + " </>", 0, 232, MID_GREY)


# ── Button handling ───────────────────────────────────────────────────────────

def _pressed(name, pin):
    val = pin.value()
    was_high = _btn_prev[name]
    _btn_prev[name] = val
    return was_high == 1 and val == 0


def _check_buttons():
    global _current_page, _prev_page, _last_input_time, _last_activity_time, _show_detail, _bl_on

    changed = False

    if _bl_blanked:
        woke = False
        for name, pin in (('A', BTN_A), ('B', BTN_B), ('Y', BTN_Y),
                           ('LEFT', JOY_LEFT), ('RIGHT', JOY_RIGHT), ('CTR', JOY_CTR)):
            val = pin.value()
            if val == 0:
                woke = True
            _btn_prev[name] = val
        if woke:
            _last_activity_time = utime.time()
        return

    if _pressed('A', BTN_A):
        _last_input_time    = utime.time()
        _last_activity_time = utime.time()
        _show_detail = False
        _startup("Refreshing...")
        _poll()
        changed = True

    if _pressed('B', BTN_B):
        _last_input_time    = utime.time()
        _last_activity_time = utime.time()
        if _current_page == 0:
            _show_detail = not _show_detail
        changed = True

    if _pressed('Y', BTN_Y):
        _last_input_time    = utime.time()
        _last_activity_time = utime.time()
        _show_detail = False
        if _current_page == 4:
            _current_page = _prev_page
        else:
            _prev_page    = _current_page
            _current_page = 4
        changed = True

    if _pressed('LEFT', JOY_LEFT):
        _last_input_time    = utime.time()
        _last_activity_time = utime.time()
        _show_detail = False
        _current_page = (_current_page - 1) % TOTAL_PAGES
        changed = True

    if _pressed('RIGHT', JOY_RIGHT):
        _last_input_time    = utime.time()
        _last_activity_time = utime.time()
        _show_detail = False
        _current_page = (_current_page + 1) % TOTAL_PAGES
        changed = True

    if _pressed('CTR', JOY_CTR):
        _last_input_time    = utime.time()
        _last_activity_time = utime.time()
        _bl_on = not _bl_on
        _apply_backlight()

    if changed:
        _render()

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    global _current_page, _last_input_time, _last_activity_time, _last_flash

    _startup("Connecting to WiFi...")
    if not _connect_wifi():
        _startup("WiFi failed - check config.py")
        utime.sleep(30)
        return

    _startup("Syncing time...")
    _sync_time()
    _last_activity_time = utime.time()  # reset after NTP clock jump

    _startup("Fetching status...")
    _poll()
    _render()

    last_poll_at = utime.time()

    while True:
        _check_udp()
        _check_buttons()
        _check_backlight()

        if _current_page != 0 and (utime.time() - _last_input_time) > config.IDLE_TIMEOUT_S:
            _current_page = 0
            _show_detail = False
            _render()

        if utime.time() - last_poll_at >= config.POLL_INTERVAL_S:
            _poll()
            last_poll_at = utime.time()
            _render()

        utime.sleep_ms(50)

        # Re-render every 500ms during PC checking flash (first 5 seconds only)
        if state['push_pc_state'] == 'checking' and _current_page == 0:
            elapsed = utime.time() - state['push_pc_started']
            if elapsed < 5:
                f = utime.ticks_ms() // 500 % 2
                if f != _last_flash:
                    _last_flash = f
                    _render()
            elif _last_flash != -1:
                _last_flash = -1   # mark flash done
                _render()          # one final render to settle on steady yellow


main()