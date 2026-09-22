"""
Tests for dns_pin.pin_host_to_ip — the LAN-IP override used by push_heartbeat()
to reach api.travelnet.dev over the LAN while keeping SNI/Host intact.
"""

import socket
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dns_pin import pin_host_to_ip


def test_pins_matching_hostname_to_ip():
    original = socket.getaddrinfo
    calls = []

    def fake_getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        return "resolved"

    socket.getaddrinfo = fake_getaddrinfo
    try:
        with pin_host_to_ip("api.travelnet.dev", "192.168.0.61"):
            result = socket.getaddrinfo("api.travelnet.dev", 443)
    finally:
        socket.getaddrinfo = original

    assert calls == ["192.168.0.61"]
    assert result == "resolved"


def test_leaves_other_hostnames_untouched():
    original = socket.getaddrinfo
    calls = []

    def fake_getaddrinfo(host, *args, **kwargs):
        calls.append(host)
        return "resolved"

    socket.getaddrinfo = fake_getaddrinfo
    try:
        with pin_host_to_ip("api.travelnet.dev", "192.168.0.61"):
            socket.getaddrinfo("example.com", 443)
    finally:
        socket.getaddrinfo = original

    assert calls == ["example.com"]


def test_restores_original_resolver_on_exit():
    original = socket.getaddrinfo
    with pin_host_to_ip("api.travelnet.dev", "192.168.0.61"):
        assert socket.getaddrinfo is not original
    assert socket.getaddrinfo is original


def test_restores_original_resolver_on_exception():
    original = socket.getaddrinfo
    try:
        with pin_host_to_ip("api.travelnet.dev", "192.168.0.61"):
            raise ValueError("boom")
    except ValueError:
        pass
    assert socket.getaddrinfo is original
