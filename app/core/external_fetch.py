"""Bounded public-web fetching for evidence retrieval.

Search results are untrusted data.  This module blocks non-public targets,
redirect chains and oversized bodies before HTML/PDF parsing ever starts.
"""
from __future__ import annotations

import ipaddress
import socket
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

MAX_EXTERNAL_RESPONSE_BYTES = 5 * 1024 * 1024


class ExternalUrlBlocked(ValueError):
    pass


def validate_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise ExternalUrlBlocked("only credential-free HTTP(S) URLs are allowed")
    if parsed.port not in {None, 80, 443}:
        raise ExternalUrlBlocked("external URL port is not allowed")
    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".local", ".internal")):
        raise ExternalUrlBlocked("local hostname is not allowed")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)}
    except socket.gaierror as exc:
        raise ExternalUrlBlocked("external hostname cannot be resolved") from exc
    if not addresses:
        raise ExternalUrlBlocked("external hostname has no addresses")
    for value in addresses:
        address = ipaddress.ip_address(value)
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved or address.is_unspecified or address.is_multicast:
            raise ExternalUrlBlocked("external hostname resolves to a non-public address")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


def read_public_url(url: str, *, timeout: int = 10, max_bytes: int = MAX_EXTERNAL_RESPONSE_BYTES) -> bytes:
    validate_public_url(url)
    request = Request(url, headers={"User-Agent": "StoreFlow/1.0"})
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            length = response.headers.get("Content-Length")
            if length and int(length) > max_bytes:
                raise ExternalUrlBlocked("external response exceeds size limit")
            body = response.read(max_bytes + 1)
    except HTTPError as exc:
        if 300 <= exc.code < 400:
            raise ExternalUrlBlocked("external redirects are not followed") from exc
        raise
    if len(body) > max_bytes:
        raise ExternalUrlBlocked("external response exceeds size limit")
    return body
