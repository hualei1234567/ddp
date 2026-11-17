"""训练闭环模块：数据需求 / 训练请求 / 修订 / 交付 / 外部训练引用。

覆盖的状态路径
--------------
- 数据需求：``DRAFT → SUBMITTED → APPROVED → PLANNED → IN_PROGRESS → SATISFIED``
  （``satisfy`` 需要 FROZEN 且 VALID 的数据集版本；``close`` 还要求关联复测评测）；
  另一条 ``DRAFT → SUBMITTED → REJECTED``；第三条 ``DRAFT → CANCELLED``；
- 训练请求：``DRAFT → PENDING_REVIEW → APPROVED → DRAFT（新 revision）→
  PENDING_REVIEW → APPROVED``；另建一条走 ``BLOCKED → DRAFT → CANCELLED``；
- 修订：revision 1 提交后审批通过并被 revision 2 取代（SUPERSEDED），
  revision 2 同样提交-审批通过；
- 训练交付：``DRAFT → READY → DELIVERED → ACCEPTED`` + 需求 ``ACCEPTED → CLOSED``；
  另一条 ``DRAFT → READY → DELIVERED → REJECTED → REWORK → READY → CANCELLED``，
  需求随之 ``REVISION_REQUIRED → CANCELLED``。两条都**仅当**项目里存在 FROZEN
  数据集版本且导出任务成功时执行；
- 外部训练引用：真实提交一次；默认 Adapter 只声明能力，返回 422
  ``training_adapter_unsupported`` / 503 ``training_adapter_unavailable``，按 BLOCKED 记录。

职责分离
--------
- 修订的提交人不得审批自己：第二身份提交、主身份（Owner）审批；
- 需求审批同理：第二身份建单、主身份审批；
- 交付验收人不得是唯一交付人：主身份 ready/deliver，第二身份 accept。

关于阻塞
--------
交付链与需求 ``satisfy`` 依赖前置产物（FROZEN 数据集版本、SUCCEEDED 导出任务）。
脚本每次运行先探测：满足就真跑，不满足就显式打印 BLOCKED 并给出依据（文件:行），
绝不用假 ID 或跳过断言来「凑绿」。

需求快照与交付核对
------------------
交付前服务端逐维度核对需求快照与数据集版本事实（requirement_match.py:214-232），
不满足即 BLOCK。因此第二条 revision 会按「当前可交付版本」的真实规模重写目标，
否则交付永远被拒——这不是放水，是把需求改到与可交付现实一致，并留下 change_reason。
"""

from __future__ import annotations

import time
from typing import Any

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "training"

_FORMAT = "LE_ROBOT"
_FORMAT_VERSION = "ddp.lerobot/1.0.0"
_EXPORT_POLL_SECONDS = 3
_EXPORT_POLL_ATTEMPTS = 40


def _create(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    payload = expect(label, client.call("POST", path, body=body), 201)
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


def _blocked(label: str, reason: str) -> None:
    print(f"  ! BLOCKED {label}：{reason}")


def _snapshot(
    stamp: str,
    *,
    priority: str = "P1",
    episodes: int = 120,
    duration_ns: int | None = None,
    scene_targets: list[str] | None = None,
    action_targets: list[str] | None = None,
    split_requirements: dict[str, Any] | None = None,
    note: str = "",
) -> dict[str, Any]:
    """需求快照：服务端要求字段齐全，且只接受 LE_ROBOT / ddp.lerobot/1.0.0。"""
    snapshot: dict[str, Any] = {
        "business_goal": f"训练家庭场景双臂抓取策略（批次 {stamp}）{note}",
        "model_type": "ACT",
        "training_type": "IMITATION",
        "data_modalities": ["RGB", "JOINT_STATE", "GRIPPER_STATE"],
        "scene_targets": scene_targets if scene_targets is not None else ["kitchen", "living_room"],
        "action_targets": (
            action_targets
            if action_targets is not None
            else ["reach", "grasp", "lift", "place", "release"]
        ),
        "robot_targets": ["dual_arm_6dof"],
        "device_targets": ["wrist_camera", "force_torque_sensor"],
        "required_labels": ["object", "grasp_state"],
        "required_schema_versions": [],
        "target_episode_count": episodes,
        "target_duration_ns": (
            duration_ns if duration_ns is not None else episodes * 30_000_000_000
        ),
        "quality_thresholds": {"completion_rate": 0.95},
        "split_requirements": split_requirements,
        "leakage_policy_id": None,
        "format": _FORMAT,
        "format_version": _FORMAT_VERSION,
        "delivery_due_at": "2026-12-31T00:00:00Z",
        "acceptance_method": "按需求快照逐维度核对 + QA 抽检 10% 片段",
        "priority": priority,
    }
    return snapshot


def _deliverable_snapshot(
    stamp: str, version: dict[str, Any], *, priority: str = "P1"
) -> dict[str, Any]:
    """按「当前唯一可交付的冻结版本」写需求快照。

    交付前服务端会逐维度核对需求快照与版本事实，任一 BLOCK 维度不通过就拒绝交付
    （backend/app/domain/datasets/requirement_match.py:214-232）。因此第二条
    revision 把目标样本数/时长调到该版本的真实规模，场景与动作目标留空
    （空集表示「不限定」，判 PASSED，见同文件 318-325、348-355）。
    """
    episodes = int(version.get("item_count") or 1)
    duration = version.get("total_duration_ns")
    return _snapshot(
        stamp,
        priority=priority,
        episodes=max(episodes, 1),
        duration_ns=int(duration) if duration else None,
        scene_targets=[],
        action_targets=[],
        note="（按实际交付版本调整规模）",
    )


def _dataset_versions(client: Client, base: str) -> list[dict[str, Any]]:
    """列出项目内全部数据集版本（跨项目不可见，服务端强制隔离）。"""
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


def _frozen_version(client: Client, base: str) -> dict[str, Any] | None:
    for version in _dataset_versions(client, base):
        if (
            version.get("status") == "FROZEN"
            and version.get("validity_status") == "VALID"
            and int(version.get("item_count") or 0) > 0
        ):
            return version
    return None


def _create_requirement(
    client: Client,
    base: str,
    ctx: Context,
    key: str,
    title: str,
    *,
    priority: str,
) -> dict[str, Any]:
    return _create(
        client,
        f"{base}/data-requirements",
        {
            "code": f"req-{ctx.stamp}-{key}",
            "title": title,
            "description": "由训练闭环种子生成的数据需求，用于演示需求审批与执行链路。",
            "source_type": "MANUAL",
            "dimensions": [
                {
                    "dimension_type": "SCENE",
                    "dimension_value": "bathroom",
                    "current_count": 0,
                    "target_count": 40,
                }
            ],
            "target_episode_count": 40,
            "quality_thresholds": {"completion_rate": 0.95},
            "priority": priority,
            "due_at": "2026-12-20T00:00:00Z",
            "owner_id": ctx.user_id,
        },
        f"创建数据需求（{key}）",
    )


def _run_requirements(ctx: Context, client: Client, base: str) -> None:
    reviewer = ctx.as_("reviewer")

    # 主流程：DRAFT → SUBMITTED → APPROVED → PLANNED → IN_PROGRESS
    requirement = _create_requirement(
        reviewer, base, ctx, "main", "补充卫生间湿地面抓取样本", priority="P0"
    )
    requirement_id = ctx.remember(GROUP, "requirement_id", str(requirement["requirement_id"]))
    path = f"{base}/data-requirements/{requirement_id}"
    requirement = _act(reviewer, path, "submit", requirement["version"], "数据需求提交")
    requirement = _act(client, path, "approve", requirement["version"], "数据需求审批通过")
    requirement = _act(client, path, "plan", requirement["version"], "数据需求排期")
    requirement = _act(client, path, "start", requirement["version"], "数据需求开始执行")
    ctx.remember(GROUP, "requirement_status", str(requirement.get("status", "")))

    frozen = _frozen_version(client, base)
    if frozen is None:
        _blocked(
            "data-requirements.satisfy",
            "满足需求必须关联至少一个 FROZEN/VALID 且非空的数据集版本"
            "（backend/app/application/modelops/requirements.py:288-310），"
            "本项目当前没有冻结版本",
        )
        _blocked(
            "data-requirements.close",
            "关闭前必须有关联复测评测（requirements.py:284），复测评测依赖冻结数据集版本",
        )
    else:
        requirement = _act(
            client,
            path,
            "satisfy",
            requirement["version"],
            "数据需求满足",
            {
                "dataset_version_ids": [frozen["dataset_version_id"]],
                "satisfaction_evidence_ref": f"dataset-version://{frozen['dataset_version_id']}",
            },
        )
        ctx.remember(GROUP, "requirement_status", str(requirement.get("status", "")))
        # 复测/关闭：只接受「已跑完」的评测作为证据，不拿 QUEUED/CANCELLED 冒充复测。
        evaluations = _read(
            client,
            f"{base}/evaluations?dataset_version_id={frozen['dataset_version_id']}&limit=100",
            "读取复测评测",
        )
        succeeded = [
            item for item in (evaluations.get("items") or []) if item.get("status") == "SUCCEEDED"
        ]
        if succeeded:
            requirement = _act(
                client,
                path,
                "link-retest",
                requirement["version"],
                "关联复测评测",
                {"evaluation_id": succeeded[0]["evaluation_id"]},
            )
            requirement = _act(client, path, "close", requirement["version"], "关闭数据需求")
            ctx.remember(GROUP, "requirement_status", str(requirement.get("status", "")))
        else:
            _blocked(
                "data-requirements.link-retest/close",
                "关闭前必须有关联复测评测（backend/app/application/modelops/requirements.py:284），"
                "而本项目没有 SUCCEEDED 状态的评测——评测完成依赖真实 Adapter 回调"
                "（INFERENCE/EVALUATION system-* 动作只走签名回调）",
            )

    rejected = _create_requirement(
        reviewer, base, ctx, "rejected", "补充夜间低照度抓取样本", priority="P2"
    )
    rejected_path = f"{base}/data-requirements/{rejected['requirement_id']}"
    rejected = _act(reviewer, rejected_path, "submit", rejected["version"], "待驳回需求提交")
    _act(
        client,
        rejected_path,
        "reject",
        rejected["version"],
        "数据需求驳回",
        {"reason": "夜间场景已有覆盖，优先级下调到下一季度"},
    )

    cancelled = _create_requirement(
        reviewer, base, ctx, "cancelled", "补充户外水泥地面抓取样本", priority="P3"
    )
    _act(
        reviewer,
        f"{base}/data-requirements/{cancelled['requirement_id']}",
        "cancel",
        cancelled["version"],
        "数据需求取消",
        {"reason": "当前阶段不做户外场景"},
    )


def _run_requests(ctx: Context, client: Client, base: str, frozen: dict[str, Any] | None) -> str:
    """训练请求与修订；返回主请求 ID。"""
    reviewer = ctx.as_("reviewer")
    stamp = ctx.stamp

    request = _create(
        client,
        f"{base}/training-requests",
        {
            "code": f"TR-{stamp}-act",
            "title": "双臂抓取策略训练需求（ACT）",
            "owner_id": ctx.user_id,
            "priority": "P1",
            "snapshot": _snapshot(stamp),
        },
        "创建训练需求",
    )
    request_id = ctx.remember(GROUP, "request_id", str(request["training_request_id"]))
    request_path = f"{base}/training-requests/{request_id}"

    revision = expect(
        "读取需求当前修订",
        client.call("GET", f"{request_path}/revisions?limit=100"),
        200,
    )
    first = (revision.get("items") or [])[0]
    first_path = f"{request_path}/revisions/{first['revision_id']}"
    first = _act(reviewer, first_path, "submit", first["version"], "修订提交")
    _act(client, first_path, "approve", first["version"], "修订审批通过")

    # 已批准后要变更内容：必须新建 revision（原 revision 转 SUPERSEDED）。
    if frozen is not None:
        revised = _deliverable_snapshot(stamp, frozen)
        change_reason = "上游首批只交付了少量样本，按实际可交付规模重写目标"
    else:
        revised = _snapshot(stamp, episodes=150)
        change_reason = "训练侧要求把目标样本数从 120 提到 150"
    second = _create(
        client,
        f"{request_path}/revisions",
        {"snapshot": revised, "change_reason": change_reason},
        "创建新修订",
    )
    second_path = f"{request_path}/revisions/{second['revision_id']}"
    second = _act(reviewer, second_path, "submit", second["version"], "新修订提交")
    second = _act(client, second_path, "approve", second["version"], "新修订审批通过")
    ctx.remember(GROUP, "revision_id", str(second["revision_id"]))
    ctx.remember(GROUP, "request_status", str(second.get("status", "")))

    request = _read(client, request_path, "读回训练需求")
    ctx.remember(GROUP, "request_status", str(request.get("status", "")))

    # 第二条：BLOCKED → DRAFT → CANCELLED
    blocked = _create(
        client,
        f"{base}/training-requests",
        {
            "code": f"TR-{stamp}-cancelled",
            "title": "桌面分拣策略训练需求（已取消）",
            "owner_id": ctx.user_id,
            "priority": "P3",
            "snapshot": _snapshot(stamp, priority="P3", episodes=40),
        },
        "创建待取消训练需求",
    )
    blocked_path = f"{base}/training-requests/{blocked['training_request_id']}"
    blocked = _act(
        client,
        blocked_path,
        "block",
        blocked["version"],
        "训练需求阻塞",
        {"reason": "上游采集计划延后，先挂起"},
    )
    blocked = _act(
        client,
        blocked_path,
        "unblock",
        blocked["version"],
        "训练需求解除阻塞",
        {"reason": "采集计划恢复，继续推进"},
    )
    _act(
        client,
        blocked_path,
        "cancel",
        blocked["version"],
        "取消训练需求",
        {"reason": "分拣场景已合并进抓取策略需求"},
    )

    return request_id


def _ensure_export_job(client: Client, base: str, version: dict[str, Any]) -> dict[str, Any]:
    """为冻结版本触发一次真实导出，并等待 Worker 产出成功产物。"""
    job = expect(
        "创建导出任务",
        client.call(
            "POST",
            f"{base}/exports",
            body={
                "dataset_version_id": version["dataset_version_id"],
                "format": _FORMAT,
                "format_version": _FORMAT_VERSION,
                "exporter_id": "ddp-exporter",
                "exporter_version": "1.0.0",
                "mapping_version": "v1",
                "config": {"seed": True},
            },
            headers={"Idempotency-Key": idempotency_key()},
        ),
        202,
    )
    assert isinstance(job, dict)
    job_id = str(job["export_job_id"])
    for _ in range(_EXPORT_POLL_ATTEMPTS):
        current = _read(client, f"{base}/exports/{job_id}", "读取导出任务")
        if current.get("status") == "SUCCEEDED":
            return current
        if current.get("status") in {"FAILED", "CANCELLED"}:
            raise SeedFailure(f"导出任务 {job_id} 未成功：{current.get('status')}")
        time.sleep(_EXPORT_POLL_SECONDS)
    raise SeedFailure(f"导出任务 {job_id} 在等待窗口内没有完成")


def _run_delivery(
    ctx: Context,
    client: Client,
    base: str,
    request_id: str,
) -> str | None:
    reviewer = ctx.as_("reviewer")
    frozen = _frozen_version(client, base)
    if frozen is None:
        _blocked(
            "training-deliveries",
            "交付只能基于 FROZEN 且有效的数据集版本"
            "（backend/app/application/datasets/training_deliveries.py:521-524），"
            "本项目当前没有冻结版本，属于 datasets 模块的前置产物",
        )
        _blocked(
            "training-requests.DELIVERED/ACCEPTED/CLOSED",
            "交付动作驱动需求进入 DELIVERED/ACCEPTED/CLOSED；没有交付单就没有这些状态",
        )
        return None

    job = _ensure_export_job(client, base, frozen)
    request = _read(client, f"{base}/training-requests/{request_id}", "读回训练需求")
    delivery = _create(
        client,
        f"{base}/training-deliveries",
        {
            "training_request_id": request_id,
            "training_request_revision_id": ctx.recall(GROUP, "revision_id"),
            "dataset_version_id": frozen["dataset_version_id"],
            "export_job_id": job["export_job_id"],
            "recipient_id": ctx.user_id,
            "recipient_team": "训练团队",
            "delivery_note_no": f"DN-{ctx.stamp}",
        },
        "创建训练交付单",
    )
    delivery_id = ctx.remember(GROUP, "delivery_id", str(delivery["training_delivery_id"]))
    path = f"{base}/training-deliveries/{delivery_id}"
    delivery = _act(client, path, "ready", delivery["version"], "交付单就绪")
    delivery = _act(client, path, "deliver", delivery["version"], "交付单发出")
    # 验收人不得是唯一交付人：交付由主身份发出，验收换第二身份。
    delivery = _act(
        reviewer,
        path,
        "accept",
        delivery["version"],
        "交付单验收",
        {"acceptance_opinion": "抽检 10% 片段通过，接受交付"},
    )
    ctx.remember(GROUP, "delivery_status", str(delivery.get("status", "")))

    request = _read(client, f"{base}/training-requests/{request_id}", "读回训练需求")
    if request.get("status") == "ACCEPTED":
        _act(
            client,
            f"{base}/training-requests/{request_id}",
            "close",
            request["version"],
            "关闭训练需求",
        )

    _run_rejected_delivery(ctx, client, base, frozen, job)
    return delivery_id


def _run_rejected_delivery(
    ctx: Context,
    client: Client,
    base: str,
    frozen: dict[str, Any],
    job: dict[str, Any],
) -> None:
    """第二条交付：被驳回 → 返工 → 取消，覆盖交付负向路径。

    同一冻结版本可以复用一个已成功的导出任务（交付只核对版本与产物清单一致），
    不必再等一次 Worker。
    """
    reviewer = ctx.as_("reviewer")
    second = _create(
        client,
        f"{base}/training-requests",
        {
            "code": f"TR-{ctx.stamp}-rework",
            "title": "双臂抓取策略训练需求（返工复验）",
            "owner_id": ctx.user_id,
            "priority": "P2",
            "snapshot": _deliverable_snapshot(ctx.stamp, frozen, priority="P2"),
        },
        "创建返工用训练需求",
    )
    second_id = str(second["training_request_id"])
    second_path = f"{base}/training-requests/{second_id}"
    revisions = _read(client, f"{second_path}/revisions?limit=100", "读取返工需求修订")
    revision = (revisions.get("items") or [])[0]
    revision_path = f"{second_path}/revisions/{revision['revision_id']}"
    revision = _act(reviewer, revision_path, "submit", revision["version"], "返工需求修订提交")
    _act(client, revision_path, "approve", revision["version"], "返工需求修订审批通过")

    delivery = _create(
        client,
        f"{base}/training-deliveries",
        {
            "training_request_id": second_id,
            "training_request_revision_id": revision["revision_id"],
            "dataset_version_id": frozen["dataset_version_id"],
            "export_job_id": job["export_job_id"],
            "recipient_id": ctx.user_id,
            "recipient_team": "训练团队",
            "delivery_note_no": f"DN-{ctx.stamp}-2",
        },
        "创建返工用交付单",
    )
    path = f"{base}/training-deliveries/{delivery['training_delivery_id']}"
    delivery = _act(client, path, "ready", delivery["version"], "返工交付单就绪")
    delivery = _act(client, path, "deliver", delivery["version"], "返工交付单发出")
    delivery = _act(
        client,
        path,
        "reject",
        delivery["version"],
        "交付单拒收",
        {"reason": "片段时码与设备时钟存在 1.5 秒漂移，需重新导出"},
    )
    delivery = _act(
        client,
        path,
        "rework",
        delivery["version"],
        "交付单返工",
        {"reason": "重新对齐时间戳后再交付"},
    )
    delivery = _act(client, path, "resume-ready", delivery["version"], "返工完成回到就绪")
    delivery = _act(
        client,
        path,
        "cancel",
        delivery["version"],
        "取消返工交付单",
        {"reason": "上游采集设备停用，本轮不再交付"},
    )
    ctx.remember(GROUP, "rejected_delivery_status", str(delivery.get("status", "")))

    request = _read(client, second_path, "读回返工需求")
    _act(
        client,
        second_path,
        "cancel",
        request["version"],
        "取消返工需求",
        {"reason": "设备停用，需求转下季度"},
    )


def _run_external_reference(
    ctx: Context,
    client: Client,
    base: str,
    delivery_id: str | None,
) -> None:
    """外部训练引用：真实提交一次；默认 Adapter 会把「不可用」如实返回 503。"""
    if delivery_id is None:
        _blocked(
            "external-references",
            "外部引用挂在交付单上（交付单必须处于 DELIVERED/ACCEPTED），本批次没有交付单",
        )
        return
    status, payload = client.call(
        "POST",
        f"{base}/training-deliveries/{delivery_id}/external-references",
        body={
            "adapter_id": "demo-trainer",
            "adapter_version": "1.0.0",
            "correlation_id": f"corr-{ctx.stamp}",
            "config": {"epochs": 10},
        },
        headers={"Idempotency-Key": idempotency_key()},
    )
    code = ""
    if isinstance(payload, dict):
        code = str((payload.get("error") or {}).get("code") or "")
    if status == 503 or code in {"training_adapter_unsupported", "training_adapter_unavailable"}:
        _blocked(
            "external-references",
            f"真实提交返回 {status} {code}：本项目未配置训练 Adapter 能力，"
            "且默认 Gateway 的 submit 一律抛 TrainingAdapterUnavailableError"
            "（backend/app/infrastructure/training_adapters.py:17-47）",
        )
        return
    reference = expect("提交外部训练引用", (status, payload), 202)
    assert isinstance(reference, dict)
    ctx.remember(GROUP, "external_ref_id", str(reference["external_ref_id"]))


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    _run_requirements(ctx, client, base)
    frozen = _frozen_version(client, base)
    request_id = _run_requests(ctx, client, base, frozen)
    delivery_id = _run_delivery(ctx, client, base, request_id)
    _run_external_reference(ctx, client, base, delivery_id)

    requests = _read(client, f"{base}/training-requests?limit=100", "读取训练需求列表")
    if len(requests.get("items") or []) < 2:
        raise SeedFailure("训练需求列表少于 2 条")
    requirements = _read(client, f"{base}/data-requirements?limit=100", "读取数据需求列表")
    if len(requirements.get("items") or []) < 3:
        raise SeedFailure("数据需求列表少于 3 条")
    print(
        "  训练闭环：需求 "
        f"{len(requirements.get('items') or [])} / 请求 {len(requests.get('items') or [])}"
    )
