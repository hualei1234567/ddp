from __future__ import annotations

from uuid import UUID

from app.runs import RunRegistry
from app.tasks import execute_training

from tests.conftest import make_context
from tests.fakes import DATASET_VERSION_ID, RecordingCallbacks, seed_export


def _training_context(storage, settings, callbacks):
    registry = RunRegistry()
    ctx = make_context(
        run_id="train-blocked",
        kind="training",
        storage=storage,
        settings=settings,
        registry=registry,
        callbacks=callbacks,
    )
    return registry, ctx


def test_training_without_media_manifest_is_blocked(
    storage, settings, callbacks: RecordingCallbacks
) -> None:
    seed_export(storage, with_media=False, with_labels=False)
    registry, ctx = _training_context(storage, settings, callbacks)
    execute_training(
        ctx,
        delivery_id=UUID("00000000-0000-0000-0000-000000000001"),
        dataset_version_id=UUID(DATASET_VERSION_ID),
        config={},
    )
    snapshot = registry.snapshot("train-blocked")
    assert snapshot is not None
    assert snapshot["status"] == "BLOCKED"
    assert snapshot["error_code"] == "blocked_media_manifest_missing"
    failed = callbacks.payloads("training:FAILED")
    assert len(failed) == 1
    assert failed[0]["external_status"] == "FAILED"
    assert failed[0]["error_code"] == "blocked_media_manifest_missing"
    assert not any(key.endswith("/model.joblib") for key in storage.objects)
    assert snapshot["callback_events"][-1]["ok"] is True


def test_training_with_media_but_no_labels_is_blocked(
    storage, settings, callbacks: RecordingCallbacks
) -> None:
    seed_export(storage, with_media=True, with_labels=False, media_has_action=False)
    registry, ctx = _training_context(storage, settings, callbacks)
    execute_training(
        ctx,
        delivery_id=UUID("00000000-0000-0000-0000-000000000002"),
        dataset_version_id=UUID(DATASET_VERSION_ID),
        config={},
    )
    snapshot = registry.snapshot("train-blocked")
    assert snapshot is not None
    assert snapshot["status"] == "BLOCKED"
    assert snapshot["error_code"] == "blocked_labels_missing"
    assert not any(key.endswith("/model.joblib") for key in storage.objects)


def test_media_parser_accepts_aliases_and_nested_videos(
    storage, settings, callbacks
) -> None:
    import json

    from app.export_dataset import resolve_export
    from app.media import load_media_manifest

    prefix = seed_export(storage, with_media=False, with_labels=False)
    rows = [
        {
            "episode": "ep-1",
            "videos": [
                {"key": "s3://ddp-raw/videos/a.mp4", "sha256": "a" * 64, "view": "main"}
            ],
        }
    ]
    storage.put_bytes(
        f"{prefix}meta/media.jsonl",
        ("\n".join(json.dumps(row) for row in rows) + "\n").encode(),
        content_type="application/x-ndjson",
    )
    layout = resolve_export(
        storage, settings, dataset_version_id=UUID(DATASET_VERSION_ID)
    )
    media = load_media_manifest(storage, layout, max_bytes=1024 * 1024)
    assert media.present
    assert len(media.entries) == 1
    assert media.entries[0].episode_id == "ep-1"
    assert media.entries[0].object_key == "videos/a.mp4"
    assert media.entries[0].camera == "main"
