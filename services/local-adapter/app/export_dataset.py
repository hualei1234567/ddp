"""导出版本定位与元数据读取。

DDP 的 Adapter submit 只保证传入 ``dataset_version_id``。项目与 export_job 可能
需要从对象存储发现：
- 显式 ``config.export_prefix`` 优先；
- 否则 ``exports/{project}/{dataset_version}/{export_job}/``；
- 未给 project/export_job 时枚举前缀并选择**最近写入**的 export job。
发现结果写入运行记录，不做静默猜测。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.config import Settings
from app.errors import DatasetError
from app.storage import ObjectStorage


@dataclass(frozen=True, slots=True)
class ExportLayout:
    prefix: str
    project_id: UUID
    dataset_version_id: UUID
    export_job_id: str
    sidecar: dict[str, Any]
    info: dict[str, Any]
    divisions: dict[str, Any]
    resolution: str
    export_created_at: str | None = None


def _normalize_prefix(value: str) -> str:
    prefix = value.strip().lstrip("/")
    if not prefix.endswith("/"):
        prefix += "/"
    if ".." in prefix.split("/"):
        raise DatasetError("export_prefix_invalid", "export_prefix 不得包含 ..")
    return prefix


def _uuid(value: object, *, code: str, field: str) -> UUID:
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DatasetError(code, f"{field} 不是合法 UUID：{value!r}") from exc


def _read_json(storage: ObjectStorage, key: str, *, max_bytes: int) -> dict[str, Any]:
    raw = storage.get_bytes(key, max_bytes=max_bytes)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatasetError(
            "export_metadata_invalid", f"导出元数据不是合法 JSON：{key}"
        ) from exc
    if not isinstance(value, dict):
        raise DatasetError("export_metadata_invalid", f"导出元数据必须是对象：{key}")
    return value


def _optional_json(
    storage: ObjectStorage, key: str, *, max_bytes: int
) -> dict[str, Any]:
    if storage.object_size(key) is None:
        return {}
    return _read_json(storage, key, max_bytes=max_bytes)


def _job_prefixes(
    storage: ObjectStorage, base: str, *, max_items: int
) -> dict[str, datetime | None]:
    jobs: dict[str, datetime | None] = {}
    for item in storage.list_objects(base, max_items=max_items):
        remainder = item.key[len(base) :]
        if not remainder:
            continue
        job_id = remainder.split("/", 1)[0]
        if not job_id or job_id.startswith("_"):
            continue
        current = jobs.get(job_id)
        if item.last_modified is not None and (
            current is None or item.last_modified > current
        ):
            jobs[job_id] = item.last_modified
        else:
            jobs.setdefault(job_id, current)
    return jobs


def _latest_job(jobs: dict[str, datetime | None]) -> str:
    if not jobs:
        raise DatasetError("export_not_found", "未找到导出产物")
    dated = [(moment, job_id) for job_id, moment in jobs.items() if moment is not None]
    if dated:
        return max(dated, key=lambda item: (item[0], item[1]))[1]
    return sorted(jobs)[-1]


def _parse_export_key(
    key: str, root: str, dataset_version_id: UUID
) -> tuple[str, str] | None:
    remainder = key[len(root) :]
    parts = remainder.split("/")
    if len(parts) < 4:
        return None
    project, dataset, job = parts[0], parts[1], parts[2]
    if dataset != str(dataset_version_id) or not project or not job:
        return None
    if job.startswith("_"):
        return None
    return project, job


def resolve_export(
    storage: ObjectStorage,
    settings: Settings,
    *,
    dataset_version_id: UUID,
    project_id: UUID | None = None,
    export_job_id: str | None = None,
    explicit_prefix: str | None = None,
    max_items: int = 20_000,
) -> ExportLayout:
    requested_project = project_id
    resolution = "explicit_prefix"
    if explicit_prefix:
        prefix = _normalize_prefix(explicit_prefix)
        job_id = export_job_id or ""
        if not job_id:
            parts = prefix.strip("/").split("/")
            job_id = parts[-1] if len(parts) >= 3 else ""
    else:
        root = f"{settings.export_prefix}/"
        if project_id is not None:
            base = f"{root}{project_id}/{dataset_version_id}/"
            jobs = _job_prefixes(storage, base, max_items=max_items)
            if export_job_id:
                if export_job_id not in jobs:
                    raise DatasetError(
                        "export_job_not_found",
                        f"数据集版本 {dataset_version_id} 下未找到导出任务 {export_job_id}",
                    )
                job_id = export_job_id
                resolution = "configured_export_job"
            else:
                job_id = _latest_job(jobs)
                resolution = "latest_export_job"
            prefix = f"{base}{job_id}/"
        else:
            projects: dict[str, dict[str, datetime | None]] = {}
            for item in storage.list_objects(root, max_items=max_items):
                parsed = _parse_export_key(item.key, root, dataset_version_id)
                if parsed is None:
                    continue
                project, job = parsed
                bucket = projects.setdefault(project, {})
                current = bucket.get(job)
                if item.last_modified is not None and (
                    current is None or item.last_modified > current
                ):
                    bucket[job] = item.last_modified
                else:
                    bucket.setdefault(job, current)
            if not projects:
                raise DatasetError(
                    "export_not_found",
                    f"对象存储中未找到数据集版本 {dataset_version_id} 的导出产物",
                )
            if len(projects) > 1:
                raise DatasetError(
                    "export_project_ambiguous",
                    "同一数据集版本出现在多个项目前缀下，必须显式提供 project_id/export_prefix",
                )
            project_text, jobs = next(iter(projects.items()))
            project_id = _uuid(
                project_text, code="export_project_invalid", field="project_id"
            )
            if export_job_id:
                if export_job_id not in jobs:
                    raise DatasetError(
                        "export_job_not_found", f"未找到导出任务 {export_job_id}"
                    )
                job_id = export_job_id
                resolution = "discovered_project_configured_job"
            else:
                job_id = _latest_job(jobs)
                resolution = "discovered_latest_export_job"
            prefix = f"{root}{project_text}/{dataset_version_id}/{job_id}/"

    sidecar_key = f"{prefix}ddp/sidecar.json"
    if storage.object_size(sidecar_key) is None:
        raise DatasetError(
            "export_sidecar_missing",
            f"导出前缀缺少 ddp/sidecar.json，不能确认为 DDP 导出产物：{prefix}",
        )
    sidecar = _read_json(storage, sidecar_key, max_bytes=settings.max_manifest_bytes)
    schema_version = str(sidecar.get("schema_version", ""))
    if not schema_version.startswith("ddp.export.sidecar/"):
        raise DatasetError(
            "export_sidecar_invalid",
            f"未知导出 sidecar schema：{schema_version or '<empty>'}",
        )
    sidecar_dataset = _uuid(
        sidecar.get("dataset_version_id"),
        code="export_sidecar_invalid",
        field="sidecar.dataset_version_id",
    )
    if sidecar_dataset != dataset_version_id:
        raise DatasetError(
            "export_sidecar_mismatch",
            "配置的 dataset_version_id 与 sidecar 不一致",
        )
    sidecar_project = _uuid(
        sidecar.get("project_id"),
        code="export_sidecar_invalid",
        field="sidecar.project_id",
    )
    if requested_project is not None and requested_project != sidecar_project:
        raise DatasetError(
            "export_project_mismatch",
            "配置的 project_id 与导出 sidecar 不一致",
        )
    sidecar_job = str(sidecar.get("export_job_id", ""))
    if job_id and sidecar_job and sidecar_job != job_id:
        raise DatasetError(
            "export_job_mismatch", "导出路径中的 job id 与 sidecar 不一致"
        )
    info = _optional_json(
        storage, f"{prefix}meta/info.json", max_bytes=settings.max_manifest_bytes
    )
    divisions = _optional_json(
        storage,
        f"{prefix}meta/ddp_divisions.json",
        max_bytes=settings.max_manifest_bytes,
    )
    return ExportLayout(
        prefix=prefix,
        project_id=sidecar_project,
        dataset_version_id=dataset_version_id,
        export_job_id=job_id or sidecar_job,
        sidecar=sidecar,
        info=info,
        divisions=divisions,
        resolution=resolution,
        export_created_at=sidecar.get("created_at"),
    )


def load_episodes(
    storage: ObjectStorage, layout: ExportLayout, *, max_bytes: int
) -> dict[str, dict[str, Any]]:
    key = f"{layout.prefix}meta/episodes.jsonl"
    if storage.object_size(key) is None:
        raise DatasetError(
            "export_episodes_missing", "导出产物缺少 meta/episodes.jsonl"
        )
    raw = storage.get_bytes(key, max_bytes=max_bytes)
    episodes: dict[str, dict[str, Any]] = {}
    try:
        lines = raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise DatasetError(
            "export_episodes_invalid", "episodes.jsonl 不是 UTF-8"
        ) from exc
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetError(
                "export_episodes_invalid", f"episodes.jsonl 第 {index} 行不是合法 JSON"
            ) from exc
        if not isinstance(row, dict) or not row.get("episode_id"):
            raise DatasetError(
                "export_episodes_invalid",
                f"episodes.jsonl 第 {index} 行缺少 episode_id",
            )
        episodes[str(row["episode_id"])] = row
    if not episodes:
        raise DatasetError("export_episodes_empty", "episodes.jsonl 为空")
    return episodes


def utc_now() -> datetime:
    return datetime.now(UTC)


__all__ = ["ExportLayout", "load_episodes", "resolve_export", "utc_now"]
