"""
Minimal HTTP server exposing watchdog status endpoints.
Runs in a background thread alongside the main monitoring loop.
"""

from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import threading
from datetime import datetime, timezone

PORT = 9001

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
            body = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # suppress default stdout logging


def start_server():
    server = HTTPServer(("0.0.0.0", PORT), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server