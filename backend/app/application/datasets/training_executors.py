"""MOCK / DRY_RUN 训练执行器（应用层）。

这两个执行器是**独立**的：它们不调用 ``TrainingAdapterGateway.submit``，不访问
任何外部平台，也不占用 HTTP 之外的资源。

- ``MockTrainingExecutor``：产生一条带 ``mock=true`` 标注的占位产物记录，状态
  收口为 ``SUCCEEDED``（外部 Run 状态），但 ``execution_mode=MOCK`` 且产物不可
  登记为模型版本。下游必须拒绝把它当真实训练结果。
- ``DryRunTrainingExecutor``：只做校验与计划，输出将执行的动作清单，状态收口为
  ``DRY_RUN``（演练终态），不产生任何训练产物。

幂等与边界
----------
- 执行器本身不写库；同一次提交的 ``external_run_id`` 由幂等键**确定性派生**，
  同一键重复调用得到同一个 run id。真正的幂等由用例先查幂等键、仓储唯一约束
  兜底。
- 两个执行器都是纯内存、无网络调用，因此没有 TCP 超时；输出体积/数量上限在
  领域层（``training_executors``）强制。异常不吞：校验失败直接抛
  ``DomainValidationError``。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.application.datasets.training_ports import AdapterRunRef
from app.domain.datasets.training_executors import build_dry_run_plan, build_mock_artifacts


def _deterministic_run_id(prefix: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    return f"{prefix}-{digest}"


@dataclass(frozen=True, slots=True)
class MockExecutionOutcome:
    run: AdapterRunRef
    artifacts: dict[str, object]


class MockTrainingExecutor:
    """MOCK 执行器：不训练，只产出显式标注为 mock 的占位记录。"""

    def execute(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        idempotency_key: str,
        delivery_id: UUID,
        dataset_version_id: UUID,
        now: datetime,
    ) -> MockExecutionOutcome:
        run_id = _deterministic_run_id("mock", idempotency_key)
        artifacts = build_mock_artifacts(
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            run_id=run_id,
            delivery_id=delivery_id,
            dataset_version_id=dataset_version_id,
            generated_at=now,
        )
        return MockExecutionOutcome(
            run=AdapterRunRef(external_run_id=run_id),
            artifacts=artifacts,
        )


@dataclass(frozen=True, slots=True)
class DryRunExecutionOutcome:
    run: AdapterRunRef
    plan: dict[str, object]


class DryRunTrainingExecutor:
    """DRY_RUN 执行器：只校验、只出计划，不训练、不调用外部平台。"""

    def execute(
        self,
        *,
        adapter_id: str,
        adapter_version: str,
        idempotency_key: str,
        delivery_id: UUID,
        dataset_version_id: UUID,
        delivery_status: str,
        artifact_manifest_sha256: str | None,
        aggregate_sha256: str | None,
        config: Mapping[str, object],
        capabilities: Iterable[str],
        now: datetime,
    ) -> DryRunExecutionOutcome:
        # delivery_id 保留在签名里：真实执行需要它定位交付，演练计划当前不展开
        # 这条引用，但显式传参可防止调用方误以为演练与具体交付无关。
        plan = build_dry_run_plan(
            adapter_id=adapter_id,
            adapter_version=adapter_version,
            dataset_version_id=dataset_version_id,
            delivery_status=delivery_status,
            artifact_manifest_sha256=artifact_manifest_sha256,
            aggregate_sha256=aggregate_sha256,
            config=config,
            capabilities=capabilities,
            generated_at=now,
        )
        run_id = _deterministic_run_id("dry-run", idempotency_key)
        return DryRunExecutionOutcome(
            run=AdapterRunRef(external_run_id=run_id),
            plan=plan.to_payload(),
        )


__all__ = [
    "DryRunExecutionOutcome",
    "DryRunTrainingExecutor",
    "MockExecutionOutcome",
    "MockTrainingExecutor",
]
