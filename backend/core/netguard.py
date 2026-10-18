"""Outbound-URL guard shared by every fetch path.

CareerOS fetches URLs it did not author: the company scraper guesses a domain
from a search query, and `apply_url` is ingested verbatim from third-party feeds
anyone can post to. Both are reachable by an unauthenticated attacker on a
default deploy, so without this guard they are SSRF primitives against the cloud
metadata service and everything on the compose network.

Two rules, both enforced per-hop:

  1. HTTPS only — a plaintext fetch on a hostile network is a free MITM.
  2. The resolved address must be globally routable — rejects 127.0.0.1,
     169.254.169.254, and every RFC1918 range, which covers the metadata
     endpoint and every sibling container on careeros-net.

Rule 2 must run *after* DNS resolution and *per redirect hop*, because
`follow_redirects=True` means the first URL is not the only URL we connect to:
an attacker's public domain can 302 to `http://169.254.169.254/`.

Residual race: DNS can return a public IP at check time and a private one at
connect time (DNS rebinding). Closing that needs a pinned-IP transport, which is
a much larger change than this codebase warrants today.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger("careeros.netguard")

# Hostnames that resolve to loopback/private by design, so checking them is
# redundant — but they are cheap to name and make the intent obvious.
_BLOCKED_HOSTNAMES = frozenset({
    "localhost",
    "metadata.google.internal",
    "metadata",
    "instance-data",
})


class BlockedTarget(ValueError):
    """Raised when a URL points somewhere we refuse to connect."""


def assert_public_https(url: str) -> None:
    """Raise BlockedTarget unless `url` is HTTPS and resolves to a public IP."""
    try:
        parsed = urlparse(url)
    except ValueError as exc:
        raise BlockedTarget(f"unparseable url: {exc}") from exc

    if parsed.scheme != "https":
        raise BlockedTarget(f"refusing non-https scheme {parsed.scheme!r}")

    host = parsed.hostname
    if not host:
        raise BlockedTarget("url has no host")
    if host.lower() in _BLOCKED_HOSTNAMES:
        raise BlockedTarget(f"refusing blocked hostname {host!r}")

    port = parsed.port or 443

    # An IP literal is checked directly, with no DNS. Two reasons: it saves a
    # resolver round-trip on every such target, and it cannot be talked into
    # looking public by a resolver that answers differently for the same name.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not literal.is_global:
            raise BlockedTarget(f"refusing non-public address {literal}")
        return

    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise BlockedTarget(f"cannot resolve {host!r}: {exc}") from exc

    if not infos:
        raise BlockedTarget(f"cannot resolve {host!r}")

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            # getaddrinfo can return a scoped/odd literal; refuse rather than guess.
            raise BlockedTarget(f"unparseable address {addr!r} for {host!r}") from None
        if not ip.is_global:
            raise BlockedTarget(f"refusing non-public address {ip} for {host!r}")


def is_public_https(url: str) -> bool:
    """Boolean form of assert_public_https, for callers that treat a refusal
    as a soft miss rather than an error."""
    try:
        assert_public_https(url)
        return True
    except BlockedTarget as exc:
        logger.warning("blocked outbound target %s: %s", _safe(url), exc)
        return False


def _safe(url: str) -> str:
    """Strip credentials and query strings before a URL reaches a log line."""
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    return f"{parsed.scheme}://{host}{parsed.path[:80]}"