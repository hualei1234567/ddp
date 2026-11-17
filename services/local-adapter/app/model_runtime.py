"""模型产物定位、下载与加载。

DDP 的评测/推理 submit 只传 ``model_version_id``，没有把 artifact object_key
带过来。因此本服务按以下顺序解析，并把最终采用的方式写进报告：
1. ``config.model_object_key``；
2. ``config.model_ref.object_key``；
3. ``config.training_run_id`` / ``config.model_id``（本服务产出的 run id）；
4. 项目下恰好只有一个 ``model.joblib`` 时回退使用（可配置关闭）；多个时
   明确报 ``model_artifact_ambiguous``，不猜。
"""

from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from typing import Any

from app.config import Settings
from app.errors import ModelArtifactError, StorageError
from app.storage import ObjectStorage

MAX_MODEL_BYTES = 2 * 1024 * 1024 * 1024
_MODEL_FILENAME = "model.joblib"
_METADATA_FILENAME = "training_metadata.json"


@dataclass(frozen=True, slots=True)
class ResolvedModel:
    object_key: str
    resolution: str


@dataclass(frozen=True, slots=True)
class LoadedModel:
    object_key: str
    sha256: str
    size_bytes: int
    model: Any
    classes: tuple[str, ...]
    image_size: int
    feature_size: int
    metadata: dict[str, Any]
    resolution: str


def _text(config: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _run_from_model_id(value: str) -> str | None:
    marker = "local-adapter/"
    if value.startswith(marker):
        value = value[len(marker) :]
    return value if value.startswith(("train-", "run-")) else value or None


def _candidate_keys(
    project_id: str, config: dict[str, object]
) -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    explicit = _text(config, "model_object_key")
    if explicit:
        candidates.append((explicit.lstrip("/"), "config.model_object_key"))
    model_ref = config.get("model_ref")
    if isinstance(model_ref, dict):
        ref_key = model_ref.get("object_key")
        if isinstance(ref_key, str) and ref_key.strip():
            candidates.append(
                (ref_key.strip().lstrip("/"), "config.model_ref.object_key")
            )
    run_id = _text(config, "training_run_id", "source_run_id", "run_id")
    if run_id is None:
        model_id = _text(config, "model_id")
        if model_id:
            run_id = _run_from_model_id(model_id)
    if run_id:
        candidates.append(
            (f"models/{project_id}/{run_id}/{_MODEL_FILENAME}", "config.run_id")
        )
    return candidates


def resolve_model_artifact(
    storage: ObjectStorage,
    settings: Settings,
    *,
    project_id: str,
    config: dict[str, object],
) -> ResolvedModel:
    for key, resolution in _candidate_keys(project_id, config):
        if storage.object_size(key) is not None:
            return ResolvedModel(object_key=key, resolution=resolution)
    prefix = f"{settings.model_prefix}/{project_id}/"
    objects = storage.list_objects(prefix, max_items=20_000)
    model_keys = sorted(
        item.key for item in objects if item.key.endswith(f"/{_MODEL_FILENAME}")
    )
    if not model_keys:
        raise ModelArtifactError(
            "model_artifact_not_found",
            f"项目 {project_id} 下没有模型产物；请在 config 提供 model_object_key 或先完成训练",
        )
    if len(model_keys) > 1:
        raise ModelArtifactError(
            "model_artifact_ambiguous",
            "项目下存在多个模型产物，无法安全猜选；请提供 config.model_object_key："
            + "、".join(model_keys[:10]),
        )
    if not settings.allow_single_model_fallback:
        raise ModelArtifactError(
            "model_artifact_not_resolved",
            "未提供 model_object_key，且单产物回退已关闭",
        )
    return ResolvedModel(
        object_key=model_keys[0], resolution="single_artifact_fallback"
    )


def load_model(
    storage: ObjectStorage,
    *,
    resolved: ResolvedModel,
    settings: Settings,
) -> LoadedModel:
    try:
        import joblib
    except ImportError as exc:  # pragma: no cover
        raise ModelArtifactError("joblib_missing", "加载模型需要 joblib") from exc
    raw = storage.get_bytes(resolved.object_key, max_bytes=MAX_MODEL_BYTES)
    digest = hashlib.sha256(raw).hexdigest()
    try:
        artifact = joblib.load(io.BytesIO(raw))
    except Exception as exc:
        raise ModelArtifactError(
            "model_artifact_invalid", f"模型文件无法反序列化：{resolved.object_key}"
        ) from exc
    if not isinstance(artifact, dict) or "model" not in artifact:
        raise ModelArtifactError(
            "model_artifact_invalid", "模型产物格式不符合本服务约定"
        )
    schema = str(artifact.get("schema_version", ""))
    if not schema.startswith("ddp.local-adapter.model/"):
        raise ModelArtifactError(
            "model_artifact_invalid", f"未知模型 schema：{schema or '<empty>'}"
        )
    model = artifact["model"]
    classes = artifact.get("classes")
    if not isinstance(classes, list) or not classes:
        classes = list(getattr(model, "classes_", []))
    classes = tuple(str(item) for item in classes)
    if len(classes) < 2:
        raise ModelArtifactError("model_artifact_invalid", "模型类别少于 2 个")
    feature_size = int(
        artifact.get("feature_size", settings.image_size * settings.image_size)
    )
    image_size = int(artifact.get("image_size", settings.image_size))
    metadata: dict[str, Any] = {}
    metadata_key = resolved.object_key.rsplit("/", 1)[0] + f"/{_METADATA_FILENAME}"
    try:
        if storage.object_size(metadata_key) is not None:
            metadata = json.loads(
                storage.get_bytes(metadata_key, max_bytes=1024 * 1024).decode("utf-8")
            )
    except (StorageError, UnicodeDecodeError, json.JSONDecodeError):
        metadata = {}
    return LoadedModel(
        object_key=resolved.object_key,
        sha256=digest,
        size_bytes=len(raw),
        model=model,
        classes=classes,
        image_size=image_size,
        feature_size=feature_size,
        metadata=metadata if isinstance(metadata, dict) else {},
        resolution=resolved.resolution,
    )


__all__ = [
    "LoadedModel",
    "ResolvedModel",
    "load_model",
    "resolve_model_artifact",
]
