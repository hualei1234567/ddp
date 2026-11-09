"""阶段 4 staging 链路验证：策略→版本→构建/校验/冻结→导出→训练交付。"""

from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx

BASE = "http://127.0.0.1:18000/api/v1"
PROJECT = "00000000-0000-4000-8000-000000000002"
DATASET = "888cc1f9-239a-42c1-ac0e-3ca22ddd80be"
OWNER = "00000000-0000-4000-8000-000000000001"
EDITOR = "00000000-0000-4000-8000-000000000003"
OWNER_TOKEN = sys.argv[1]
EDITOR_TOKEN = sys.argv[2]

results: list[dict[str, object]] = []


def record(step: str, status: int, detail: object) -> None:
    results.append({"step": step, "status": status, "detail": detail})
    print(f"[{step}] {status} {detail}")


def client(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=BASE,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
        trust_env=False,
    )


def call(step, method, path, *, token, **kwargs):
    with client(token) as c:
        response = c.request(method, path, **kwargs)
    if response.status_code >= 400:
        print(f"!! {step} {response.status_code} {response.text}")
        raise SystemExit(1)
    body = response.json() if response.content else None
    record(step, response.status_code, "")
    return body


def get(step, path, token=EDITOR_TOKEN):
    return call(step, "GET", path, token=token)


def post(step, path, token=EDITOR_TOKEN, **kwargs):
    return call(step, "POST", path, token=token, **kwargs)


owner = client(OWNER_TOKEN)
editor = client(EDITOR_TOKEN)

# 1. 策略
dedup = post(
    "创建去重策略",
    f"/projects/{PROJECT}/datasets/{DATASET}/dedup-policies",
    json={
        "exact_keys": ["EPISODE_LINEAGE", "MANIFEST_SHA256", "RAW_ASSET_SHA256"],
        "near_duplicate_enabled": False,
        "near_duplicate_enforcement": "WARN_ONLY",
        "retain_rule": "NEWEST_ACCEPTED",
    },
)
split = post(
    "创建划分策略",
    f"/projects/{PROJECT}/datasets/{DATASET}/split-policies",
    json={
        "strategy": "RATIO",
        "train_ratio": 0.8,
        "validation_ratio": 0.1,
        "test_ratio": 0.1,
        "seed": 42,
        "group_by": ["EPISODE_LINEAGE"],
        "empty_split_policy": "FAIL",
    },
)
leakage = post(
    "创建泄漏策略",
    f"/projects/{PROJECT}/datasets/{DATASET}/leakage-policies",
    json={
        "hard_rules": [
            "EPISODE_LINEAGE_CROSS_SPLIT",
            "MANIFEST_SHA256_CROSS_SPLIT",
            "RAW_ASSET_SHA256_CROSS_SPLIT",
        ],
        "soft_rules": ["SCENE_GROUP_CROSS_SPLIT", "TEMPORAL_OVERLAP_CROSS_SPLIT"],
        "waiver_allowed_rules": ["SCENE_GROUP_CROSS_SPLIT", "TEMPORAL_OVERLAP_CROSS_SPLIT"],
        "scene_grouping_enabled": True,
    },
)
quality = post(
    "创建质量策略",
    f"/projects/{PROJECT}/datasets/{DATASET}/quality-policies",
    json={"min_selected_count": 1, "allow_warn_quality": True},
)

# 2. 版本 + 预览
version = post(
    "创建数据集版本",
    f"/projects/{PROJECT}/datasets/{DATASET}/versions",
    json={
        "selection_rule_version": "dataset-selection/1",
        "selection_filters": {},
        "dedup_policy_id": dedup["dedup_policy_id"],
        "dedup_policy_version": dedup["version"],
        "split_policy_id": split["split_policy_id"],
        "split_policy_version": split["version"],
        "leakage_policy_id": leakage["leakage_policy_id"],
        "leakage_policy_version": leakage["version"],
        "quality_policy_id": quality["quality_policy_id"],
        "quality_policy_version": quality["version"],
    },
    headers={"Idempotency-Key": f"e2e-version-{uuid4().hex[:8]}"},
)
VID = version["dataset_version_id"]
preview = post(
    "选择预览",
    f"/projects/{PROJECT}/datasets/{DATASET}/versions/{VID}/selection-preview",
    json={
        "selection_filters": {},
        "dedup_policy_id": dedup["dedup_policy_id"],
        "dedup_policy_version": dedup["version"],
        "split_policy_id": split["split_policy_id"],
        "split_policy_version": split["version"],
        "leakage_policy_id": leakage["leakage_policy_id"],
        "leakage_policy_version": leakage["version"],
        "quality_policy_id": quality["quality_policy_id"],
        "quality_policy_version": quality["version"],
    },
)
print("preview:", preview["candidate_count"], preview["selected_count"], preview["split_counts"])

# 3. 构建 / 校验 / 冻结
built = post(
    "构建版本",
    f"/projects/{PROJECT}/datasets/{DATASET}/versions/{VID}/actions/build",
    headers={"If-Match": f'"{version["version"]}"'},
    json={},
)
validated = post(
    "校验版本",
    f"/projects/{PROJECT}/datasets/{DATASET}/versions/{VID}/actions/validate",
    headers={"If-Match": f'"{built["version"]}"'},
    json={},
)
frozen = post(
    "冻结版本",
    f"/projects/{PROJECT}/datasets/{DATASET}/versions/{VID}/actions/freeze",
    token=OWNER_TOKEN,
    headers={"If-Match": f'"{validated["version"]}"'},
    json={"reason": "staging 全链路验证", "payload": {"qa_verified": True}},
)
record("冻结状态", 200, frozen["status"])

# 4. 导出
export = post(
    "创建导出任务",
    f"/projects/{PROJECT}/exports",
    json={
        "dataset_version_id": VID,
        "format": "LE_ROBOT",
        "format_version": "ddp.lerobot/1.0.0",
        "exporter_id": "ddp.lerobot-metadata",
        "exporter_version": "1.0.0",
        "mapping_version": "ddp.lerobot/1.0.0",
        "config": {"include_sidecar": True},
    },
    headers={"Idempotency-Key": f"e2e-export-{uuid4().hex[:8]}"},
)
EXPORT_ID = export["export_job_id"]
print("export job:", EXPORT_ID, export["status"])

# 5. 训练需求 / 修订 / 交付
snapshot = {
    "business_goal": "staging 全链路训练数据",
    "model_type": "diffusion-policy",
    "training_type": "IMITATION",
    "data_modalities": ["RGB", "PROPRIOCEPTION"],
    "scene_targets": ["kitchen"],
    "action_targets": ["pick"],
    "robot_targets": ["arm-a"],
    "device_targets": ["cam-a"],
    "required_labels": ["action"],
    "required_schema_versions": [],
    "target_episode_count": 10,
    "quality_thresholds": {"min_quality_score": 0.8},
    "split_requirements": {"train_ratio": 0.8, "validation_ratio": 0.1, "test_ratio": 0.1},
    "format": "LE_ROBOT",
    "format_version": "ddp.lerobot/1.0.0",
    "delivery_due_at": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
    "acceptance_method": "离线回放验收",
    "priority": "P1",
}
request = post(
    "创建训练需求",
    f"/projects/{PROJECT}/training-requests",
    json={
        "code": f"TR-E2E-{uuid4().hex[:6]}",
        "title": "staging 全链路训练需求",
        "owner_id": OWNER,
        "priority": "P1",
        "snapshot": snapshot,
    },
    headers={"Idempotency-Key": f"e2e-tr-{uuid4().hex[:8]}"},
)
REQUEST_ID = request["training_request_id"]
REVISION_ID = request["current_revision_id"]


def revision():
    return get(
        "读取修订",
        f"/projects/{PROJECT}/training-requests/{REQUEST_ID}/revisions/{REVISION_ID}",
    )


current = revision()
current = post(
    "提交修订",
    f"/projects/{PROJECT}/training-requests/{REQUEST_ID}/revisions/{REVISION_ID}/actions/submit",
    headers={"If-Match": f'"{current["version"]}"'},
    json={},
)
current = post(
    "批准修订",
    f"/projects/{PROJECT}/training-requests/{REQUEST_ID}/revisions/{REVISION_ID}/actions/approve",
    token=OWNER_TOKEN,
    headers={"If-Match": f'"{current["version"]}"'},
    json={},
)
record("修订状态", 200, current["status"])

delivery = post(
    "创建训练交付",
    f"/projects/{PROJECT}/training-deliveries",
    json={
        "training_request_id": REQUEST_ID,
        "training_request_revision_id": REVISION_ID,
        "dataset_version_id": VID,
        "export_job_id": EXPORT_ID,
        "recipient_id": OWNER,
        "recipient_team": "算法组",
        "delivery_note_no": f"DN-E2E-{uuid4().hex[:6]}",
    },
    headers={"Idempotency-Key": f"e2e-dn-{uuid4().hex[:8]}"},
)
DELIVERY_ID = delivery["training_delivery_id"]
print("delivery:", DELIVERY_ID, delivery["status"])

out = {
    "dataset_version_id": VID,
    "export_job_id": EXPORT_ID,
    "training_request_id": REQUEST_ID,
    "revision_id": REVISION_ID,
    "delivery_id": DELIVERY_ID,
    "results": results,
}
open("/tmp/stage4_chain.json", "w").write(json.dumps(out, ensure_ascii=False, indent=2))
print("阶段 4 前半链路完成")
