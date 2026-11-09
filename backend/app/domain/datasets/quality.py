"""数据集质量报告。

报告覆盖数量、划分、分布、时长/大小、覆盖度、去重、泄漏、警告与阻断项；
存在阻断项时报告状态为 FAILED，不得用于冻结。报告 Hash 由服务端复算。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.domain.datasets.canonical import sha256_value
from app.domain.datasets.enums import LeakageStatus, QualityReportStatus
from app.domain.datasets.errors import DomainValidationError
from app.domain.datasets.policies import PolicyRef
from app.domain.datasets.validation import (
    aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
    text_value,
)
from app.domain.datasets.validation import (
    sha256_value as ensure_sha,
)

_DISTRIBUTION_KEYS = frozenset({"scene", "action", "robot", "device", "label", "quality_status"})


def _utc_iso(value: datetime | None) -> str | None:
    """报告 Hash 必须与数据库时区无关：统一按 UTC 规范化后再序列化。"""
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("报告时间必须带时区")
    return value.astimezone(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class QualityReport:
    quality_report_id: UUID
    project_id: UUID
    dataset_version_id: UUID
    quality_policy_ref: PolicyRef
    candidate_count: int
    selected_count: int
    excluded_count: int
    duplicated_count: int
    split_counts: dict[str, int]
    distributions: dict[str, dict[str, int]]
    total_duration_ns: int
    total_bytes: int
    coverage: dict[str, object]
    dedup_summary: dict[str, object]
    leakage_summary: dict[str, object]
    warnings: tuple[str, ...]
    blocking_issues: tuple[str, ...]
    source_snapshot_at: datetime
    created_at: datetime
    generator_version: str = "dataset-quality/1.0.0"
    status: QualityReportStatus | str = QualityReportStatus.READY
    report_key: str | None = None
    report_sha256: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        for name in ("quality_report_id", "project_id", "dataset_version_id"):
            required_uuid(getattr(self, name), name)
        if not isinstance(self.quality_policy_ref, PolicyRef):
            raise DomainValidationError("quality_policy_ref 必须是 PolicyRef")
        for name in (
            "candidate_count",
            "selected_count",
            "excluded_count",
            "duplicated_count",
            "total_duration_ns",
            "total_bytes",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise DomainValidationError(f"{name} 必须是非负整数")
        object.__setattr__(self, "split_counts", dict(self.split_counts))
        if set(self.split_counts) != {"train", "validation", "test"}:
            raise DomainValidationError("split_counts 必须包含 train/validation/test")
        distributions = {
            key: {str(label): int(count) for label, count in values.items()}
            for key, values in self.distributions.items()
        }
        unknown = set(distributions) - _DISTRIBUTION_KEYS
        if unknown:
            raise DomainValidationError("distributions 包含未知维度")
        object.__setattr__(self, "distributions", distributions)
        object.__setattr__(self, "coverage", dict(self.coverage))
        object.__setattr__(self, "dedup_summary", dict(self.dedup_summary))
        object.__setattr__(self, "leakage_summary", dict(self.leakage_summary))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        object.__setattr__(self, "blocking_issues", tuple(self.blocking_issues))
        object.__setattr__(
            self,
            "source_snapshot_at",
            aware_datetime(self.source_snapshot_at, "source_snapshot_at"),
        )
        object.__setattr__(self, "created_at", aware_datetime(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "generator_version",
            text_value(self.generator_version, "generator_version", max_length=100),
        )
        if self.blocking_issues:
            object.__setattr__(self, "status", QualityReportStatus.FAILED)
        else:
            object.__setattr__(self, "status", QualityReportStatus.READY)
        object.__setattr__(
            self,
            "report_key",
            optional_text(self.report_key, "report_key", max_length=1024),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        digest = sha256_value(self._payload())
        if (
            self.report_sha256 is not None
            and ensure_sha(self.report_sha256, "report_sha256") != digest
        ):
            raise DomainValidationError("report_sha256 与质量报告内容不一致")
        object.__setattr__(self, "report_sha256", digest)

    @property
    def is_ready(self) -> bool:
        return self.status is QualityReportStatus.READY and not self.blocking_issues

    def _payload(self) -> dict[str, object]:
        return {
            "quality_report_id": str(self.quality_report_id),
            "project_id": str(self.project_id),
            "dataset_version_id": str(self.dataset_version_id),
            "quality_policy_ref": self.quality_policy_ref.to_payload(),
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "excluded_count": self.excluded_count,
            "duplicated_count": self.duplicated_count,
            "split_counts": self.split_counts,
            "distributions": self.distributions,
            "total_duration_ns": self.total_duration_ns,
            "total_bytes": self.total_bytes,
            "coverage": self.coverage,
            "dedup_summary": self.dedup_summary,
            "leakage_summary": self.leakage_summary,
            "warnings": list(self.warnings),
            "blocking_issues": list(self.blocking_issues),
            "source_snapshot_at": _utc_iso(self.source_snapshot_at),
            "generator_version": self.generator_version,
            "created_at": _utc_iso(self.created_at),
        }

    def to_payload(self) -> dict[str, object]:
        payload = self._payload()
        payload["status"] = self.status.value if hasattr(self.status, "value") else str(self.status)
        payload["report_key"] = self.report_key
        payload["report_sha256"] = self.report_sha256
        payload["version"] = self.version
        return payload


def leakage_summary(
    leakage_status: LeakageStatus | str | None, hard: int, soft: int, sha256: str | None
) -> dict[str, object]:
    if leakage_status is None:
        return {
            "status": LeakageStatus.UNKNOWN.value,
            "hard_violation_count": 0,
            "soft_violation_count": 0,
            "report_sha256": None,
        }
    status = (
        leakage_status
        if isinstance(leakage_status, LeakageStatus)
        else LeakageStatus(leakage_status)
    )
    return {
        "status": status.value,
        "hard_violation_count": hard,
        "soft_violation_count": soft,
        "report_sha256": sha256,
    }


__all__ = ["QualityReport", "leakage_summary"]
