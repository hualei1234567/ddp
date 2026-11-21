"""新数据回流闭环端到端验收（DDP-V21-N7-006）。

**这条测试证明什么**

在既有主链（Evaluation → Failure → Gap → Requirement）之上，用**真实执行**的
第二批采集数据把闭环走完：

1. 缺口 → 数据需求（反馈转需求、提交、审批）；
2. 新采集：第二个数据源 → 真实导入 Worker → 物化新 Episode → 质检 ACCEPTED；
3. 新数据集版本：只圈定新 Episode，构建 → 校验 → 冻结（与评测所用版本不同）；
4. 训练引用：训练需求 → 交付 → 外部训练引用。MOCK 结果**显式标注**且被
   模型版本登记入口拒绝；LOCAL 引用经真实状态同步到 SUCCEEDED 后登记模型版本；
5. 复评：新模型版本在新数据集版本上评测 PASS；
6. 缺口关闭：需求 satisfy(新版本) → link-retest → close，反馈 resolve，
   缺口视图 open_feedback_count=0、实测覆盖达到目标、建议补采 0。

每一步都断言落库事实（状态、血缘、审计、outbox），不是替身返回值。

**这条测试不证明什么**

- 不证明真实训练平台可用、不产生真实权重/指标：`LocalTrainingGateway` 只是
  Adapter 网关替身（LOCAL 执行方式），真实训练仍 `BLOCKED`；
- 不证明真实 HF 可达或真实 MinIO 可写（由 `scripts/e2e/*` 的真实环境验收负责）；
- 不证明迁移可升级/回滚（由 `tests/migrations` 负责）。

运行：``cd backend && .venv/bin/python -m pytest tests/e2e -q``；
设置 ``DDP_TEST_POSTGRES_URL`` 时在真实 PostgreSQL 随机 schema 中复跑。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from app.api.v1.modelops_dependencies import get_artifact_inspector
from app.api.v1.training_dependencies import get_training_adapter_gateway
from app.core.config import Settings
from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
from sqlalchemy import select

from tests.api.conftest import make_client
from tests.api.dataset_support import url
from tests.api.modelops_support import FakeInspector
from tests.api.training_support import FakeTrainingGateway
from tests.e2e import test_main_chain_e2e as _main_chain
from tests.e2e.reflow_support import (
    REFLOW_EXPECTED_ASSETS,
    REFLOW_EXPECTED_EPISODES,
    LocalTrainingGateway,
    annotate_reflow_batch,
    build_reflow_version,
    create_external_reference,
    import_reflow_batch,
    prepare_training_delivery,
)
from tests.e2e.support import (
    EXPECTED_EPISODES,
    MODEL_ADAPTER_SECRET,
    InMemoryObjectStore,
    episode_action,
    model_version_payload,
    post_adapter_callback,
)

# 复用主链的 module 级夹具：本模块与主链模块各自实例化一次（独立随机 schema），
# 回流用例只在其上追加新数据，不改写主链已冻结的断言。
chain = _main_chain.chain

ARTIFACT_SHA = "sha256:" + "a" * 64
TARGET_EPISODE_COUNT = EXPECTED_EPISODES + REFLOW_EXPECTED_EPISODES


def _kitchen(gaps: dict[str, Any]) -> dict[str, Any]:
    return next(item for item in gaps["items"] if item["dimension_value"] == "kitchen")


def _model_clients(factory: Any, ids: Any, store: InMemoryObjectStore) -> tuple[Any, Any]:
    settings = Settings(
        env="local",
        model_adapter_shared_secrets={"mock": MODEL_ADAPTER_SECRET},
        # 回流链路写操作远超单窗口默认配额；限流本身由专门的中间件测试覆盖，
        # 这里关闭以免把"写得多"误判成限流缺陷。
        rate_limit_enabled=False,
    )
    inspector = FakeInspector(128, ARTIFACT_SHA)
    editor = make_client(factory, ids.editor_a, object_storage=store, settings_override=settings)
    owner = make_client(factory, ids.owner_a, object_storage=store, settings_override=settings)
    for client in (editor, owner):
        client.app.dependency_overrides[get_artifact_inspector] = lambda: inspector
    return editor, owner


def _plain_clients(factory: Any, ids: Any, store: InMemoryObjectStore) -> tuple[Any, Any]:
    settings = Settings(env="local", rate_limit_enabled=False)
    return (
        make_client(factory, ids.editor_a, object_storage=store, settings_override=settings),
        make_client(factory, ids.owner_a, object_storage=store, settings_override=settings),
    )


def test_requirement_to_new_data_to_retrain_to_retest_closes_gap(chain: dict[str, Any]) -> None:
    env = chain["env"]
    factory, ids = env.factory, env.ids
    project_id = ids.project_a
    model_id = chain["model"]["model_id"]
    model_version_id = chain["model_version"]["model_version_id"]
    dataset_id = UUID(chain["dataset"]["dataset_id"])
    evaluated_version_id = chain["version_id"]
    # 同一实例贯穿导入 Worker 与 API：冻结写 Manifest、交付回读走的都是它。
    store = InMemoryObjectStore()
    editor, owner = _plain_clients(factory, ids, store)
    model_client, model_owner = _model_clients(factory, ids, store)

    # ---- 1. 缺口：目标 20，当前实测 10，建议补采 10 ----
    feedback = editor.post(
        url(project_id, "/model-feedback"),
        json={
            "model_version_id": model_version_id,
            "evaluation_id": chain["evaluation_id"],
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "厨房场景抓取成功率不足，需要补充同场景新采集数据",
            "dimensions": [
                {"dimension_type": "SCENE", "dimension_value": "kitchen", "target_count": 20}
            ],
            "evidence_refs": ["evaluations/reflow-report.json"],
        },
    )
    assert feedback.status_code == 201, feedback.text
    feedback_body = feedback.json()
    gap_before = _kitchen(editor.get(url(project_id, "/data-gaps")).json())
    assert gap_before["coverage_source"] == "MEASURED", gap_before
    assert gap_before["current_count"] == EXPECTED_EPISODES, gap_before
    assert gap_before["target_count"] == TARGET_EPISODE_COUNT, gap_before
    assert gap_before["suggested_episode_count"] == REFLOW_EXPECTED_EPISODES, gap_before
    assert gap_before["open_feedback_count"] == 1, gap_before

    # ---- 2. 反馈 → 数据需求：DRAFT → SUBMITTED → APPROVED ----
    converted = episode_action(
        model_client,
        project_id,
        f"/model-feedback/{feedback_body['feedback_id']}/actions/create-requirement",
        version=feedback_body["version"],
        payload={"requirement_code": f"DR-REFLOW-{feedback_body['feedback_id'][:8]}"},
    ).json()
    assert converted["status"] == "REQUIREMENT_CREATED", converted
    requirement_id = converted["converted_requirement_id"]
    requirement = model_client.get(url(project_id, f"/data-requirements/{requirement_id}")).json()
    submitted = episode_action(
        model_client,
        project_id,
        f"/data-requirements/{requirement_id}/actions/submit",
        version=requirement["version"],
    ).json()
    approved = episode_action(
        model_owner,
        project_id,
        f"/data-requirements/{requirement_id}/actions/approve",
        version=submitted["version"],
    ).json()
    assert approved["status"] == "APPROVED", approved

    # ---- 3. 新采集：第二个数据源 → 真实导入 Worker → 新 Episode ACCEPTED ----
    batch = import_reflow_batch(
        editor,
        factory,
        project_id,
        code=f"hf-reflow-{feedback_body['feedback_id'][:8]}",
        store=store,
    )
    new_episode_ids: list[UUID] = batch["episode_ids"]
    assert len(new_episode_ids) == REFLOW_EXPECTED_EPISODES
    assert set(new_episode_ids).isdisjoint(set(chain["episode_ids"]))
    new_keys = {item["episode_key"] for item in batch["materialized"]["episodes"]}
    assert new_keys == {f"reflow_{index:03d}" for index in range(1, 11)}, new_keys
    # 新 Episode 的 RawAsset 来源是**本次**导入任务，而不是重放首批数据。
    first_episode = editor.get(url(project_id, f"/episodes/{new_episode_ids[0]}")).json()
    assert first_episode["status"] == "ACCEPTED", first_episode
    assert first_episode["quality_status"] == "PASS", first_episode
    assert first_episode["scene_labels"] == ["kitchen"], first_episode
    new_assets = editor.get(
        url(project_id, f"/data-source-imports/{batch['import_job']['import_id']}/assets")
    ).json()["items"]
    assert len(new_assets) == REFLOW_EXPECTED_ASSETS, new_assets
    new_raw_asset_ids = [
        UUID(asset_id)
        for item in batch["materialized"]["episodes"]
        for asset_id in item["raw_asset_ids"]
    ]
    raw_asset = editor.get(url(project_id, f"/raw-assets/{new_raw_asset_ids[0]}")).json()
    assert raw_asset["raw_metadata"]["import_id"] == batch["import_job"]["import_id"], raw_asset

    # ---- 4. 新数据集版本：只圈定新 Episode，冻结后与评测版本不同 ----
    annotate_reflow_batch(
        editor,
        owner,
        project_id,
        new_episode_ids,
        code=f"reflow-{feedback_body['feedback_id'][:8]}",
        assignee_id=ids.editor_a,
    )
    annotated = [
        editor.get(url(project_id, f"/episodes/{item}")).json() for item in new_episode_ids
    ]
    assert all(item["annotation_status"] == "APPROVED" for item in annotated), annotated

    reflow_version = build_reflow_version(
        editor,
        owner,
        project_id,
        dataset_id,
        new_episode_ids,
        code=f"reflow-{feedback_body['feedback_id'][:8]}",
    )
    reflow_version_id = reflow_version["version_id"]
    assert reflow_version_id != str(evaluated_version_id)
    assert reflow_version["version"]["item_count"] == REFLOW_EXPECTED_EPISODES, reflow_version
    items = editor.get(
        url(project_id, f"/datasets/{dataset_id}/versions/{reflow_version_id}/items")
    ).json()
    assert {item["episode_id"] for item in items["items"]} == {
        str(item) for item in new_episode_ids
    }

    # ---- 5. 训练引用：交付 → MOCK（显式标注、下游拒绝）/ LOCAL（可登记） ----
    training = prepare_training_delivery(
        factory,
        editor,
        owner,
        project_id,
        version_id=reflow_version_id,
        target_episode_count=REFLOW_EXPECTED_EPISODES,
        recipient_id=ids.owner_a,
        code=f"reflow-{feedback_body['feedback_id'][:8]}",
    )
    delivery = training["delivery"]
    assert delivery["status"] == "ACCEPTED", delivery
    assert delivery["requirement_match"]["failed_dimensions"] == [], delivery
    assert delivery["requirement_match"]["blocked"] is False, delivery
    export_client = training["export_client"]

    # MOCK：显式标注的占位结果，不得被登记成真实模型版本。
    export_client.app.dependency_overrides[get_training_adapter_gateway] = lambda: (
        FakeTrainingGateway("MOCK")
    )
    mock_reference = create_external_reference(
        export_client,
        project_id,
        delivery,
        adapter_id="mock-training",
        execution_mode="MOCK",
        idempotency_key=f"ext-mock-{feedback_body['feedback_id'][:8]}",
    )
    assert mock_reference.status_code == 202, mock_reference.text
    mock_body = mock_reference.json()
    assert mock_body["execution_mode"] == "MOCK", mock_body
    assert mock_body["external_status"] == "SUCCEEDED", mock_body
    assert mock_body["artifacts"]["mock"] is True, mock_body
    mock_rejected = model_client.post(
        url(project_id, f"/models/{model_id}/versions"),
        json={
            **model_version_payload(sha256=ARTIFACT_SHA),
            "external_ref_id": mock_body["external_ref_id"],
            "training_delivery_id": delivery["training_delivery_id"],
            "training_request_id": delivery["training_request_id"],
            "dataset_version_ids": [reflow_version_id],
        },
    )
    assert mock_rejected.status_code == 422, mock_rejected.text
    assert mock_rejected.json()["error"]["code"] == "model_training_source_invalid"

    # LOCAL：Adapter 网关替身（不执行真实训练），经真实状态同步到 SUCCEEDED。
    export_client.app.dependency_overrides[get_training_adapter_gateway] = (
        lambda: LocalTrainingGateway()
    )
    local_reference = create_external_reference(
        export_client,
        project_id,
        delivery,
        adapter_id="local-training",
        execution_mode="LOCAL",
        idempotency_key=f"ext-local-{feedback_body['feedback_id'][:8]}",
    )
    assert local_reference.status_code == 202, local_reference.text
    local_body = local_reference.json()
    assert local_body["execution_mode"] == "LOCAL", local_body
    synced = episode_action(
        export_client,
        project_id,
        f"/external-training-references/{local_body['external_ref_id']}/actions/sync",
        version=local_body["version"],
    ).json()
    assert synced["external_status"] == "SUCCEEDED", synced
    assert synced["model_ref"]["model_id"] == "local-policy", synced

    # 新模型版本：训练来源三件套 + 新数据集版本。
    new_version = model_client.post(
        url(project_id, f"/models/{model_id}/versions"),
        json={
            **model_version_payload(sha256=ARTIFACT_SHA),
            "training_request_id": delivery["training_request_id"],
            "training_delivery_id": delivery["training_delivery_id"],
            "external_ref_id": synced["external_ref_id"],
            "dataset_version_ids": [reflow_version_id],
        },
    )
    assert new_version.status_code == 201, new_version.text
    new_version_body = new_version.json()
    assert new_version_body["external_ref_id"] == synced["external_ref_id"], new_version_body
    assert new_version_body["dataset_version_ids"] == [reflow_version_id], new_version_body
    ready_version = episode_action(
        model_client,
        project_id,
        f"/models/{model_id}/versions/{new_version_body['model_version_id']}/actions/validate",
        version=new_version_body["version"],
    ).json()
    assert ready_version["status"] == "READY", ready_version

    # 血缘：新数据集版本 --TRAINED_ON--> 两条训练引用（MOCK 与 LOCAL 都在图里）。
    lineage = editor.get(
        url(project_id, f"/datasets/{dataset_id}/versions/{reflow_version_id}/lineage"),
        params={"direction": "forward", "depth": 1, "limit": 100},
    )
    assert lineage.status_code == 200, lineage.text
    trained_on = {
        (item["to_node_id"], item["metadata"].get("execution_mode"))
        for item in lineage.json()["items"]
        if item["relation"] == "TRAINED_ON" and item["from_node_id"] == reflow_version_id
    }
    assert trained_on == {
        (mock_body["external_ref_id"], "MOCK"),
        (synced["external_ref_id"], "LOCAL"),
    }, trained_on

    # ---- 6. 复评：新模型版本在新数据集版本上评测 PASS ----
    retest = model_client.post(
        url(project_id, "/evaluations"),
        json={
            "model_version_id": new_version_body["model_version_id"],
            "dataset_version_id": reflow_version_id,
            "metrics": {},
            "thresholds": {"success_rate": {"min": 0.9}},
        },
    )
    assert retest.status_code == 202, retest.text
    retest_id = retest.json()["evaluation_id"]
    callback = post_adapter_callback(
        model_client,
        {
            "event_id": f"evt-reflow-{feedback_body['feedback_id'][:8]}",
            "adapter_version": "1.0.0",
            "event_type": "evaluation.completed",
            "project_id": str(project_id),
            "evaluation_id": retest_id,
            "metrics": {"success_rate": 0.95},
        },
    )
    assert callback.status_code == 204, callback.text
    retest_body = model_client.get(url(project_id, f"/evaluations/{retest_id}")).json()
    assert retest_body["status"] == "SUCCEEDED", retest_body
    assert retest_body["verdict"] == "PASS", retest_body

    # ---- 7. 缺口关闭：satisfy(新版本) → link-retest → close → 反馈 resolve ----
    satisfied = episode_action(
        model_owner,
        project_id,
        f"/data-requirements/{requirement_id}/actions/satisfy",
        version=approved["version"],
        payload={"dataset_version_ids": [reflow_version_id]},
    ).json()
    assert satisfied["status"] == "SATISFIED", satisfied
    assert satisfied["linked_dataset_version_ids"] == [reflow_version_id], satisfied
    linked = episode_action(
        model_owner,
        project_id,
        f"/data-requirements/{requirement_id}/actions/link-retest",
        version=satisfied["version"],
        payload={"evaluation_id": retest_id},
    ).json()
    assert linked["retest_evaluation_ids"] == [retest_id], linked
    closed = episode_action(
        model_owner,
        project_id,
        f"/data-requirements/{requirement_id}/actions/close",
        version=linked["version"],
    ).json()
    assert closed["status"] == "CLOSED", closed

    resolved = episode_action(
        model_client,
        project_id,
        f"/model-feedback/{feedback_body['feedback_id']}/actions/resolve",
        version=converted["version"],
        payload={"resolution_note": "新采集 10 条同场景数据并完成重训复评，缺口关闭"},
    ).json()
    assert resolved["status"] == "RESOLVED", resolved

    # ---- 8. 缺口视图：无未关闭反馈/需求，实测覆盖达到目标，建议补采 0 ----
    #
    # ``target_count`` 只来自**未关闭**反馈的人工目标；反馈 RESOLVED 后该维度
    # 不再有目标值，因此是 None（不是 0）。缺口是否关闭由"无未关闭反馈/需求 +
    # 实测覆盖 + 建议补采 0"共同回答，不能用 target_count 反推。
    gap_after = _kitchen(model_client.get(url(project_id, "/data-gaps")).json())
    assert gap_after["open_feedback_count"] == 0, gap_after
    assert gap_after["coverage_source"] == "MEASURED", gap_after
    assert gap_after["current_count"] == TARGET_EPISODE_COUNT, gap_after
    assert gap_after["target_count"] is None, gap_after
    assert gap_after["suggested_episode_count"] == 0, gap_after
    assert requirement_id in gap_after["requirement_ids"], gap_after
    assert gap_after["sources"] == [], gap_after

    # ---- 9. 审计与 outbox：每一步都有留痕，且都是留痕类事件 ----
    with factory() as session:
        audit_actions = set(
            session.scalars(
                select(AuditLog.action).where(
                    AuditLog.project_id == project_id,
                    AuditLog.resource_type == "DATA_REQUIREMENT",
                    AuditLog.resource_id == UUID(requirement_id),
                )
            )
        )
        assert {"create-from-feedback", "submit", "approve", "satisfy", "link-retest", "close"} <= (
            audit_actions
        ), audit_actions
        reference_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.project_id == project_id,
                AuditLog.resource_type == "EXTERNAL_TRAINING_REFERENCE",
                AuditLog.resource_id.in_(
                    [UUID(mock_body["external_ref_id"]), UUID(synced["external_ref_id"])]
                ),
            )
        ).all()
        assert {item.action for item in reference_audits} == {"create", "sync"}, reference_audits
        evaluation_audits = session.scalars(
            select(AuditLog).where(
                AuditLog.project_id == project_id,
                AuditLog.resource_type == "EVALUATION",
                AuditLog.resource_id == UUID(retest_id),
            )
        ).all()
        assert {item.action for item in evaluation_audits} >= {"create"}, evaluation_audits

        events = session.scalars(
            select(OutboxEvent).where(
                OutboxEvent.project_id == project_id,
                OutboxEvent.event_type.in_(
                    [
                        "modelops.feedback.created",
                        "modelops.feedback.create-requirement",
                        "modelops.feedback.resolve",
                        "modelops.data_requirement.created",
                        "modelops.data_requirement.submit",
                        "modelops.data_requirement.approve",
                        "modelops.data_requirement.satisfy",
                        "modelops.data_requirement.link-retest",
                        "modelops.data_requirement.close",
                        "training.external_reference.created",
                        "modelops.model_version.created",
                        "modelops.evaluation.created",
                        "dataset.version.frozen",
                    ]
                ),
            )
        ).all()
        seen = {item.event_type for item in events}
        assert {
            "modelops.feedback.created",
            "modelops.feedback.create-requirement",
            "modelops.feedback.resolve",
            "modelops.data_requirement.created",
            "modelops.data_requirement.submit",
            "modelops.data_requirement.approve",
            "modelops.data_requirement.satisfy",
            "modelops.data_requirement.link-retest",
            "modelops.data_requirement.close",
            "training.external_reference.created",
            "modelops.model_version.created",
            "modelops.evaluation.created",
            "dataset.version.frozen",
        } <= seen, seen
        # 回流链路全部是留痕类事件：写入即终态，不应出现在待投递队列里。
        assert all(item.dispatch is False for item in events), events
        assert all(item.status == "RECORDED" for item in events), events

    # ---- 10. 跨项目隔离：新数据集版本从 B 项目路径访问一律 404 ----
    cross = make_client(factory, ids.owner_a)
    denied = cross.get(url(ids.project_b, f"/datasets/{dataset_id}/versions/{reflow_version_id}"))
    assert denied.status_code == 404, denied.text
