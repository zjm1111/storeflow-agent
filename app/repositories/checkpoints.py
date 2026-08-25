"""Deprecated compatibility import for the renamed task snapshot history."""
from app.repositories.task_snapshot_history import TaskSnapshotHistoryRepository

# External callers written before the architecture convergence can still
# import this name. New application code must use TaskSnapshotHistoryRepository
# and its explicit record_snapshot/latest_snapshot methods.
CheckpointRepository = TaskSnapshotHistoryRepository

__all__ = ["CheckpointRepository", "TaskSnapshotHistoryRepository"]
