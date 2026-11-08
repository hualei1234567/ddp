"""DatasetVersion Manifest。

Manifest 使用 RFC 8785 JCS 风格的规范化 JSON，服务端计算 SHA256；
``manifest_sha256`` 不在被哈希的载荷内，客户端提交的 Hash 一律忽略。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.datasets.canonical import sha256_value
from app.domain.datasets.errors import ManifestMismatchError
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.validation import (
    aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
    sha256_value as ensure_sha,
)

MANIFEST_SCHEMA_VERSION = "ddp.dataset.manifest/1.0.0"


@dataclass(frozen=True, slots=True)
class Manifest:
    dataset_version_id: UUID
    dataset_lineage_id: UUID
    dataset_version: int
    project_id: UUID
    created_by: UUID
    created_at: datetime
    policy_refs: dict[str, dict[str, object]]
    selection_rule_version: str
    source_snapshot_at: datetime
    items: tuple[DatasetItem, ...]
    split_counts: dict[str, int]
    quality_report_sha256: str
    leakage_report_sha256: str
    annotation_schema_versions: tuple[dict[str, object], ...]
    episode_version_digest: str
    annotation_submission_refs_digest: str | None = None
    manifest_key: str | None = None
    size_bytes: int | None = None
    manifest_sha256: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "dataset_version_id",
            "dataset_lineage_id",
            "project_id",
            "created_by",
        ):
            required_uuid(getattr(self, name), name)
        object.__setattr__(self, "dataset_version", positive_int(self.dataset_version, "dataset_version"))
        object.__setattr__(self, "created_at", aware_datetime(self.created_at, "created_at"))
        object.__setattr__(
            self,
            "source_snapshot_at",
            aware_datetime(self.source_snapshot_at, "source_snapshot_at"),
        )
        object.__setattr__(self, "items", tuple(self.items))
        if not self.items:
            raise ValueError("Manifest items 不得为空")
        object.__setattr__(self, "split_counts", dict(self.split_counts))
        object.__setattr__(
            self,
            "quality_report_sha256",
            ensure_sha(self.quality_report_sha256, "quality_report_sha256"),
        )
        object.__setattr__(
            self,
            "leakage_report_sha256",
            ensure_sha(self.leakage_report_sha256, "leakage_report_sha256"),
        )
        object.__setattr__(
            self,
            "annotation_schema_versions",
            tuple(dict(item) for item in self.annotation_schema_versions),
        )
        object.__setattr__(
            self,
            "episode_version_digest",
            ensure_sha(self.episode_version_digest, "episode_version_digest"),
        )
        if self.annotation_submission_refs_digest is not None:
            object.__setattr__(
                self,
                "annotation_submission_refs_digest",
                ensure_sha(
                    self.annotation_submission_refs_digest,
                    "annotation_submission_refs_digest",
                ),
            )
        object.__setattr__(
            self,
            "manifest_key",
            optional_text(self.manifest_key, "manifest_key", max_length=1024),
        )
        if self.size_bytes is not None and self.size_bytes < 0:
            raise ValueError("size_bytes 必须是非负整数")
        digest = sha256_value(self._hash_payload())
        if self.manifest_sha256 is not None and ensure_sha(
            self.manifest_sha256,
            "manifest_sha256",
        ) != digest:
            raise ManifestMismatchError("manifest_sha256 与 Manifest 内容不一致")
        object.__setattr__(self, "manifest_sha256", digest)

    def _hash_payload(self) -> dict[str, object]:
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "dataset_version_id": str(self.dataset_version_id),
            "dataset_lineage_id": str(self.dataset_lineage_id),
            "dataset_version": self.dataset_version,
            "project_id": str(self.project_id),
            "created_by": str(self.created_by),
            "created_at": self.created_at.isoformat(),
            "policy_refs": self.policy_refs,
            "selection_rule_version": self.selection_rule_version,
            "source_snapshot_at": self.source_snapshot_at.isoformat(),
            "items": [
                item.to_payload()
                for item in sorted(self.items, key=lambda item: str(item.dataset_item_id))
            ],
            "split_counts": self.split_counts,
            "quality_report_sha256": self.quality_report_sha256,
            "leakage_report_sha256": self.leakage_report_sha256,
            "annotation_schema_versions": list(self.annotation_schema_versions),
            "episode_version_digest": self.episode_version_digest,
            "annotation_submission_refs_digest": self.annotation_submission_refs_digest,
            "canonicalization": "RFC8785_JCS",
        }

    def to_payload(self, *, inline: bool = True, download_url: str | None = None) -> dict[str, object]:
        payload = self._hash_payload()
        payload["manifest_sha256"] = self.manifest_sha256
        payload["manifest_key"] = self.manifest_key
        payload["size_bytes"] = self.size_bytes
        payload["inline"] = inline
        payload["download_url"] = download_url
        return payload


def build_manifest(
    version: DatasetVersion,
    items: tuple[DatasetItem, ...],
    *,
    episode_version_digest: str,
    annotation_submission_refs_digest: str | None,
    annotation_schema_versions: tuple[dict[str, object], ...],
    created_at: datetime,
) -> Manifest:
    if version.quality_report_sha256 is None or version.leakage_report_sha256 is None:
        raise ManifestMismatchError("生成 Manifest 需要质量报告与泄漏报告 Hash")
    if version.source_snapshot_at is None or version.created_by is None:
        raise ManifestMismatchError("生成 Manifest 需要快照时间与创建人")
    split_counts = {
        "train": version.train_count,
        "validation": version.validation_count,
        "test": version.test_count,
    }
    manifest = Manifest(
        dataset_version_id=version.dataset_version_id,
        dataset_lineage_id=version.dataset_lineage_id,
        dataset_version=version.dataset_version,
        project_id=version.project_id,
        created_by=version.created_by,
        created_at=created_at,
        policy_refs={
            "dedup": version.dedup_policy_ref.to_payload(),
            "split": version.split_policy_ref.to_payload(),
            "leakage": version.leakage_policy_ref.to_payload(),
            "quality": version.quality_policy_ref.to_payload(),
        },
        selection_rule_version=version.selection_rule_version,
        source_snapshot_at=version.source_snapshot_at,
        items=items,
        split_counts=split_counts,
        quality_report_sha256=version.quality_report_sha256,
        leakage_report_sha256=version.leakage_report_sha256,
        annotation_schema_versions=annotation_schema_versions,
        episode_version_digest=episode_version_digest,
        annotation_submission_refs_digest=annotation_submission_refs_digest,
        manifest_key=version.manifest_key,
    )
    if version.manifest_sha256 is not None and manifest.manifest_sha256 != version.manifest_sha256:
        raise ManifestMismatchError("Manifest Hash 与版本冻结记录不一致")
    return manifest


__all__ = ["MANIFEST_SCHEMA_VERSION", "Manifest", "build_manifest"]
