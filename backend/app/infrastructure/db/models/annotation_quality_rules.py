"""质量规则执行记录 ORM 模型。

两张表都**只追加**，不参与乐观锁：一次质量检查是不可变的事实记录，
"改结论"的正确做法是重跑一次（新 run），而不是编辑旧 run。因此这里不混入
``AnnotationAuditMixin``（它带 ``version`` / ``updated_at`` / ``updated_by``，
语义上会暗示"可更新"）。

项目隔离按既有范式用复合外键兜底：``findings`` 到 ``runs`` 与
``annotation_records`` 的外键都带 ``project_id``，跨项目拼 ID 在数据库层直接
被拒，不依赖应用层自觉。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.annotation.enums import (
    QualityCheckStatus,
    QualityRuleSeverity,
)
from app.infrastructure.db.base import Base


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class AnnotationQualityRun(Base):
    __tablename__ = "annotation_quality_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    submission_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    rule_set_version: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warn_count: Mapped[int] = mapped_column(Integer, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("users.id"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_annotation_quality_runs_project_id_id",
        ),
        ForeignKeyConstraint(
            ["project_id", "submission_id"],
            ["annotation_submissions.project_id", "annotation_submissions.id"],
            name="fk_annotation_quality_runs_project_submission",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"status IN ({_values(QualityCheckStatus)})",
            name="status_allowed",
        ),
        CheckConstraint("error_count >= 0", name="error_count_non_negative"),
        CheckConstraint("warn_count >= 0", name="warn_count_non_negative"),
        CheckConstraint("finished_at >= started_at", name="finished_after_started"),
        CheckConstraint(
            "length(rule_set_version) = 71 AND substr(rule_set_version, 1, 7) = 'sha256:'",
            name="rule_set_version_format",
        ),
        Index(
            "ix_annotation_quality_runs_project_submission_created",
            "project_id",
            "submission_id",
            "created_at",
        ),
    )


class AnnotationQualityFinding(Base):
    __tablename__ = "annotation_quality_findings"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    annotation_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    # NULL 表示"该规则对这条记录没有结论"（缺边界/缺阈值），与"判定通过"不同。
    # 用可空布尔而不是塞一个 NOT_APPLICABLE 字符串：布尔列能直接聚合，
    # 而三值字符串会让每次统计都要写 CASE。
    passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    message: Mapped[str] = mapped_column(String(500), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "id",
            name="uq_annotation_quality_findings_project_id_id",
        ),
        UniqueConstraint(
            "project_id",
            "run_id",
            "annotation_id",
            "rule_id",
            name="uq_annotation_quality_findings_run_record_rule",
        ),
        ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["annotation_quality_runs.project_id", "annotation_quality_runs.id"],
            name="fk_annotation_quality_findings_project_run",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["project_id", "annotation_id"],
            ["annotation_records.project_id", "annotation_records.id"],
            name="fk_annotation_quality_findings_project_annotation",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            f"severity IN ({_values(QualityRuleSeverity)})",
            name="severity_allowed",
        ),
        CheckConstraint("rule_version >= 1", name="rule_version_positive"),
        Index(
            "ix_annotation_quality_findings_project_run",
            "project_id",
            "run_id",
        ),
        Index(
            "ix_annotation_quality_findings_project_annotation",
            "project_id",
            "annotation_id",
        ),
    )


__all__ = ["AnnotationQualityFinding", "AnnotationQualityRun"]
