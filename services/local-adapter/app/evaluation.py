"""真实评测与推理：加载训练产物，在导出数据上逐帧预测并写产物。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.config import Settings
from app.errors import AdapterError, DatasetError
from app.export_dataset import ExportLayout, resolve_export
from app.features import FeatureBatch, collect_frame_samples, ensure_same_feature_size
from app.labels import LabelSet, load_episode_labels
from app.media import ensure_media_available, load_media_manifest
from app.model_runtime import LoadedModel, load_model, resolve_model_artifact
from app.storage import ObjectStorage
from app.tensors import to_matrix

logger = logging.getLogger("ddp.local_adapter.evaluation")


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    evaluation_id: UUID
    layout: ExportLayout
    metrics: dict[str, float]
    report_key: str
    report_sha256: str
    report_size_bytes: int
    detail: dict[str, object]
    model: LoadedModel


@dataclass(frozen=True, slots=True)
class InferenceOutcome:
    task_id: UUID
    layout: ExportLayout
    metrics: dict[str, float]
    predictions_key: str
    predictions_sha256: str
    predictions_size_bytes: int
    row_count: int
    detail: dict[str, object]
    model: LoadedModel


def _text(config: dict[str, object], key: str) -> str | None:
    value = config.get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _project(config: dict[str, object]) -> UUID | None:
    value = config.get("project_id")
    if value in (None, ""):
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise DatasetError(
            "project_id_invalid", "config.project_id 不是合法 UUID"
        ) from exc


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
    if storage.get_bytes(key, max_bytes=max(len(data) + 1, 1024)) != data:
        raise AdapterError("artifact_verify_failed", f"产物回读校验失败：{key}")


def _resolve_dataset(
    storage: ObjectStorage,
    settings: Settings,
    *,
    dataset_version_id: UUID,
    config: dict[str, object],
) -> ExportLayout:
    return resolve_export(
        storage,
        settings,
        dataset_version_id=dataset_version_id,
        project_id=_project(config),
        export_job_id=_text(config, "export_job_id"),
        explicit_prefix=_text(config, "export_prefix"),
    )


def _prediction_arrays(model: LoadedModel, batch: FeatureBatch) -> tuple[Any, Any, Any]:
    ensure_same_feature_size(batch, model.image_size)
    matrix = to_matrix(batch.features)
    probabilities = model.model.predict_proba(matrix)
    predicted = model.model.predict(matrix)
    return predicted, probabilities, matrix


def run_evaluation(
    *,
    storage: ObjectStorage,
    settings: Settings,
    evaluation_id: UUID,
    model_version_id: UUID,
    dataset_version_id: UUID,
    config: dict[str, object],
    check_cancel: Callable[[], None],
) -> EvaluationOutcome:
    layout = _resolve_dataset(
        storage, settings, dataset_version_id=dataset_version_id, config=config
    )
    check_cancel()
    media = load_media_manifest(storage, layout, max_bytes=settings.max_manifest_bytes)
    ensure_media_available(media)
    labels = load_episode_labels(storage, layout, media, required=True)
    resolved = resolve_model_artifact(
        storage, settings, project_id=str(layout.project_id), config=config
    )
    model = load_model(storage, resolved=resolved, settings=settings)
    split = (_text(config, "split") or "test").lower()
    if split not in {"train", "validation", "test", "all"}:
        raise DatasetError(
            "split_invalid", "config.split 只允许 train/validation/test/all"
        )
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
    expected = [str(value) for value in batch.labels]
    predicted, probabilities, matrix = _prediction_arrays(model, batch)

    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        confusion_matrix,
        f1_score,
    )

    classes = list(model.model.classes_)
    truth = expected
    predictions = [str(value) for value in predicted]
    accuracy = float(accuracy_score(truth, predictions))
    macro_f1 = float(
        f1_score(truth, predictions, labels=classes, average="macro", zero_division=0)
    )
    confidences = [float(max(row)) for row in probabilities]
    mean_confidence = sum(confidences) / len(confidences) if confidences else 0.0
    per_class = classification_report(
        truth,
        predictions,
        labels=classes,
        output_dict=True,
        zero_division=0,
    )
    matrix_counts = confusion_matrix(truth, predictions, labels=classes)
    metrics = {
        "accuracy": round(accuracy, 6),
        "macro_f1": round(macro_f1, 6),
        "mean_confidence": round(mean_confidence, 6),
        "test_samples": float(len(matrix)),
        "episode_count": float(batch.stats["episode_count"]),
        "class_count": float(len(classes)),
    }
    report = {
        "schema_version": "ddp.local-adapter.evaluation-report/1.0.0",
        "evaluation_id": str(evaluation_id),
        "project_id": str(layout.project_id),
        "model_version_id": str(model_version_id),
        "dataset_version_id": str(dataset_version_id),
        "created_at": datetime.now(UTC).isoformat(),
        "split": split,
        "model": {
            "object_key": model.object_key,
            "sha256": f"sha256:{model.sha256}",
            "size_bytes": model.size_bytes,
            "resolution": model.resolution,
            "classes": list(model.classes),
        },
        "dataset": {
            "export_prefix": layout.prefix,
            "export_job_id": layout.export_job_id,
            "resolution": layout.resolution,
            "label_source": labels.source,
        },
        "metrics": metrics,
        "per_class": per_class,
        "confusion_matrix": {
            "labels": classes,
            "matrix": matrix_counts.tolist(),
        },
        "sample_stats": batch.stats,
        "limitations": [
            "frame_level_action_classification",
            "episode_level_labels_applied_to_frames",
            "not_a_robot_policy_evaluation",
        ],
    }
    data = _json_bytes(report)
    digest = hashlib.sha256(data).hexdigest()
    key = (
        f"{settings.evaluation_prefix}/{layout.project_id}/{evaluation_id}/report.json"
    )
    _put_verified(storage, key, data, content_type="application/json")
    detail = {
        "report": {
            "object_key": key,
            "sha256": f"sha256:{digest}",
            "size_bytes": len(data),
        },
        "model_object_key": model.object_key,
        "model_resolution": model.resolution,
        "label_source": labels.source,
        "split": split,
        "sample_stats": batch.stats,
    }
    logger.info("评测完成 evaluation=%s accuracy=%s", evaluation_id, accuracy)
    return EvaluationOutcome(
        evaluation_id=evaluation_id,
        layout=layout,
        metrics=metrics,
        report_key=key,
        report_sha256=digest,
        report_size_bytes=len(data),
        detail=detail,
        model=model,
    )


def _resolve_inference_dataset(
    storage: ObjectStorage,
    settings: Settings,
    *,
    dataset_version_id: UUID | None,
    config: dict[str, object],
) -> ExportLayout:
    configured = dataset_version_id
    if configured is None and _text(config, "dataset_version_id"):
        configured = UUID(str(_text(config, "dataset_version_id")))
    if configured is None:
        raise DatasetError(
            "dataset_location_required",
            "推理未提供 dataset_version_id；请在 config.dataset_version_id 或 config.export_prefix 指定数据位置",
        )
    return _resolve_dataset(
        storage, settings, dataset_version_id=configured, config=config
    )


def run_inference(
    *,
    storage: ObjectStorage,
    settings: Settings,
    task_id: UUID,
    model_version_id: UUID,
    service_id: UUID | None,
    dataset_version_id: UUID | None,
    episode_ids: list[UUID],
    config: dict[str, object],
    check_cancel: Callable[[], None],
) -> InferenceOutcome:
    layout = _resolve_inference_dataset(
        storage, settings, dataset_version_id=dataset_version_id, config=config
    )
    check_cancel()
    media = load_media_manifest(storage, layout, max_bytes=settings.max_manifest_bytes)
    ensure_media_available(media)
    label_warning: str | None = None
    try:
        labels: LabelSet = load_episode_labels(storage, layout, media, required=False)
    except AdapterError as exc:
        labels = LabelSet(labels={}, source="unavailable", skipped_rows=0)
        label_warning = f"{exc.code}: {exc.summary}"
    resolved = resolve_model_artifact(
        storage, settings, project_id=str(layout.project_id), config=config
    )
    model = load_model(storage, resolved=resolved, settings=settings)
    requested = {str(item) for item in episode_ids} if episode_ids else None
    split = (_text(config, "split") or "").lower() or None
    if split is not None and split not in {"train", "validation", "test", "all"}:
        raise DatasetError(
            "split_invalid", "config.split 只允许 train/validation/test/all"
        )
    batch = collect_frame_samples(
        storage=storage,
        settings=settings,
        layout=layout,
        media=media,
        labels=labels.labels,
        episode_ids=requested,
        split=None if split in {None, "all"} else split,
        require_labels=False,
        check_cancel=check_cancel,
    )
    predicted, probabilities, matrix = _prediction_arrays(model, batch)
    classes = [str(value) for value in model.model.classes_]
    rows: list[str] = []
    confidences: list[float] = []
    for index, payload in enumerate(predicted):
        probability_row = probabilities[index]
        confidence = float(max(probability_row))
        confidences.append(confidence)
        row = {
            "episode_id": batch.episode_ids[index],
            "camera": batch.cameras[index],
            "frame_index": batch.frame_indices[index],
            "timestamp_seconds": batch.timestamps[index],
            "predicted_action": str(payload),
            "confidence": round(confidence, 6),
            "probabilities": {
                name: round(float(probability_row[position]), 6)
                for position, name in enumerate(classes)
            },
        }
        rows.append(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )
    data = ("\n".join(rows) + "\n").encode("utf-8")
    digest = hashlib.sha256(data).hexdigest()
    key = f"{settings.inference_prefix}/{layout.project_id}/{task_id}/predictions.jsonl"
    _put_verified(storage, key, data, content_type="application/x-ndjson")
    metrics = {
        "prediction_count": float(len(rows)),
        "episode_count": float(batch.stats["episode_count"]),
        "mean_confidence": round(sum(confidences) / len(confidences), 6)
        if confidences
        else 0.0,
        "class_count": float(len(classes)),
    }
    detail = {
        "predictions": {
            "object_key": key,
            "sha256": f"sha256:{digest}",
            "size_bytes": len(data),
            "row_count": len(rows),
        },
        "model_object_key": model.object_key,
        "model_resolution": model.resolution,
        "service_id": str(service_id) if service_id else None,
        "label_warning": label_warning,
        "sample_stats": batch.stats,
        "episode_filter": sorted(requested) if requested else None,
        "predicted_counts": dict(Counter(str(value) for value in predicted)),
    }
    logger.info("推理完成 task=%s rows=%s", task_id, len(rows))
    return InferenceOutcome(
        task_id=task_id,
        layout=layout,
        metrics=metrics,
        predictions_key=key,
        predictions_sha256=digest,
        predictions_size_bytes=len(data),
        row_count=len(rows),
        detail=detail,
        model=model,
    )


__all__ = ["EvaluationOutcome", "InferenceOutcome", "run_evaluation", "run_inference"]
