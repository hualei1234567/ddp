"""LeRobot 元数据导出产物生成（确定性、无帧级映射）。

本批只生成 ``ddp.lerobot/1.0.0`` 的元数据与 DDP sidecar：
- meta/info.json
- meta/episodes.jsonl
- meta/ddp_divisions.json
- ddp/sidecar.json

未实现真实 LeRobot 帧级映射、视频/observation/action 对齐与 Loader 兼容性验证；
因此 ``verification_status`` 固定 ``UNVERIFIED``，不得宣称已兼容 LeRobot 生态。
所有输出按规范 JSON 生成，相同输入必然得到相同文件清单与 Hash。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.domain.datasets.canonical import canonical_json, plain, sha256_text
from app.domain.datasets.export_jobs import ExportJob
from app.domain.datasets.models import DatasetItem, DatasetVersion

JSON_CONTENT_TYPE = "application/json"
NDJSON_CONTENT_TYPE = "application/x-ndjson"


@dataclass(frozen=True, slots=True)
class ExportArtifactSpec:
    path: str
    data: bytes
    content_type: str
    episode_refs: tuple[UUID, ...] = ()
    sidecar: bool = False
    time_range: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.path or self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError("产物路径非法")
        if "\\" in self.path:
            raise ValueError("产物路径不得包含反斜杠")
        object.__setattr__(self, "episode_refs", tuple(dict.fromkeys(self.episode_refs)))
        object.__setattr__(
            self,
            "time_range",
            dict(self.time_range) if self.time_range is not None else None,
        )

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def sha256(self) -> str:
        return sha256_text(self.data.decode("utf-8"))

    def to_entry(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "content_type": self.content_type,
            "episode_refs": [str(item) for item in self.episode_refs],
            "time_range": self.time_range,
            "sidecar": self.sidecar,
        }


def _json_bytes(payload: Mapping[str, object] | list[object]) -> bytes:
    return canonical_json(payload).encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    return "".join(canonical_json(plain(row)) + "\n" for row in rows).encode("utf-8")


def build_export_artifacts(
    job: ExportJob,
    version: DatasetVersion,
    items: Iterable[DatasetItem],
    *,
    created_at: datetime,
) -> tuple[ExportArtifactSpec, ...]:
    """生成确定性的元数据产物；调用方负责写入对象存储并回读校验。"""
    ordered = tuple(sorted(items, key=lambda item: (str(item.episode_id), item.item_sha256 or "")))
    if not ordered:
        raise ValueError("导出产物不得为空")
    episode_refs = tuple(item.episode_id for item in ordered)
    split_members: dict[str, list[str]] = {"train": [], "validation": [], "test": []}
    for item in ordered:
        split = item.split.value if hasattr(item.split, "value") else str(item.split)
        split_members.setdefault(split.lower(), []).append(str(item.episode_id))
    for values in split_members.values():
        values.sort()

    info = {
        "format": job.format.value if hasattr(job.format, "value") else str(job.format),
        "format_version": job.format_version,
        "dataset_version_id": str(version.dataset_version_id),
        "dataset_version": version.dataset_version,
        "manifest_sha256": version.manifest_sha256,
        "item_count": len(ordered),
        "split_counts": {
            "train": version.train_count,
            "validation": version.validation_count,
            "test": version.test_count,
        },
        "total_duration_ns": version.total_duration_ns,
        "canonicalization": "RFC8785_JCS",
        "frame_level_mapping": False,
        "verification_status": "UNVERIFIED",
        "created_at": created_at.isoformat(),
    }
    episode_rows = []
    for item in ordered:
        episode_rows.append(
            {
                "episode_id": str(item.episode_id),
                "episode_version": item.episode_version,
                "episode_lineage_id": str(item.episode_lineage_id),
                "split": item.split.value if hasattr(item.split, "value") else str(item.split),
                "sample_role": (
                    item.sample_role.value
                    if hasattr(item.sample_role, "value")
                    else str(item.sample_role)
                ),
                "weight": item.weight,
                "manifest_sha256": item.manifest_sha256,
                "annotation_submission_id": str(item.annotation_submission_id),
                "annotation_version": item.annotation_version,
                "schema_id": str(item.schema_id),
                "schema_version": item.schema_version,
                "item_sha256": item.item_sha256,
                "time_range": plain(item.time_range),
            }
        )
    divisions = {
        "dataset_version_id": str(version.dataset_version_id),
        "divisions": split_members,
        "counts": {
            "train": len(split_members.get("train", [])),
            "validation": len(split_members.get("validation", [])),
            "test": len(split_members.get("test", [])),
        },
    }
    sidecar = {
        "schema_version": "ddp.export.sidecar/1.0.0",
        "export_job_id": str(job.export_job_id),
        "dataset_version_id": str(version.dataset_version_id),
        "dataset_version": version.dataset_version,
        "project_id": str(version.project_id),
        "manifest_sha256": version.manifest_sha256,
        "quality_report_sha256": version.quality_report_sha256,
        "leakage_report_sha256": version.leakage_report_sha256,
        "episode_version_digest": version.episode_version_digest,
        "annotation_submission_refs_digest": version.annotation_submission_refs_digest,
        "policy_refs": {
            "dedup": version.dedup_policy_ref.to_payload(),
            "split": version.split_policy_ref.to_payload(),
            "leakage": version.leakage_policy_ref.to_payload(),
            "quality": version.quality_policy_ref.to_payload(),
        },
        "exporter_id": job.exporter_id,
        "exporter_version": job.exporter_version,
        "mapping_version": job.mapping_version,
        "config_sha256": job.config_sha256,
        "frame_level_mapping": False,
        "verification_status": "UNVERIFIED",
        "created_at": created_at.isoformat(),
    }
    artifacts = (
        ExportArtifactSpec(
            path="meta/info.json",
            data=_json_bytes(info),
            content_type=JSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
        ExportArtifactSpec(
            path="meta/episodes.jsonl",
            data=_jsonl_bytes(episode_rows),
            content_type=NDJSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
        ExportArtifactSpec(
            path="meta/ddp_divisions.json",
            data=_json_bytes(divisions),
            content_type=JSON_CONTENT_TYPE,
            episode_refs=episode_refs,
        ),
        ExportArtifactSpec(
            path="ddp/sidecar.json",
            data=_json_bytes(sidecar),
            content_type=JSON_CONTENT_TYPE,
            episode_refs=episode_refs,
            sidecar=True,
        ),
    )
    return tuple(sorted(artifacts, key=lambda artifact: artifact.path))


def aggregate_sha256(artifacts: Iterable[ExportArtifactSpec]) -> str:
    entries = [
        {
            "path": artifact.path,
            "size_bytes": artifact.size_bytes,
            "sha256": artifact.sha256,
            "content_type": artifact.content_type,
        }
        for artifact in sorted(artifacts, key=lambda item: item.path)
    ]
    return sha256_text(canonical_json({"artifacts": entries}))


def build_artifact_manifest(
    job: ExportJob,
    artifacts: tuple[ExportArtifactSpec, ...],
    *,
    aggregate: str,
) -> dict[str, object]:
    entries = [artifact.to_entry() for artifact in artifacts]
    total_bytes = sum(artifact.size_bytes for artifact in artifacts)
    return {
        "export_job_id": str(job.export_job_id),
        "format": job.format.value if hasattr(job.format, "value") else str(job.format),
        "format_version": job.format_version,
        "exporter_version": job.exporter_version,
        "mapping_version": job.mapping_version,
        "config_sha256": job.config_sha256,
        "artifact_count": len(artifacts),
        "total_bytes": total_bytes,
        "artifacts": entries,
        "aggregate_sha256": aggregate,
        "verification_status": "UNVERIFIED",
    }


def manifest_bytes(manifest: Mapping[str, object]) -> bytes:
    return _json_bytes(plain(manifest))


def decode_json(data: bytes) -> object:
    return json.loads(data.decode("utf-8"))


__all__ = [
    "ExportArtifactSpec",
    "JSON_CONTENT_TYPE",
    "NDJSON_CONTENT_TYPE",
    "aggregate_sha256",
    "build_artifact_manifest",
    "build_export_artifacts",
    "decode_json",
    "manifest_bytes",
]
