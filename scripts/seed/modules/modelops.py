"""模型闭环模块：模型 / 版本 / 评估 / 推理服务 / 推理任务 / 反馈 / 数据缺口。

覆盖的状态路径
--------------
- 模型：``DRAFT → ACTIVE``（主模型）；另一条 ``DRAFT → ACTIVE → DEPRECATED →
  ACTIVE → DEPRECATED → ARCHIVED``（验证 deprecate/restore/archive）；
- 模型版本：``DRAFT → READY``（validate 同步完成产物校验）；另一条
  ``DRAFT → READY → RELEASED``（发布人另有其人与 Owner 校验）；
- 推理服务：``PENDING → DEPLOYING → ACTIVE → STOPPED → RETIRED``；另一条
  ``PENDING → DEPLOYING → ACTIVE``（保留 ACTIVE 供界面展示）；
- 模型反馈：``OPEN → TRIAGED → REQUIREMENT_CREATED``（create-requirement 会自动
  建数据需求，保证数据缺口视图有内容）；另一条 ``OPEN → DISMISSED``；第三条保留 OPEN；
- 评测：有 FROZEN 数据集版本时 ``QUEUED → CANCELLED``（演示环境不接评测集群）；
- 推理任务：有 Episode 时 ``QUEUED → CANCELLED``（演示环境不接推理集群）。

关于产物
--------
``validate`` 会去对象存储读取产物并核对 size/sha256。平台没有「直接写对象」的
接口，只有上传会话（``/upload-sessions``）这一条真实通路，因此这里先走
上传会话把演示产物落盘，再用它的 ``object_key`` 建版本。上传 helper 暂放在本
模块内——基座 ``client.py`` 还没有上传能力（见交付报告）。

关于阻塞
--------
- 评测要求 ``dataset_version_id`` 处于 FROZEN（evaluations.py:189-190）；没有冻结
  版本时显式打印 BLOCKED，不用假 ID 蒙混（跨项目 ID 会被项目隔离拒绝）；
- 推理任务要求 ``dataset_version_id`` 或 ``episode_ids``（inference.py:328）；
  两者都没有时同样打印 BLOCKED。
"""

from __future__ import annotations

import hashlib
import http.client
import socket
import urllib.parse
from typing import Any

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "modelops"

# 演示产物内容：真实字节，size/sha256 由服务端校验，不是占位串。
_ARTIFACT_BYTES = (
    b'{"seed": "ddp-modelops", "format": "other", "note": "demo artifact for local stack"}\n'
)
_ARTIFACT_FORMAT = "OTHER"
_ARTIFACT_CONTENT_TYPE = "application/json"


def _create(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    payload = expect(label, client.call("POST", path, body=body), 201)
    assert isinstance(payload, dict)
    return payload


def _accept(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    payload = expect(label, client.call("POST", path, body=body), 202)
    assert isinstance(payload, dict)
    return payload


def _read(client: Client, path: str, label: str) -> dict[str, Any]:
    payload = expect(label, client.call("GET", path), 200)
    assert isinstance(payload, dict)
    return payload


def _act(
    client: Client,
    path: str,
    action: str,
    version: int,
    label: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # 部分动作端点把请求体声明成「必填但可为空」，因此统一发字典而不是 None。
    payload = expect(
        label,
        client.call(
            "POST",
            f"{path}/actions/{action}",
            body=dict(body or {}),
            headers=if_match(version),
        ),
        200,
    )
    assert isinstance(payload, dict)
    return payload


def _connect(url: str) -> tuple[http.client.HTTPConnection, str, str]:
    """把预签名 URL 拆成「可连接的地址 + 保留原 Host 头 + 请求路径」。

    预签名地址里的主机名是容器网络内的服务名（``minio``），宿主机解析不了；
    但 SigV4 把 Host 算进了签名，不能直接换域名。这里保持 Host 不变、只把 TCP
    连接指向本机，等价于 ``curl --resolve``。
    """
    parts = urllib.parse.urlsplit(url)
    authority = parts.netloc
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.getaddrinfo(host, port)
        target = host
    except socket.gaierror:
        target = "127.0.0.1"
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    factory = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    return factory(target, port, timeout=120), authority, path


def _put_object(url: str, headers: dict[str, str], data: bytes, label: str) -> str:
    conn, authority, path = _connect(url)
    try:
        conn.request("PUT", path, body=data, headers={**headers, "Host": authority})
        response = conn.getresponse()
        response.read()
        if response.status != 200:
            raise SeedFailure(f"{label}：上传字节失败，状态 {response.status}")
        etag = response.getheader("ETag")
    finally:
        conn.close()
    if not etag:
        raise SeedFailure(f"{label}：对象存储未返回 ETag")
    return etag.strip('"')


def _storage_quota_configured(client: Client, base: str) -> bool:
    """项目是否配置了对象存储配额（决定上传 422 的归因，不是猜测）。"""
    quotas = _read(client, f"{base}/quotas", "读取资源配额")
    return any(
        str(item.get("resource_kind")) == "OBJECT_STORAGE_BYTES"
        for item in (quotas.get("items") or [])
    )


def _existing_object(client: Client, base: str) -> dict[str, Any] | None:
    """取项目内已存在的 RawAsset，作为产物对象的降级来源（取最小的一个）。"""
    assets = _read(client, f"{base}/raw-assets?limit=100", "读取已有原始资产")
    candidates = [
        item
        for item in (assets.get("items") or [])
        if int(item.get("size_bytes") or 0) > 0 and item.get("object_key")
    ]
    if not candidates:
        return None
    item = min(candidates, key=lambda entry: int(entry["size_bytes"]))
    return {
        "object_key": str(item["object_key"]),
        "sha256": str(item["sha256"]),
        "size_bytes": int(item["size_bytes"]),
        "raw_asset_id": str(item["asset_id"]),
    }


def _upload(
    client: Client,
    ctx: Context,
    *,
    filename: str,
    content: bytes,
    content_type: str,
    format_hint: str,
    label: str,
) -> dict[str, Any]:
    """走真实上传会话把字节写进对象存储，返回会话与 RawAsset。

    已知缺陷（backend/app/infrastructure/repositories/governance.py:311-317、
    backend/app/domain/governance/models.py:120-128）：
    ``coalesce(sum(raw_assets.size_bytes), 0)`` 在 Postgres 里是 numeric，被
    ``ResourceUsage`` 判成非整数，于是**只要项目配了 OBJECT_STORAGE_BYTES 配额，
    所有上传都会 422**。这里显式识别该场景，打印 DEGRADED 并复用项目内已有对象；
    没有可用对象就如实失败，不伪造 object_key。
    """
    base = f"/api/v1/projects/{ctx.project_id}"
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    status, session = client.call(
        "POST",
        f"{base}/upload-sessions",
        body={
            "original_filename": filename,
            "declared_content_type": content_type,
            "declared_size_bytes": len(content),
            "expected_sha256": digest,
            "format_hint": format_hint,
        },
        headers={"Idempotency-Key": idempotency_key()},
    )
    if status != 201:
        if status == 422 and _storage_quota_configured(client, base):
            fallback = _existing_object(client, base)
            if fallback is None:
                raise SeedFailure(
                    f"{label}：对象存储配额导致上传 422，且项目内没有可复用的 RawAsset"
                )
            print(
                f"  ! DEGRADED {label}：治理配额缺陷使上传不可用，"
                f"复用项目内已有对象 {fallback['object_key']}"
            )
            return fallback
        expect(f"{label}创建上传会话", (status, session), 201)
    assert isinstance(session, dict)
    session_id = str(session["upload_session_id"])
    part_path = f"{base}/upload-sessions/{session_id}/parts/1"
    part = expect(
        f"{label}申请分片地址",
        client.call("POST", f"{part_path}/url", body={"sha256": digest}),
        200,
    )
    assert isinstance(part, dict)
    etag = _put_object(part["url"], dict(part.get("headers") or {}), content, label)
    expect(
        f"{label}登记分片",
        client.call(
            "POST",
            f"{part_path}/ack",
            body={"etag": etag, "size_bytes": len(content), "sha256": digest},
        ),
        200,
    )
    # 分片登记会推进会话版本；完成前必须读一次拿最新 If-Match。
    current = _read(client, f"{base}/upload-sessions/{session_id}", f"{label}读取上传会话")
    completed = expect(
        f"{label}完成上传",
        client.call(
            "POST",
            f"{base}/upload-sessions/{session_id}/complete",
            body={
                "expected_sha256": digest,
                "expected_size_bytes": len(content),
                "parts": [
                    {
                        "part_number": 1,
                        "etag": etag,
                        "size_bytes": len(content),
                        "sha256": digest,
                    }
                ],
                "metadata": {"seed_stamp": ctx.stamp},
            },
            headers={
                "If-Match": if_match(int(current["version"]))["If-Match"],
                "Idempotency-Key": idempotency_key(),
            },
        ),
        202,
    )
    assert isinstance(completed, dict)
    stored = completed["upload_session"]
    return {
        "object_key": stored["object_key"],
        "sha256": digest,
        "size_bytes": len(content),
        "raw_asset_id": (completed.get("raw_asset") or {}).get("asset_id"),
    }


def _blocked(label: str, reason: str) -> None:
    """显式标注走不到的环节；不做静默跳过。"""
    print(f"  ! BLOCKED {label}：{reason}")


def _dataset_versions(client: Client, base: str) -> list[dict[str, Any]]:
    """列出项目内全部数据集版本；跨项目不可见（服务端强制隔离）。"""
    datasets = _read(client, f"{base}/datasets?limit=100", "读取数据集列表")
    versions: list[dict[str, Any]] = []
    for dataset in datasets.get("items") or []:
        page = _read(
            client,
            f"{base}/datasets/{dataset['dataset_id']}/versions?limit=100",
            "读取数据集版本",
        )
        versions.extend(page.get("items") or [])
    return versions


def _frozen_dataset_version(client: Client, base: str) -> dict[str, Any] | None:
    for version in _dataset_versions(client, base):
        if (
            version.get("status") == "FROZEN"
            and version.get("validity_status") == "VALID"
            and int(version.get("item_count") or 0) > 0
        ):
            return version
    return None


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    stamp = ctx.stamp
    reviewer = ctx.as_("reviewer")

    # --- 模型 ---
    model = _create(
        client,
        f"{base}/models",
        {
            "code": f"model-{stamp}-act-pick",
            "name": "双臂抓取策略模型（ACT）",
            "description": "基于家庭场景抓取-放置数据训练的行为克隆策略，输入双臂关节与腕部相机。",
            "task_type": "IMITATION",
            "framework": "pytorch",
            "owner_id": ctx.user_id,
            "tags": ["manipulation", "imitation", "household"],
        },
        "创建抓取策略模型",
    )
    model_id = ctx.remember(GROUP, "model_id", str(model["model_id"]))
    model_path = f"{base}/models/{model_id}"
    model = _act(client, model_path, "activate", model["version"], "启用抓取策略模型")

    archived_model = _create(
        client,
        f"{base}/models",
        {
            "code": f"model-{stamp}-legacy-sort",
            "name": "桌面分拣模型（已归档）",
            "description": "旧版分拣策略，已被抓取策略模型取代，保留用于版本追溯。",
            "task_type": "OTHER",
            "framework": "onnxruntime",
            "owner_id": ctx.user_id,
            "tags": ["sorting", "legacy"],
        },
        "创建待归档模型",
    )
    archived_model_path = f"{base}/models/{archived_model['model_id']}"
    archived_model = _act(
        client, archived_model_path, "activate", archived_model["version"], "启用待归档模型"
    )
    archived_model = _act(
        client,
        archived_model_path,
        "deprecate",
        archived_model["version"],
        "弃用待归档模型",
        {"reason": "分拣场景已合并进抓取策略模型"},
    )
    archived_model = _act(
        client, archived_model_path, "restore", archived_model["version"], "恢复待归档模型"
    )
    archived_model = _act(
        client,
        archived_model_path,
        "deprecate",
        archived_model["version"],
        "再次弃用待归档模型",
        {"reason": "确认无下游使用"},
    )
    _act(
        client,
        archived_model_path,
        "archive",
        archived_model["version"],
        "归档待归档模型",
        {"reason": "历史模型下线，元数据保留"},
    )

    # --- 模型版本 ---
    # 产物必须先真实存在：validate 会读取对象并核对 size/sha256。
    artifact = _upload(
        ctx=ctx,
        client=client,
        filename=f"act-policy-{stamp}.bin",
        content=_ARTIFACT_BYTES,
        content_type=_ARTIFACT_CONTENT_TYPE,
        format_hint="JSON",
        label="上传模型产物",
    )

    # 发布人不得是版本唯一创建人，且 publish 要求项目 Owner：
    # 由第二身份（editor）建版本，由主身份（owner）发布。
    released = _create(
        reviewer,
        f"{base}/models/{model_id}/versions",
        {
            "artifact": {
                "object_key": artifact["object_key"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "format": _ARTIFACT_FORMAT,
            },
            "runtime": {
                "framework": "pytorch",
                "framework_version": "2.4.1",
                "python_version": "3.11",
                "cuda_version": "12.1",
                "image_ref": "ddp/demo-act:local",
            },
            "metrics_summary": {"train_loss": 0.084, "val_mae": 0.031},
            "release_notes": "首个可发布版本，覆盖厨房与客厅两类场景。",
        },
        "创建待发布模型版本",
    )
    released_id = ctx.remember(GROUP, "released_version_id", str(released["model_version_id"]))
    released_path = f"{base}/models/{model_id}/versions/{released_id}"
    released = _act(client, released_path, "validate", released["version"], "校验模型版本产物")
    released = _act(client, released_path, "publish", released["version"], "发布模型版本")
    ctx.remember(GROUP, "released_version_status", str(released.get("status", "")))

    ready = _create(
        client,
        f"{base}/models/{model_id}/versions",
        {
            "artifact": {
                "object_key": artifact["object_key"],
                "sha256": artifact["sha256"],
                "size_bytes": artifact["size_bytes"],
                "format": _ARTIFACT_FORMAT,
            },
            "runtime": {
                "framework": "pytorch",
                "framework_version": "2.4.1",
                "python_version": "3.11",
            },
            "metrics_summary": {"train_loss": 0.097, "val_mae": 0.036},
            "release_notes": "候选版本，等待更多场景数据后发布。",
        },
        "创建候选模型版本",
    )
    ready_id = ctx.remember(GROUP, "ready_version_id", str(ready["model_version_id"]))
    ready = _act(
        client,
        f"{base}/models/{model_id}/versions/{ready_id}",
        "validate",
        ready["version"],
        "校验候选模型版本",
    )

    # --- 推理服务 ---
    service = _create(
        client,
        f"{base}/inference-services",
        {
            "model_version_id": released_id,
            "environment": "DEV",
            "endpoint_url": "http://inference-dev.local/api/v1/predict",
            "resource_ref": "gpu-pool/dev-1",
            "config": {"max_batch_size": 8, "timeout_ms": 2000},
        },
        "登记推理服务",
    )
    service_id = ctx.remember(GROUP, "service_id", str(service["service_id"]))
    service_path = f"{base}/inference-services/{service_id}"
    service = _act(client, service_path, "deploy", service["version"], "部署推理服务")
    service = _act(client, service_path, "activate", service["version"], "激活推理服务")
    ctx.remember(GROUP, "service_status", str(service.get("status", "")))

    retired = _create(
        client,
        f"{base}/inference-services",
        {
            "model_version_id": ready_id,
            "environment": "STAGING",
            "endpoint_url": "http://inference-staging.local/api/v1/predict",
            "resource_ref": "gpu-pool/staging-1",
            "config": {"max_batch_size": 4, "timeout_ms": 3000},
        },
        "登记待退役推理服务",
    )
    retired_id = ctx.remember(GROUP, "retired_service_id", str(retired["service_id"]))
    retired_path = f"{base}/inference-services/{retired_id}"
    retired = _act(client, retired_path, "deploy", retired["version"], "部署待退役服务")
    retired = _act(client, retired_path, "activate", retired["version"], "激活待退役服务")
    retired = _act(client, retired_path, "stop", retired["version"], "停止待退役服务")
    retired = _act(
        client,
        retired_path,
        "retire",
        retired["version"],
        "退役推理服务",
        {"reason": "候选版本未发布，临时服务下线"},
    )
    ctx.remember(GROUP, "retired_service_status", str(retired.get("status", "")))

    # --- 评测（依赖冻结数据集版本） ---
    frozen = _frozen_dataset_version(client, base)
    if frozen is None:
        _blocked(
            "evaluations",
            "EvaluationCreate 要求 dataset_version_id 处于 FROZEN 且有效"
            "（backend/app/application/modelops/evaluations.py:189-190），"
            "本项目当前没有冻结版本；冻结链路由 datasets 模块负责",
        )
    else:
        evaluation = _accept(
            client,
            f"{base}/evaluations",
            {
                "model_version_id": released_id,
                "dataset_version_id": frozen["dataset_version_id"],
                "metrics": {"success_rate": 0.86, "avg_latency_ms": 42.0},
                "thresholds": {"success_rate": {"min": 0.8}},
            },
            "创建评测",
        )
        evaluation_id = ctx.remember(GROUP, "evaluation_id", str(evaluation["evaluation_id"]))
        evaluation = _act(
            client,
            f"{base}/evaluations/{evaluation_id}",
            "cancel",
            evaluation["version"],
            "取消评测",
            {"reason": "演示环境不接真实评测集群"},
        )
        ctx.remember(GROUP, "evaluation_status", str(evaluation.get("status", "")))

    # --- 推理任务（依赖数据集版本或 Episode） ---
    episodes = _read(client, f"{base}/episodes?limit=5", "读取 Episode 列表")
    episode_ids = [str(item["episode_id"]) for item in (episodes.get("items") or [])[:3]]
    if episode_ids:
        task = _accept(
            client,
            f"{base}/inference-tasks",
            {
                "model_version_id": released_id,
                "service_id": service_id,
                "episode_ids": episode_ids,
                "config": {"batch_size": 2},
            },
            "创建推理任务",
        )
        task_id = ctx.remember(GROUP, "inference_task_id", str(task["task_id"]))
        task = _act(
            client,
            f"{base}/inference-tasks/{task_id}",
            "cancel",
            task["version"],
            "取消推理任务",
            {"reason": "演示环境不接真实推理集群"},
        )
        ctx.remember(GROUP, "inference_task_status", str(task.get("status", "")))
    else:
        _blocked(
            "inference-tasks",
            "InferenceTaskCreate 要求 dataset_version_id 或 episode_ids"
            "（backend/app/application/modelops/inference.py:328），本项目两者都没有",
        )

    # --- 模型反馈 ---
    feedback = _create(
        client,
        f"{base}/model-feedback",
        {
            "model_version_id": released_id,
            "issue_type": "SCENE_COVERAGE",
            "severity": "MAJOR",
            "description": "卫生间与阳台场景几乎无样本，模型在湿地面物件上抓取失败率偏高。",
            "dimensions": [
                {"dimension_type": "SCENE", "dimension_value": "bathroom", "target_count": 40},
                {"dimension_type": "SCENE", "dimension_value": "balcony", "target_count": 20},
            ],
            "evidence_refs": [f"object://demo/eval/{stamp}/bathroom-failures.json"],
        },
        "创建场景覆盖反馈",
    )
    feedback_id = ctx.remember(GROUP, "feedback_id", str(feedback["feedback_id"]))
    feedback_path = f"{base}/model-feedback/{feedback_id}"
    feedback = _act(client, feedback_path, "triage", feedback["version"], "反馈分诊")
    feedback = expect(
        "反馈转数据需求",
        client.call(
            "POST",
            f"{feedback_path}/actions/create-requirement",
            body={
                "requirement_code": f"DR-{stamp}-bathroom",
                "requirement_title": "补充卫生间与阳台抓取样本",
                "target_episode_count": 60,
                "priority": "P1",
            },
            headers=if_match(feedback["version"]),
        ),
        # 契约写 201（paths.stage5.yaml modelFeedbackActions），实现返回默认 200
        # （backend/app/api/v1/feedback_routes.py:91 未声明 status_code）。
        (200, 201),
    )
    assert isinstance(feedback, dict)
    ctx.remember(
        GROUP, "converted_requirement_id", str(feedback.get("converted_requirement_id", ""))
    )
    ctx.remember(GROUP, "feedback_status", str(feedback.get("status", "")))

    dismissed = _create(
        client,
        f"{base}/model-feedback",
        {
            "model_version_id": ready_id,
            "issue_type": "OTHER",
            "severity": "MINOR",
            "description": "希望输出更详细的动作分割时间戳。",
            "dimensions": [{"dimension_type": "MODALITY", "dimension_value": "action_timestamp"}],
        },
        "创建待忽略反馈",
    )
    dismissed_path = f"{base}/model-feedback/{dismissed['feedback_id']}"
    _act(
        client,
        dismissed_path,
        "dismiss",
        dismissed["version"],
        "忽略反馈",
        {"reason": "属于下游推理服务职责，不在数据补采范围内"},
    )

    open_feedback = _create(
        client,
        f"{base}/model-feedback",
        {
            "model_version_id": released_id,
            "issue_type": "LABEL_GAP",
            "severity": "MINOR",
            "description": "夹爪开合状态缺少细粒度标注，导致失败样本难以归因。",
            "dimensions": [
                {"dimension_type": "LABEL", "dimension_value": "gripper_state", "target_count": 30}
            ],
        },
        "创建待处理反馈",
    )
    ctx.remember(GROUP, "open_feedback_id", str(open_feedback["feedback_id"]))

    # --- 读回校验 ---
    gaps = _read(client, f"{base}/data-gaps", "读取数据缺口汇总")
    items = gaps.get("items") or []
    if not items:
        raise SeedFailure("数据缺口汇总为空：OPEN/TRIAGED 反馈的维度没有聚合出来")
    ctx.remember(GROUP, "gap_items", str(len(items)))

    versions = _read(client, f"{base}/models/{model_id}/versions", "读取模型版本列表")
    if len(versions.get("items") or []) < 2:
        raise SeedFailure("模型版本列表少于 2 条")

    services = _read(client, f"{base}/inference-services", "读取推理服务列表")
    if len(services.get("items") or []) < 2:
        raise SeedFailure("推理服务列表少于 2 条")

    feedbacks = _read(client, f"{base}/model-feedback", "读取模型反馈列表")
    if len(feedbacks.get("items") or []) < 3:
        raise SeedFailure("模型反馈列表少于 3 条")

    print(
        "  模型闭环：模型 2 / 版本 2 / 服务 2 / 反馈 3 / 推理任务 "
        f"{'1' if episode_ids else '0（BLOCKED）'} / 缺口 {len(items)} 项；"
        f"评测 {'已创建' if frozen is not None else 'BLOCKED'}"
    )
