"""数据集 HTTP API 测试：生命周期、权限、幂等、跨项目、去重与不可变。"""

from __future__ import annotations

import hashlib
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from app.infrastructure.db.models.datasets import DatasetModel, DatasetVersionModel
from app.infrastructure.db.models.episodes import EpisodeModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    url,
    version_payload,
)
from tests.api.export_support import FakeExportStorage

SHA_E = "sha256:" + "e" * 64


def _setup(factory: sessionmaker[Session], ids: ApiIds, *, count: int = 10, **seed) -> dict:
    episodes = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=count,
        **seed,
    )
    # 冻结会把 Manifest 写入对象存储并回读校验；内存替身让断言可以复核
    # 「库里 Hash == 存储里的字节」而不依赖本机 MinIO。
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    owner = make_client(factory, ids.owner_a, object_storage=storage)
    viewer = make_client(factory, ids.viewer_a, object_storage=storage)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    return {
        "episodes": episodes,
        "storage": storage,
        "editor": editor,
        "owner": owner,
        "viewer": viewer,
        "dataset": dataset,
        "policies": policies,
        "version": created.json(),
    }


def _preview_body(policies: dict[str, object], **filters: object) -> dict[str, object]:
    return {
        "selection_filters": filters,
        "dedup_policy_id": policies["dedup"]["dedup_policy_id"],
        "dedup_policy_version": policies["dedup"]["version"],
        "split_policy_id": policies["split"]["split_policy_id"],
        "split_policy_version": policies["split"]["version"],
        "leakage_policy_id": policies["leakage"]["leakage_policy_id"],
        "leakage_policy_version": policies["leakage"]["version"],
        "quality_policy_id": policies["quality"]["quality_policy_id"],
        "quality_policy_version": policies["quality"]["version"],
        "sample_limit": 20,
    }


def test_dataset_version_lifecycle_reports_and_freeze(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    ctx = _setup(factory, ids)
    editor = ctx["editor"]
    owner = ctx["owner"]
    project_id = ids.project_a
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]

    preview = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/selection-preview"),
        json=_preview_body(ctx["policies"]),
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["candidate_count"] == 10
    assert body["selected_count"] == 10
    assert body["duplicated_count"] == 0
    assert body["split_counts"] == {"train": 8, "validation": 1, "test": 1}
    assert body["split_preview_status"] == "OK"
    assert body["preview_sha256"].startswith("sha256:")

    built = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/build"),
        headers={"If-Match": '"1"'},
        json={"reason": None},
    )
    assert built.status_code == 200, built.text
    version = built.json()
    assert version["status"] == "VALIDATING"
    assert version["leakage_status"] in {"WARN", "PASS"}
    assert version["manifest_sha256"].startswith("sha256:")

    leakage = editor.get(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/leakage-report")
    )
    assert leakage.status_code == 200, leakage.text
    assert leakage.json()["leakage_status"] == "WARN"
    assert leakage.json()["soft_violation_count"] >= 1
    quality = editor.get(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/quality-report")
    )
    assert quality.status_code == 200, quality.text
    assert quality.json()["status"] == "READY"
    assert quality.json()["blocking_issues"] == []
    assert quality.json()["split_counts"] == {"train": 8, "validation": 1, "test": 1}

    validated = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/validate"),
        headers={"If-Match": f'"{version["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    ready = validated.json()
    assert ready["status"] == "READY"
    assert ready["item_count"] == 10
    assert (ready["train_count"], ready["validation_count"], ready["test_count"]) == (8, 1, 1)

    items = editor.get(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/items?limit=100")
    )
    assert items.status_code == 200, items.text
    assert len(items.json()["items"]) == 10
    splits = [item["split"] for item in items.json()["items"]]
    assert splits.count("TRAIN") == 8
    assert splits.count("VALIDATION") == 1
    assert splits.count("TEST") == 1

    manifest = editor.get(url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/manifest"))
    assert manifest.status_code == 200, manifest.text
    assert manifest.json()["manifest_sha256"] == ready["manifest_sha256"]
    assert len(manifest.json()["items"]) == 10

    self_freeze = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/freeze"),
        headers={"If-Match": f'"{ready["version"]}"'},
        json={"payload": {"qa_verified": True}},
    )
    assert self_freeze.status_code == 403, self_freeze.text
    assert self_freeze.json()["error"]["code"] == "dataset_freeze_approval_forbidden"

    frozen = owner.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/freeze"),
        headers={"If-Match": f'"{ready["version"]}"'},
        json={"reason": "QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 200, frozen.text
    frozen_body = frozen.json()
    assert frozen_body["status"] == "FROZEN"
    assert frozen_body["frozen_by"] == str(ids.owner_a)
    assert frozen_body["freeze_approval_id"]
    assert frozen_body["manifest_sha256"] == ready["manifest_sha256"]
    # V31-040：冻结时 Manifest 必须真正落到对象存储，且可独立复算校验。
    manifest_key = frozen_body["manifest_key"]
    assert manifest_key == ready["manifest_key"]
    assert manifest_key == (f"datasets/{project_id}/{dataset_id}/{version_id}/manifest.json")
    stored = ctx["storage"].objects[manifest_key]
    assert hashlib.sha256(stored).hexdigest() == frozen_body["manifest_sha256"][7:]

    dataset = editor.get(url(project_id, f"/datasets/{dataset_id}"))
    assert dataset.status_code == 200
    assert dataset.json()["status"] == "ACTIVE"
    assert dataset.json()["current_version_id"] == version_id

    illegal = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/build"),
        headers={"If-Match": f'"{frozen_body["version"]}"'},
        json={},
    )
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "state_transition_invalid"

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AuditLog)) >= 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) >= 1
        with pytest.raises(IntegrityError):
            session.execute(
                update(DatasetVersionModel)
                .where(DatasetVersionModel.id == UUID(version_id))
                .values(manifest_sha256=SHA_E)
            )
            session.flush()
        session.rollback()
        with pytest.raises(IntegrityError):
            session.execute(delete(DatasetModel).where(DatasetModel.id == UUID(dataset_id)))
            session.flush()
        session.rollback()


def test_dataset_freeze_rejects_manifest_readback_mismatch(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """回读字节与声明 Hash 不一致时必须拒绝冻结，不能只信写成功。"""
    factory, ids = api_db
    ctx = _setup(factory, ids)
    editor = ctx["editor"]
    project_id = ids.project_a
    dataset_id = ctx["dataset"]["dataset_id"]
    version_id = ctx["version"]["dataset_version_id"]

    built = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/build"),
        headers={"If-Match": '"1"'},
        json={"reason": None},
    )
    assert built.status_code == 200, built.text
    validated = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/validate"),
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    ready = validated.json()
    assert ready["status"] == "READY"

    ctx["storage"].corrupt_next_read()
    frozen = ctx["owner"].post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/freeze"),
        headers={"If-Match": f'"{ready["version"]}"'},
        json={"reason": "QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 409, frozen.text
    assert frozen.json()["error"]["code"] == "dataset_manifest_mismatch"

    current = editor.get(url(project_id, f"/datasets/{dataset_id}/versions/{version_id}"))
    assert current.status_code == 200
    assert current.json()["status"] == "READY"


def test_dataset_permissions_idempotency_cross_project_and_if_match(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    owner = make_client(factory, ids.owner_a)
    viewer = make_client(factory, ids.viewer_a)
    outsider = make_client(factory, ids.outsider)
    base = "/datasets"

    denied = viewer.post(
        url(ids.project_a, base),
        json={
            "code": "viewer-denied",
            "name": "拒绝",
            "purpose": "TRAINING",
            "owner_id": str(ids.owner_a),
            "usage_policy": "TRAINING_ALLOWED",
        },
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "forbidden"
    assert outsider.get(url(ids.project_a, base)).status_code == 403

    payload = {
        "code": "ds-idem",
        "name": "幂等数据集",
        "purpose": "TRAINING",
        "owner_id": str(ids.owner_a),
        "usage_policy": "TRAINING_ALLOWED",
    }
    created = editor.post(
        url(ids.project_a, base),
        json=payload,
        headers={"Idempotency-Key": "idem-dataset-0001"},
    )
    assert created.status_code == 201, created.text
    first = created.json()
    replay = editor.post(
        url(ids.project_a, base),
        json=payload,
        headers={"Idempotency-Key": "idem-dataset-0001"},
    )
    assert replay.status_code == 201
    assert replay.json()["dataset_id"] == first["dataset_id"]
    reused = editor.post(
        url(ids.project_a, base),
        json={**payload, "name": "不同请求体"},
        headers={"Idempotency-Key": "idem-dataset-0001"},
    )
    assert reused.status_code == 409
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    duplicate = editor.post(url(ids.project_a, base), json=payload)
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_resource"
    extra = editor.post(url(ids.project_a, base), json={**payload, "status": "ACTIVE"})
    assert extra.status_code == 422
    assert extra.json()["error"]["code"] == "validation_failed"

    missing = editor.patch(
        url(ids.project_a, f"/datasets/{first['dataset_id']}"),
        json={"name": "缺少 If-Match"},
    )
    assert missing.status_code == 428
    updated = editor.patch(
        url(ids.project_a, f"/datasets/{first['dataset_id']}"),
        headers={"If-Match": '"1"'},
        json={"name": "新名称"},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    stale = editor.patch(
        url(ids.project_a, f"/datasets/{first['dataset_id']}"),
        headers={"If-Match": '"1"'},
        json={"name": "陈旧版本"},
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "version_conflict"

    assert viewer.get(url(ids.project_a, f"/datasets/{first['dataset_id']}")).status_code == 200
    foreign = owner.post(
        url(ids.project_b, base),
        json={
            "code": "foreign-dataset",
            "name": "项目 B 数据集",
            "purpose": "TRAINING",
            "owner_id": str(ids.owner_a),
            "usage_policy": "TRAINING_ALLOWED",
        },
    )
    assert foreign.status_code == 201, foreign.text
    cross = editor.get(url(ids.project_a, f"/datasets/{foreign.json()['dataset_id']}"))
    assert cross.status_code == 404
    assert cross.json()["error"]["code"] == "resource_not_found"

    policies = create_policies(editor, ids.project_a, first["dataset_id"])
    viewer_version = viewer.post(
        url(ids.project_a, f"/datasets/{first['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert viewer_version.status_code == 403


def test_selection_exclusion_and_duplicate_statistics(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    lineage = uuid4()
    manifest = "sha256:" + "f" * 64
    raw = "sha256:" + "1" * 64
    lineages = [lineage, lineage] + [uuid4() for _ in range(8)]
    manifests = [f"sha256:{index:064x}" for index in range(10)]
    manifests[2] = manifests[3] = manifest
    raw_hashes = [f"sha256:{index + 20:064x}" for index in range(10)]
    raw_hashes[4] = raw_hashes[5] = raw
    episodes = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=10,
        lineage_ids=lineages,
        manifest_shas=manifests,
        raw_hashes=raw_hashes,
    )
    with factory() as session:
        session.execute(
            update(EpisodeModel).where(EpisodeModel.id == episodes[8]).values(status="INVALIDATED")
        )
        session.execute(
            update(EpisodeModel)
            .where(EpisodeModel.id == episodes[9])
            .values(annotation_status="REJECTED")
        )
        session.commit()
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, "dup-dataset")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    created = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["dataset_version_id"]
    preview = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/selection-preview",
        ),
        json=_preview_body(policies),
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["candidate_count"] == 10
    assert body["excluded_count"] == 2
    assert body["duplicated_count"] == 3
    assert body["selected_count"] == 5
    assert body["split_preview_status"] == "EMPTY_TEST_SPLIT"
    codes = {item["code"] for item in body["exclusion_summary"]}
    assert {
        "EPISODE_STATUS_INELIGIBLE",
        "ANNOTATION_NOT_APPROVED",
        "DUPLICATE",
    } <= codes


def test_create_version_with_build_now_does_not_crash(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """``build_now=true`` 必须真的能建并构建，而不是 500。

    这是一个真实存在过的缺陷：``DatasetVersionUseCases.create`` 在
    ``build_now`` 分支调用 ``apply_action`` 时漏传必填关键字参数 ``path``，
    于是「建版本并立即构建」这条最常用的路径**在任何情况下都会 TypeError**。
    既有测试从未传 ``build_now``，因此一直没被发现。
    """
    factory, ids = api_db
    # 8:1:1 划分下 6 条会让 test 为空（业务校验会拒绝，这是正确行为）；
    # 本用例要验的是"不 500"，故用 20 条确保划分成立。
    seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=20)
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])

    response = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json={**version_payload(policies), "build_now": True},
    )

    assert response.status_code == 201, response.text
    body = response.json()
    # build 由 create 内部触发，返回的是推进后的版本：状态不再是 DRAFT。
    assert body["status"] in {"BUILDING", "READY", "VALIDATING"}


def test_dedup_similarity_method_rejects_unsupported_value_with_supported_list(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """不支持的近重复方法必须 422 并回显 ``supported``，不静默降级。

    契约刻意保留自由字符串（收窄已发布请求字段属破坏性变更），因此"只支持
    ``TEMPORAL_OVERLAP_V1``"这条约束必须由响应表达清楚：客户端要知道哪个值
    不可用、能用什么，而不是收到一句泛化的校验失败。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{uuid4().hex[:8]}")

    rejected = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/dedup-policies"),
        json={
            "exact_keys": ["EPISODE_LINEAGE"],
            "near_duplicate_enabled": True,
            "similarity_method": "PERCEPTUAL_HASH_V9",
        },
    )
    assert rejected.status_code == 422, rejected.text
    body = rejected.json()["error"]
    assert body["code"] == "validation_failed"
    assert body["details"] == {
        "field": "similarity_method",
        "provided": "PERCEPTUAL_HASH_V9",
        "supported": ["TEMPORAL_OVERLAP_V1"],
    }

    accepted = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/dedup-policies"),
        json={
            "exact_keys": ["EPISODE_LINEAGE"],
            "near_duplicate_enabled": True,
            "similarity_method": "TEMPORAL_OVERLAP_V1",
        },
    )
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["similarity_method"] == "TEMPORAL_OVERLAP_V1"
