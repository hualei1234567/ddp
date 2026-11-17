"""ExportJob HTTP API 测试：权限、跨项目、未冻结 409、幂等与产物清单。"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

from app.infrastructure.db.models.collection import OutboxEvent
from app.infrastructure.db.models.datasets import DatasetVersionModel
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    url,
    version_payload,
)
from tests.api.export_support import (
    FakeExportStorage,
    build_frozen_version,
    export_payload,
    run_export_worker,
)


def test_export_create_list_detail_idempotency_and_permissions(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    viewer = make_client(factory, ids.viewer_a, object_storage=storage)
    outsider = make_client(factory, ids.outsider, object_storage=storage)
    owner = make_client(factory, ids.owner_a, object_storage=storage)
    payload = export_payload(ctx["version_id"])

    missing = editor.post(url(ids.project_a, "/exports"), json=payload)
    assert missing.status_code == 428
    assert missing.json()["error"]["code"] == "precondition_required"

    denied = viewer.post(
        url(ids.project_a, "/exports"),
        json=payload,
        headers={"Idempotency-Key": "export-viewer-denied"},
    )
    assert denied.status_code == 403

    created = editor.post(
        url(ids.project_a, "/exports"),
        json=payload,
        headers={"Idempotency-Key": "export-idem-0001"},
    )
    assert created.status_code == 202, created.text
    job = created.json()
    assert job["status"] == "QUEUED"
    assert job["project_id"] == str(ids.project_a)
    assert job["requested_by"] == str(ids.editor_a)
    assert job["attempt"] == 0
    assert job["verification_status"] == "UNVERIFIED"
    assert job["config_sha256"].startswith("sha256:")
    assert job["output_prefix"].startswith(f"exports/{ids.project_a}/")
    assert job["verification_evidence_ref"] is None

    replay = editor.post(
        url(ids.project_a, "/exports"),
        json=payload,
        headers={"Idempotency-Key": "export-idem-0001"},
    )
    assert replay.status_code == 202
    assert replay.json()["export_job_id"] == job["export_job_id"]

    reused = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"], config={"include_sidecar": False}),
        headers={"Idempotency-Key": "export-idem-0001"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"

    listed = viewer.get(url(ids.project_a, "/exports"))
    assert listed.status_code == 200, listed.text
    assert [item["export_job_id"] for item in listed.json()["items"]] == [job["export_job_id"]]
    filtered = viewer.get(
        url(ids.project_a, f"/exports?status=SUCCEEDED&dataset_version_id={ctx['version_id']}")
    )
    assert filtered.status_code == 200
    assert filtered.json()["items"] == []

    detail = viewer.get(url(ids.project_a, f"/exports/{job['export_job_id']}"))
    assert detail.status_code == 200
    assert detail.json()["export_job_id"] == job["export_job_id"]

    assert outsider.get(url(ids.project_a, "/exports")).status_code == 403
    cross = owner.get(url(ids.project_b, f"/exports/{job['export_job_id']}"))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    with factory() as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(OutboxEvent)
                .where(OutboxEvent.event_type == "dataset.export.requested")
            )
            >= 1
        )


def test_export_rejects_non_frozen_version_and_unsupported_format(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=4)
    editor = make_client(factory, ids.editor_a, object_storage=FakeExportStorage())
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"draft-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    draft = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert draft.status_code == 201, draft.text
    draft_id = draft.json()["dataset_version_id"]

    response = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(draft_id),
        headers={"Idempotency-Key": "export-not-frozen"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "dataset_version_not_frozen"

    unsupported = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(draft_id, format="PARQUET"),
        headers={"Idempotency-Key": "export-format-bad"},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "export_format_unsupported"

    version_bad = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(draft_id, format_version="ddp.lerobot/9.9.9"),
        headers={"Idempotency-Key": "export-version-bad"},
    )
    assert version_bad.status_code == 422
    assert version_bad.json()["error"]["code"] == "export_format_version_unsupported"

    secret = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(draft_id, config={"api_token": "must-not-store"}),
        headers={"Idempotency-Key": "export-config-bad"},
    )
    assert secret.status_code == 422
    assert secret.json()["error"]["code"] == "export_config_invalid"


def test_export_worker_success_manifest_and_download(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    created = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": "export-worker-success"},
    )
    assert created.status_code == 202, created.text
    job_id = created.json()["export_job_id"]

    result = run_export_worker(factory, storage)
    assert result.status == "PUBLISHED", result

    detail = editor.get(url(ids.project_a, f"/exports/{job_id}")).json()
    assert detail["status"] == "SUCCEEDED"
    assert detail["attempt"] == 1
    assert detail["artifact_count"] == 6
    assert detail["total_bytes"] > 0
    assert detail["aggregate_sha256"].startswith("sha256:")
    assert detail["artifact_manifest_sha256"].startswith("sha256:")
    assert detail["verification_status"] == "UNVERIFIED"

    manifest = editor.get(url(ids.project_a, f"/exports/{job_id}/artifacts"))
    assert manifest.status_code == 200, manifest.text
    body = manifest.json()
    assert body["export_job_id"] == job_id
    assert body["artifact_count"] == 6
    assert body["aggregate_sha256"] == detail["aggregate_sha256"]
    assert body["verification_status"] == "UNVERIFIED"
    paths = {item["path"] for item in body["artifacts"]}
    assert paths == {
        "meta/info.json",
        "meta/episodes.jsonl",
        "meta/media.jsonl",
        "data/episodes.jsonl",
        "meta/ddp_divisions.json",
        "ddp/sidecar.json",
    }
    for item in body["artifacts"]:
        assert item["sha256"].startswith("sha256:")
        object_key = f"{detail['output_prefix']}{item['path']}"
        assert storage.objects[object_key] is not None
        assert storage.sha256(object_key) == item["sha256"]

    divisions_key = f"{detail['output_prefix']}meta/ddp_divisions.json"
    assert b'"train"' in storage.objects[divisions_key]

    # 训练输入索引：媒体位置与标签必须来自真实数据（测试库是真实 SQLite）。
    media_key = f"{detail['output_prefix']}meta/media.jsonl"
    media_rows = [
        json.loads(line) for line in storage.objects[media_key].decode("utf-8").splitlines()
    ]
    assert len(media_rows) == 10
    assert {row["role"] for row in media_rows} == {"PRIMARY"}
    for row in media_rows:
        assert row["object_key"].startswith("raw/")
        assert row["object_key"].endswith("/content.mcap")
        assert row["sha256"].startswith("sha256:")
        assert row["size_bytes"] > 0
        assert row["duration_ns"] == 10_000
        assert row["content_type"] == "application/x-mcap"

    training_key = f"{detail['output_prefix']}data/episodes.jsonl"
    training_rows = [
        json.loads(line) for line in storage.objects[training_key].decode("utf-8").splitlines()
    ]
    assert len(training_rows) == 10
    assert {row["split"] for row in training_rows} <= {
        "TRAIN",
        "VALIDATION",
        "TEST",
    }
    for row in training_rows:
        # 测试库的批准记录 labels 为空：训练列必须如实留空，不得编造。
        assert row["labels"] == []
        assert row["action"] is None
        assert row["quality_score"] is None
        assert row["success"] is True  # 退回 Episode 自身的 success 事实列
        assert row["duration_ns"] == 10_000
        assert len(row["media_keys"]) == 1
        assert row["media_keys"][0].startswith("raw/")

    download = editor.get(
        url(ids.project_a, f"/exports/{job_id}/artifacts/meta/info.json/download")
    )
    assert download.status_code == 200, download.text
    assert download.json()["download_url"].startswith("https://fake-export.test/")
    assert storage.presign_calls[-1]["object_key"].endswith("meta/info.json")

    traversal = editor.get(url(ids.project_a, f"/exports/{job_id}/artifacts/../secret/download"))
    assert traversal.status_code == 404

    second = run_export_worker(factory, storage)
    assert second.status == "IDLE"


def test_export_rejects_stale_validity_and_cross_project_create(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    owner = make_client(factory, ids.owner_a, object_storage=storage)

    cross = owner.post(
        url(ids.project_b, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": "export-cross-project"},
    )
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    with factory() as session:
        session.execute(
            update(DatasetVersionModel)
            .where(DatasetVersionModel.id == UUID(ctx["version_id"]))
            .values(validity_status="STALE")
        )
        session.commit()

    stale = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": "export-stale-at-create"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "dataset_upstream_invalidated"


def test_export_cancel_and_if_match(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids)
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    created = editor.post(
        url(ids.project_a, "/exports"),
        json=export_payload(ctx["version_id"]),
        headers={"Idempotency-Key": "export-cancel-0001"},
    )
    assert created.status_code == 202
    job = created.json()

    missing = editor.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/cancel"),
        json={"reason": "不再需要"},
    )
    assert missing.status_code == 428

    stale = editor.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/cancel"),
        headers={"If-Match": '"99"'},
        json={"reason": "不再需要"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    cancelled = editor.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/cancel"),
        headers={"If-Match": f'"{job["version"]}"'},
        json={"reason": "不再需要"},
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"

    assert run_export_worker(factory, storage).status == "PUBLISHED"
    assert storage.objects == {}
    assert (
        editor.get(url(ids.project_a, f"/exports/{job['export_job_id']}")).json()["status"]
        == "CANCELLED"
    )

    retry = editor.post(
        url(ids.project_a, f"/exports/{job['export_job_id']}/actions/retry"),
        headers={"If-Match": f'"{cancelled.json()["version"]}"'},
        json={},
    )
    assert retry.status_code == 410
    assert retry.json()["error"]["code"] == "export_job_cancelled"
