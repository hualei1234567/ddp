"""N7-007：MOCK / DRY_RUN 独立执行器的领域与应用层单元测试。

覆盖：演练计划只做校验与计划、硬校验失败抛错、计划有界；MOCK 产物显式标注；
两种执行器 run id 由幂等键确定性派生（同键同 id）；领域不变量拒绝把模拟/演练
结果伪装成真实成功。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.application.datasets.training_executors import (
    DryRunTrainingExecutor,
    MockTrainingExecutor,
)
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.training_enums import ExecutionMode, ExternalTrainingStatus
from app.domain.datasets.training_executors import (
    MAX_PLAN_BYTES,
    build_dry_run_plan,
    build_mock_artifacts,
)
from app.domain.datasets.training_references import (
    ExternalTrainingReference,
    new_external_reference,
)
from app.domain.datasets.training_state_machines import (
    allowed_external_actions,
    external_target,
)

NOW = datetime(2026, 11, 19, 12, 0, tzinfo=UTC)


def _plan(**overrides: object):
    values: dict[str, object] = {
        "adapter_id": "dry-run-training",
        "adapter_version": "1.0.0",
        "dataset_version_id": uuid4(),
        "delivery_status": "DELIVERED",
        "artifact_manifest_sha256": "sha256:" + "a" * 64,
        "aggregate_sha256": "sha256:" + "b" * 64,
        "config": {"epochs": 3},
        "capabilities": {"submit", "status"},
        "generated_at": NOW,
    }
    values.update(overrides)
    return build_dry_run_plan(**values)  # type: ignore[arg-type]


def test_dry_run_plan_is_a_plan_not_a_result() -> None:
    plan = _plan().to_payload()
    assert plan["dry_run"] is True
    assert plan["artifacts_produced"] is False
    assert plan["external_platform_called"] is False
    assert plan["checks"]
    assert plan["actions"]
    # 计划里不能出现任何训练产物字段。
    for forbidden in ("model", "artifacts", "metrics", "model_ref"):
        assert forbidden not in plan


def test_dry_run_plan_is_bounded() -> None:
    import json

    encoded = json.dumps(_plan().to_payload(), ensure_ascii=False).encode("utf-8")
    assert len(encoded) <= MAX_PLAN_BYTES


@pytest.mark.parametrize(
    "overrides",
    [
        {"delivery_status": "READY"},
        {"artifact_manifest_sha256": None},
        {"aggregate_sha256": None},
        {"capabilities": {"status"}},
    ],
)
def test_dry_run_hard_checks_fail_closed(overrides: dict[str, object]) -> None:
    with pytest.raises(DomainValidationError):
        _plan(**overrides)


def test_dry_run_warns_on_empty_config_and_capabilities() -> None:
    plan = _plan(config={}, capabilities=set())
    assert plan.warnings
    statuses = {check.name: check.status for check in plan.checks}
    assert statuses["training_config"] == "WARN"
    assert statuses["adapter_capabilities"] == "WARN"


def test_mock_artifacts_are_explicitly_mock_and_not_registrable() -> None:
    artifacts = build_mock_artifacts(
        adapter_id="mock-training",
        adapter_version="1.0.0",
        run_id="mock-abc",
        delivery_id=uuid4(),
        dataset_version_id=uuid4(),
        generated_at=NOW,
    )
    assert artifacts["mock"] is True
    assert artifacts["materialized"] is False
    assert artifacts["execution_mode"] == "MOCK"
    assert "model" not in artifacts


def test_mock_executor_run_id_is_deterministic() -> None:
    executor = MockTrainingExecutor()
    kwargs = {
        "adapter_id": "mock-training",
        "adapter_version": "1.0.0",
        "idempotency_key": "same-key-12345678",
        "delivery_id": uuid4(),
        "dataset_version_id": uuid4(),
        "now": NOW,
    }
    first = executor.execute(**kwargs)
    second = executor.execute(**kwargs)
    assert first.run.external_run_id == second.run.external_run_id
    other = executor.execute(**{**kwargs, "idempotency_key": "other-key-12345678"})
    assert other.run.external_run_id != first.run.external_run_id


def test_dry_run_executor_is_deterministic_and_propagates_validation_error() -> None:
    executor = DryRunTrainingExecutor()
    kwargs = {
        "adapter_id": "dry-run-training",
        "adapter_version": "1.0.0",
        "idempotency_key": "same-key-12345678",
        "delivery_id": uuid4(),
        "dataset_version_id": uuid4(),
        "delivery_status": "DELIVERED",
        "artifact_manifest_sha256": "sha256:" + "a" * 64,
        "aggregate_sha256": "sha256:" + "b" * 64,
        "config": {"epochs": 1},
        "capabilities": {"submit"},
        "now": NOW,
    }
    first = executor.execute(**kwargs)
    second = executor.execute(**kwargs)
    assert first.run.external_run_id == second.run.external_run_id
    assert first.plan == second.plan
    with pytest.raises(DomainValidationError):
        executor.execute(**{**kwargs, "delivery_status": "READY"})


def test_dry_run_is_a_terminal_state() -> None:
    assert external_target(ExternalTrainingStatus.SUBMITTED, "system-dry-run") is (
        ExternalTrainingStatus.DRY_RUN
    )
    assert allowed_external_actions(ExternalTrainingStatus.DRY_RUN) == frozenset()
    assert (
        external_target(ExternalTrainingStatus.SUBMITTED, "system-succeed")
        is ExternalTrainingStatus.SUCCEEDED
    )


def _reference(mode: ExecutionMode) -> ExternalTrainingReference:
    return new_external_reference(
        external_ref_id=uuid4(),
        project_id=uuid4(),
        adapter_id="adapter",
        adapter_version="1.0.0",
        training_delivery_id=uuid4(),
        training_request_revision_id=uuid4(),
        dataset_version_id=uuid4(),
        external_run_id="run-1",
        external_run_url=None,
        correlation_id="corr-1",
        idempotency_key="idem-12345678",
        execution_mode=mode,
        model_ref=None,
        evaluation_ref=None,
        actor_id=uuid4(),
        now=NOW,
    )


def test_legacy_mock_reference_without_label_still_readable() -> None:
    """存量 MOCK + SUCCEEDED 记录可能没有 mock 标注，必须仍能读出（不 500）。"""
    reference = _reference(ExecutionMode.MOCK)
    legacy = reference.apply_status(
        ExternalTrainingStatus.SUCCEEDED,
        now=NOW,
        actor_id=uuid4(),
        artifacts={"model": {"object_key": "legacy/model.bin"}},
    )
    assert legacy.effective_status is ExternalTrainingStatus.SUCCEEDED
    assert "mock" not in (legacy.artifacts or {})


def test_dry_run_status_requires_dry_run_mode() -> None:
    reference = _reference(ExecutionMode.LOCAL)
    with pytest.raises(DomainValidationError):
        reference.apply_status(ExternalTrainingStatus.DRY_RUN, now=NOW, actor_id=uuid4())


def test_dry_run_plan_only_on_dry_run_mode() -> None:
    with pytest.raises(DomainValidationError):
        new_external_reference(
            external_ref_id=uuid4(),
            project_id=uuid4(),
            adapter_id="adapter",
            adapter_version="1.0.0",
            training_delivery_id=uuid4(),
            training_request_revision_id=uuid4(),
            dataset_version_id=uuid4(),
            external_run_id="run-1",
            external_run_url=None,
            correlation_id="corr-1",
            idempotency_key="idem-12345678",
            execution_mode=ExecutionMode.LOCAL,
            model_ref=None,
            evaluation_ref=None,
            actor_id=uuid4(),
            now=NOW,
            dry_run_plan={"dry_run": True},
        )
