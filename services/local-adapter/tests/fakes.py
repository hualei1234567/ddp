"""测试替身：内存对象存储与记录型回调。"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.callbacks import CallbackResult
from app.errors import StorageError
from app.storage import DownloadedObject, StoredObject


class InMemoryStorage:
    def __init__(self, bucket: str = "ddp-raw") -> None:
        self.bucket = bucket
        self.objects: dict[str, bytes] = {}
        self.modified: dict[str, datetime] = {}
        self._clock = datetime(2026, 1, 1, tzinfo=UTC)

    def _tick(self) -> datetime:
        self._clock += timedelta(seconds=1)
        return self._clock

    def get_bytes(self, key: str, *, max_bytes: int) -> bytes:
        if key not in self.objects:
            raise StorageError("object_not_found", f"对象不存在：{key}")
        data = self.objects[key]
        if len(data) > max_bytes:
            raise StorageError("object_too_large", f"对象超过大小上限：{key}")
        return data

    def put_bytes(self, key: str, data: bytes, *, content_type: str) -> None:
        self.objects[key] = bytes(data)
        self.modified[key] = self._tick()

    def object_size(self, key: str) -> int | None:
        data = self.objects.get(key)
        return None if data is None else len(data)

    def list_objects(self, prefix: str, *, max_items: int) -> list[StoredObject]:
        items = [
            StoredObject(key=key, size=len(data), last_modified=self.modified.get(key))
            for key, data in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        if len(items) > max_items:
            raise StorageError("object_listing_truncated", "对象过多")
        return items

    def download_to_file(
        self, key: str, path: Path, *, max_bytes: int
    ) -> DownloadedObject:
        data = self.get_bytes(key, max_bytes=max_bytes)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return DownloadedObject(
            path=path, size=len(data), sha256=hashlib.sha256(data).hexdigest()
        )

    def close(self) -> None:
        return None


class RecordingCallbacks:
    def __init__(self, *, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[dict[str, object]] = []

    def send_training(
        self, payload: dict[str, object], *, label: str
    ) -> CallbackResult:
        self.calls.append({"channel": "training", "label": label, "payload": payload})
        return CallbackResult(
            label=label, ok=self.ok, attempts=1, status_code=204 if self.ok else 500
        )

    def send_model(self, payload: dict[str, object], *, label: str) -> CallbackResult:
        self.calls.append({"channel": "model", "label": label, "payload": payload})
        return CallbackResult(
            label=label, ok=self.ok, attempts=1, status_code=204 if self.ok else 500
        )

    def close(self) -> None:
        return None

    def payloads(self, label: str) -> list[dict[str, object]]:
        return [item["payload"] for item in self.calls if item["label"] == label]


PROJECT_ID = "1bca073d-c9af-4601-9b0f-7888d0747db0"
DATASET_VERSION_ID = "bddff4ae-416f-4e53-b951-513409fd83ba"
JOB_ID = "1aca9578-a841-448a-81a7-1326e80f35d6"

TRAIN_EPISODES = [
    ("ep-1", "pick"),
    ("ep-2", "place"),
    ("ep-3", "pick"),
]
TEST_EPISODES = [("ep-4", "place")]


def seed_export(
    storage: InMemoryStorage,
    *,
    with_media: bool = True,
    with_labels: bool = True,
    media_has_action: bool = False,
    label_rows: list[dict[str, object]] | None = None,
    extra_media_rows: list[dict[str, object]] | None = None,
) -> str:
    prefix = f"exports/{PROJECT_ID}/{DATASET_VERSION_ID}/{JOB_ID}/"
    storage.put_bytes(
        f"{prefix}ddp/sidecar.json",
        (
            '{"schema_version":"ddp.export.sidecar/1.0.0",'
            f'"export_job_id":"{JOB_ID}","dataset_version_id":"{DATASET_VERSION_ID}",'
            f'"project_id":"{PROJECT_ID}","created_at":"2026-11-17T00:00:00+00:00"}}'
        ).encode(),
        content_type="application/json",
    )
    storage.put_bytes(
        f"{prefix}meta/info.json",
        b'{"format":"ddp.lerobot","format_version":"1.0.0"}',
        content_type="application/json",
    )
    divisions = {
        "dataset_version_id": DATASET_VERSION_ID,
        "divisions": {
            "train": [item[0] for item in TRAIN_EPISODES],
            "validation": [],
            "test": [item[0] for item in TEST_EPISODES],
        },
        "counts": {"train": 3, "validation": 0, "test": 1},
    }
    import json

    storage.put_bytes(
        f"{prefix}meta/ddp_divisions.json",
        json.dumps(divisions, sort_keys=True).encode(),
        content_type="application/json",
    )
    storage.put_bytes(
        f"{prefix}meta/episodes.jsonl",
        (
            "".join(
                json.dumps({"episode_id": episode, "split": split}) + "\n"
                for episode, split in (
                    [(item[0], "TRAIN") for item in TRAIN_EPISODES]
                    + [(item[0], "TEST") for item in TEST_EPISODES]
                )
            )
        ).encode(),
        content_type="application/x-ndjson",
    )
    if with_media:
        media_rows = []
        for episode, action in TRAIN_EPISODES + TEST_EPISODES:
            video = f"video-{episode}".encode()
            key = f"{prefix}videos/{episode}.mp4"
            storage.put_bytes(key, video, content_type="video/mp4")
            row = {
                "episode_id": episode,
                "camera": "main",
                "object_key": key,
                "sha256": f"sha256:{hashlib.sha256(video).hexdigest()}",
            }
            if media_has_action:
                row["action"] = action
            media_rows.append(row)
        if extra_media_rows:
            media_rows.extend(extra_media_rows)
        storage.put_bytes(
            f"{prefix}meta/media.jsonl",
            (
                "".join(json.dumps(row, sort_keys=True) + "\n" for row in media_rows)
            ).encode(),
            content_type="application/x-ndjson",
        )
    if with_labels:
        import pyarrow as pa
        import pyarrow.parquet as pq

        rows = label_rows or [
            {
                "episode_id": episode,
                "split": "train" if (episode, action) in TRAIN_EPISODES else "test",
                "action": action,
                "success": True,
                "quality_score": 0.9,
            }
            for episode, action in TRAIN_EPISODES + TEST_EPISODES
        ]
        import io

        buffer = io.BytesIO()
        table = pa.table(
            {
                "episode_id": [str(row["episode_id"]) for row in rows],
                "split": [str(row["split"]) for row in rows],
                "action": [str(row["action"]) for row in rows],
                "success": [bool(row.get("success", True)) for row in rows],
                "quality_score": [float(row.get("quality_score", 1.0)) for row in rows],
            }
        )
        pq.write_table(table, buffer)
        storage.put_bytes(
            f"{prefix}data/episodes.parquet",
            buffer.getvalue(),
            content_type="application/octet-stream",
        )
    return prefix
