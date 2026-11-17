"""真实训练：视频抽帧 → 32×32 灰度 → 标准化 + 逻辑回归。"""

from __future__ import annotations

import hashlib
import json
import logging
import tempfile
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from app import frames
from app.config import Settings
from app.errors import AdapterError, BlockedError, DatasetError
from app.export_dataset import ExportLayout, resolve_export
from app.features import FeatureBatch, collect_frame_samples
from app.labels import load_episode_labels
from app.media import ensure_media_available, load_media_manifest
from app.storage import ObjectStorage
from app.tensors import to_matrix

logger = logging.getLogger("ddp.local_adapter.training")


@dataclass(frozen=True, slots=True)
class TrainingOutcome:
    run_id: str
    layout: ExportLayout
    model_key: str
    metadata_key: str
    model_sha256: str
    model_size_bytes: int
    metrics: dict[str, float]
    detail: dict[str, object]
    model_ref: dict[str, object]
    created_at: str


def _project_id(config: dict[str, object]) -> UUID | None:
    value = config.get("project_id")
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DatasetError(
            "project_id_invalid", "config.project_id 不是合法 UUID"
        ) from exc


def _text(config: dict[str, object], key: str) -> str | None:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _matrix(batch: FeatureBatch) -> Any:
    if any(label is None for label in batch.labels):
        raise AdapterError("training_labels_missing", "训练样本缺少 action 标签")
    return to_matrix(batch.features), [str(label) for label in batch.labels]


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _put_verified(
    storage: ObjectStorage, key: str, data: bytes, *, content_type: str
) -> None:
    storage.put_bytes(key, data, content_type=content_type)
    readback = storage.get_bytes(key, max_bytes=max(len(data) + 1, 1024))
    if readback != data:
        raise AdapterError("artifact_verify_failed", f"产物回读校验失败：{key}")


def run_training(
    *,
    storage: ObjectStorage,
    settings: Settings,
    run_id: str,
    delivery_id: UUID,
    dataset_version_id: UUID,
    config: dict[str, object],
    check_cancel: Callable[[], None],
) -> TrainingOutcome:
    layout = resolve_export(
        storage,
        settings,
        dataset_version_id=dataset_version_id,
        project_id=_project_id(config),
        export_job_id=_text(config, "export_job_id"),
        explicit_prefix=_text(config, "export_prefix"),
    )
    check_cancel()
    media = load_media_manifest(storage, layout, max_bytes=settings.max_manifest_bytes)
    ensure_media_available(media)
    labels = load_episode_labels(storage, layout, media, required=True)
    split = (_text(config, "split") or "train").lower()
    if split not in {"train", "validation", "all"}:
        raise DatasetError("split_invalid", "config.split 只允许 train/validation/all")
    batch = collect_frame_samples(
        storage=storage,
        settings=settings,
        layout=layout,
        media=media,
        labels=labels.labels,
        episode_ids=None,
        split=None if split == "all" else split,
        require_labels=True,
        check_cancel=check_cancel,
    )
    check_cancel()
    matrix, labels_list = _matrix(batch)
    class_counts = Counter(labels_list)
    if len(class_counts) < 2:
        raise BlockedError(
            "blocked_insufficient_classes",
            f"训练集只有 {len(class_counts)} 个 action 类别，无法训练分类器",
        )

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    model = Pipeline(
        steps=[
            ("scaler", StandardScaler()),
            (
                "clf",
                LogisticRegression(
                    C=0.1,
                    max_iter=1000,
                    solver="saga",
                    class_weight="balanced",
                    random_state=0,
                ),
            ),
        ]
    )
    model.fit(matrix, labels_list)
    train_accuracy = float(accuracy_score(labels_list, model.predict(matrix)))
    check_cancel()

    created_at = datetime.now(UTC).isoformat()
    classes = [str(item) for item in model.named_steps["clf"].classes_]
    feature_size = int(matrix.shape[1])
    artifact = {
        "schema_version": "ddp.local-adapter.model/1.0.0",
        "created_at": created_at,
        "run_id": run_id,
        "project_id": str(layout.project_id),
        "dataset_version_id": str(layout.dataset_version_id),
        "export_job_id": layout.export_job_id,
        "feature_size": feature_size,
        "image_size": settings.image_size,
        "frames_per_video": settings.frames_per_video,
        "classes": classes,
        "train_accuracy": train_accuracy,
        "model": model,
    }
    metadata = {
        "schema_version": "ddp.local-adapter.training-metadata/1.0.0",
        "run_id": run_id,
        "created_at": created_at,
        "project_id": str(layout.project_id),
        "dataset_version_id": str(layout.dataset_version_id),
        "export_job_id": layout.export_job_id,
        "export_prefix": layout.prefix,
        "delivery_id": str(delivery_id),
        "adapter_version": settings.training_adapter_version,
        "label_source": labels.source,
        "label_rows_skipped": labels.skipped_rows,
        "split": split,
        "classes": classes,
        "class_counts": dict(sorted(class_counts.items())),
        "train_accuracy": train_accuracy,
        "feature_size": feature_size,
        "image_size": settings.image_size,
        "frames_per_video": settings.frames_per_video,
        "ffmpeg_version": frames.ffmpeg_version(settings.ffmpeg_path),
        "stats": batch.stats,
        "limitations": [
            "frame_level_action_classification",
            "episode_level_action_labels_applied_to_all_frames",
            "not_a_robot_policy_model",
        ],
    }

    try:
        import joblib
    except ImportError as exc:  # pragma: no cover
        raise AdapterError("joblib_missing", "训练产物序列化需要 joblib") from exc
    with tempfile.TemporaryDirectory(prefix="ddp-local-adapter-model-") as workdir:
        model_path = Path(workdir) / "model.joblib"
        joblib.dump(artifact, model_path)
        model_data = model_path.read_bytes()
    model_sha256 = hashlib.sha256(model_data).hexdigest()
    model_key = f"{settings.model_prefix}/{layout.project_id}/{run_id}/model.joblib"
    metadata_key = (
        f"{settings.model_prefix}/{layout.project_id}/{run_id}/training_metadata.json"
    )
    metadata_data = _json_bytes(metadata)
    _put_verified(
        storage, model_key, model_data, content_type="application/octet-stream"
    )
    check_cancel()
    _put_verified(storage, metadata_key, metadata_data, content_type="application/json")

    metrics = {
        "train_accuracy": round(train_accuracy, 6),
        "frame_count": float(batch.stats["frame_count"]),
        "episode_count": float(batch.stats["episode_count"]),
        "video_count": float(batch.stats["video_count"]),
        "class_count": float(len(classes)),
        "model_size_bytes": float(len(model_data)),
    }
    detail = {
        "layout": {
            "export_prefix": layout.prefix,
            "export_job_id": layout.export_job_id,
            "project_id": str(layout.project_id),
            "dataset_version_id": str(layout.dataset_version_id),
            "resolution": layout.resolution,
        },
        "label_source": labels.source,
        "classes": classes,
        "class_counts": dict(sorted(class_counts.items())),
        "stats": batch.stats,
        "model": {
            "object_key": model_key,
            "sha256": f"sha256:{model_sha256}",
            "size_bytes": len(model_data),
        },
        "metadata": {
            "object_key": metadata_key,
            "sha256": f"sha256:{hashlib.sha256(metadata_data).hexdigest()}",
            "size_bytes": len(metadata_data),
        },
    }
    model_ref = {
        "model_id": f"local-adapter/{run_id}",
        "model_version": settings.training_adapter_version,
        "registered_at": created_at,
    }
    logger.info("训练完成 run=%s frames=%s classes=%s", run_id, len(matrix), classes)
    return TrainingOutcome(
        run_id=run_id,
        layout=layout,
        model_key=model_key,
        metadata_key=metadata_key,
        model_sha256=model_sha256,
        model_size_bytes=len(model_data),
        metrics=metrics,
        detail=detail,
        model_ref=model_ref,
        created_at=created_at,
    )


__all__ = ["TrainingOutcome", "run_training"]
