"""
Minimal HTTP server exposing watchdog status endpoints.
Runs in a background thread alongside the main monitoring loop.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import os
import threading
import time
from log_mirror import MIRROR_LOG_DIR, MIRROR_CONTAINERS
import actions as _actions

logger = logging.getLogger(__name__)

import socket as _socket
import json as _json

PICO_IP       = "192.168.0.73"
PICO_UDP_PORT = 9002

PORT = 9001
MAINTENANCE_MAX_SECONDS = 600
_LOG_PATH = "/home/dan/watchdog/watchdog.log"


def _push_to_pico(step: str, text: str, msg_type: str = "watchdog") -> None:
    """Fire-and-forget UDP push to the Pico display. Never raises."""
    try:
        payload = _json.dumps({"type": msg_type, "step": step, "text": text}).encode()
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.sendto(payload, (PICO_IP, PICO_UDP_PORT))
        sock.close()
    except Exception as e:
        logger.warning(f"Pico push failed (non-fatal): {e}")


def _push_maintenance_to_pico(active: bool) -> None:
    """Fire-and-forget UDP push to notify the Pico of maintenance mode changes."""
    try:
        payload = _json.dumps({"type": "maintenance", "active": active}).encode()
        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.sendto(payload, (PICO_IP, PICO_UDP_PORT))
        sock.close()
    except Exception as e:
        logger.warning(f"Pico maintenance push failed (non-fatal): {e}")


# Shared state
_maintenance_until: float = 0.0
_maintenance_forced: bool = False
_lock = threading.Lock()

_state: dict = {
    "consecutive_failures": 0,
    "shelly_failures": 0,
    "cloudflare_failures": 0,
    "prefect_failures": 0,
    "last_recovery_at": 0.0,
    "internet_ok": None,
    "tailscale_ok": None,
    "api_ok": None,
    "shelly_ok": None,
    "cloudflare_ok": None,
    "prefect_ok": None,
    "last_check_at": None,
    "internet_detail": "",
    "tailscale_detail": "",
    "api_detail": "",
    "shelly_detail": "",
    "cloudflare_detail": "",
    "prefect_detail": "",
}
_last_action: dict = {
    "name": None,
    "started_at": None,
    "finished_at": None,
    "success": None,
    "detail": None,
    "running": False,
}


def is_maintenance_mode() -> bool:
    with _lock:
        return time.time() < _maintenance_until

def set_maintenance_mode(forced: bool = False):
    global _maintenance_until, _maintenance_forced
    with _lock:
        _maintenance_until = time.time() + MAINTENANCE_MAX_SECONDS
        _maintenance_forced = forced
    _push_maintenance_to_pico(True)

def is_maintenance_forced() -> bool:
    with _lock:
        return _maintenance_forced and time.time() < _maintenance_until

def clear_maintenance_mode():
    global _maintenance_until, _maintenance_forced
    with _lock:
        _maintenance_until = 0.0
        _maintenance_forced = False
    _push_maintenance_to_pico(False)

def update_watchdog_state(**kwargs):
    with _lock:
        _state.update(kwargs)

def _get_status_snapshot() -> dict:
    with _lock:
        s = dict(_state)
        la = dict(_last_action)
        now = time.time()
        maint_active = now < _maintenance_until
        maint_until_val = _maintenance_until if maint_active else None
        maint_forced = _maintenance_forced and maint_active
    s["maintenance_mode"] = maint_active
    s["maintenance_until"] = maint_until_val
    s["maintenance_forced"] = maint_forced
    s["last_action"] = la
    return s


def get_last_wol() -> str:
    try:
        with open("/tmp/last_wol_sent", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "1970-01-01T00:00:00+00:00"


def _run_action(name: str, fn):
    with _lock:
        _last_action.update({
            "name": name,
            "started_at": time.time(),
            "finished_at": None,
            "success": None,
            "detail": None,
            "running": True,
        })
    try:
        ok, detail = fn()
    except Exception as e:
        ok, detail = False, str(e)
    with _lock:
        _last_action.update({
            "finished_at": time.time(),
            "success": ok,
            "detail": detail,
            "running": False,
        })


_ACTION_MAP = {
    "restart-docker":  ("restart-docker",  _actions.ssh_restart_docker),
    "rebuild-docker":  ("rebuild-docker",  _actions.ssh_rebuild_docker),
    "reboot-travelnet":("reboot-travelnet",_actions.ssh_reboot_travelnet),
    "power-cycle":     ("power-cycle",     _actions.shelly_power_cycle),
    "wake-pc":         ("wake-pc",         _actions.wake_pc),
    "shutdown-pc":     ("shutdown-pc",     _actions.shutdown_pc),
}


_DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Watchdog</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0f0f0f;color:#e0e0e0;font-family:'Courier New',Courier,monospace;font-size:14px;padding:16px;max-width:900px;margin:0 auto}
h1{color:#e0e0e0;font-size:18px;margin-bottom:16px;letter-spacing:2px}
.section{background:#1e1e1e;border-radius:4px;padding:16px;margin-bottom:14px}
.section h2{font-size:11px;text-transform:uppercase;color:#555;margin-bottom:12px;letter-spacing:2px}
.ok{color:#00ff88}
.fail{color:#ff4444}
.warn{color:#ffaa00}
.muted{color:#555}
.checks{display:flex;flex-wrap:wrap;gap:16px;align-items:center}
.check{display:flex;align-items:baseline;gap:6px}
.check .sym{font-size:16px}
.check .lbl{color:#888;font-size:12px}
.check .det{color:#555;font-size:11px}
.meta-row{margin-top:10px;font-size:11px;color:#555}
button{font-family:inherit;font-size:12px;padding:7px 12px;border:1px solid #333;border-radius:4px;cursor:pointer;background:#252525;color:#e0e0e0}
button:hover:not(:disabled){background:#303030}
button:disabled{opacity:0.35;cursor:not-allowed}
button.armed{color:#ffaa00;border-color:#ffaa00}
.maint-status{font-size:14px;margin-bottom:10px;min-height:20px}
.maint-btns{display:flex;gap:8px;flex-wrap:wrap}
.act-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.last-act .name{font-size:14px}
.last-act .ts{font-size:11px;color:#555;margin-top:4px}
.last-act .det{font-size:11px;color:#888;margin-top:4px;word-break:break-all}
.log-ctrl{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-bottom:8px}
.log-ctrl label{font-size:12px;color:#888}
.log-ctrl select{font-family:inherit;font-size:12px;background:#252525;color:#e0e0e0;border:1px solid #333;border-radius:4px;padding:4px 6px}
.log-ctrl input[type=checkbox]{margin-right:4px}
pre{font-family:'Courier New',Courier,monospace;font-size:11px;line-height:1.5;background:#0a0a0a;padding:12px;border-radius:4px;max-height:420px;overflow-y:auto;white-space:pre-wrap;word-break:break-all}
.le{color:#ff4444}
.lw{color:#ffaa00}
@media(max-width:480px){.act-grid{grid-template-columns:1fr 1fr}.checks{gap:10px}}
</style>
</head>
<body>
<h1>WATCHDOG</h1>

<div class="section">
  <h2>Checks</h2>
  <div class="checks" id="checks"><span class="muted">Loading...</span></div>
  <div class="meta-row" id="meta-row"></div>
</div>

<div class="section">
  <h2>Maintenance Mode</h2>
  <div class="maint-status" id="maint-status"><span class="muted">Loading...</span></div>
  <div class="maint-btns">
    <button id="btn-maint-enable" onclick="toggleMaintenance()" style="display:none">Enable</button>
    <button id="btn-maint-force" onclick="forceMaintenance()" style="display:none">Force Enable</button>
    <button id="btn-maint-clear" onclick="clearMaintenance()" style="display:none">Clear</button>
  </div>
</div>

<div class="section">
  <h2>Last Manual Action</h2>
  <div class="last-act" id="last-act"><span class="muted">None</span></div>
</div>

<div class="section">
  <h2>Actions</h2>
  <div class="act-grid">
    <button onclick="triggerAction('restart-docker',this)">Restart Docker</button>
    <button onclick="triggerAction('rebuild-docker',this)">Rebuild Docker</button>
    <button onclick="triggerAction('reboot-travelnet',this)">Reboot Pi</button>
    <button onclick="triggerAction('power-cycle',this)">Power Cycle</button>
    <button onclick="triggerAction('wake-pc',this)">Wake PC</button>
    <button onclick="triggerAction('shutdown-pc',this)">Shutdown PC</button>
  </div>
</div>

<div class="section">
  <h2>Logs</h2>
  <div class="log-ctrl">
    <label>Lines:
      <select id="log-n" onchange="fetchLogs()">
        <option value="50">50</option>
        <option value="100" selected>100</option>
        <option value="200">200</option>
        <option value="500">500</option>
      </select>
    </label>
    <label><input type="checkbox" id="log-auto" onchange="toggleLogAuto()"> Auto-refresh (10s)</label>
    <button onclick="fetchLogs()" style="padding:4px 10px">Refresh</button>
  </div>
  <pre id="log-pre"><span class="muted">Loading...</span></pre>
</div>

<script>
var _confirmTimers = {};
var _logTimer = null;
var _cntdwnTimer = null;
var _cntdwnUntil = null;

function esc(s){
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function statusRefresh(){
  fetch('/status').then(function(r){return r.json();}).then(function(d){
    renderChecks(d);
    renderMaintenance(d);
    renderLastAction(d);
    renderActionButtons(d);
  }).catch(function(){});
}

function renderChecks(d){
  var checks = [
    {label:'Internet',   ok:d.internet_ok,     det:d.internet_detail||''},
    {label:'Tailscale',  ok:d.tailscale_ok,    det:d.tailscale_detail||''},
    {label:'API',        ok:d.api_ok,          det:d.api_detail||''},
    {label:'Shelly',     ok:d.shelly_ok,       det:d.shelly_detail||''},
    {label:'Cloudflare', ok:d.cloudflare_ok,   det:d.cloudflare_detail||''},
    {label:'Prefect',    ok:d.prefect_healthy, det:d.prefect_failures>0?d.prefect_failures+' failures':''},
  ];
  var html = '';
  for(var i=0;i<checks.length;i++){
    var c = checks[i];
    var sym,cls;
    if(c.ok===true){sym='&#10003;';cls='ok';}
    else if(c.ok===false){sym='&#10007;';cls='fail';}
    else{sym='?';cls='muted';}
    html += '<div class="check"><span class="sym '+cls+'">'+sym+'</span>'
          + '<span class="lbl">'+c.label+'</span>';
    if(c.det) html += '<span class="det">'+esc(c.det)+'</span>';
    html += '</div>';
  }
  document.getElementById('checks').innerHTML = html;
  var cf = d.consecutive_failures||0;
  var lc = d.last_check_at ? new Date(d.last_check_at).toLocaleTimeString() : '—';
  document.getElementById('meta-row').textContent =
    'Consecutive failures: '+cf+' | Last check: '+lc;
}

function renderMaintenance(d){
  var active = d.maintenance_mode;
  var until  = d.maintenance_until;
  var forced = d.maintenance_forced;
  var status  = document.getElementById('maint-status');
  var btnEn   = document.getElementById('btn-maint-enable');
  var btnFo   = document.getElementById('btn-maint-force');
  var btnCl   = document.getElementById('btn-maint-clear');
  if(active){
    var secs = Math.max(0,Math.round(until - Date.now()/1000));
    status.innerHTML = '<span class="warn">ACTIVE</span>'+(forced?' <span class="muted">(forced)</span>':'')+
      ' — expires in <span id="cntdwn">'+secs+'</span>s';
    btnEn.style.display='none';
    btnFo.style.display='none';
    btnCl.style.display='';
    startCountdown(until);
  } else {
    status.innerHTML = '<span class="muted">Inactive</span>';
    btnEn.style.display='';
    btnFo.style.display='';
    btnCl.style.display='none';
    stopCountdown();
  }
}

function startCountdown(until){
  _cntdwnUntil = until;
  if(_cntdwnTimer) return;
  _cntdwnTimer = setInterval(function(){
    var el = document.getElementById('cntdwn');
    if(!el){stopCountdown();return;}
    var secs = Math.max(0,Math.round(_cntdwnUntil - Date.now()/1000));
    el.textContent = secs;
    if(secs<=0) stopCountdown();
  },1000);
}
function stopCountdown(){
  if(_cntdwnTimer){clearInterval(_cntdwnTimer);_cntdwnTimer=null;}
  _cntdwnUntil=null;
}

function renderLastAction(d){
  var la = d.last_action;
  var el = document.getElementById('last-act');
  if(!la||!la.name){el.innerHTML='<span class="muted">None</span>';return;}
  var started = la.started_at ? new Date(la.started_at*1000).toLocaleTimeString() : '—';
  var finished = la.finished_at ? new Date(la.finished_at*1000).toLocaleTimeString() : '—';
  var stHtml;
  if(la.running) stHtml='<span class="warn">Running…</span>';
  else if(la.success===true) stHtml='<span class="ok">&#10003; Success</span>';
  else if(la.success===false) stHtml='<span class="fail">&#10007; Failed</span>';
  else stHtml='<span class="muted">—</span>';
  el.innerHTML = '<div class="name">'+esc(la.name)+' '+stHtml+'</div>'
    +'<div class="ts">Started: '+started+(la.running?'':' | Finished: '+finished)+'</div>'
    +(la.detail?'<div class="det">'+esc(la.detail)+'</div>':'');
}

function renderActionButtons(d){
  var running = d.last_action && d.last_action.running;
  document.querySelectorAll('.act-grid button').forEach(function(b){
    if(!b._armed) b.disabled = running;
  });
}

function triggerAction(action,btn){
  if(_confirmTimers[action]){
    clearTimeout(_confirmTimers[action]);
    delete _confirmTimers[action];
    btn.textContent = btn._orig;
    btn.classList.remove('armed');
    btn._armed = false;
    fetch('/action/'+action+'?confirm=yes',{method:'POST'})
      .then(function(r){return r.json();})
      .then(function(){setTimeout(statusRefresh,600);})
      .catch(function(){});
  } else {
    btn._orig = btn.textContent;
    btn.textContent = 'Confirm? ('+btn._orig+')';
    btn.classList.add('armed');
    btn._armed = true;
    _confirmTimers[action] = setTimeout(function(){
      btn.textContent = btn._orig;
      btn.classList.remove('armed');
      btn._armed = false;
      delete _confirmTimers[action];
    },5000);
  }
}

function toggleMaintenance(){
  fetch('/maintenance',{method:'POST'}).then(statusRefresh).catch(function(){});
}
function forceMaintenance(){
  fetch('/maintenance?force=1',{method:'POST'}).then(statusRefresh).catch(function(){});
}
function clearMaintenance(){
  fetch('/maintenance',{method:'DELETE'}).then(statusRefresh).catch(function(){});
}

function fetchLogs(){
  var n = document.getElementById('log-n').value;
  fetch('/logs?n='+n).then(function(r){return r.json();}).then(function(d){
    var pre = document.getElementById('log-pre');
    if(!d.lines||d.lines.length===0){pre.innerHTML='<span class="muted">No log lines.</span>';return;}
    pre.innerHTML = d.lines.map(function(line){
      var e = esc(line);
      if(/ERROR/.test(line)) return '<span class="le">'+e+'</span>';
      if(/WARNING/.test(line)) return '<span class="lw">'+e+'</span>';
      return e;
    }).join('\\n');
    pre.scrollTop = pre.scrollHeight;
  }).catch(function(){});
}

function toggleLogAuto(){
  if(document.getElementById('log-auto').checked){
    _logTimer = setInterval(fetchLogs,10000);
  } else {
    if(_logTimer){clearInterval(_logTimer);_logTimer=null;}
  }
}

statusRefresh();
setInterval(statusRefresh,15000);
fetchLogs();
</script>
</body>
</html>"""


class Handler(BaseHTTPRequestHandler):
    def _json_response(self, code: int, data: dict):
        body = json.dumps(data).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/":
            body = _DASHBOARD_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/compute/last-wol":
            self._json_response(200, {"timestamp": get_last_wol()})

        elif path == "/heartbeat":
            self._json_response(200, {"status": "ok", "maintenance": str(is_maintenance_mode())})

        elif path == "/status":
            try:
                snap = _get_status_snapshot()
                snap["checks"] = {
                    "internet":   {"ok": snap.get("internet_ok"),    "detail": snap.get("internet_detail", "")},
                    "tailscale":  {"ok": snap.get("tailscale_ok"),   "detail": snap.get("tailscale_detail", "")},
                    "api":        {"ok": snap.get("api_ok"),          "detail": snap.get("api_detail", "")},
                    "shelly":     {"ok": snap.get("shelly_ok"),       "detail": snap.get("shelly_detail", "")},
                    "cloudflare": {"ok": snap.get("cloudflare_ok"),  "detail": snap.get("cloudflare_detail", "")},
                    "prefect":    {"ok": snap.get("prefect_ok"),      "detail": snap.get("prefect_detail", "")},
                }
                snap["timestamp"] = snap.get("last_check_at")
                self._json_response(200, snap)
            except Exception as e:
                self._json_response(500, {"error": str(e)})

        elif path == "/logs":
            try:
                n = int(query.get("n", ["100"])[0])
                n = max(1, min(n, 500))
                if not os.path.exists(_LOG_PATH):
                    self._json_response(200, {"lines": [], "file": _LOG_PATH, "error": "Log file not found"})
                    return
                with open(_LOG_PATH, "r", errors="replace") as f:
                    all_lines = f.readlines()
                tail = [l.rstrip("\n") for l in all_lines[-n:]]
                self._json_response(200, {"lines": tail, "file": _LOG_PATH})
            except Exception as e:
                self._json_response(500, {"error": str(e), "lines": []})

        elif path.startswith("/logs/"):
            container = path[len("/logs/"):]

            if container == "watchdog":
                try:
                    lines = int(query.get("lines", ["200"])[0])
                    lines = max(1, min(lines, 1000))
                    if not os.path.exists(_LOG_PATH):
                        self._json_response(200, {"lines": [], "total_lines": 0, "file": "watchdog.log", "error": "Log file not found"})
                        return
                    with open(_LOG_PATH, "r", errors="replace") as f:
                        all_lines = f.readlines()
                    tail = [l.rstrip("\n") for l in all_lines[-lines:]]
                    self._json_response(200, {"lines": tail, "total_lines": len(all_lines), "file": "watchdog.log"})
                except Exception as e:
                    self._json_response(500, {"error": str(e), "lines": []})

            elif container not in MIRROR_CONTAINERS:
                self.send_response(404)
                self.end_headers()

            else:
                log_path = f"{MIRROR_LOG_DIR}/{container}.log"
                try:
                    with open(log_path, "r") as f:
                        body = f.read().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(body)
                except FileNotFoundError:
                    self.send_response(404)
                    self.end_headers()

        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/maintenance":
            forced = query.get("force") == ["1"]
            set_maintenance_mode(forced=forced)
            self._json_response(200, {
                "status": "ok",
                "maintenance_seconds": MAINTENANCE_MAX_SECONDS,
                "forced": forced,
            })

        elif path.startswith("/action/"):
            action_key = path[len("/action/"):]
            if action_key not in _ACTION_MAP:
                self._json_response(404, {"error": "Unknown action"})
                return
            if query.get("confirm") != ["yes"]:
                self._json_response(400, {"error": "Pass ?confirm=yes to execute"})
                return
            with _lock:
                if _last_action["running"]:
                    self._json_response(409, {"error": "An action is already running"})
                    return
            name, fn = _ACTION_MAP[action_key]
            t = threading.Thread(target=_run_action, args=(name, fn), daemon=True)
            t.start()
            self._json_response(200, {"status": "started", "action": name})

        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        if self.path == "/maintenance":
            clear_maintenance_mode()
            self._json_response(200, {"status": "ok", "cleared": True})
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
