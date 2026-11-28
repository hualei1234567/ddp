"""V61-T3-03 ORM：``training_run_evidence`` 训练证据表。

ADR-0036 的 ``external_training_references`` 是「引用行即 run 记录」的权威
落点，但只承载引用与状态，没有 seed / checkpoint_hash / 逐步 metrics 这些
训练过程证据。本模型把 GATE-B（``scripts/train/gate_b_train_smoke.py``）这类
训练证据落成正式行，是对 ADR-0036 的**补充而非推翻**：引用行仍是 run 的权威
身份，证据行挂在引用之下，一个引用可有多条证据（smoke 多次重跑 / smoke+real）。

列口径与迁移 0088 严格一致（DDL 以迁移为准，本模型不新增约束）。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.infrastructure.db.base import Base

# JSON：SQLite 落 JSON 文本，PostgreSQL 落 JSONB（同 training.py 先例）。
_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
# 可空 JSON：None 必须存成 SQL NULL（``col IS NULL`` 判定依赖真 NULL）。
_NULLABLE_JSON_TYPE = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")

_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"

#: run_kind 允许值：SMOKE（GATE-B 冒烟）/ REAL（正式训练）。
RUN_KIND_SMOKE = "SMOKE"
RUN_KIND_REAL = "REAL"


class TrainingRunEvidenceModel(Base):
    """一次训练 run 的过程证据（GATE-B smoke 与正式训练共用）。"""

    __tablename__ = "training_run_evidence"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    # 引用行即 run 记录（ADR-0036）：证据挂在引用之下，一个引用可有多条。
    external_training_reference_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    # 可空：证据行允许不绑定具体数据集版本（如环境自检类 smoke）。
    dataset_version_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)
    # SMOKE / REAL。
    run_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    device: Mapped[str] = mapped_column(String(64), nullable=False)
    # 运行时版本留痕（torch/lerobot/驱动等），GATE-B evidence.json 的直接映射。
    runtime_versions: Mapped[dict[str, object]] = mapped_column(_JSON_TYPE, nullable=False)
    config_sha256: Mapped[str] = mapped_column(String(71), nullable=False)
    seed: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_size: Mapped[int] = mapped_column(Integer, nullable=False)
    steps: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # 可空：失败/被中断的 run 没有完成时间。
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 可空：失败 run 没有指标；NULL 表示"没有"，不是"零"。
    metrics_summary: Mapped[dict[str, object] | None] = mapped_column(
        _NULLABLE_JSON_TYPE, nullable=True
    )
    # 可空：checkpoint 引用与哈希；smoke 可能在存 checkpoint 前失败。
    checkpoint_ref: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    checkpoint_sha256: Mapped[str | None] = mapped_column(String(71), nullable=True)
    # int：0 成功、非 0（含负数，信号杀死）失败；退出码域开放，不设枚举。
    exit_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # 可空：失败 run 的 stdout 尾部留痕（有界，不存全量日志）。
    stdout_tail: Mapped[str | None] = mapped_column(String(8192), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    created_by: Mapped[UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    __table_args__ = (
        ForeignKeyConstraint(
            ["external_training_reference_id"],
            ["external_training_references.id"],
            name="fk_training_run_evidence_reference",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            name="fk_training_run_evidence_dataset_version_id_dataset_versions",
        ),
        CheckConstraint("run_kind IN ('SMOKE', 'REAL')", name="ck_training_run_evidence_run_kind"),
        CheckConstraint("steps >= 1", name="ck_training_run_evidence_steps_positive"),
        CheckConstraint("seed >= 0", name="ck_training_run_evidence_seed_non_negative"),
        CheckConstraint(
            _SHA_CHECK.format(column="config_sha256"),
            name="ck_training_run_evidence_config_sha256_format",
        ),
        CheckConstraint(
            "checkpoint_sha256 IS NULL OR (" + _SHA_CHECK.format(column="checkpoint_sha256") + ")",
            name="ck_training_run_evidence_checkpoint_sha256_format",
        ),
        Index("ix_training_run_evidence_project_created_at", "project_id", "created_at"),
        Index("ix_training_run_evidence_reference_id", "external_training_reference_id"),
    )


__all__ = ["TrainingRunEvidenceModel", "RUN_KIND_REAL", "RUN_KIND_SMOKE"]
