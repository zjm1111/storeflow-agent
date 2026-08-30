from app.agent.state import initial_state
from app.core.config import get_settings
from app.repositories import models
from app.worker import celery_app


def test_cleanup_removes_legacy_state_contracts_and_keeps_canonical_fields():
    state = initial_state("cleanup-test", "调查门店补货风险")
    assert "evidence_context_pack" in state
    assert "context_pack" not in state
    assert not hasattr(models, "CheckpointRecord")


def test_celery_uses_broker_only_and_ignores_result_storage():
    assert not hasattr(get_settings(), "celery_result_backend")
    if celery_app is not None:
        assert celery_app.conf.task_ignore_result is True
