"""
Pins a hostname to a fixed IP for the duration of a single outbound call,
without touching system DNS or /etc/hosts.

Used by push_heartbeat() to reach api.travelnet.dev over the LAN (192.168.0.61)
instead of the public internet, while keeping the hostname as the Host header
and TLS SNI — so nginx still presents the Let's Encrypt cert for that vhost and
`verify=CERT_PATH` validates correctly. This replaces a Tailscale-hostname hop
with a LAN one without requiring any nginx or DNS changes.

Process-wide monkeypatch of socket.getaddrinfo, guarded by a lock so concurrent
calls (e.g. from server.py's background HTTP thread) can't race on the patch.
"""

import socket
import threading
from contextlib import contextmanager

_lock = threading.Lock()


@contextmanager
def pin_host_to_ip(hostname: str, ip: str):
    original_getaddrinfo = socket.getaddrinfo

    def patched_getaddrinfo(host, *args, **kwargs):
        if host == hostname:
            host = ip
        return original_getaddrinfo(host, *args, **kwargs)

    with _lock:
        socket.getaddrinfo = patched_getaddrinfo
        try:
            yield
        finally:
            socket.getaddrinfo = original_getaddrinfo
