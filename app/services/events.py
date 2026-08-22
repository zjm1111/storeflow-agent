import json
from collections import defaultdict
from datetime import datetime, timezone
from queue import Empty, Queue
from threading import Lock
from uuid import uuid4

from redis import Redis

from app.core import get_settings


class TaskEventBroker:
    """Redis Streams-backed SSE broker with an in-process no-Redis fallback."""
    def __init__(self):
        self._history: dict[str, list[dict]] = defaultdict(list)
        self._subscribers: dict[str, list[Queue]] = defaultdict(list)
        self._lock = Lock()
        self._redis = Redis.from_url(get_settings().redis_url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        try:
            self._redis.ping()
            self._redis_available = True
        except Exception:
            self._redis_available = False

    @staticmethod
    def _stream(task_id: str) -> str:
        return f"supplymind:task-events:{task_id}"

    def publish(self, task_id: str, event_type: str, payload: dict) -> None:
        event = {"id": str(uuid4()), "type": event_type, "timestamp": datetime.now(timezone.utc).isoformat(), "payload": payload}
        persisted = False
        try:
            if not self._redis_available:
                raise RuntimeError("Redis unavailable")
            stream_id = self._redis.xadd(self._stream(task_id), {"type": event_type, "timestamp": event["timestamp"], "payload": json.dumps(payload, ensure_ascii=False)}, maxlen=1000, approximate=True)
            event["id"] = stream_id
            persisted = True
        except Exception:
            self._redis_available = False
        # Test/no-Redis mode remains deterministic, but a successful Redis
        # append is the sole source of truth in Compose deployments.
        if persisted:
            return
        with self._lock:
            self._history[task_id].append(event)
            for subscriber in self._subscribers[task_id]:
                subscriber.put(event)

    def history(self, task_id: str, after_id: str | None = None) -> list[dict]:
        try:
            if not self._redis_available:
                raise RuntimeError("Redis unavailable")
            entries = self._redis.xrange(self._stream(task_id), min=f"({after_id}" if after_id else "-", max="+")
            return [{"id": stream_id, "type": values["type"], "timestamp": values["timestamp"], "payload": json.loads(values["payload"])} for stream_id, values in entries]
        except Exception:
            self._redis_available = False
            pass
        with self._lock:
            events = list(self._history[task_id])
        if not after_id:
            return events
        for index, event in enumerate(events):
            if event["id"] == after_id:
                return events[index + 1:]
        return events

    @property
    def persistence_mode(self) -> str:
        return "redis_streams" if self._redis_available else "in_memory_fallback"

    def subscribe(self, task_id: str) -> Queue:
        subscriber: Queue = Queue()
        with self._lock:
            self._subscribers[task_id].append(subscriber)
        return subscriber

    def unsubscribe(self, task_id: str, subscriber: Queue) -> None:
        with self._lock:
            if subscriber in self._subscribers[task_id]:
                self._subscribers[task_id].remove(subscriber)

    @staticmethod
    def encode(event: dict) -> str:
        return f"id: {event['id']}\nevent: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"

    @staticmethod
    def take(subscriber: Queue, timeout: float = 15.0) -> dict | None:
        try:
            return subscriber.get(timeout=timeout)
        except Empty:
            return None
