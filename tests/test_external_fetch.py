import socket

import pytest

from app.core.external_fetch import ExternalUrlBlocked, validate_public_url


def test_external_fetch_blocks_internal_and_non_standard_targets():
    for url in ("http://127.0.0.1:8000/admin", "http://169.254.169.254/latest/meta-data", "file:///etc/passwd", "https://example.com:8443"):
        with pytest.raises(ExternalUrlBlocked):
            validate_public_url(url)


def test_external_fetch_allows_public_dns(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_args, **_kwargs: [(None, None, None, None, ("93.184.216.34", 0))])
    validate_public_url("https://public.example/path")
