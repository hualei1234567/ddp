"""ExportJob API/Worker 测试夹具：真实建库、冻结版本与内存对象存储替身。"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

from app.application.ingestion.ports import PresignedDownload
from app.domain.ingestion.errors import (
    ObjectNotFoundError,
    ObjectStorageUnavailableError,
)
from app.workers.exports import ExportWorker
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    url,
    version_payload,
)

EXPORT_PAYLOAD = {
    "format": "LE_ROBOT",
    "format_version": "ddp.lerobot/1.0.0",
    "exporter_id": "ddp.lerobot-metadata",
    "exporter_version": "1.0.0",
    "mapping_version": "ddp.lerobot/1.0.0",
    "config": {"include_sidecar": True},
}


class FakeExportStorage:
    """显式标注：内存对象存储替身，不替代真实 MinIO/S3 集成验收。"""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self._fail_puts: list[int] = []
        self._corrupt_reads: list[int] = []
        self.presign_calls: list[dict[str, object]] = []

    def fail_next_put(self, *, after_successes: int = 0) -> None:
        self._fail_puts.append(after_successes)

    def corrupt_next_read(self, *, after_successes: int = 0) -> None:
        self._corrupt_reads.append(after_successes)

    def put_bytes(self, object_key: str, data: bytes, *, content_type: str) -> None:
        if self._fail_puts and self._fail_puts[0] == 0:
            self._fail_puts.pop(0)
            raise ObjectStorageUnavailableError("模拟写入失败")
        if self._fail_puts:
            self._fail_puts[0] -= 1
        self.objects[object_key] = bytes(data)
        self.content_types[object_key] = content_type

    def stream_object(self, object_key: str, *, chunk_size: int) -> Iterator[bytes]:
        content = self.objects.get(object_key)
        if content is None:
            raise ObjectNotFoundError("对象不存在")
        if self._corrupt_reads and self._corrupt_reads[0] == 0:
            self._corrupt_reads.pop(0)
            content = content + b"corrupted"
        elif self._corrupt_reads:
            self._corrupt_reads[0] -= 1
        for start in range(0, len(content), chunk_size):
            yield content[start : start + chunk_size]

    def presign_download(
        self,
        object_key: str,
        *,
        expires_in_seconds: int,
    ) -> PresignedDownload:
        self.presign_calls.append(
            {"object_key": object_key, "expires_in_seconds": expires_in_seconds}
        )
        return PresignedDownload(
            url=f"https://fake-export.test/{object_key}?signature=fake",
            expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
        )

    def object_size(self, object_key: str) -> int | None:
        content = self.objects.get(object_key)
        return None if content is None else len(content)

    def delete_object(self, object_key: str) -> None:
        self.objects.pop(object_key, None)

    def sha256(self, object_key: str) -> str:
        return "sha256:" + hashlib.sha256(self.objects[object_key]).hexdigest()


def export_payload(dataset_version_id: UUID | str, **overrides: object) -> dict[str, object]:
    payload = dict(EXPORT_PAYLOAD)
    payload["dataset_version_id"] = str(dataset_version_id)
    payload.update(overrides)
    return payload


def build_frozen_version(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    code: str | None = None,
    seed_prefix: str | None = None,
    seed_salt: str = "",
    selection_filters: dict[str, object] | None = None,
) -> dict[str, object]:
    """通过真实 API 构建并冻结一个数据集版本。

    ``seed_prefix`` / ``seed_salt`` 只在同一项目里需要**多个**冻结版本时传：

    - Episode 上有 ``UNIQUE(project_id, code)``，两次都用默认前缀 ``ep``
      会在造数据阶段就 ``UNIQUE constraint failed: episodes.project_id, episodes.code``；
    - RawAsset 上有 ``UNIQUE(project_id, sha256, size_bytes)``，只改前缀仍会
      撞车，因此还要让 Hash 随 ``seed_salt`` 变化才真正唯一。

    Hash 必须是**合法的 sha256:64 位小写十六进制**：直接拼盐字符串会产出
    非十六进制内容，下游的候选校验会把它判成不合格，build 报
    ``dataset_insufficient_data``——失败点离原因很远，极难排查。因此用
    ``hashlib.sha256`` 真算一遍。默认值（``None`` / 空串）保持既有行为不变。
    """
    from uuid import uuid4

    def _digest(seed: str) -> str:
        return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()

    seed_kwargs: dict[str, object] = {}
    if seed_prefix is not None:
        seed_kwargs["prefix"] = seed_prefix
    if seed_salt:
        seed_kwargs["manifest_shas"] = [
            _digest(f"{seed_salt}-manifest-{index:02d}") for index in range(10)
        ]
        seed_kwargs["raw_hashes"] = [_digest(f"{seed_salt}-raw-{index:02d}") for index in range(10)]
    episodes = seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=10, **seed_kwargs)
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    viewer = make_client(factory, ids.viewer_a)
    dataset_code = code or f"export-ds-{uuid4().hex[:8]}"
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, dataset_code)
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies, **(selection_filters or {})),
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["dataset_version_id"]
    built = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/build",
        ),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert built.status_code == 200, built.text
    validated = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/validate",
        ),
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    frozen = owner.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/freeze",
        ),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
        json={"reason": "QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 200, frozen.text
    return {
        "dataset": dataset,
        "version": frozen.json(),
        "version_id": version_id,
        "episodes": episodes,
        "editor": editor,
        "owner": owner,
        "viewer": viewer,
    }


def run_export_worker(
    factory: sessionmaker[Session],
    storage: FakeExportStorage,
    *,
    now: datetime | None = None,
):
    from app.core.config import Settings

    worker = ExportWorker(factory, Settings(env="local"), storage)
    return worker.process_once(now=now)
