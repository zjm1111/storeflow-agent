"""Small Prometheus text exposition without a mandatory third-party runtime."""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from threading import Lock
from time import perf_counter


class _Metric:
    def __init__(self, name: str, kind: str, description: str, labels: list[str]):
        self.name, self.kind, self.description, self.label_names = name, kind, description, labels
        self.values: dict[tuple[str, ...], float] = defaultdict(float)
        self._lock = Lock()

    def labels(self, *values: str) -> "_BoundMetric":
        return _BoundMetric(self, tuple(str(value) for value in values))

    def _format(self) -> list[str]:
        lines = [f"# HELP {self.name} {self.description}", f"# TYPE {self.name} {self.kind}"]
        with self._lock:
            for label_values, value in self.values.items():
                labels = "" if not self.label_names else "{" + ",".join(f'{key}="{item.replace(chr(34), chr(92) + chr(34))}"' for key, item in zip(self.label_names, label_values)) + "}"
                suffix = "_count" if self.kind == "histogram" else ""
                lines.append(f"{self.name}{suffix}{labels} {value}")
        return lines


class _BoundMetric:
    def __init__(self, metric: _Metric, labels: tuple[str, ...]): self.metric, self.labels_value = metric, labels
    def inc(self, value: float = 1.0) -> None:
        with self.metric._lock: self.metric.values[self.labels_value] += value
    @contextmanager
    def time(self):
        started = perf_counter()
        try: yield
        finally:
            with self.metric._lock: self.metric.values[self.labels_value] += perf_counter() - started


HTTP_REQUESTS = _Metric("supplymind_http_requests_total", "counter", "HTTP requests", ["method", "path", "status"])
HTTP_LATENCY = _Metric("supplymind_http_request_duration_seconds", "histogram", "Cumulative HTTP request duration", ["method", "path"])
EXTERNAL_FAILURES = _Metric("supplymind_external_dependency_failures_total", "counter", "External dependency failures", ["dependency", "reason"])
EXTERNAL_CALLS = _Metric("supplymind_external_dependency_duration_seconds", "histogram", "Cumulative external dependency duration", ["dependency"])


def render_metrics() -> tuple[bytes, str]:
    lines = ["# HELP supplymind_up Service process availability", "# TYPE supplymind_up gauge", "supplymind_up 1"]
    for metric in (HTTP_REQUESTS, HTTP_LATENCY, EXTERNAL_FAILURES, EXTERNAL_CALLS): lines.extend(metric._format())
    return ("\n".join(lines) + "\n").encode(), "text/plain; version=0.0.4; charset=utf-8"
