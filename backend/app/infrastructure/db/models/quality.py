"""统一质量引擎 ORM 模型（N4-004 / V31-030）。

``quality_findings`` 只追加：一次质量运行的结论明细是不可变事实。

``quality_runs`` 的**结论**同样只追加——"改结论"是重跑新 run（不引入
``version`` / ``updated_at``）；但 **生命周期**（排队 → 执行 → 成功/失败 →
重投/取消）会推进同一行，因此该行在成功前是可变的，由 ``lifecycle`` +
``attempt`` 描述。两个事实分列：``lifecycle`` 说"走到哪一步"，``status``
说"结论是什么"，只有 ``SUCCEEDED`` 有结论。

项目隔离在数据库层兜底：findings 到 runs 的复合外键带 ``project_id``，
跨项目拼 ID 直接失败，不依赖应用层自觉。``subject_id`` 是多态引用
（Episode / 导入任务 / 数据集版本），因此不建单表外键；可见性由仓储层
经 ``ProjectScope`` 与对应对象仓储双重校验。
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import JSON

from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualityRunLifecycle,
    QualityRunStatus,
    QualitySeverity,
    QualitySubjectType,
)
from app.infrastructure.db.base import Base

_JSON_TYPE = JSON().with_variant(JSONB, "postgresql")
_SHA_CHECK = "length({column}) = 71 AND substr({column}, 1, 7) = 'sha256:'"
_SCORE_CHECK = "score IS NULL OR (score >= 0 AND score <= 1)"
#: 结论只对 ``SUCCEEDED`` 存在；未完成的行 ``status`` 必须为空。
_STATUS_CHECK = (
    f"status IS NULL OR status IN ({', '.join(repr(i.value) for i in QualityRunStatus)})"
)
#: 未完成的行可以不写时间；写了就必须有终点不早于起点。
_FINISHED_CHECK = "finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at"
#: 执行错误信息成对出现，避免只有错误码没有说明（或反之）。
_ERROR_PAIR_CHECK = (
    "(error_code IS NULL AND error_summary IS NULL)"
    " OR (error_code IS NOT NULL AND error_summary IS NOT NULL)"
)
#: 生命周期决定哪些列必须有值。把这条不变量放在数据库，任何绕过领域层
#: 的写入都写不出"QUEUED 却有结论"的行。
_LIFECYCLE_FIELDS_CHECK = (
    "(lifecycle = 'QUEUED'"
    " AND status IS NULL AND started_at IS NULL"
    " AND finished_at IS NULL AND error_code IS NULL)"
    " OR (lifecycle IN ('RUNNING', 'RETRYING')"
    " AND status IS NULL AND started_at IS NOT NULL AND finished_at IS NULL)"
    " OR (lifecycle = 'SUCCEEDED'"
    " AND status IS NOT NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL"
    " AND error_code IS NULL)"
    " OR (lifecycle = 'FAILED'"
    " AND status IS NULL AND started_at IS NOT NULL AND finished_at IS NOT NULL"
    " AND error_code IS NOT NULL)"
    # 取消可能发生在重投之后：保留上一次失败原因（成对约束仍由
    # error_fields_paired 兜底），不强制清空。
    " OR (lifecycle = 'CANCELLED'"
    " AND status IS NULL AND finished_at IS NOT NULL)"
)


def _values(enum_type: type) -> str:
    return ", ".join(repr(item.value) for item in enum_type)


class QualityRunModel(Base):
    __tablename__ = "quality_runs"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    subject_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    rule_set_version: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False)
    warn_count: Mapped[int] = mapped_column(Integer, nullable=False)
    category_summary: Mapped[dict[str, dict[str, object]]] = mapped_column(
        _JSON_TYPE,
        nullable=False,
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 生命周期与结论正交：``lifecycle`` 说"走到哪一步"，``status`` 说"结论是什么"。
    # 默认 ``SUCCEEDED`` 兼容 0070 之前的写入方（它们落库的都是已算完的结论）。
    lifecycle: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=QualityRunLifecycle.SUCCEEDED.value,
        server_default=text("'SUCCEEDED'"),
    )
    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
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
        UniqueConstraint("project_id", "id", name="uq_quality_runs_project_id_id"),
        CheckConstraint(
            f"subject_type IN ({_values(QualitySubjectType)})",
            name="subject_type_allowed",
        ),
        CheckConstraint(_STATUS_CHECK, name="status_allowed"),
        CheckConstraint(
            f"lifecycle IN ({_values(QualityRunLifecycle)})",
            name="lifecycle_allowed",
        ),
        CheckConstraint(
            _SHA_CHECK.format(column="rule_set_version"),
            name="rule_set_version_format",
        ),
        CheckConstraint("finding_count >= 0", name="finding_count_non_negative"),
        CheckConstraint("error_count >= 0", name="error_count_non_negative"),
        CheckConstraint("warn_count >= 0", name="warn_count_non_negative"),
        CheckConstraint(
            "error_count + warn_count <= finding_count",
            name="fail_counts_within_findings",
        ),
        CheckConstraint(_SCORE_CHECK, name="score_range"),
        CheckConstraint(_FINISHED_CHECK, name="finished_after_started"),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint("max_attempts >= 1", name="max_attempts_positive"),
        CheckConstraint("attempt <= max_attempts", name="attempt_within_max"),
        CheckConstraint(_ERROR_PAIR_CHECK, name="error_fields_paired"),
        CheckConstraint(_LIFECYCLE_FIELDS_CHECK, name="lifecycle_fields_consistent"),
        Index(
            "ix_quality_runs_project_subject_created",
            "project_id",
            "subject_type",
            "subject_id",
            "created_at",
        ),
        Index("ix_quality_runs_project_created", "project_id", "created_at"),
    )


class QualityFindingModel(Base):
    __tablename__ = "quality_findings"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    project_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(Uuid, nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)
    rule_id: Mapped[str] = mapped_column(String(64), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    # 同一规则作用于多个对象时区分对象；空字符串表示对象级单条结论。
    subject_ref: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        server_default=text("''"),
    )
    message: Mapped[str] = mapped_column(String(500), nullable=False)
    evidence: Mapped[dict[str, object] | None] = mapped_column(_JSON_TYPE, nullable=True)
    # NULL 表示"没有结论"（NOT_APPLICABLE / ERROR），不能当作 0 或 1 参与平均。
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
    )

    __table_args__ = (
        UniqueConstraint("project_id", "id", name="uq_quality_findings_project_id_id"),
        UniqueConstraint(
            "project_id",
            "run_id",
            "subject_ref",
            "category",
            "rule_id",
            name="uq_quality_findings_run_subject_rule",
        ),
        ForeignKeyConstraint(
            ["project_id", "run_id"],
            ["quality_runs.project_id", "quality_runs.id"],
            name="fk_quality_findings_project_run",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            f"category IN ({_values(QualityCategory)})",
            name="category_allowed",
        ),
        # OVERALL 是运行级派生类别，不得落成单条 finding。
        CheckConstraint("category <> 'OVERALL'", name="category_not_overall"),
        CheckConstraint(f"severity IN ({_values(QualitySeverity)})", name="severity_allowed"),
        CheckConstraint(
            f"status IN ({_values(QualityFindingStatus)})",
            name="status_allowed",
        ),
        CheckConstraint(_SCORE_CHECK, name="score_range"),
        CheckConstraint(
            "(status IN ('NOT_APPLICABLE', 'ERROR') AND score IS NULL) OR "
            "(status IN ('PASS', 'FAIL') AND score IS NOT NULL)",
            name="score_matches_status",
        ),
        Index(
            "ix_quality_findings_project_run_category",
            "project_id",
            "run_id",
            "category",
        ),
        Index(
            "ix_quality_findings_project_run_status",
            "project_id",
            "run_id",
            "status",
        ),
    )


__all__ = ["QualityFindingModel", "QualityRunModel"]
