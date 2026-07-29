"""
Tests for TLS certificate expiry checking logic in checks.py.
"""

import sys
import os
import subprocess
import tempfile
from datetime import datetime, timedelta, UTC
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from checks import _cert_status, _parse_cert_expiry, check_cert_expiry


# --- _cert_status: pure classification, no I/O ---

def test_status_ok_well_above_warn():
    assert _cert_status(90) == "ok"

def test_status_ok_just_above_warn():
    assert _cert_status(22) == "ok"

def test_status_warn_at_boundary():
    assert _cert_status(21) == "warn"

def test_status_warn_mid_range():
    assert _cert_status(14) == "warn"

def test_status_warn_just_above_critical():
    assert _cert_status(8) == "warn"

def test_status_critical_at_boundary():
    assert _cert_status(7) == "critical"

def test_status_critical_mid_range():
    assert _cert_status(4) == "critical"

def test_status_critical_one_day():
    assert _cert_status(1) == "critical"

def test_status_expired_zero_days():
    assert _cert_status(0) == "expired"

def test_status_expired_negative():
    assert _cert_status(-10) == "expired"

def test_status_error_on_none():
    assert _cert_status(None) == "error"


# --- _parse_cert_expiry: subprocess openssl, tested against real files ---

REAL_CERT = "/home/dan/watchdog/certs/api.travelnet.dev.crt"

def test_parse_real_cert_returns_int_and_date():
    if not os.path.exists(REAL_CERT):
        return  # skip if not present on this machine
    days, date_str = _parse_cert_expiry(REAL_CERT)
    assert isinstance(days, int), f"expected int, got {days!r}"
    assert date_str is not None
    # date_str should parse as YYYY-MM-DD
    datetime.strptime(date_str, "%Y-%m-%d")

def test_parse_missing_file_returns_none():
    days, date_str = _parse_cert_expiry("/nonexistent/path/cert.crt")
    assert days is None
    assert date_str is None

def test_parse_temp_cert_known_expiry():
    """Generate a self-signed cert with a known expiry and verify the parsed date."""
    with tempfile.TemporaryDirectory() as d:
        key = os.path.join(d, "key.pem")
        cert = os.path.join(d, "cert.pem")
        # Create a cert expiring in 30 days
        r = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key, "-out", cert,
                "-days", "30", "-nodes",
                "-subj", "/CN=test.example.com",
            ],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            return  # openssl not available or too slow, skip
        days, date_str = _parse_cert_expiry(cert)
        assert days is not None
        # A 30-day cert should show ~28-30 days remaining (allow ±2 for timing)
        assert 27 <= days <= 31, f"expected ~30 days, got {days}"
        assert date_str is not None
        datetime.strptime(date_str, "%Y-%m-%d")


# --- check_cert_expiry: integration, tests missing-file path ---

def test_check_cert_expiry_missing_file():
    fake_paths = [("test-cert", "/nonexistent/cert.crt")]
    with patch("checks.CERT_CHECK_PATHS", fake_paths):
        results = check_cert_expiry()
    assert len(results) == 1
    r = results[0]
    assert r["name"] == "test-cert"
    assert r["status"] == "missing"
    assert r["days_remaining"] is None
    assert r["expiry_date"] is None

def test_check_cert_expiry_returns_one_entry_per_path():
    fake_paths = [
        ("cert-a", "/nonexistent/a.crt"),
        ("cert-b", "/nonexistent/b.crt"),
    ]
    with patch("checks.CERT_CHECK_PATHS", fake_paths):
        results = check_cert_expiry()
    assert len(results) == 2
    assert {r["name"] for r in results} == {"cert-a", "cert-b"}
    assert all(r["status"] == "missing" for r in results)

def test_check_cert_expiry_real_file_has_expected_keys():
    if not os.path.exists(REAL_CERT):
        return
    fake_paths = [("api.travelnet.dev", REAL_CERT)]
    with patch("checks.CERT_CHECK_PATHS", fake_paths):
        results = check_cert_expiry()
    assert len(results) == 1
    r = results[0]
    assert set(r.keys()) == {"name", "path", "days_remaining", "expiry_date", "status"}
    assert r["status"] in ("ok", "warn", "critical", "expired", "error")
