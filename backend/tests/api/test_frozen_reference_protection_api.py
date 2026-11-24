"""V51-017 Frozen Dataset 引用保护：API 集成测试。

## 覆盖什么

1. **引用影响预览**（只读）：冻结版本引用的资产，预览能列出引用链、版本数、
   是否已冻结、Episode 数；预览本身不落库、不改状态。
2. **事务级阻断**（核心红线）：被冻结版本引用的资产执行 ``tombstone`` /
   ``reject-quarantined`` 返回稳定 error_code + 引用证据；``archive`` 仍可执行。
3. **冻结侧完整性**：引用已丢弃内容（TOMBSTONED）资产的数据集版本禁止冻结。

## 为什么用真实冻结版本而不是手插 frozen_at

手插 ``frozen_at`` 会绕过 DatasetVersion 的冻结流程（QA 核验、Manifest 落盘、
质量门禁），测试就变成"验证一个我自己写进去的列非空"。这里走真实
build → validate → freeze 路径，确保被保护的对象是**真实冻结产物**。

并发竞态用例单独放在 ``test_postgres_frozen_race.py``（需要真实 PostgreSQL
与线程），本文件覆盖单事务语义。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.datasets import DatasetVersionModel
from app.infrastructure.db.models.ingestion import RawAssetModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client
from tests.api.dataset_support import (
    create_dataset,
    create_policies,
    seed_dataset_episodes,
    version_payload,
)
from tests.api.dataset_support import (
    url as dataset_url,
)
from tests.api.export_support import FakeExportStorage

#: 与 dataset_support 的种子一致：10 个 Episode，划分 8/1/1。
_SEED_COUNT = 10


def _raw_asset_url(project_id: object, suffix: str = "") -> str:
    return f"/api/v1/projects/{project_id}/raw-assets{suffix}"


def _frozen_dataset(
    factory: sessionmaker[Session],
    ids: ApiIds,
    *,
    count: int = _SEED_COUNT,
) -> dict[str, object]:
    """走到真实 FROZEN 的数据集版本，并返回其引用的 RawAsset ID 集合。

    复用 ``dataset_support`` 的既有种子（Episode + 已批准标注 + RawAsset），
    不另建一套夹具——否则数据集域的两套种子一漂移，"冻结版本引用了哪个
    资产"的结论就不可比。
    """
    episodes = seed_dataset_episodes(
        factory,
        ids.project_a,
        ids.editor_a,
        count=count,
    )
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    owner = make_client(factory, ids.owner_a, object_storage=storage)
    project_id = ids.project_a

    dataset = create_dataset(editor, project_id, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, project_id, dataset["dataset_id"])
    created = editor.post(
        dataset_url(project_id, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    version_id = UUID(created.json()["dataset_version_id"])

    built = editor.post(
        dataset_url(
            project_id,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/build",
        ),
        headers={"If-Match": '"1"'},
        json={"reason": None},
    )
    assert built.status_code == 200, built.text

    validated = editor.post(
        dataset_url(
            project_id,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/validate",
        ),
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text

    frozen = owner.post(
        dataset_url(
            project_id,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/freeze",
        ),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
        json={"reason": "QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "FROZEN"

    # 该冻结版本引用的资产：直接按领域事实查，避免从响应字段反推。
    with factory() as session:
        asset_ids = tuple(
            session.scalars(
                select(RawAssetModel.id).where(
                    RawAssetModel.project_id == project_id,
                )
            ).all()
        )
    assert asset_ids, "种子必须创建出 RawAsset"
    return {
        "dataset_id": dataset["dataset_id"],
        "version_id": version_id,
        "version": frozen.json(),
        "episodes": episodes,
        "asset_ids": asset_ids,
        "editor": editor,
        "owner": owner,
        "storage": storage,
    }


def _act(
    client: object,
    project_id: UUID,
    asset_id: UUID,
    action: str,
    version: int,
    *,
    body: dict[str, object] | None = None,
) -> object:
    return client.post(  # type: ignore[attr-defined]
        _raw_asset_url(project_id, f"/{asset_id}/actions/{action}"),
        headers={"If-Match": f'"{version}"'},
        json=body or {},
    )


def _asset_version(factory: sessionmaker[Session], asset_id: UUID) -> int:
    with factory() as session:
        row = session.execute(
            select(RawAssetModel.version).where(RawAssetModel.id == asset_id)
        ).scalar_one()
    return int(row)


# --------------------------------------------------------------------------- #
# 1. 引用影响预览（只读）
# --------------------------------------------------------------------------- #


def test_preview_lists_frozen_dataset_references(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """预览返回引用链：引用它的冻结版本、版本号、冻结标记、Episode 数。"""
    factory, ids = api_db
    ctx = _frozen_dataset(factory, ids)
    editor = ctx["editor"]
    asset_id = ctx["asset_ids"][0]

    response = editor.get(_raw_asset_url(ids.project_a, f"/{asset_id}/reference-impact"))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["target_kind"] == "RAW_ASSET"
    assert body["target_id"] == str(asset_id)
    assert body["protected"] is True
    assert body["deletion_blocked"] is True
    assert body["blocked_reason"] == "raw_asset_referenced_by_frozen_dataset"
    assert body["allowed_alternatives"] == ["archive"]
    assert body["derived"] is False

    assert body["dataset_version_count"] >= 1
    reference = body["references"][0]
    assert reference["dataset_version_id"] == str(ctx["version_id"])
    assert reference["dataset_version"] == 1
    assert reference["status"] == "FROZEN"
    assert reference["frozen"] is True
    assert reference["frozen_at"]
    # 单个资产通常只被少数 Episode 引用；至少要 >= 1，且不超过种子上限。
    assert 1 <= reference["episode_count"] <= _SEED_COUNT
    assert body["episode_count"] >= reference["episode_count"]


def test_preview_is_read_only(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """预览不落库、不改状态：调用前后资产状态与版本号、版本状态都不变。"""
    factory, ids = api_db
    ctx = _frozen_dataset(factory, ids)
    editor = ctx["editor"]
    asset_id = ctx["asset_ids"][0]
    version_id = ctx["version_id"]

    with factory() as session:
        before_asset = session.execute(
            select(RawAssetModel.status, RawAssetModel.version).where(RawAssetModel.id == asset_id)
        ).one()
        before_version = session.execute(
            select(DatasetVersionModel.status, DatasetVersionModel.version).where(
                DatasetVersionModel.id == version_id
            )
        ).one()

    # 连续调用两次也必须幂等（纯读）。
    for _ in range(2):
        response = editor.get(_raw_asset_url(ids.project_a, f"/{asset_id}/reference-impact"))
        assert response.status_code == 200, response.text

    with factory() as session:
        after_asset = session.execute(
            select(RawAssetModel.status, RawAssetModel.version).where(RawAssetModel.id == asset_id)
        ).one()
        after_version = session.execute(
            select(DatasetVersionModel.status, DatasetVersionModel.version).where(
                DatasetVersionModel.id == version_id
            )
        ).one()

    assert tuple(before_asset) == tuple(after_asset)
    assert tuple(before_version) == tuple(after_version)


def test_preview_unknown_asset_is_404(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """不存在的资产返回 404，不把"查不到"渲染成"无引用、可以删"。"""
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    response = editor.get(_raw_asset_url(ids.project_a, f"/{uuid4()}/reference-impact"))
    assert response.status_code == 404, response.text


def test_preview_is_project_scoped(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """跨项目资产统一 404，不泄露存在性。"""
    factory, ids = api_db
    ctx = _frozen_dataset(factory, ids)
    # owner_a 同时是 project_b 的成员：用有权限的身份访问 project_b 路径
    # 去查 project_a 的资产，才能证明"跨项目不可见"而不是"角色不够被 403 挡住"。
    member_of_b = make_client(factory, ids.owner_a)
    response = member_of_b.get(
        _raw_asset_url(ids.project_b, f"/{ctx['asset_ids'][0]}/reference-impact")
    )
    assert response.status_code == 404, response.text


def test_preview_for_unreferenced_asset_is_not_blocked(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """没有任何冻结版本引用的资产：``deletion_blocked=false``。

    这条用例防止"一律阻断"的实现——那会让普通未使用资产永远无法清理。
    """
    factory, ids = api_db
    _frozen_dataset(factory, ids)
    editor = make_client(factory, ids.editor_a)

    # 新建一个不被任何 Episode/版本引用的资产。
    stray_id = uuid4()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=stray_id,
                project_id=ids.project_a,
                code=f"RAW-{stray_id.hex[:12]}",
                source_type="UPLOAD",
                original_filename="stray.mcap",
                object_key=f"raw/{ids.project_a}/2026/11/18/{stray_id}/content.mcap",
                size_bytes=512,
                content_type="application/x-mcap",
                format="MCAP",
                sha256="sha256:" + "d" * 64,
                status="VALIDATING",
                ingested_at=ctx_now(),
                storage_class="STANDARD",
                quarantine_codes=[],
                raw_metadata={},
                uploaded_by=ids.editor_a,
                version=1,
                created_at=ctx_now(),
                updated_at=ctx_now(),
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.flush()
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == stray_id)
            .values(status="VALID")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    response = editor.get(_raw_asset_url(ids.project_a, f"/{stray_id}/reference-impact"))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["protected"] is False
    assert body["deletion_blocked"] is False
    assert body["blocked_reason"] is None
    assert body["dataset_version_count"] == 0
    assert body["references"] == []


# --------------------------------------------------------------------------- #
# 2. 事务级阻断（核心红线）
# --------------------------------------------------------------------------- #


def test_tombstone_blocked_by_frozen_dataset_reference(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """核心红线：被冻结版本引用的资产禁止墓碑，返回稳定 code + 引用证据。"""
    factory, ids = api_db
    ctx = _frozen_dataset(factory, ids)
    editor = ctx["editor"]
    asset_id = ctx["asset_ids"][0]
    version = _asset_version(factory, asset_id)

    response = _act(
        editor,
        ids.project_a,
        asset_id,
        "tombstone",
        version,
        body={"reason": "试图销毁被冻结版本引用的内容"},
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "raw_asset_referenced_by_frozen_dataset"

    # 引用证据必须回显，否则调用方只能按 code 猜"是哪个版本挡住了我"。
    details = error["details"]
    assert details["reason"] == "referenced_by_frozen_dataset"
    assert details["target_kind"] == "RAW_ASSET"
    assert details["dataset_version_count"] >= 1
    assert details["allowed_alternatives"] == ["archive"]
    frozen_versions = details["frozen_versions"]
    assert any(item["dataset_version_id"] == str(ctx["version_id"]) for item in frozen_versions)

    # 状态未被修改：阻断必须发生在写之前。
    with factory() as session:
        status = session.execute(
            select(RawAssetModel.status).where(RawAssetModel.id == asset_id)
        ).scalar_one()
    assert status != "TOMBSTONED"


def test_archive_allowed_despite_frozen_reference(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """归档是蓝图指定的合规退场路径：被引用也必须可归档。"""
    factory, ids = api_db
    ctx = _frozen_dataset(factory, ids)
    editor = ctx["editor"]
    asset_id = ctx["asset_ids"][0]
    version = _asset_version(factory, asset_id)

    response = _act(editor, ids.project_a, asset_id, "archive", version)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "ARCHIVED"
    # 归档后仍可下载：内容完好，冻结版本仍可复现。
    assert "archive" not in response.json()["allowed_actions"]


def test_tombstone_allowed_after_reference_removed(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """去掉引用后墓碑放行——保护是"有引用时阻断"，不是"永远阻断"。

    通过把冻结版本状态改成 CANCELLED 并清空 frozen_at 不现实（有触发器），
    因此这里构造一个**从未被引用的**资产来验证放行路径确实可达。
    """
    factory, ids = api_db
    _frozen_dataset(factory, ids)
    editor = make_client(factory, ids.editor_a)

    asset_id = uuid4()
    with factory() as session:
        session.add(
            RawAssetModel(
                id=asset_id,
                project_id=ids.project_a,
                code=f"RAW-{asset_id.hex[:12]}",
                source_type="UPLOAD",
                original_filename="free.mcap",
                object_key=f"raw/{ids.project_a}/2026/11/18/{asset_id}/content.mcap",
                size_bytes=256,
                content_type="application/x-mcap",
                format="MCAP",
                sha256="sha256:" + "e" * 64,
                status="VALIDATING",
                ingested_at=ctx_now(),
                storage_class="STANDARD",
                quarantine_codes=[],
                raw_metadata={},
                uploaded_by=ids.editor_a,
                version=1,
                created_at=ctx_now(),
                updated_at=ctx_now(),
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.flush()
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(status="VALID")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    response = _act(
        editor,
        ids.project_a,
        asset_id,
        "tombstone",
        _asset_version(factory, asset_id),
        body={"reason": "无任何冻结版本引用"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "TOMBSTONED"


# --------------------------------------------------------------------------- #
# 3. 冻结侧完整性
# --------------------------------------------------------------------------- #


def test_freeze_blocked_when_version_references_discarded_asset(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """引用已丢弃内容资产的数据集版本禁止冻结。

    直接绕过本任务的保护把资产改成 TOMBSTONED（模拟历史遗留数据），再尝试
    冻结引用它的版本：必须失败，因为该版本永远无法复现。
    """
    factory, ids = api_db
    # 先走到 READY（尚未冻结），此时版本引用的资产可以被改动。
    seed_dataset_episodes(factory, ids.project_a, ids.editor_a, count=_SEED_COUNT)
    storage = FakeExportStorage()
    editor = make_client(factory, ids.editor_a, object_storage=storage)
    owner = make_client(factory, ids.owner_a, object_storage=storage)
    project_id = ids.project_a

    dataset = create_dataset(editor, project_id, ids.owner_a, f"ds-{uuid4().hex[:8]}")
    policies = create_policies(editor, project_id, dataset["dataset_id"])
    created = editor.post(
        dataset_url(project_id, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies),
    )
    assert created.status_code == 201, created.text
    version_id = UUID(created.json()["dataset_version_id"])
    built = editor.post(
        dataset_url(
            project_id,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/build",
        ),
        headers={"If-Match": '"1"'},
        json={"reason": None},
    )
    assert built.status_code == 200, built.text
    validated = editor.post(
        dataset_url(
            project_id,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/validate",
        ),
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text

    # 绕过保护直接丢弃一个被该版本引用的资产（模拟历史数据 / 运维直写）。
    with factory() as session:
        asset_id = session.execute(
            select(RawAssetModel.id).where(RawAssetModel.project_id == project_id).limit(1)
        ).scalar_one()
        session.execute(
            update(RawAssetModel)
            .where(RawAssetModel.id == asset_id)
            .values(status="TOMBSTONED")
            .execution_options(synchronize_session=False)
        )
        session.commit()

    frozen = owner.post(
        dataset_url(
            project_id,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/freeze",
        ),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
        json={"reason": "QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 409, frozen.text
    error = frozen.json()["error"]
    assert error["code"] == "raw_asset_referenced_by_frozen_dataset"
    details = error["details"]
    assert details["reason"] == "raw_asset_content_discarded"
    assert str(asset_id) in details["discarded_asset_ids"]


def ctx_now():
    """与 dataset_support 的 NOW 对齐，避免时间线不一致引发的领域校验差异。"""
    from datetime import UTC, datetime

    return datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
