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

logger = logging.getLogger(__name__)

import socket as _socket
import json as _json

PICO_IP       = "192.168.0.73"
PICO_UDP_PORT = 9002

PORT = 9001
MAINTENANCE_MAX_SECONDS = 600


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
_lock = threading.Lock()


def set_maintenance_mode():
    global _maintenance_until
    with _lock:
        _maintenance_until = time.time() + MAINTENANCE_MAX_SECONDS
    _push_maintenance_to_pico(True)


def is_maintenance_mode() -> bool:
    with _lock:
        return time.time() < _maintenance_until


def clear_maintenance_mode():
    global _maintenance_until
    with _lock:
        _maintenance_until = 0.0
    _push_maintenance_to_pico(False)


def get_last_wol() -> str:
    try:
        with open("/tmp/last_wol_sent", "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "1970-01-01T00:00:00+00:00"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/compute/last-wol":
            body = json.dumps({"timestamp": get_last_wol()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/heartbeat":
            body = json.dumps({"status": "ok", "maintenance": str(is_maintenance_mode())}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/logs/"):
            from urllib.parse import urlparse, parse_qs
            parsed = urlparse(self.path)
            container = parsed.path[len("/logs/"):]

            if container == "watchdog":
                try:
                    query = parse_qs(parsed.query)
                    lines = int(query.get("lines", ["200"])[0])
                    lines = max(1, min(lines, 1000))

                    log_path = os.path.join(os.path.dirname(__file__), "watchdog.log")

                    if not os.path.exists(log_path):
                        result = {"lines": [], "total_lines": 0, "file": "watchdog.log", "error": "Log file not found"}
                    else:
                        with open(log_path, "r", errors="replace") as f:
                            all_lines = f.readlines()
                        tail = [l.rstrip("\n") for l in all_lines[-lines:]]
                        result = {"lines": tail, "total_lines": len(all_lines), "file": "watchdog.log"}

                    body = json.dumps(result).encode("utf-8")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except Exception as e:
                    body = json.dumps({"error": str(e), "lines": []}).encode("utf-8")
                    self.send_response(500)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)

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
        elif self.path == "/status":
            import re
            try:
                log_path = os.path.join(os.path.dirname(__file__), "watchdog.log")
                last_status_line = None

                if os.path.exists(log_path):
                    with open(log_path, "r", errors="replace") as f:
                        for line in f:
                            if "internet=" in line and "tailscale=" in line:
                                last_status_line = line.strip()

                if last_status_line is None:
                    result = {"error": "No status line found", "checks": {}}
                else:
                    # Parse: "2026-05-31 12:48:25,951 | INFO| internet=True(ok) ..."
                    parts = last_status_line.split("|", 2)
                    timestamp = parts[0].strip() if parts else ""
                    payload = parts[2].strip() if len(parts) > 2 else last_status_line

                    checks = {}
                    for match in re.finditer(r'(\w+)=(True|False)\(([^)]*)\)', payload):
                        key, ok_str, detail = match.group(1), match.group(2), match.group(3)
                        checks[key] = {"ok": ok_str == "True", "detail": detail}

                    result = {"timestamp": timestamp, "checks": checks}

                body = json.dumps(result).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as e:
                body = json.dumps({"error": str(e), "checks": {}}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == "/maintenance":
            set_maintenance_mode()
            body = json.dumps({"status": "ok", "maintenance_seconds": MAINTENANCE_MAX_SECONDS}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
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
