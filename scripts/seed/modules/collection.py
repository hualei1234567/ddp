"""采集模块：采集方案 / SOP / 场景 / 动作库 / 采集任务 / 批次。

覆盖的状态路径
--------------
- 方案：``DRAFT → ACTIVE``，另建一条 ``ACTIVE → ARCHIVED``；
- SOP / 场景 / 动作库：``DRAFT → ACTIVE``；
- 采集任务：``DRAFT → PENDING_REVIEW → APPROVED → READY → ASSIGNED →
  IN_PROGRESS → PAUSED → IN_PROGRESS → SUBMITTED``；另建一条走 ``CANCELLED``；
- 采集批次：``PLANNED → READY → IN_PROGRESS → PAUSED → IN_PROGRESS →
  SUBMITTED → ACCEPTED``。

关于任务终态
------------
``SUBMITTED`` 之后由 ``domain-worker`` 的巡检推进到 ``DATA_CHECKING``（系统动作
``system-data-check`` 不对外暴露，也不该由客户端调用），随后 accept / complete
是公开动作。本模块只负责把任务推进到 ``SUBMITTED`` 并验证数据检查前的状态；
完整闭环由 ``scripts/qa/full_chain_smoke.py`` 的 ``[4b]`` 在真实栈上验证。
"""

from __future__ import annotations

from typing import Any

from seed.client import Client, Context, SeedFailure, expect, if_match

GROUP = "collection"


def _create(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    payload = expect(label, client.call("POST", path, body=body), 201)
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
        client.call("POST", f"{path}/actions/{action}", body=body, headers=if_match(version)),
        200,
    )
    assert isinstance(payload, dict)
    return payload


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    stamp = ctx.stamp

    # --- 采集方案 ---
    plan = _create(
        client,
        f"{base}/collection-plans",
        {
            "code": f"plan-{stamp}-demo",
            "name": "家庭场景双臂抓取采集方案",
            "plan_version": "1",
            "description": "面向家庭厨房与客厅场景，单臂/双臂抓取与放置动作，目标 8 小时有效数据。",
        },
        "创建采集方案",
    )
    plan_id = ctx.remember(GROUP, "plan_id", str(plan["plan_id"]))
    plan = _act(
        client, f"{base}/collection-plans/{plan_id}", "activate", plan["version"], "启用采集方案"
    )
    ctx.remember(GROUP, "plan_version", str(plan["version"]))

    archived_plan = _create(
        client,
        f"{base}/collection-plans",
        {
            "code": f"plan-{stamp}-archived",
            "name": "旧版桌面分拣采集方案（已停用）",
            "plan_version": "1",
            "description": "已被家庭场景方案取代，保留用于历史任务追溯。",
        },
        "创建待归档方案",
    )
    archived_plan = _act(
        client,
        f"{base}/collection-plans/{archived_plan['plan_id']}",
        "activate",
        archived_plan["version"],
        "启用待归档方案",
    )
    _act(
        client,
        f"{base}/collection-plans/{archived_plan['plan_id']}",
        "archive",
        archived_plan["version"],
        "停用待归档方案",
    )

    # --- SOP ---
    sop = _create(
        client,
        f"{base}/collection-sops",
        {
            "code": f"sop-{stamp}-kitchen",
            "name": "厨房台面取放标准作业流程",
            "sop_version": "1",
            "steps": [
                {"step": 1, "action": "标定相机与机械臂基座坐标系", "duration_sec": 600},
                {"step": 2, "action": "检查夹爪开合与力矩传感", "duration_sec": 300},
                {"step": 3, "action": "按场景清单依次执行抓取-放置轨迹", "duration_sec": 3600},
                {"step": 4, "action": "导出原始数据并核对 episode 数量", "duration_sec": 300},
            ],
            "description": "适用于厨房台面、餐桌两类布局；出现异常先暂停并记录原因。",
        },
        "创建 SOP",
    )
    sop_id = ctx.remember(GROUP, "sop_id", str(sop["sop_id"]))
    _act(client, f"{base}/collection-sops/{sop_id}", "activate", sop["version"], "启用 SOP")

    # --- 场景 ---
    scenario = _create(
        client,
        f"{base}/scenarios",
        {
            "code": f"scene-{stamp}-kitchen",
            "name": "家庭厨房台面",
            "scene_labels": ["indoor", "kitchen", "tabletop", "manipulation"],
            "description": "台面高度 90cm，背景含橱柜与餐具，光照 300-500lux。",
        },
        "创建场景",
    )
    scenario_id = ctx.remember(GROUP, "scenario_id", str(scenario["scenario_id"]))
    _act(client, f"{base}/scenarios/{scenario_id}", "activate", scenario["version"], "启用场景")

    living = _create(
        client,
        f"{base}/scenarios",
        {
            "code": f"scene-{stamp}-living",
            "name": "家庭客厅地面",
            "scene_labels": ["indoor", "living_room", "floor", "navigation"],
            "description": "含沙发、茶几与地毯，用于移动与避障数据采集。",
        },
        "创建客厅场景",
    )
    _act(
        client,
        f"{base}/scenarios/{living['scenario_id']}",
        "activate",
        living["version"],
        "启用客厅场景",
    )

    # --- 动作库 ---
    spec = _create(
        client,
        f"{base}/action-specs",
        {
            "code": f"act-{stamp}-pick-place",
            "name": "抓取-搬运-放置动作集",
            "spec_version": "1",
            "actions": [
                {"name": "reach", "duration_sec": 2.5, "frame": "base"},
                {"name": "grasp", "duration_sec": 1.5, "frame": "gripper"},
                {"name": "lift", "duration_sec": 2.0, "frame": "base"},
                {"name": "place", "duration_sec": 2.5, "frame": "base"},
                {"name": "release", "duration_sec": 1.0, "frame": "gripper"},
            ],
            "description": "五段式基础操作序列，供家庭物件整理任务复用。",
        },
        "创建动作库",
    )
    spec_id = ctx.remember(GROUP, "action_spec_id", str(spec["action_spec_id"]))
    spec = _act(client, f"{base}/action-specs/{spec_id}", "activate", spec["version"], "启用动作库")
    ctx.remember(GROUP, "action_spec_version", "1")

    # --- 采集任务：主流程走到 SUBMITTED ---
    task = _create(
        client,
        f"{base}/collection-tasks",
        {
            "code": f"task-{stamp}-kitchen",
            "name": "厨房台面抓取采集（第一批）",
            "plan_id": plan_id,
            "sop_id": sop_id,
            "sop_version": "1",
            "scenario_id": scenario_id,
            "action_spec_id": spec_id,
            "action_spec_version": "1",
            "priority": "P0",
            "target_episode_count": 120,
            "target_duration_sec": 7200,
            "scheduled_start": "2026-11-18T01:00:00Z",
            "scheduled_end": "2026-11-20T09:00:00Z",
        },
        "创建采集任务",
    )
    task_id = ctx.remember(GROUP, "task_id", str(task["task_id"]))
    task_path = f"{base}/collection-tasks/{task_id}"
    # 职责分离：创建人不得审批自己的任务，评审必须换一个身份。
    reviewer = ctx.as_("reviewer")
    task = _act(client, task_path, "submit-review", task["version"], "任务提交评审")
    task = _act(reviewer, task_path, "approve", task["version"], "任务评审通过")
    task = _act(client, task_path, "ready", task["version"], "任务进入就绪")
    version = task["version"]
    # 分配同时绑定执行人、机器人、设备与传感器：这几列此前从未被真实写过，
    # `collection_task_devices` / `collection_task_sensors` 一直是空表。
    assignment: dict[str, Any] = {
        "assigned_to": ctx.user_id,
        "scheduled_start": "2026-11-18T01:00:00Z",
    }
    for key, field_name in (
        ("robot_primary", "assigned_robot_id"),
        ("device_camera", "assigned_device_ids"),
        ("sensor_rgb", "assigned_sensor_ids"),
    ):
        try:
            value = ctx.recall("fleet", key)
        except SeedFailure:
            # 未跑 fleet 模块时如实跳过，不伪造 ID。
            continue
        assignment[field_name] = [value] if field_name.endswith("_ids") else value
    task = _act(
        client,
        task_path,
        "assign",
        version,
        "任务指派（含机器人/设备/传感器）",
        assignment,
    )
    version = task["version"]
    task = _act(client, task_path, "start", version, "任务开始")
    # pause / resume 等动作强制要求原因（_REASON_REQUIRED_ACTIONS）。
    task = _act(
        client, task_path, "pause", task["version"], "任务暂停", {"reason": "等待补光灯返修"}
    )
    task = _act(
        client, task_path, "resume", task["version"], "任务恢复", {"reason": "补光灯已更换"}
    )

    # --- 采集批次 ---
    # 批次动作受任务状态约束（BATCH 的 ready/start 要求任务处于
    # READY/ASSIGNED/IN_PROGRESS），所以必须在任务 SUBMITTED 之前推进。
    batch = _create(
        client,
        f"{base}/collection-tasks/{task_id}/batches",
        {
            "code": f"batch-{stamp}-kitchen-1",
            "sequence_no": 1,
            "expected_count": 30,
            "assigned_to": ctx.user_id,
            "scheduled_start": "2026-11-18T01:00:00Z",
            "scheduled_end": "2026-11-18T05:00:00Z",
        },
        "创建采集批次",
    )
    batch_id = ctx.remember(GROUP, "batch_id", str(batch["batch_id"]))
    batch_path = f"{base}/collection-batches/{batch_id}"
    batch = _act(client, batch_path, "ready", batch["version"], "批次就绪")
    batch = _act(client, batch_path, "start", batch["version"], "批次开始")
    batch = _act(
        client, batch_path, "pause", batch["version"], "批次暂停", {"reason": "现场设备标定"}
    )
    batch = _act(client, batch_path, "resume", batch["version"], "批次恢复", {"reason": "标定完成"})
    batch = _act(client, batch_path, "submit", batch["version"], "批次提交")
    ctx.remember(GROUP, "batch_status", str(batch.get("status", "")))

    task = _act(client, task_path, "submit", task["version"], "任务提交数据")
    version = task["version"]
    ctx.remember(GROUP, "task_status", str(task.get("status", "")))

    # --- 采集任务：另一条走取消 ---
    cancelled = _create(
        client,
        f"{base}/collection-tasks",
        {
            "code": f"task-{stamp}-cancelled",
            "name": "客厅地面导航采集（已取消）",
            "plan_id": plan_id,
            "priority": "P2",
            "target_episode_count": 40,
            "target_duration_sec": 3600,
        },
        "创建待取消任务",
    )
    _act(
        client,
        f"{base}/collection-tasks/{cancelled['task_id']}",
        "cancel",
        cancelled["version"],
        "取消任务",
        {"reason": "场景排期冲突，改到下个采集窗口"},
    )

    ctx.remember(GROUP, "batch_status", str(batch.get("status", "")))
