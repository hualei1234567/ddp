"""V31 标注链路真实种子：为浏览器端到端准备一条可标注的分配。

只调用真实 HTTP API（服务端状态机是唯一权威），不直接写库、不 mock。

链路顺序由数据库守卫固定（`annotation_work_guards`）::

    schema: create → submit-review(owner) → publish(reviewer)
    sample set: create → preview → snapshot
    task: create → submit-review(owner) → approve(reviewer) → ready
    batch: create → ready → assign
    assignment: create → （任务 assign）→ claim(annotator) →（任务/批次 start）→ start

全部成功后在标准输出打印一行 JSON，供
`apps/web/e2e/annotation-console.spec.ts` 使用::

    export DDP_E2E_BASE_URL=http://127.0.0.1:5174
    export DDP_E2E_PROJECT_ID=<输出里的 project_id>
    export DDP_E2E_USER_ID=<输出里的 annotator_id>
    export DDP_E2E_ANNOTATION_ASSIGNMENT_ID=<输出里的 assignment_id>

前置（本机真实栈）::

    docker compose up -d postgres minio redis
    backend/.venv/bin/alembic upgrade head
    # 另开终端启动真实后端：DDP_ENV=local 指向上述 Postgres/MinIO

用法::

    DDP_E2E_PROJECT_ID=<uuid> \
    DDP_E2E_OWNER_ID=<uuid> \
    DDP_E2E_REVIEWER_ID=<uuid> \
    DDP_E2E_USER_ID=<uuid> \
    backend/.venv/bin/python scripts/e2e/v31-annotation-chain.py

注意：每次运行都会新建 Schema / 任务 / 分配，因此可重复执行；但同一个
分配只能被提交一次，重复跑 E2E 前请重新执行本脚本换一条新分配。
"""

from __future__ import annotations

import json
import os
import sys
import time

import httpx

API_BASE = os.environ.get("DDP_E2E_API_BASE", "http://127.0.0.1:8010/api/v1").rstrip("/")


def env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"缺少环境变量 {name}；见本文件顶部用法说明")
    return value


def client(user: str, project: str) -> httpx.Client:
    return httpx.Client(
        base_url=f"{API_BASE}/projects/{project}",
        headers={"X-User-Id": user},
        timeout=30.0,
    )


def act(
    http: httpx.Client, path: str, action: str, version: int, body: dict | None = None
) -> dict:
    response = http.post(
        f"{path}/actions/{action}",
        headers={
            "If-Match": f'"{version}"',
            "Idempotency-Key": f"v31-{action}-{version}-{time.time_ns()}",
        },
        json=body or {},
    )
    if response.status_code >= 400:
        print(
            f"{action} -> {response.status_code}: {response.text[:500]}",
            file=sys.stderr,
        )
        response.raise_for_status()
    return response.json()


def create_schema(owner: httpx.Client, stamp: str) -> dict:
    response = owner.post(
        "/annotation-schemas",
        json={
            "code": f"v31-console-{stamp}",
            "name": "V31 真实链路标注 Schema",
            "description": "Episode 结果 + 时间片段 + 物体跟踪框。",
            "annotation_levels": ["EPISODE", "TIME_RANGE", "OBJECT_TRACK"],
            "labels": [
                {
                    "label_key": "episode_outcome",
                    "label_type": "ENUM",
                    "required": True,
                    "level": "EPISODE",
                    "visibility": "ANNOTATOR",
                    "description": "Episode 是否成功完成",
                    "value_spec": {"enum_values": ["success", "failure"]},
                    "quality_rules": ["required_labels_present"],
                },
                {
                    "label_key": "action_segment",
                    "label_type": "TIME_RANGE",
                    "required": True,
                    "level": "TIME_RANGE",
                    "visibility": "ANNOTATOR",
                    "description": "主要操作片段",
                    "value_spec": {},
                    "quality_rules": [],
                },
                {
                    "label_key": "bbox",
                    "label_type": "OBJECT_TRACK",
                    "required": False,
                    "level": "OBJECT_TRACK",
                    "visibility": "ANNOTATOR",
                    "description": "目标物体跟踪轨迹",
                    "value_spec": {},
                    "quality_rules": [],
                },
                {
                    "label_key": "quality_score",
                    "label_type": "NUMBER",
                    "required": False,
                    "level": "EPISODE",
                    "visibility": "ANNOTATOR",
                    "description": "人工质量评分",
                    "value_spec": {"min_value": 0, "max_value": 1, "unit": "ratio"},
                    "quality_rules": [],
                },
            ],
            "schema_version": 1,
        },
    )
    response.raise_for_status()
    return response.json()


def create_sample_set(owner: httpx.Client, stamp: str, episode_ids: list[str]) -> dict:
    response = owner.post(
        "/annotation-sample-sets",
        json={
            "code": f"v31-sample-{stamp}",
            "name": "V31 真实链路样本集",
            "strategy": "FILTER",
            "selection_rule_version": "stage3-sample-selection/1",
            "filters": {"include_episode_ids": episode_ids},
            "dedup_key": "EPISODE_LINEAGE",
            "data_source": "REAL",
            "warn_confirmed": True,
        },
    )
    response.raise_for_status()
    return response.json()


def main() -> None:
    project = env("DDP_E2E_PROJECT_ID")
    owner = client(env("DDP_E2E_OWNER_ID"), project)
    reviewer = client(env("DDP_E2E_REVIEWER_ID"), project)
    annotator = client(env("DDP_E2E_USER_ID"), project)
    stamp = time.strftime("%Y%m%d%H%M%S")

    # 1. Schema：草稿 → 送审 → 发布。
    schema = create_schema(owner, stamp)
    schema_path = f"/annotation-schemas/{schema['schema_id']}"
    schema = act(owner, schema_path, "submit-review", schema["version"])
    schema = act(reviewer, schema_path, "publish", schema["version"])
    assert schema["status"] == "PUBLISHED", schema["status"]

    # 2. 样本集：真实 Episode（必须有 Manifest，保证有上游资产可审看）→ 快照。
    episodes = owner.get("/episodes", params={"status": "ACCEPTED", "limit": 5})
    episodes.raise_for_status()
    episode_ids = [
        item["episode_id"] for item in episodes.json()["items"] if item["manifest_sha256"]
    ][:3]
    assert len(episode_ids) == 3, episode_ids

    sample = create_sample_set(owner, stamp, episode_ids)
    sample_path = f"/annotation-sample-sets/{sample['sample_set_id']}"
    sample = act(owner, sample_path, "preview", sample["version"], {"warn_confirmed": True})
    sample = act(owner, sample_path, "snapshot", sample["version"])
    assert sample["status"] == "SNAPSHOTTED", sample["status"]

    # 3. 任务：创建 → 送审 → 审核通过 → 就绪。
    response = owner.post(
        "/annotation-tasks",
        json={
            "code": f"v31-task-{stamp}",
            "name": "V31 真实链路标注任务",
            "description": "浏览器端到端：工作台、轨迹编辑器、提交与复核。",
            "sample_set_id": sample["sample_set_id"],
            "schema_id": schema["schema_id"],
            "target_item_count": len(episode_ids),
            "target_episode_count": len(episode_ids),
            "priority": "P1",
            "assignment_mode": "MANUAL",
            "iaa_policy": {},
            "quality_policy": {"max_rework_rounds": 2},
        },
    )
    response.raise_for_status()
    task = response.json()
    task_path = f"/annotation-tasks/{task['task_id']}"
    task = act(owner, task_path, "submit-review", task["version"])
    task = act(reviewer, task_path, "approve", task["version"])
    task = act(owner, task_path, "ready", task["version"])
    assert task["status"] == "READY", task["status"]

    # 4. 批次：创建 → 就绪 → 派发。
    response = owner.post(
        f"{task_path}/batches",
        json={
            "planned_item_count": len(episode_ids),
            "code": f"v31-batch-{stamp}",
            "name": "V31 批次",
            "item_refs": episode_ids,
        },
    )
    response.raise_for_status()
    batch = response.json()
    batch_path = f"/annotation-batches/{batch['batch_id']}"
    batch = act(owner, batch_path, "ready", batch["version"])
    batch = act(owner, batch_path, "assign", batch["version"])
    assert batch["status"] == "ASSIGNED", batch["status"]

    # 5. 分配：创建（指定标注人）→ 任务 assign → 标注人 claim →
    #    任务 start → 批次 start → 分配 start。
    response = owner.post(
        "/annotation-assignments",
        json={
            "batch_id": batch["batch_id"],
            "assignee_id": os.environ["DDP_E2E_USER_ID"].strip(),
            "assignment_role": "ANNOTATOR",
            "planned_item_count": len(episode_ids),
            "planned_minutes": 30,
        },
    )
    response.raise_for_status()
    assignment = response.json()
    assignment_path = f"/annotation-assignments/{assignment['assignment_id']}"

    task = act(owner, task_path, "assign", task["version"])
    assignment = act(annotator, assignment_path, "claim", assignment["version"])
    task = act(owner, task_path, "start", task["version"])
    batch = act(owner, batch_path, "start", batch["version"])
    assignment = act(annotator, assignment_path, "start", assignment["version"])
    assert assignment["status"] == "IN_PROGRESS", assignment["status"]

    print(
        json.dumps(
            {
                "project_id": project,
                "annotator_id": os.environ["DDP_E2E_USER_ID"].strip(),
                "schema_id": schema["schema_id"],
                "task_id": task["task_id"],
                "assignment_id": assignment["assignment_id"],
                "episode_ids": episode_ids,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPStatusError as exc:
        print(
            f"HTTP {exc.response.status_code}: {exc.response.text[:800]}",
            file=sys.stderr,
        )
        raise
