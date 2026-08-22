"""Verify the single Nginx entrypoint after `docker compose up -d`."""
from __future__ import annotations

import sys
from urllib.error import HTTPError
from urllib.request import urlopen


def request(path: str):
    return urlopen(f"http://127.0.0.1:5173{path}", timeout=10)


def main() -> int:
    checks = [("/health", 200), ("/api/health", 200), ("/api/ready", 200), ("/demo/review", 200)]
    for path, expected in checks:
        try:
            with request(path) as response:
                if response.status != expected:
                    raise RuntimeError(f"{path}: expected {expected}, got {response.status}")
                if path.startswith("/api/") and not response.headers.get("X-Request-ID"):
                    raise RuntimeError(f"{path}: missing X-Request-ID")
                if path == "/demo/review" and "no-store" not in response.headers.get("Cache-Control", ""):
                    raise RuntimeError("SPA response must not be cached")
        except HTTPError as exc:
            raise RuntimeError(f"{path}: HTTP {exc.code}") from exc
    print("Nginx single-entrypoint smoke test passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
