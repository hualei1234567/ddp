"""主链端到端验收：数据源 → 导入 → RawAsset → Episode → 标注 → DatasetVersion
→ 导出 → 训练交付，并覆盖血缘可追溯性与跨项目隔离。

**这条测试证明什么**

每一跳都用真实 HTTP 用例或真实 Worker 执行，断言的是**落库事实与 API 响应**，
不是 mock 的返回值。外部系统只有两处替身，均在 ``support`` 中显式标注：

- Hugging Face 网络：``httpx.MockTransport``（URL 构造、内容签名、sha256、
  大小上限仍走生产 ``fetch_file``）；
- MinIO/S3：内存写入替身（导入 Worker 的唯一基础设施适配器）。

**这条测试不证明什么**

- 不证明真实 HF 可达、真实 MinIO 可写（由 ``scripts/e2e/human1m-full-chain.py``
  的真实环境验收负责）；
- 不证明迁移可升级/回滚（由 ``tests/migrations`` 负责）；
- 不证明视频内容语义正确（只验证字节级事实）。

运行：``cd backend && .venv/bin/python -m pytest tests/e2e -q``。
设置 ``DDP_TEST_POSTGRES_URL=postgresql+psycopg://ddp:ddp@localhost:55432/ddp``
时同一套用例在真实 PostgreSQL 的随机 schema 中复跑。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from uuid import UUID, uuid4

import pytest
from app.api.v1.modelops_dependencies import get_artifact_inspector
from app.application.modelops.ports import ArtifactInspector
from app.core.config import Settings

from tests.api.annotation_support import create_published_schema, create_snapshot_set
from tests.api.conftest import make_client
from tests.api.dataset_support import create_dataset, create_policies, url, version_payload
from tests.api.export_support import FakeExportStorage, export_payload, run_export_worker
from tests.api.training_support import (
    approve_revision,
    create_request,
    delivery_payload,
    submit_revision,
)
from tests.e2e.conftest import E2EEnv
from tests.e2e.support import (
    EXPECTED_ASSETS,
    EXPECTED_EPISODES,
    MODEL_ADAPTER_SECRET,
    InMemoryObjectStore,
    MediaTransport,
    advance_episode_to_accepted,
    approve_annotation,
    episode_action,
    expected_media_paths,
    install_object_store,
    materialize_episodes,
    model_payload,
    model_version_payload,
    post_adapter_callback,
    register_import_chain,
    run_import_worker,
)

ARTIFACT_SHA = "sha256:" + "a" * 64


class FakeArtifactInspector(ArtifactInspector):
    """模型产物检查器替身（显式标注）：产物由外部训练平台上传，不在本链路内。

    与 ``tests/api/test_modelops_api.py`` 的同名替身同义：只声明"对象存在且
    大小/哈希与登记一致"，不替代真实对象存储读取。
    """

    def object_size(self, object_key: str) -> int | None:
        del object_key
        return 128

    def object_sha256(self, object_key: str) -> str | None:
        del object_key
        return ARTIFACT_SHA


@pytest.fixture(scope="module")
def chain(e2e_env: E2EEnv) -> Iterator[dict[str, Any]]:
    """把主链一次性搭起来；各用例只做断言，不再重复昂贵的链路构建。"""
    factory, ids = e2e_env.factory, e2e_env.ids
    # 导入 Worker 与 API 共用同一个对象存储替身：冻结要写 Manifest 并回读校验
    # （V31-040），训练交付还要再读同一个对象。API 客户端不注入替身时，CI 里
    # 没有 MinIO，冻结会 503 object_storage_unavailable——链路本身没问题，
    # 是测试没把外部基础设施替换完整。
    store = InMemoryObjectStore()
    editor = make_client(factory, ids.editor_a, object_storage=store)
    owner = make_client(factory, ids.owner_a, object_storage=store)
    viewer = make_client(factory, ids.viewer_a, object_storage=store)

    # ---- 1. 登记数据源 + 创建导入任务 ----
    code = f"hf-e2e-{uuid4().hex[:8]}"
    source, import_job = register_import_chain(editor, ids.project_a, code=code)
    assert import_job["status"] == "PENDING"

    # ---- 2. 导入执行：真实 DataSourceImportWorker + 真实 ImportRunner ----
    with pytest.MonkeyPatch.context() as patch:
        install_object_store(patch, store)
        worker_result = run_import_worker(factory, MediaTransport())
    assert worker_result["status"] == "PUBLISHED", worker_result
    assert worker_result["episodes"] == EXPECTED_EPISODES, worker_result
    assert worker_result["assets"] == EXPECTED_ASSETS, worker_result

    imported = editor.get(url(ids.project_a, f"/data-source-imports/{import_job['import_id']}"))
    assert imported.status_code == 200, imported.text
    import_body = imported.json()
    assert import_body["status"] == "SUCCEEDED", import_body
    assert import_body["imported_row_count"] == EXPECTED_EPISODES, import_body
    assert import_body["imported_file_count"] == EXPECTED_ASSETS, import_body

    # ---- 3. 质量报告：平台自身的导入质量规则引擎（真实规则，不是常量 PASS）----
    report = editor.get(
        url(ids.project_a, f"/data-source-imports/{import_job['import_id']}/quality-report")
    )
    assert report.status_code == 200, report.text
    quality = report.json()
    assert quality["failures"] == [], quality

    assets_response = editor.get(
        url(ids.project_a, f"/data-source-imports/{import_job['import_id']}/assets")
    )
    assert assets_response.status_code == 200, assets_response.text
    media_assets = assets_response.json()["items"]
    assert len(media_assets) == EXPECTED_ASSETS

    # ---- 4. 物化 Canonical Episode ----
    materialized = materialize_episodes(
        editor,
        ids.project_a,
        UUID(import_job["import_id"]),
        scene_labels=["kitchen"],
    )
    assert materialized["episode_count"] == EXPECTED_EPISODES, materialized
    assert materialized["skipped_episode_keys"] == []
    episode_ids = [UUID(item["episode_id"]) for item in materialized["episodes"]]
    episodes_by_key = {item["episode_key"]: item for item in materialized["episodes"]}
    cameras_by_episode: dict[str, set[str]] = {}
    for asset in media_assets:
        cameras_by_episode.setdefault(asset["episode_key"], set()).add(asset["camera"])
    raw_asset_ids = [
        UUID(asset) for item in materialized["episodes"] for asset in item["raw_asset_ids"]
    ]
    assert len(raw_asset_ids) == EXPECTED_ASSETS

    # ---- 5. Episode 就绪（平台自动生成 Manifest）→ 质检 → ACCEPTED ----
    #
    # 导入物化时平台自己生成了 Canonical Manifest：这里断言"没有人提交
    # Manifest，Episode 也已经就绪"，并且失败时必须给出可读原因。
    ready_bodies = {
        key: editor.get(url(ids.project_a, f"/episodes/{UUID(item['episode_id'])}")).json()
        for key, item in episodes_by_key.items()
    }
    for body in ready_bodies.values():
        # demo_001 是多机位且源数据不含标定：就绪放行（缺标定只记 notice，
        # 能否训练由数据集准入判断），因此这里与其余 Episode 同样是 READY。
        assert body["status"] == "READY", body
        assert body["manifest_key"] and body["manifest_sha256"], body
        assert body["frame_count"] > 0, body
        assert body["readiness_blockers"] == [], body
        # 帧数按「Episode 时长 × 声明帧率」推导：0.0–5.0s / 30fps -> 150 帧起。
        assert body["frame_count"] >= 150, body
    accepted = [
        advance_episode_to_accepted(editor, ids.project_a, episode_id) for episode_id in episode_ids
    ]
    assert all(item["status"] == "ACCEPTED" for item in accepted), accepted
    assert all(item["quality_status"] == "PASS" for item in accepted), accepted

    # ---- 6. 标注：Schema → 样本集 → 任务 → 提交 → 复核批准 ----
    schema = create_published_schema(editor, owner, ids.project_a, f"schema-{code}")
    sample_set = create_snapshot_set(editor, ids.project_a, f"sample-{code}")
    assert sample_set["selected_count"] == EXPECTED_EPISODES, sample_set
    annotation = approve_annotation(
        editor,
        owner,
        ids.project_a,
        schema=schema,
        sample_set=sample_set,
        episode_ids=episode_ids,
        assignee_id=ids.editor_a,
        code=f"task-{code}",
    )
    annotated = [editor.get(url(ids.project_a, f"/episodes/{item}")).json() for item in episode_ids]
    assert all(item["annotation_status"] == "APPROVED" for item in annotated), annotated

    # ---- 7. DatasetVersion：构建 → 校验 → 冻结 ----
    dataset = create_dataset(editor, ids.project_a, ids.owner_a, f"ds-{code}")
    policies = create_policies(editor, ids.project_a, dataset["dataset_id"])
    filters = {
        # 公开数据集不含标定、也不声明时钟同步：按域模型提供的显式豁免确认，
        # 不把 CALIBRATION_MISSING / TIME_SYNC_DEGRADED 当成"默认通过"。
        "allow_missing_calibration": True,
        "missing_calibration_reason": "HF 公开预览数据不含标定（导入链路如实留空）",
        "allow_degraded_time_sync": True,
        "degraded_confirmation_reason": "数据集未声明时钟同步，导入链路如实标记 DEGRADED",
        "scene_labels": ["kitchen"],
    }
    created_version = editor.post(
        url(ids.project_a, f"/datasets/{dataset['dataset_id']}/versions"),
        json=version_payload(policies, **filters),
    )
    assert created_version.status_code == 201, created_version.text
    version_id = created_version.json()["dataset_version_id"]
    built = editor.post(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/actions/build",
        ),
        headers={"If-Match": f'"{created_version.json()["version"]}"'},
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
    version_body = frozen.json()
    assert version_body["status"] == "FROZEN", version_body

    items = editor.get(
        url(
            ids.project_a,
            f"/datasets/{dataset['dataset_id']}/versions/{version_id}/items",
        )
    )
    assert items.status_code == 200, items.text
    item_episode_ids = {item["episode_id"] for item in items.json()["items"]}
    assert item_episode_ids == {str(item) for item in episode_ids}, item_episode_ids

    # ---- 8. 导出：真实导出 Worker + 对象存储替身 ----
    export_storage = FakeExportStorage()
    export_client = make_client(factory, ids.editor_a, object_storage=export_storage)
    export_created = export_client.post(
        url(ids.project_a, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": f"export-{code}"},
    )
    assert export_created.status_code == 202, export_created.text
    export_job_id = export_created.json()["export_job_id"]
    export_result = run_export_worker(factory, export_storage)
    assert export_result.status == "PUBLISHED", export_result
    export_job = editor.get(url(ids.project_a, f"/exports/{export_job_id}")).json()
    assert export_job["status"] == "SUCCEEDED", export_job

    # ---- 9. 训练需求 → 修订批准 → 交付（含需求核对）----
    request = create_request(
        editor,
        ids.project_a,
        ids.owner_a,
        target_episode_count=EXPECTED_EPISODES,
        target_duration_ns=None,
        scene_targets=["kitchen"],
        action_targets=["pick"],
        data_modalities=["RGB"],
        quality_thresholds={"min_selected_count": EXPECTED_EPISODES},
        required_schema_versions=[],
        split_requirements=None,
        format="LE_ROBOT",
        format_version="ddp.lerobot/1.0.0",
    )
    submit_revision(editor, ids.project_a, request)
    approve_revision(owner, ids.project_a, request)
    request_body = owner.get(
        url(ids.project_a, f"/training-requests/{request['training_request_id']}")
    ).json()

    # 交付创建/就绪都会回读导出产物做校验，必须使用注入了对象存储替身的客户端。
    delivery = export_client.post(
        url(ids.project_a, "/training-deliveries"),
        json=delivery_payload(
            request_body,
            {"version_id": version_id, "export_job_id": export_job_id},
            # recipient 有 FK 到 users：必须指向真实项目成员。
            recipient_id=ids.owner_a,
        ),
        headers={"Idempotency-Key": f"delivery-{code}"},
    )
    assert delivery.status_code == 201, delivery.text
    delivery_body = delivery.json()
    match = delivery_body["requirement_match"]
    # 数据模态维度没有可核对的版本事实 → UNVERIFIABLE（WARN），但它不是 FAILED；
    # 其余维度必须真实通过，且整体不可被阻断。
    assert match["status"] == "UNVERIFIABLE", match
    assert match["failed_dimensions"] == [], match
    assert match["blocked"] is False, match

    ready = episode_action(
        export_client,
        ids.project_a,
        f"/training-deliveries/{delivery_body['training_delivery_id']}/actions/ready",
        version=delivery_body["version"],
        idempotency_key=f"delivery-ready-{code}",
    ).json()
    assert ready["status"] == "READY", ready
    delivered = episode_action(
        editor,
        ids.project_a,
        f"/training-deliveries/{delivery_body['training_delivery_id']}/actions/deliver",
        version=ready["version"],
        idempotency_key=f"delivery-deliver-{code}",
    ).json()
    assert delivered["status"] == "DELIVERED", delivered
    accepted_delivery = episode_action(
        owner,
        ids.project_a,
        f"/training-deliveries/{delivery_body['training_delivery_id']}/actions/accept",
        version=delivered["version"],
        payload={"acceptance_opinion": "样本数、划分、需求核对均符合"},
        idempotency_key=f"delivery-accept-{code}",
    ).json()
    assert accepted_delivery["status"] == "ACCEPTED", accepted_delivery
    accepted_match = accepted_delivery["requirement_match"]
    assert accepted_match["status"] == "UNVERIFIABLE", accepted_match
    assert accepted_match["failed_dimensions"] == [], accepted_match

    # ---- 10. 模型版本 → 评测 → 反馈 → 数据缺口 → 数据需求闭环 ----
    model_settings = Settings(
        env="local",
        model_adapter_shared_secrets={"mock": MODEL_ADAPTER_SECRET},
    )
    inspector = FakeArtifactInspector()
    model_client = make_client(factory, ids.editor_a, settings_override=model_settings)
    model_client.app.dependency_overrides[get_artifact_inspector] = lambda: inspector
    model_owner = make_client(factory, ids.owner_a, settings_override=model_settings)
    model_owner.app.dependency_overrides[get_artifact_inspector] = lambda: inspector

    model = model_client.post(
        url(ids.project_a, "/models"), json=model_payload(ids.owner_a, f"model-{code}")
    )
    assert model.status_code == 201, model.text
    model_body = model.json()
    version = model_client.post(
        url(ids.project_a, f"/models/{model_body['model_id']}/versions"),
        json=model_version_payload(sha256=ARTIFACT_SHA),
    )
    assert version.status_code == 201, version.text
    model_version_body = version.json()
    validated_version = episode_action(
        model_client,
        ids.project_a,
        f"/models/{model_body['model_id']}/versions/{model_version_body['model_version_id']}"
        "/actions/validate",
        version=model_version_body["version"],
    ).json()
    assert validated_version["status"] == "READY", validated_version
    released = episode_action(
        model_owner,
        ids.project_a,
        f"/models/{model_body['model_id']}/versions/{model_version_body['model_version_id']}"
        "/actions/publish",
        version=validated_version["version"],
    ).json()
    assert released["status"] == "RELEASED", released

    evaluation = model_client.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": model_version_body["model_version_id"],
            "dataset_version_id": version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert evaluation.status_code == 202, evaluation.text
    evaluation_id = evaluation.json()["evaluation_id"]
    callback = post_adapter_callback(
        model_client,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": evaluation_id,
            "metrics": {"success_rate": 0.42},
        },
    )
    assert callback.status_code == 204, callback.text
    evaluated = model_client.get(url(ids.project_a, f"/evaluations/{evaluation_id}")).json()
    assert evaluated["status"] == "SUCCEEDED", evaluated
    assert evaluated["verdict"] == "FAIL", evaluated

    feedback = model_client.post(
        url(ids.project_a, "/model-feedback"),
        json={
            "model_version_id": model_version_body["model_version_id"],
            "evaluation_id": evaluation_id,
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "厨房场景抓取成功率仅 0.42，需要补充同场景样本",
            "dimensions": [
                {
                    "dimension_type": "SCENE",
                    "dimension_value": "kitchen",
                    "current_count": 0,
                    "target_count": 10,
                }
            ],
            "evidence_refs": ["evaluations/report.json"],
        },
    )
    assert feedback.status_code == 201, feedback.text
    feedback_body = feedback.json()

    gaps = model_client.get(url(ids.project_a, "/data-gaps")).json()
    kitchen = next(item for item in gaps["items"] if item["dimension_value"] == "kitchen")
    # 手工填的 current_count 被真实覆盖统计取代：项目内 2 条 kitchen Episode 可统计。
    assert kitchen["coverage_source"] == "MEASURED", kitchen
    assert kitchen["current_count"] == EXPECTED_EPISODES, kitchen
    assert kitchen["suggested_episode_count"] == 10 - EXPECTED_EPISODES, kitchen

    converted = episode_action(
        model_client,
        ids.project_a,
        f"/model-feedback/{feedback_body['feedback_id']}/actions/create-requirement",
        version=feedback_body["version"],
        payload={"requirement_code": f"DR-{code}", "target_episode_count": 8},
    ).json()
    assert converted["status"] == "REQUIREMENT_CREATED", converted
    requirement_id = converted["converted_requirement_id"]

    requirement = model_client.get(
        url(ids.project_a, f"/data-requirements/{requirement_id}")
    ).json()
    assert requirement["source_feedback_ids"] == [feedback_body["feedback_id"]]

    submitted = episode_action(
        model_client,
        ids.project_a,
        f"/data-requirements/{requirement_id}/actions/submit",
        version=requirement["version"],
    ).json()
    approved = episode_action(
        model_owner,
        ids.project_a,
        f"/data-requirements/{requirement_id}/actions/approve",
        version=submitted["version"],
    ).json()
    assert approved["status"] == "APPROVED", approved
    satisfied = episode_action(
        model_owner,
        ids.project_a,
        f"/data-requirements/{requirement_id}/actions/satisfy",
        version=approved["version"],
        payload={"dataset_version_ids": [str(version_id)]},
    ).json()
    assert satisfied["status"] == "SATISFIED", satisfied
    assert satisfied["linked_dataset_version_ids"] == [str(version_id)]

    retest = model_client.post(
        url(ids.project_a, "/evaluations"),
        json={
            "model_version_id": model_version_body["model_version_id"],
            "dataset_version_id": version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert retest.status_code == 202, retest.text
    retest_id = retest.json()["evaluation_id"]
    retest_callback = post_adapter_callback(
        model_client,
        {
            "event_id": f"evt-{uuid4().hex[:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(ids.project_a),
            "evaluation_id": retest_id,
            "metrics": {"success_rate": 0.95},
        },
    )
    assert retest_callback.status_code == 204, retest_callback.text
    linked = episode_action(
        model_owner,
        ids.project_a,
        f"/data-requirements/{requirement_id}/actions/link-retest",
        version=satisfied["version"],
        payload={"evaluation_id": retest_id},
    ).json()
    assert linked["retest_evaluation_ids"] == [retest_id], linked
    closed = episode_action(
        model_owner,
        ids.project_a,
        f"/data-requirements/{requirement_id}/actions/close",
        version=linked["version"],
    ).json()
    assert closed["status"] == "CLOSED", closed

    # ---- 11. 推理：任务入队（外部 Adapter 执行，见报告 BLOCKED 说明）----
    inference = model_client.post(
        url(ids.project_a, "/inference-tasks"),
        json={
            "model_version_id": model_version_body["model_version_id"],
            "episode_ids": [str(episode_ids[0])],
        },
    )
    assert inference.status_code == 202, inference.text
    inference_body = inference.json()
    assert inference_body["status"] == "QUEUED", inference_body

    yield {
        "env": e2e_env,
        "editor": editor,
        "owner": owner,
        "viewer": viewer,
        "source": source,
        "import_job": import_job,
        "import_body": import_body,
        "quality": quality,
        "media_assets": media_assets,
        "episodes_by_key": episodes_by_key,
        "cameras_by_episode": cameras_by_episode,
        "episode_ids": episode_ids,
        "raw_asset_ids": raw_asset_ids,
        "annotation": annotation,
        "dataset": dataset,
        "version_id": UUID(version_id),
        "version": version_body,
        "item_episode_ids": item_episode_ids,
        "export_job": export_job,
        "request": request_body,
        "delivery": accepted_delivery,
        "model": model_body,
        "model_version": released,
        "evaluation_id": evaluation_id,
        "feedback": feedback_body,
        "requirement": closed,
        "inference_task": inference_body,
        "store": store,
    }


def test_main_chain_reaches_frozen_dataset_version_and_training_delivery(
    chain: dict[str, Any],
) -> None:
    # Episode 承载多模态流：demo_001 两路机位、其余单机位；机位集合来自真实资产。
    assert chain["cameras_by_episode"]["demo_001"] == {"main", "wrist"}
    assert len(chain["episodes_by_key"]["demo_001"]["raw_asset_ids"]) == 2
    assert len(chain["episodes_by_key"]["demo_002"]["raw_asset_ids"]) == 1
    # 时间基座：导入链路没有时钟同步声明，必须如实 DEGRADED + NONE，不能伪造成 VERIFIED。
    first_episode = (
        chain["editor"]
        .get(url(chain["env"].ids.project_a, f"/episodes/{chain['episode_ids'][0]}"))
        .json()
    )
    assert first_episode["time_base"]["sync_status"] == "DEGRADED"
    assert first_episode["time_base"]["sync_method"] == "NONE"

    assert chain["import_body"]["status"] == "SUCCEEDED"
    assert chain["import_body"]["imported_row_count"] == EXPECTED_EPISODES
    assert chain["import_body"]["imported_file_count"] == EXPECTED_ASSETS
    assert chain["version"]["status"] == "FROZEN"
    assert chain["version"]["item_count"] == EXPECTED_EPISODES
    assert chain["delivery"]["status"] == "ACCEPTED"
    assert chain["delivery"]["requirement_match"]["failed_dimensions"] == []
    assert chain["requirement"]["status"] == "CLOSED"


def test_lineage_is_traceable_in_both_directions(chain: dict[str, Any]) -> None:
    """Episode → RawAsset → 导入任务 → 数据源 与 DatasetVersion → Episode。"""
    editor = chain["editor"]
    project_id = chain["env"].ids.project_a
    episode_ids: list[UUID] = chain["episode_ids"]

    for episode_id in episode_ids:
        # 边方向：RAW_ASSET --SEGMENTED_INTO--> EPISODE，所以从 Episode 出发用 reverse。
        inbound = editor.get(
            f"/api/v1/episodes/{episode_id}/lineage",
            params={"project_id": str(project_id), "direction": "reverse", "depth": 2},
        )
        assert inbound.status_code == 200, inbound.text
        edges = inbound.json()["items"]
        asset_edges = [
            edge
            for edge in edges
            if edge["from_node_type"] == "RAW_ASSET" and edge["relation"] == "SEGMENTED_INTO"
        ]
        assert asset_edges, edges
        for edge in asset_edges:
            asset_id = edge["from_node_id"]
            forward = editor.get(
                f"/api/v1/raw-assets/{asset_id}/lineage",
                params={"project_id": str(project_id), "direction": "forward", "depth": 1},
            )
            assert forward.status_code == 200, forward.text
            assert {
                (item["to_node_type"], item["to_node_id"]) for item in forward.json()["items"]
            } >= {("EPISODE", str(episode_id))}

            asset = editor.get(url(project_id, f"/raw-assets/{asset_id}")).json()
            metadata = asset["raw_metadata"]
            assert metadata["origin"] == "data_source_import"
            assert metadata["data_source_id"] == chain["source"]["data_source_id"]
            assert metadata["import_id"] == chain["import_job"]["import_id"]
            assert metadata["revision"] == chain["source"]["revision"]
            assert metadata["source_path"] in expected_media_paths()

    # DatasetVersion → Episode：反向遍历能枚举全部入选 Episode。
    reverse = editor.get(
        url(
            project_id,
            f"/datasets/{chain['dataset']['dataset_id']}/versions/{chain['version_id']}/lineage",
        ),
        params={"direction": "reverse", "depth": 1, "limit": 100},
    )
    assert reverse.status_code == 200, reverse.text
    included = {
        item["from_node_id"]
        for item in reverse.json()["items"]
        if item["to_node_type"] == "DATASET_VERSION" and item["relation"] == "INCLUDED_IN"
    }
    assert included == {str(item) for item in episode_ids}, included


def test_cross_project_access_returns_404_for_read_and_write(chain: dict[str, Any]) -> None:
    """A 项目资源从 B 项目路径访问：一律 404，不泄露存在性。"""
    ids = chain["env"].ids
    project_b = ids.project_b
    cross = make_client(chain["env"].factory, ids.owner_a)  # owner_a 同时是 B 的 owner

    read_urls = [
        f"/data-sources/{chain['source']['data_source_id']}",
        f"/data-source-imports/{chain['import_job']['import_id']}",
        f"/episodes/{chain['episode_ids'][0]}",
        f"/datasets/{chain['dataset']['dataset_id']}",
        f"/datasets/{chain['dataset']['dataset_id']}/versions/{chain['version_id']}",
        f"/training-requests/{chain['request']['training_request_id']}",
        f"/training-deliveries/{chain['delivery']['training_delivery_id']}",
        f"/models/{chain['model']['model_id']}",
        f"/evaluations/{chain['evaluation_id']}",
        f"/data-requirements/{chain['requirement']['requirement_id']}",
    ]
    for suffix in read_urls:
        response = cross.get(url(project_b, suffix))
        assert response.status_code == 404, (suffix, response.status_code, response.text)

    # 不带项目路径的血缘端点：项目由查询参数决定，同样不得跨项目。
    lineage = cross.get(
        f"/api/v1/episodes/{chain['episode_ids'][0]}/lineage",
        params={"project_id": str(project_b), "direction": "forward"},
    )
    assert lineage.status_code == 404, lineage.text
    asset_lineage = cross.get(
        f"/api/v1/raw-assets/{chain['raw_asset_ids'][0]}/lineage",
        params={"project_id": str(project_b), "direction": "reverse"},
    )
    assert asset_lineage.status_code == 404, asset_lineage.text

    episode = chain["editor"].get(url(ids.project_a, f"/episodes/{chain['episode_ids'][0]}")).json()
    # 每个端点的请求体按各自 Schema 构造：先通过参数校验，才证明 404 来自
    # 服务端项目隔离，而不是请求体不合法。
    write_cases = [
        (
            f"/data-source-imports/{chain['import_job']['import_id']}/actions/cancel",
            chain["import_body"]["version"],
            {},
        ),
        (
            f"/episodes/{chain['episode_ids'][0]}/actions/invalidate",
            episode["version"],
            {"reason": "跨项目写入"},
        ),
        (
            f"/datasets/{chain['dataset']['dataset_id']}/versions/{chain['version_id']}"
            "/actions/build",
            chain["version"]["version"],
            {},
        ),
        (
            f"/training-deliveries/{chain['delivery']['training_delivery_id']}/actions/ready",
            chain["delivery"]["version"],
            {},
        ),
        (
            f"/models/{chain['model']['model_id']}/actions/activate",
            chain["model"]["version"],
            {},
        ),
    ]
    for suffix, version, body in write_cases:
        response = cross.post(
            url(project_b, suffix),
            headers={"If-Match": f'"{version}"', "Idempotency-Key": f"x-{uuid4().hex[:8]}"},
            json=body,
        )
        assert response.status_code == 404, (suffix, response.status_code, response.text)

    # 非成员访问本项目：403（存在"项目"但无成员资格），不落到 404 混淆。
    outsider = make_client(chain["env"].factory, ids.outsider)
    denied = outsider.get(url(ids.project_a, f"/episodes/{chain['episode_ids'][0]}"))
    assert denied.status_code == 403, denied.text


def test_model_loop_details_are_recorded(chain: dict[str, Any]) -> None:
    assert chain["model_version"]["status"] == "RELEASED"
    assert chain["delivery"]["item_count"] == EXPECTED_EPISODES
    # 需求核对逐维度留证：失败维度为空，且关键维度有真实 actual 值。
    dimensions = {
        item["dimension"]: item for item in chain["delivery"]["requirement_match"]["dimensions"]
    }
    assert dimensions["target_episode_count"]["status"] == "PASSED"
    assert dimensions["scene_targets"]["status"] == "PASSED"
    assert dimensions["action_targets"]["status"] == "PASSED"
    assert dimensions["format"]["status"] == "PASSED"
    assert dimensions["target_episode_count"]["actual"] == EXPECTED_EPISODES
    assert dimensions["quality_thresholds"]["status"] == "PASSED"
    # 版本/产物尚未声明数据模态：必须如实 UNVERIFIABLE，不能伪造成"通过"。
    assert dimensions["data_modalities"]["status"] == "UNVERIFIABLE"
    # 推理任务已入队；外部 Adapter 未配置时平台不会伪造成功（见报告）。
    assert chain["inference_task"]["status"] == "QUEUED"
