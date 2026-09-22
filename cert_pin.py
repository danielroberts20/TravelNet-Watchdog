"""
True leaf-certificate pinning for push_heartbeat(), via urllib3's public
`assert_fingerprint` parameter — not chain-of-trust verification.

Why this exists: verify=CERT_PATH against a fullchain.pem (leaf + intermediate
+ root CAs) doesn't pin anything specific. Because the bundle contains the
public Let's Encrypt root CAs, OpenSSL will happily validate *any* certificate
that chains through those same roots — including a completely different
certificate than the one the bundle was built around. Confirmed live: this
let a mismatched cert (Cloudflare's edge cert, a different leaf entirely)
pass verification against our origin's cert bundle with no error.

assert_fingerprint compares the SHA-256 digest of the exact DER bytes of the
peer's leaf certificate against an expected value, after skipping normal
chain/hostname verification (cert_reqs=CERT_NONE) — a byte-for-byte pin, not
"any cert signed by a CA we trust". Single TLS handshake: the fingerprint
check happens against the same connection the request already made, not a
separate preflight connection.

This only makes sense for push_heartbeat(), which connects LAN-direct to our
own nginx (see dns_pin.pin_host_to_ip) and therefore always receives our own
origin leaf cert. It's deliberately NOT used for check_cloudflare(), which
goes over the real Cloudflare edge and receives a different, Cloudflare-
managed certificate — pinning to our own cert there would fail on every
healthy check. check_cloudflare() uses standard verify=True instead.
"""

import hashlib
import re
import ssl

from requests.adapters import HTTPAdapter

_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
    re.DOTALL,
)


def extract_leaf_pem(bundle_text: str) -> str:
    """Return just the first PEM certificate block from a bundle (a
    fullchain.pem's leaf, ignoring any intermediate/root certs that follow)."""
    match = _PEM_BLOCK_RE.search(bundle_text)
    if not match:
        raise ValueError("No PEM certificate block found")
    return match.group(0)


def leaf_fingerprint_from_file(cert_path: str) -> str:
    """SHA-256 fingerprint (lowercase hex, no colons) of the leaf cert in
    cert_path. Re-reads the file each call — CERT_PATH is refreshed monthly
    by the existing scp cron job, so this picks up renewals automatically
    with no separate distribution mechanism."""
    with open(cert_path, "r") as f:
        bundle_text = f.read()
    leaf_pem = extract_leaf_pem(bundle_text)
    der = ssl.PEM_cert_to_DER_cert(leaf_pem)
    return hashlib.sha256(der).hexdigest()


class PinnedFingerprintAdapter(HTTPAdapter):
    """requests HTTPAdapter that pins the TLS connection to an exact leaf
    certificate fingerprint (urllib3's assert_fingerprint), instead of
    chain-of-trust verification. Mount on a Session for a specific host:

        session = requests.Session()
        session.mount(f"https://{hostname}", PinnedFingerprintAdapter(fingerprint))
        session.get(url, ...)

    Forces cert_reqs=CERT_NONE on every request through this adapter
    (overriding cert_verify unconditionally, regardless of what `verify=`
    the caller passes) so the fingerprint check is the sole trust mechanism.
    Without this override, assert_fingerprint alone does NOT disable chain
    validation — confirmed empirically: mounting this adapter but calling
    with the default verify=True still set cert_reqs=CERT_REQUIRED on the
    connection, so a publicly-trusted-but-wrong cert would pass Python's
    ssl-layer chain check first and only get caught by the fingerprint
    comparison afterward — i.e. both checks would run, not fingerprint-only,
    and the whole point of pinning (no dependence on public CA trust) would
    quietly not hold. Forcing CERT_NONE here removes that footgun instead of
    relying on every call site remembering to pass verify=False.
    """

    def __init__(self, fingerprint_hex: str, *args, **kwargs):
        self._fingerprint = fingerprint_hex
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["assert_fingerprint"] = self._fingerprint
        return super().init_poolmanager(*args, **kwargs)

    def cert_verify(self, conn, url, verify, cert):
        super().cert_verify(conn, url, verify=False, cert=cert)
