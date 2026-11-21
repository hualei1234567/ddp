"""新数据回流闭环（DDP-V21-N7-006）端到端支撑。

**复用审计（不另起平行体系）**

- 导入链路：`tests.e2e.support` 的真实 `DataSourceImportWorker` + 物化 API +
  Episode 质量动作；本模块只把第二批数据源的 episode_key 换成
  `REFLOW_EPISODE_SPECS`，其余（URL 构造、内容签名、sha256、解析、状态机、
  RawAsset 生命周期）全部走生产实现；
- 标注链路：`tests.api.annotation_support`（Schema / 样本集 / 任务 / 批次 /
  分配 / 提交 / 复核）；
- 数据集：`tests.api.dataset_support`（策略、版本载荷）与真实版本动作；
- 导出：`tests.api.export_support` 的导出 Worker 与内存对象存储替身；
- 训练：`tests.api.training_support`（需求 / 修订 / 交付载荷）。

**外部系统的替身（显式标注）**

- Hugging Face 网络与 MinIO：沿用 `tests.e2e.support` 的显式替身；
- 训练 Adapter 网关：`LocalTrainingGateway` 只回报 `execution_mode=LOCAL`
  下的 run 引用与状态，**不执行任何真实训练计算**。真实训练平台仍 `BLOCKED`；
  MOCK 执行器由既有 `MockTrainingExecutor` 产生显式标注的占位产物，本模块
  不伪造任何训练指标。
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

from app.application.datasets.training_ports import AdapterRunRef, AdapterRunStatus
from app.domain.datasets.training_enums import ExecutionMode, ExternalTrainingStatus

from tests.api.annotation_support import (
    create_published_schema,
    create_snapshot_set,
)
from tests.api.dataset_support import (
    create_policies,
    url,
    version_payload,
)
from tests.api.export_support import (
    FakeExportStorage,
    export_payload,
    run_export_worker,
)
from tests.api.training_support import (
    approve_revision,
    create_request,
    delivery_payload,
    submit_revision,
)
from tests.e2e.support import (
    InMemoryObjectStore,
    MediaTransport,
    advance_episode_to_accepted,
    approve_annotation,
    episode_action,
    install_object_store,
    materialize_episodes,
    register_import_chain,
    run_import_worker,
)

# 第二批真实采集数据：与首批**不同**的 episode_key，物化后得到新的 Episode
# code，构成"新采集数据"而不是重放旧数据。同样是 10 条：8:1:1 划分下不会出现
# 空分片（`empty_split_policy=FAIL` 会拒绝空分片）。
REFLOW_EPISODE_SPECS: tuple[tuple[str, str, str, tuple[tuple[str, float, float], ...]], ...] = (
    tuple(
        (
            f"reflow_{index:03d}",
            f"Reflow manipulation task {index}",
            f"complete reflow manipulation task {index}",
            (("main", 0.0, 5.0 + index),),
        )
        for index in range(1, 11)
    )
)
REFLOW_EXPECTED_EPISODES = len(REFLOW_EPISODE_SPECS)
REFLOW_EXPECTED_ASSETS = sum(len(spec[3]) for spec in REFLOW_EPISODE_SPECS)

# 与主链一致的数据集筛选豁免：HF 公开预览数据不含标定、不声明时钟同步。
# 这不是"默认通过"——领域模型要求显式理由，这里如实提供。
DATASET_FILTER_FLAGS: dict[str, object] = {
    "allow_missing_calibration": True,
    "missing_calibration_reason": "HF 公开预览数据不含标定（导入链路如实留空）",
    "allow_degraded_time_sync": True,
    "degraded_confirmation_reason": "数据集未声明时钟同步，导入链路如实标记 DEGRADED",
    "scene_labels": ["kitchen"],
}


class LocalTrainingGateway:
    """训练 Adapter 网关替身（显式标注）：只回报 LOCAL 执行方式下的 run 引用。

    它**不执行训练**，也不产生任何指标；`get_status` 固定回报 SUCCEEDED 与一个
    模型引用，用于验证"训练引用 → 模型版本登记 → 评测"这条既有链路的编排。
    真实训练平台与真实权重仍 `BLOCKED`。
    """

    def __init__(self, execution_mode: str = ExecutionMode.LOCAL.value) -> None:
        self._execution_mode = execution_mode

    def capabilities(self, adapter_id: str) -> frozenset[str]:
        del adapter_id
        return frozenset({"submit", "status", "cancel"})

    def execution_mode(self, adapter_id: str) -> ExecutionMode | None:
        del adapter_id
        return ExecutionMode(self._execution_mode)

    def submit(self, **kwargs: object) -> AdapterRunRef:
        del kwargs
        return AdapterRunRef(external_run_id=f"local-run-{uuid4().hex[:8]}")

    def get_status(self, **kwargs: object) -> AdapterRunStatus:
        del kwargs
        return AdapterRunStatus(
            status=ExternalTrainingStatus.SUCCEEDED,
            model_ref={"model_id": "local-policy", "model_version": "v1"},
        )

    def cancel(self, **kwargs: object) -> AdapterRunStatus:
        del kwargs
        raise AssertionError("回流链路不取消训练")


def import_reflow_batch(
    editor: Any,
    factory: Any,
    project_id: UUID,
    *,
    code: str,
    store: InMemoryObjectStore,
) -> dict[str, Any]:
    """第二批真实采集数据：数据源 → 导入 Worker → 物化 Episode → ACCEPTED。

    ``store`` 由调用方持有：同一实例既是导入 Worker 的存储，也是 API 侧的
    存储（冻结写 Manifest、训练交付回读），否则两条路径看到的是两个世界。
    """
    source, job = register_import_chain(
        editor,
        project_id,
        code=code,
        sample_limit=REFLOW_EXPECTED_EPISODES,
        repo_id="ddp/e2e-reflow",
        revision="c" * 40,
    )
    import pytest

    with pytest.MonkeyPatch.context() as patch:
        install_object_store(patch, store)
        worker_result = run_import_worker(
            factory, MediaTransport(episode_specs=REFLOW_EPISODE_SPECS)
        )
    assert worker_result["status"] == "PUBLISHED", worker_result
    assert worker_result["episodes"] == REFLOW_EXPECTED_EPISODES, worker_result

    imported = editor.get(url(project_id, f"/data-source-imports/{job['import_id']}")).json()
    assert imported["status"] == "SUCCEEDED", imported
    assert imported["imported_row_count"] == REFLOW_EXPECTED_EPISODES, imported

    materialized = materialize_episodes(
        editor,
        project_id,
        UUID(job["import_id"]),
        scene_labels=["kitchen"],
    )
    assert materialized["episode_count"] == REFLOW_EXPECTED_EPISODES, materialized
    assert materialized["skipped_episode_keys"] == [], materialized
    episode_ids = [UUID(item["episode_id"]) for item in materialized["episodes"]]
    accepted = [
        advance_episode_to_accepted(editor, project_id, episode_id) for episode_id in episode_ids
    ]
    assert all(item["status"] == "ACCEPTED" for item in accepted), accepted
    return {
        "source": source,
        "import_job": job,
        "materialized": materialized,
        "episode_ids": episode_ids,
        "accepted": accepted,
    }


def annotate_reflow_batch(
    editor: Any,
    owner: Any,
    project_id: UUID,
    episode_ids: list[UUID],
    *,
    code: str,
    assignee_id: UUID,
) -> dict[str, Any]:
    """只对新采集的 Episode 走完整标注链路（Schema → 样本集 → 任务 → 复核批准）。"""
    schema = create_published_schema(editor, owner, project_id, f"schema-{code}")
    sample_set = create_snapshot_set(
        editor,
        project_id,
        f"sample-{code}",
        filters={"include_episode_ids": [str(item) for item in episode_ids]},
    )
    assert sample_set["selected_count"] == len(episode_ids), sample_set
    return approve_annotation(
        editor,
        owner,
        project_id,
        schema=schema,
        sample_set=sample_set,
        episode_ids=episode_ids,
        assignee_id=assignee_id,
        code=f"task-{code}",
    )


def build_reflow_version(
    editor: Any,
    owner: Any,
    project_id: UUID,
    dataset_id: UUID,
    episode_ids: list[UUID],
    *,
    code: str,
) -> dict[str, Any]:
    """在同一数据集上构建**新版本**：只圈定新采集的 Episode，构建→校验→冻结。"""
    policies = create_policies(editor, project_id, dataset_id)
    created = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions"),
        json=version_payload(
            policies,
            episode_ids=[str(item) for item in episode_ids],
            **DATASET_FILTER_FLAGS,
        ),
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["dataset_version_id"]
    built = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/build"),
        headers={"If-Match": f'"{created.json()["version"]}"'},
        json={},
    )
    assert built.status_code == 200, built.text
    validated = editor.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/validate"),
        headers={"If-Match": f'"{built.json()["version"]}"'},
        json={},
    )
    assert validated.status_code == 200, validated.text
    frozen = owner.post(
        url(project_id, f"/datasets/{dataset_id}/versions/{version_id}/actions/freeze"),
        headers={"If-Match": f'"{validated.json()["version"]}"'},
        json={"reason": "新数据回流版本，QA 独立核验通过", "payload": {"qa_verified": True}},
    )
    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["status"] == "FROZEN", frozen.json()
    return {"version_id": version_id, "version": frozen.json(), "policies": policies}


def prepare_training_delivery(
    factory: Any,
    editor: Any,
    owner: Any,
    project_id: UUID,
    *,
    version_id: str,
    target_episode_count: int,
    recipient_id: UUID,
    code: str,
) -> dict[str, Any]:
    """训练需求 → 修订提交/批准 → 导出 → 交付 → 就绪/交付/验收。"""
    request = create_request(
        editor,
        project_id,
        recipient_id,
        target_episode_count=target_episode_count,
        target_duration_ns=None,
        scene_targets=["kitchen"],
        action_targets=["pick"],
        data_modalities=["RGB"],
        quality_thresholds={"min_selected_count": target_episode_count},
        required_schema_versions=[],
        split_requirements=None,
        format="LE_ROBOT",
        format_version="ddp.lerobot/1.0.0",
    )
    submit_revision(editor, project_id, request)
    approve_revision(owner, project_id, request)
    request_body = owner.get(
        url(project_id, f"/training-requests/{request['training_request_id']}")
    ).json()

    storage = FakeExportStorage()
    export_client = _client_with_storage(factory, editor, storage)
    export_created = export_client.post(
        url(project_id, "/exports"),
        json=export_payload(version_id),
        headers={"Idempotency-Key": f"export-{code}"},
    )
    assert export_created.status_code == 202, export_created.text
    export_job_id = export_created.json()["export_job_id"]
    export_result = run_export_worker(factory, storage)
    assert export_result.status == "PUBLISHED", export_result
    export_job = export_client.get(url(project_id, f"/exports/{export_job_id}")).json()
    assert export_job["status"] == "SUCCEEDED", export_job

    delivery = export_client.post(
        url(project_id, "/training-deliveries"),
        json=delivery_payload(
            request_body,
            {"version_id": version_id, "export_job_id": export_job_id},
            recipient_id=recipient_id,
        ),
        headers={"Idempotency-Key": f"delivery-{code}"},
    )
    assert delivery.status_code == 201, delivery.text
    delivery_body = delivery.json()
    ready = episode_action(
        export_client,
        project_id,
        f"/training-deliveries/{delivery_body['training_delivery_id']}/actions/ready",
        version=delivery_body["version"],
        idempotency_key=f"delivery-ready-{code}",
    ).json()
    delivered = episode_action(
        editor,
        project_id,
        f"/training-deliveries/{delivery_body['training_delivery_id']}/actions/deliver",
        version=ready["version"],
        idempotency_key=f"delivery-deliver-{code}",
    ).json()
    accepted = episode_action(
        owner,
        project_id,
        f"/training-deliveries/{delivery_body['training_delivery_id']}/actions/accept",
        version=delivered["version"],
        payload={"acceptance_opinion": "新数据回流批次样本数、划分与需求核对均符合"},
        idempotency_key=f"delivery-accept-{code}",
    ).json()
    assert accepted["status"] == "ACCEPTED", accepted
    return {
        "request": request_body,
        "export_job": export_job,
        "delivery": accepted,
        "storage": storage,
        "export_client": export_client,
    }


def _client_with_storage(factory: Any, template: Any, storage: FakeExportStorage) -> Any:
    """按模板客户端的认证主体新建一个注入了对象存储替身的客户端。"""
    from app.api.deps import get_current_principal

    from tests.api.conftest import make_client

    principal = template.app.dependency_overrides[get_current_principal]()
    return make_client(factory, principal.user_id, object_storage=storage)


def create_external_reference(
    client: Any,
    project_id: UUID,
    delivery: dict[str, Any],
    *,
    adapter_id: str,
    execution_mode: str,
    idempotency_key: str,
) -> Any:
    return client.post(
        url(
            project_id,
            f"/training-deliveries/{delivery['training_delivery_id']}/external-references",
        ),
        json={
            "adapter_id": adapter_id,
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{uuid4().hex[:8]}",
            "execution_mode": execution_mode,
        },
        headers={"Idempotency-Key": idempotency_key},
    )


__all__ = [
    "DATASET_FILTER_FLAGS",
    "REFLOW_EPISODE_SPECS",
    "REFLOW_EXPECTED_ASSETS",
    "REFLOW_EXPECTED_EPISODES",
    "LocalTrainingGateway",
    "annotate_reflow_batch",
    "build_reflow_version",
    "create_external_reference",
    "import_reflow_batch",
    "prepare_training_delivery",
]
