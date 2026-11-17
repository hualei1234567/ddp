#!/usr/bin/env python3
"""核对"每个模块都有数据"：逐模块调用列表端点，打印计数与状态分布。

只读，不发任何写请求。用法::

    backend/.venv/bin/python scripts/seed/verify.py
    backend/.venv/bin/python scripts/seed/verify.py --min 1
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from seed.client import Client  # noqa: E402
from seed.run import _load_env, _project, _token  # noqa: E402

# 模块 → [(展示名, 列表端点, 期望至少多少条)]
CHECKS: dict[str, list[tuple[str, str, int]]] = {
    "设备与场地": [
        ("机器人", "/robots", 2),
        ("设备", "/devices", 2),
        ("传感器", "/sensors", 2),
        ("校准", "/calibrations", 1),
        ("场地", "/locations", 2),
    ],
    "采集": [
        ("采集方案", "/collection-plans", 2),
        ("SOP", "/collection-sops", 1),
        ("场景", "/scenarios", 2),
        ("动作库", "/action-specs", 1),
        ("采集任务", "/collection-tasks", 2),
        ("采集批次", "__batches__", 1),
    ],
    "数据接入": [
        ("数据源", "/data-sources", 1),
        ("导入任务", "/data-source-imports", 1),
    ],
    "Episode": [("Episode", "/episodes", 3)],
    "标注": [
        ("标注 schema", "/annotation-schemas", 1),
        ("抽样集", "/annotation-sample-sets", 1),
        ("标注任务", "/annotation-tasks", 1),
        ("指派", "/annotation-assignments", 1),
        ("提交", "__sum_annotation__/annotation-submissions", 2),
        ("金标集", "/gold-sets", 1),
        ("IAA 报告", "__sum_annotation__/iaa-reports", 1),
        ("质量抽样", "__sum_annotation__/quality-samples", 1),
    ],
    "数据集": [
        ("数据集", "/datasets", 2),
    ],
    "导出": [("导出任务", "/exports", 2)],
    "训练": [
        ("训练需求", "/data-requirements", 1),
        ("训练请求", "/training-requests", 1),
        ("训练交付", "/training-deliveries", 1),
    ],
    "模型与推理": [
        ("模型", "/models", 1),
        ("评估", "/evaluations", 1),
        ("推理服务", "/inference-services", 1),
        ("推理任务", "/inference-tasks", 1),
        ("模型反馈", "/model-feedback", 1),
    ],
    "供应商与交付": [
        ("供应商", "/suppliers", 1),
        ("到货批次", "/inbound-batches", 1),
        ("验收台账", "/acceptance-ledger", 1),
    ],
    "治理": [("资源配额", "/quotas", 1), ("用量报告", "/usage-report", 0)],
    "审计": [("审计日志", "/audit-logs", 1)],
}


def _first_id(client: Client, base: str, path: str, key: str) -> str | None:
    status, payload = client.call("GET", base + path)
    if status != 200 or not isinstance(payload, dict):
        return None
    items = payload.get("items") or []
    return str(items[0][key]) if items else None


def _sum_over_annotation_tasks(client: Client, base: str, path: str) -> int:
    """标注子资源（提交/IAA/质检抽样）挂在任务下，需按任务累加。"""
    status, payload = client.call("GET", base + "/annotation-tasks")
    if status != 200 or not isinstance(payload, dict):
        return -1
    total = 0
    for task in payload.get("items") or []:
        sub_status, sub = client.call("GET", f"{base}{path}?task_id={task['task_id']}")
        if sub_status == 200 and isinstance(sub, dict):
            total += len(sub.get("items") or [])
    return total


def _all_batches(client: Client, base: str) -> int:
    """采集批次挂在任务下，没有项目级列表：按任务逐个累加。"""
    status, payload = client.call("GET", base + "/collection-tasks")
    if status != 200 or not isinstance(payload, dict):
        return -1
    total = 0
    for task in payload.get("items") or []:
        sub_status, sub = client.call("GET", f"{base}/collection-tasks/{task['task_id']}/batches")
        if sub_status == 200 and isinstance(sub, dict):
            total += len(sub.get("items") or [])
    return total


def _count(client: Client, base: str, path: str) -> tuple[int, Counter[str]]:
    if path == "__batches__":
        return _all_batches(client, base), Counter()
    if path.startswith("__sum_annotation__"):
        sub_path = "/" + path.split("/", 1)[1]
        total = _sum_over_annotation_tasks(client, base, sub_path)
        return total, Counter()
    if "{first_annotation_task}" in path:
        task_id = _first_id(client, base, "/annotation-tasks", "task_id")
        if task_id is None:
            return -1, Counter()
        path = path.replace("{first_annotation_task}", task_id)
    status, payload = client.call("GET", base + path)
    if status != 200 or not isinstance(payload, dict):
        return -1, Counter()
    items = payload.get("items")
    if items is None:
        return (1 if payload else 0), Counter()
    return len(items), Counter(str(item.get("status", "-")) for item in items)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="核对各模块是否有数据")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    env = _load_env()
    client = Client(args.api_base, _token(env))
    base = f"/api/v1/projects/{_project(client, env)}"

    missing: list[str] = []
    for module, checks in CHECKS.items():
        print(f"\n{module}")
        for label, path, want in checks:
            count, statuses = _count(client, base, path)
            if count < 0:
                print(f"  ✗ {label:<12} 读取失败")
                missing.append(f"{module}/{label}")
                continue
            detail = "  ".join(f"{k}:{v}" for k, v in sorted(statuses.items()))
            mark = "✓" if count >= want else "✗"
            if count < want:
                missing.append(f"{module}/{label}")
            print(f"  {mark} {label:<12} {count:>4} 条   {detail}")

    print()
    if missing:
        print(f"缺少数据的条目：{len(missing)}")
        for item in missing:
            print(f"  - {item}")
        return 1
    print("所有模块均有数据。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
