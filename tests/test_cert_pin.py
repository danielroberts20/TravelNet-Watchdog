"""
Tests for cert_pin.py — true leaf-certificate pinning for push_heartbeat().

This logic went through a real bug-and-fix cycle live (see cert_pin.py's
docstring and the PinnedFingerprintAdapter docstring): the first version of
PinnedFingerprintAdapter only injected assert_fingerprint, and empirically
did NOT disable chain validation on its own — confirmed by mounting it and
calling with the default verify=True, which still left cert_reqs=CERT_REQUIRED
on the connection. These tests lock in the fixed behavior (cert_verify forcing
CERT_NONE unconditionally) and the leaf-only PEM extraction it depends on.
"""

import hashlib
import os
import ssl
import subprocess
import sys
import tempfile
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cert_pin import extract_leaf_pem, leaf_fingerprint_from_file, PinnedFingerprintAdapter


def _generate_self_signed_pem(common_name: str) -> str:
    """Generate a self-signed cert and return its PEM text. Returns None if
    openssl isn't available (mirrors test_cert_expiry.py's skip pattern)."""
    with tempfile.TemporaryDirectory() as d:
        key = os.path.join(d, "key.pem")
        cert = os.path.join(d, "cert.pem")
        r = subprocess.run(
            [
                "openssl", "req", "-x509", "-newkey", "rsa:2048",
                "-keyout", key, "-out", cert,
                "-days", "30", "-nodes",
                "-subj", f"/CN={common_name}",
            ],
            capture_output=True, timeout=30,
        )
        if r.returncode != 0:
            return None
        with open(cert, "r") as f:
            return f.read()


# --- extract_leaf_pem: pure string/regex logic, no I/O ---

def test_extract_leaf_pem_single_block():
    pem = _generate_self_signed_pem("single.example.com")
    if pem is None:
        return  # openssl unavailable, skip
    leaf = extract_leaf_pem(pem)
    assert leaf.count("-----BEGIN CERTIFICATE-----") == 1
    assert leaf.strip() == pem.strip()


def test_extract_leaf_pem_grabs_only_first_of_multiple_blocks():
    """The real bug this guards against: a fullchain.pem has multiple blocks
    (leaf + intermediate + root). Fingerprinting the wrong block — or the
    whole bundle — would silently defeat pinning."""
    leaf_pem = _generate_self_signed_pem("leaf.example.com")
    second_pem = _generate_self_signed_pem("intermediate.example.com")
    if leaf_pem is None or second_pem is None:
        return  # openssl unavailable, skip

    bundle = leaf_pem + second_pem
    assert bundle.count("-----BEGIN CERTIFICATE-----") == 2  # sanity on the fixture

    extracted = extract_leaf_pem(bundle)
    assert extracted.count("-----BEGIN CERTIFICATE-----") == 1
    assert extracted.strip() == leaf_pem.strip()
    assert extracted.strip() != second_pem.strip()


def test_extract_leaf_pem_raises_on_no_certificate():
    try:
        extract_leaf_pem("not a pem file at all")
        assert False, "expected ValueError"
    except ValueError:
        pass


# --- leaf_fingerprint_from_file: reads a file, extracts leaf, hashes it ---

def test_leaf_fingerprint_from_file_matches_known_leaf():
    pem = _generate_self_signed_pem("fingerprint.example.com")
    if pem is None:
        return  # openssl unavailable, skip

    expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem)).hexdigest()

    with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as f:
        f.write(pem)
        path = f.name
    try:
        assert leaf_fingerprint_from_file(path) == expected
    finally:
        os.unlink(path)


def test_leaf_fingerprint_from_file_ignores_trailing_certs_in_bundle():
    """Fingerprinting a fullchain.pem must hash only the leaf, not the
    intermediate/root certs appended after it — otherwise CERT_PATH's
    monthly-refreshed fullchain would pin to the wrong (or a moving) value."""
    leaf_pem = _generate_self_signed_pem("bundle-leaf.example.com")
    other_pem = _generate_self_signed_pem("bundle-other.example.com")
    if leaf_pem is None or other_pem is None:
        return  # openssl unavailable, skip

    expected_leaf_fp = hashlib.sha256(ssl.PEM_cert_to_DER_cert(leaf_pem)).hexdigest()
    other_fp = hashlib.sha256(ssl.PEM_cert_to_DER_cert(other_pem)).hexdigest()
    assert expected_leaf_fp != other_fp  # sanity: the two certs actually differ

    with tempfile.NamedTemporaryFile(mode="w", suffix=".crt", delete=False) as f:
        f.write(leaf_pem + other_pem)
        path = f.name
    try:
        result = leaf_fingerprint_from_file(path)
        assert result == expected_leaf_fp
        assert result != other_fp
    finally:
        os.unlink(path)


# --- PinnedFingerprintAdapter: assert_fingerprint wiring + CERT_NONE enforcement ---

def test_adapter_injects_assert_fingerprint_into_poolmanager():
    adapter = PinnedFingerprintAdapter("deadbeef" * 8)
    assert adapter.poolmanager.connection_pool_kw.get("assert_fingerprint") == "deadbeef" * 8


def test_adapter_forces_cert_none_when_caller_passes_verify_true():
    """The bug this guards against: without overriding cert_verify, mounting
    this adapter but calling with the default verify=True left
    cert_reqs=CERT_REQUIRED on the connection — chain validation ran ahead of
    (not instead of) the fingerprint check, confirmed empirically live."""
    adapter = PinnedFingerprintAdapter("deadbeef" * 8)
    conn = types.SimpleNamespace(cert_reqs=None, ca_certs=None, ca_cert_dir=None)
    adapter.cert_verify(conn, "https://example.com", True, None)
    assert conn.cert_reqs == "CERT_NONE"


def test_adapter_forces_cert_none_when_caller_passes_a_ca_bundle_path():
    adapter = PinnedFingerprintAdapter("deadbeef" * 8)
    conn = types.SimpleNamespace(cert_reqs=None, ca_certs=None, ca_cert_dir=None)
    adapter.cert_verify(conn, "https://example.com", "/etc/ssl/certs/ca-certificates.crt", None)
    assert conn.cert_reqs == "CERT_NONE"


def test_adapter_forces_cert_none_when_caller_passes_verify_false():
    adapter = PinnedFingerprintAdapter("deadbeef" * 8)
    conn = types.SimpleNamespace(cert_reqs=None, ca_certs=None, ca_cert_dir=None)
    adapter.cert_verify(conn, "https://example.com", False, None)
    assert conn.cert_reqs == "CERT_NONE"
