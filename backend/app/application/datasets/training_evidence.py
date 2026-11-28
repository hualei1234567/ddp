"""训练过程证据（``training_run_evidence``）应用层用例。

遵循 ADR-0036 与迁移 0088：
- 外部训练引用（``external_training_references``）仍是 run 的权威身份；
- 证据行挂在外部训练引用之下，一个引用允许多条证据（smoke 重跑 / smoke + real）；
- 严格校验 ``ProjectScope`` 项目隔离：跨项目引用或数据集版本一律返回 404，不泄露存在性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.application.datasets.ports import DatasetVersionRepository, UnitOfWork
from app.application.datasets.training_ports import (
    ExternalReferenceRepository,
    TrainingDeliveryRepository,
)
from app.domain.datasets.errors import DomainValidationError, ResourceNotFoundError
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.training_evidence import RUN_KIND_REAL, RUN_KIND_SMOKE
from app.infrastructure.repositories.training_evidence import (
    SqlTrainingEvidenceRepository,
    TrainingRunEvidence,
)

_ALLOWED_RUN_KINDS = frozenset({RUN_KIND_SMOKE, RUN_KIND_REAL})
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class NewTrainingRunEvidence:
    """登记一条训练过程证据的输入命令。"""

    run_kind: str
    device: str
    runtime_versions: dict[str, object]
    config_sha256: str
    seed: int
    batch_size: int
    steps: int
    started_at: datetime
    dataset_version_id: UUID | None = None
    finished_at: datetime | None = None
    metrics_summary: dict[str, object] | None = None
    checkpoint_ref: str | None = None
    checkpoint_sha256: str | None = None
    exit_code: int = 0
    stdout_tail: str | None = None


class TrainingEvidenceUseCases:
    """训练证据的写入与查询用例。"""

    def __init__(
        self,
        *,
        deliveries: TrainingDeliveryRepository,
        references: ExternalReferenceRepository,
        versions: DatasetVersionRepository,
        evidence: SqlTrainingEvidenceRepository,
        uow: UnitOfWork,
    ) -> None:
        self._deliveries = deliveries
        self._references = references
        self._versions = versions
        self._evidence = evidence
        self._uow = uow

    def record(
        self,
        scope: ProjectScope,
        external_ref_id: UUID,
        command: NewTrainingRunEvidence,
    ) -> TrainingRunEvidence:
        """为指定外部训练引用登记一条训练证据。"""
        reference = self._references.get(scope, external_ref_id)
        if reference is None:
            raise ResourceNotFoundError("外部训练引用", external_ref_id)

        dataset_version_id = command.dataset_version_id
        if dataset_version_id is not None:
            if self._versions.get(scope, dataset_version_id) is None:
                raise ResourceNotFoundError("数据集版本", dataset_version_id)
        else:
            dataset_version_id = reference.dataset_version_id

        self._validate_command(command)
        started_at = _as_utc(command.started_at)
        finished_at = _as_utc(command.finished_at) if command.finished_at is not None else None
        if finished_at is not None and finished_at < started_at:
            raise DomainValidationError("finished_at 不得早于 started_at")

        created = self._evidence.add(
            project_id=scope.project_id,
            external_training_reference_id=reference.external_ref_id,
            dataset_version_id=dataset_version_id,
            run_kind=command.run_kind,
            device=command.device.strip(),
            runtime_versions=dict(command.runtime_versions),
            config_sha256=command.config_sha256,
            seed=command.seed,
            batch_size=command.batch_size,
            steps=command.steps,
            started_at=started_at,
            finished_at=finished_at,
            metrics_summary=(
                dict(command.metrics_summary) if command.metrics_summary is not None else None
            ),
            checkpoint_ref=command.checkpoint_ref,
            checkpoint_sha256=command.checkpoint_sha256,
            exit_code=command.exit_code,
            stdout_tail=command.stdout_tail,
            created_by=scope.actor_id,
            now=_utcnow(),
        )
        self._uow.commit()
        return created

    def list_for_reference(
        self,
        scope: ProjectScope,
        external_ref_id: UUID,
    ) -> list[TrainingRunEvidence]:
        """按外部训练引用查询其下全部训练证据。"""
        if self._references.get(scope, external_ref_id) is None:
            raise ResourceNotFoundError("外部训练引用", external_ref_id)
        return self._evidence.find_by_reference(external_ref_id, project_id=scope.project_id)

    def list_for_delivery(
        self,
        scope: ProjectScope,
        delivery_id: UUID,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> list[TrainingRunEvidence]:
        """按训练交付查询其下全部外部训练引用的证据。"""
        if self._deliveries.get(scope, delivery_id) is None:
            raise ResourceNotFoundError("训练交付", delivery_id)
        return self._evidence.list_for_delivery(
            scope.project_id,
            delivery_id,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _validate_command(command: NewTrainingRunEvidence) -> None:
        if command.run_kind not in _ALLOWED_RUN_KINDS:
            raise DomainValidationError("run_kind 必须为 SMOKE 或 REAL")
        if not command.device.strip():
            raise DomainValidationError("device 不能为空")
        if command.steps < 1:
            raise DomainValidationError("steps 必须大于等于 1")
        if command.batch_size < 1:
            raise DomainValidationError("batch_size 必须大于等于 1")
        if command.seed < 0:
            raise DomainValidationError("seed 必须大于等于 0")
        if not _SHA256_RE.match(command.config_sha256):
            raise DomainValidationError("config_sha256 格式必须为 sha256:<64位小写hex>")
        if command.checkpoint_sha256 is not None and not _SHA256_RE.match(
            command.checkpoint_sha256
        ):
            raise DomainValidationError("checkpoint_sha256 格式必须为 sha256:<64位小写hex>")


__all__ = ["NewTrainingRunEvidence", "TrainingEvidenceUseCases"]
