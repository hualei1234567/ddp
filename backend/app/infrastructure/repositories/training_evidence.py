"""``training_run_evidence`` 训练证据表的 SQLAlchemy 仓储（V61-T3-03）。

职责边界（对齐 ``SqlWebhookOutboxRepository`` 口径）：
- **写入**（:meth:`SqlTrainingEvidenceRepository.add`）：业务事务内登记一条
  训练证据，**不提交**——与训练提交/状态流转同事务，业务回滚则证据不落库；
- **读取**：项目维度分页列表（:meth:`list_for_project`，按
  ``(project_id, created_at)`` 索引键集扫描）与按引用反查
  （:meth:`find_by_reference`，ADR-0036「引用行即 run 记录」的反查面）。

证据行是 GATE-B（``scripts/train/gate_b_train_smoke.py``）这类训练产物的
正式落库面：引用行仍是 run 的权威身份，证据行挂在引用之下，一个引用可有
多条证据（smoke 多次重跑 / smoke + real）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.db.models.training_evidence import (
    TrainingRunEvidenceModel as _Model,
)


@dataclass(frozen=True, slots=True)
class TrainingRunEvidence:
    """一条训练 run 的过程证据（仓储读出面）。"""

    id: UUID
    project_id: UUID
    external_training_reference_id: UUID
    dataset_version_id: UUID | None
    run_kind: str
    device: str
    runtime_versions: dict[str, object]
    config_sha256: str
    seed: int
    batch_size: int
    steps: int
    started_at: datetime
    finished_at: datetime | None
    metrics_summary: dict[str, object] | None
    checkpoint_ref: str | None
    checkpoint_sha256: str | None
    exit_code: int
    stdout_tail: str | None
    created_at: datetime
    created_by: UUID


def _to_domain(row: _Model) -> TrainingRunEvidence:
    return TrainingRunEvidence(
        id=row.id,
        project_id=row.project_id,
        external_training_reference_id=row.external_training_reference_id,
        dataset_version_id=row.dataset_version_id,
        run_kind=row.run_kind,
        device=row.device,
        runtime_versions=dict(row.runtime_versions or {}),
        config_sha256=row.config_sha256,
        seed=row.seed,
        batch_size=row.batch_size,
        steps=row.steps,
        started_at=row.started_at,
        finished_at=row.finished_at,
        metrics_summary=dict(row.metrics_summary) if row.metrics_summary is not None else None,
        checkpoint_ref=row.checkpoint_ref,
        checkpoint_sha256=row.checkpoint_sha256,
        exit_code=row.exit_code,
        stdout_tail=row.stdout_tail,
        created_at=row.created_at,
        created_by=row.created_by,
    )


class SqlTrainingEvidenceRepository:
    """训练证据写入侧（不提交，由调用方的 UoW 统一提交）。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        *,
        project_id: UUID,
        external_training_reference_id: UUID,
        run_kind: str,
        device: str,
        runtime_versions: dict[str, object],
        config_sha256: str,
        seed: int,
        batch_size: int,
        steps: int,
        started_at: datetime,
        created_by: UUID,
        now: datetime,
        dataset_version_id: UUID | None = None,
        finished_at: datetime | None = None,
        metrics_summary: dict[str, object] | None = None,
        checkpoint_ref: str | None = None,
        checkpoint_sha256: str | None = None,
        exit_code: int = 0,
        stdout_tail: str | None = None,
    ) -> TrainingRunEvidence:
        """登记一条训练证据（GATE-B smoke 或正式训练共用）。

        ``exit_code`` 用 int：``0`` 成功、非 0（含负数，信号杀死）失败；
        ``metrics_summary`` / ``checkpoint_*`` / ``finished_at`` / ``stdout_tail``
        均可空——失败 run 没有指标与 checkpoint，NULL 表示"没有"而非"零"。
        """
        row = _Model(
            id=uuid4(),
            project_id=project_id,
            external_training_reference_id=external_training_reference_id,
            dataset_version_id=dataset_version_id,
            run_kind=run_kind,
            device=device,
            runtime_versions=dict(runtime_versions),
            config_sha256=config_sha256,
            seed=seed,
            batch_size=batch_size,
            steps=steps,
            started_at=started_at,
            finished_at=finished_at,
            metrics_summary=metrics_summary,
            checkpoint_ref=checkpoint_ref,
            checkpoint_sha256=checkpoint_sha256,
            exit_code=exit_code,
            stdout_tail=stdout_tail,
            created_at=now,
            created_by=created_by,
        )
        self._session.add(row)
        return _to_domain(row)

    def list_for_project(
        self, project_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> list[TrainingRunEvidence]:
        """项目内证据分页列表（最新在前，走 ``(project_id, created_at)`` 索引）。"""
        rows = self._session.scalars(
            select(_Model)
            .where(_Model.project_id == project_id)
            .order_by(_Model.created_at.desc(), _Model.id)
            .limit(limit)
            .offset(offset)
        ).all()
        return [_to_domain(row) for row in rows]

    def find_by_reference(self, external_training_reference_id: UUID) -> list[TrainingRunEvidence]:
        """按引用反查证据（一个引用可有多条：smoke 重跑 / smoke + real）。"""
        rows = self._session.scalars(
            select(_Model)
            .where(_Model.external_training_reference_id == external_training_reference_id)
            .order_by(_Model.created_at, _Model.id)
        ).all()
        return [_to_domain(row) for row in rows]


__all__ = ["SqlTrainingEvidenceRepository", "TrainingRunEvidence"]
