"""数据源模块：登记数据源 / 导入任务 / 导入审核。

覆盖的状态路径
--------------
- 数据源：登记（首次）或复用（同库已有同 repo + revision 的登记）
  → ``PATCH`` 修正治理字段（带 ``If-Match``，version 自增）；
- 导入任务 A：``PENDING → RUNNING → SUCCEEDED``，由 datasource worker 真实下载
  预览子集后推进。种子只**读状态**，不代跑、不伪造成功；
- 导入任务 B：``PENDING → CANCELLED``（客户端动作 ``cancel``），
  覆盖「客户端可自行终止导入」这条路径；
- 审核：``PENDING → APPROVED``（``ACCEPT``，评审人身份）与
  ``PENDING → REJECTED``（``NEEDS_FIX``，必须带原因）。

为什么数据源编码不带时间戳
--------------------------
``data_sources`` 上有 ``(project_id, repo_id, revision, config_name, split)`` 唯一
约束（见迁移 ``uq_data_sources_project_repo_revision``）：同一份数据集在同一项目里
只能登记一次，否则血缘会分叉。因此数据源是**稳定身份**，重复运行会复用既有登记，
只有导入任务每次新增。

关于「下载失败」
----------------
导入真实依赖 Hugging Face 网络与对象存储。失败时本模块**不伪造成功**：先按真实
流程把审核结论记为 ``NEEDS_FIX``（这条记录本身是有效业务数据），再抛
``SeedFailure("BLOCKED: ...")`` 让 run.py 以非零码退出。
"""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "datasources"

# 公开第一人称数据集：许可与访问条件明确（CC-BY-4.0 / PUBLIC）。
# revision 固定到 commit sha——分支名会移动，导入就不可复现。
REPO_ID = "nvidia/PhysicalAI-Human-1M-100K"
REVISION = "7e312e654818d1e0d3ebec3b78e50ee833b429f8"
LICENSE = "CC-BY-4.0"
DATASET_CARD_URL = "https://huggingface.co/datasets/nvidia/PhysicalAI-Human-1M-100K"

# 数据源是稳定身份（见模块文档），重复运行复用同一份登记。
SOURCE_CODE = "seed-hf-human1m-100k"
SOURCE_NAME = "Human-1M 第一人称演示数据集（预览子集）"
SOURCE_NOTES = (
    "本地演示种子登记：revision 固定到 commit sha，导入只取 preview 子集的小样本，"
    "不代表已获得全量数据或商业使用权。"
)

# 小样本导入：每个 episode 约 3 个机位，5 条即 15 个视频文件。
SAMPLE_LIMIT = 5

IMPORT_TOOL_NAME = "ddp-data-huggingface-importer"
IMPORT_TOOL_VERSION = "1.0.0"

TERMINAL_IMPORT_STATUSES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED"})
IMPORT_POLL_SECONDS = 2.0
IMPORT_TIMEOUT_SECONDS = 600.0
# 取消时与 worker 抢同一行会得到 version_conflict；重读后重试即可，次数有界。
CANCEL_MAX_ATTEMPTS = 5


def _as_dict(label: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _get(client: Client, path: str, label: str) -> dict[str, Any]:
    return _as_dict(label, expect(label, client.call("GET", path), 200))


def _list_all(client: Client, path: str, label: str) -> list[dict[str, Any]]:
    """按游标翻完所有页；``limit`` 取契约上限 100。"""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        url = f"{path}{'&' if '?' in path else '?'}limit=100"
        if cursor:
            url += f"&cursor={quote(cursor, safe='')}"
        page = _get(client, url, label)
        raw_items = page.get("items") or []
        if not isinstance(raw_items, list):
            raise SeedFailure(f"{label}：items 不是数组")
        items.extend(item for item in raw_items if isinstance(item, dict))
        next_cursor = page.get("next_cursor")
        if not next_cursor:
            return items
        cursor = str(next_cursor)


def _ensure_source(client: Client, base: str) -> dict[str, Any]:
    """返回本项目内该 repo + revision 的数据源；没有则登记。

    服务端对 ``(repo_id, revision, config_name, split)`` 有唯一约束，重复 POST
    会返回 409。因此先查后建，并把 409 当作「并发下已被登记」重新查一次，
    而不是把冲突吞掉。
    """
    for item in _list_all(client, f"{base}/data-sources?kind=HUGGINGFACE", "读取数据源列表"):
        if (
            item.get("repo_id") == REPO_ID
            and item.get("revision") == REVISION
            and item.get("config_name") is None
            and item.get("split") is None
        ):
            return item

    body = {
        "code": SOURCE_CODE,
        "name": SOURCE_NAME,
        "kind": "HUGGINGFACE",
        "repo_id": REPO_ID,
        "revision": REVISION,
        "license": LICENSE,
        "access_condition": "PUBLIC",
        "is_gated": False,
        "dataset_card_url": DATASET_CARD_URL,
        "notes": SOURCE_NOTES,
    }
    status, payload = client.call(
        "POST",
        f"{base}/data-sources",
        body=body,
        headers={"Idempotency-Key": idempotency_key()},
    )
    if status == 201:
        return _as_dict("登记数据源", payload)
    if status == 409:
        existing = _list_all(client, f"{base}/data-sources?kind=HUGGINGFACE", "重新读取数据源列表")
        for item in existing:
            if item.get("repo_id") == REPO_ID and item.get("revision") == REVISION:
                return item
        raise SeedFailure(f"登记数据源：服务端报冲突但查不到既有登记\n  响应：{payload}")
    raise SeedFailure(f"登记数据源：期望 201/409，实际 {status}\n  响应：{payload}")


def _patch_notes(client: Client, base: str, source: dict[str, Any]) -> dict[str, Any]:
    """修正治理说明；已是目标值时不写，避免每次运行都涨 version。"""
    if (source.get("notes") or "") == SOURCE_NOTES:
        return source
    return _as_dict(
        "修改数据源治理字段",
        expect(
            "修改数据源治理字段",
            client.call(
                "PATCH",
                f"{base}/data-sources/{source['data_source_id']}",
                body={"notes": SOURCE_NOTES},
                headers=if_match(int(source["version"])),
            ),
            200,
        ),
    )


def _create_import(
    client: Client, base: str, source_id: str, *, sample_limit: int
) -> dict[str, Any]:
    body = {
        "scope": "SAMPLE",
        "tool_name": IMPORT_TOOL_NAME,
        "tool_version": IMPORT_TOOL_VERSION,
        "sample_limit": sample_limit,
        "cache_location": None,
    }
    created = _as_dict(
        "创建导入任务",
        expect(
            "创建导入任务",
            client.call(
                "POST",
                f"{base}/data-sources/{source_id}/imports",
                body=body,
                headers={"Idempotency-Key": idempotency_key()},
            ),
            201,
        ),
    )
    if created.get("status") != "PENDING":
        raise SeedFailure(f"创建导入任务：初始状态应为 PENDING，实际 {created.get('status')}")
    return created


def _wait_terminal(client: Client, base: str, import_id: str) -> tuple[dict[str, Any], list[str]]:
    """等 worker 把导入推进到终态；返回终态记录与观察到的状态序列。"""
    seen: list[str] = []
    deadline = time.monotonic() + IMPORT_TIMEOUT_SECONDS
    while True:
        current = _get(client, f"{base}/data-source-imports/{import_id}", "读取导入任务")
        status = str(current.get("status"))
        if not seen or seen[-1] != status:
            seen.append(status)
        if status in TERMINAL_IMPORT_STATUSES:
            return current, seen
        if time.monotonic() >= deadline:
            raise SeedFailure(
                f"BLOCKED: 导入任务 {import_id} 在 {IMPORT_TIMEOUT_SECONDS:.0f}s 内未结束"
                f"（当前 {status}）；检查 ddp-datasource-worker 与 Hugging Face 连通性"
            )
        time.sleep(IMPORT_POLL_SECONDS)


def _ensure_review(client: Client, base: str, import_id: str) -> dict[str, Any]:
    review = _as_dict(
        "创建导入审核",
        expect(
            "创建导入审核",
            client.call("POST", f"{base}/data-source-imports/{import_id}/review"),
            201,
        ),
    )
    if review.get("status") != "PENDING":
        raise SeedFailure(f"创建导入审核：初始状态应为 PENDING，实际 {review.get('status')}")
    return review


def _decide_review(
    reviewer: Client,
    base: str,
    import_id: str,
    review: dict[str, Any],
    *,
    decision: str,
    reason: str,
    label: str,
) -> dict[str, Any]:
    decided = _as_dict(
        label,
        expect(
            label,
            reviewer.call(
                "POST",
                f"{base}/data-source-imports/{import_id}/review/decisions",
                body={"decision": decision, "reason": reason},
                headers=if_match(int(review["version"])),
            ),
            200,
        ),
    )
    expected_status = "APPROVED" if decision == "ACCEPT" else "REJECTED"
    if decided.get("status") != expected_status or decided.get("decision") != decision:
        raise SeedFailure(
            f"{label}：期望 {decision}/{expected_status}，"
            f"实际 {decided.get('decision')}/{decided.get('status')}"
        )
    return decided


def _cancel_import(client: Client, base: str, import_id: str) -> dict[str, Any]:
    """用客户端动作取消导入；worker 抢到同一行时按 version_conflict 重读重试。"""
    for _ in range(CANCEL_MAX_ATTEMPTS):
        current = _get(client, f"{base}/data-source-imports/{import_id}", "读取待取消的导入任务")
        status = str(current.get("status"))
        if status == "CANCELLED":
            return current
        if status in TERMINAL_IMPORT_STATUSES:
            raise SeedFailure(f"取消导入：任务已到终态 {status}，cancel 动作不再可用")

        # 契约把 requestBody 标为 required:false，但服务端该字段没有默认值，
        # 不带 body 会被 FastAPI 判为 missing body（422）。发空对象既满足实现，
        # 也未越过契约（所有字段都可选）。
        code, payload = client.call(
            "POST",
            f"{base}/data-source-imports/{import_id}/actions/cancel",
            body={},
            headers=if_match(int(current["version"])),
        )
        if code == 200:
            cancelled = _as_dict("取消导入任务", payload)
            if cancelled.get("status") != "CANCELLED":
                raise SeedFailure(f"取消导入任务：期望 CANCELLED，实际 {cancelled.get('status')}")
            return cancelled
        if code == 409 and _error_code(payload) == "version_conflict":
            continue
        raise SeedFailure(
            f"取消导入任务：期望 200/409(version_conflict)，实际 {code}\n  响应：{payload}"
        )
    raise SeedFailure(f"取消导入任务：连续 {CANCEL_MAX_ATTEMPTS} 次版本冲突")


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"

    # --- 数据源：登记 / 复用 + 治理字段 ---
    source = _ensure_source(client, base)
    source_id = ctx.remember(GROUP, "data_source_id", str(source["data_source_id"]))
    source = _patch_notes(client, base, source)
    ctx.remember(GROUP, "data_source_version", str(source["version"]))

    # --- 导入任务 A：真实下载，等 worker 推进到终态 ---
    import_a = _create_import(client, base, source_id, sample_limit=SAMPLE_LIMIT)
    import_a_id = ctx.remember(GROUP, "import_id", str(import_a["import_id"]))
    import_a, transitions = _wait_terminal(client, base, import_a_id)
    ctx.remember(GROUP, "import_status", str(import_a["status"]))

    # --- 审核：先建记录，再由第二个身份给结论（职责分离）---
    review = _ensure_review(client, base, import_a_id)
    ctx.remember(GROUP, "review_id", str(review["review_id"]))
    reviewer = ctx.as_("reviewer")

    if import_a["status"] != "SUCCEEDED":
        # 失败也必须留痕：审核结论写成 NEEDS_FIX（受审对象确实不可用）。
        failure_detail = import_a.get("error_message") or "无错误信息"
        _decide_review(
            reviewer,
            base,
            import_a_id,
            review,
            decision="NEEDS_FIX",
            reason=f"导入未成功（状态 {import_a['status']}）：{failure_detail}",
            label="导入审核给出待修复结论",
        )
        raise SeedFailure(
            f"BLOCKED: 数据源导入未成功（状态 {import_a['status']}）："
            f"{import_a.get('error_message') or '无错误信息'}；"
            f"观察到的状态序列 {transitions}，检查 ddp-datasource-worker 与 Hugging Face 连通性"
        )

    quality = _get(
        client,
        f"{base}/data-source-imports/{import_a_id}/quality-report",
        "读取导入质量报告",
    )
    if not isinstance(quality.get("passed"), bool):
        raise SeedFailure(f"读取导入质量报告：缺少 passed 布尔值：{quality}")
    ctx.remember(GROUP, "quality_passed", str(quality["passed"]))

    # 审核结论按质量报告的真实结论给出，不预设「一定通过」。
    summary = (
        f"质量报告：规则版本 {quality.get('rule_version')}，"
        f"样本范围 {quality.get('sample_scope')}，"
        f"{quality.get('total_episodes')} 条 episode / {quality.get('total_assets')} 个资产。"
    )
    if quality["passed"]:
        decision = "ACCEPT"
        reason = f"{summary}全部规则通过，准予进入 Canonical 层。"
    else:
        failures = "、".join(
            str(item.get("rule_id"))
            for item in quality.get("failures") or []
            if isinstance(item, dict)
        )
        decision = "NEEDS_FIX"
        reason = f"{summary}未通过规则：{failures or '见质量报告'}，修复后重新导入。"
    _decide_review(
        reviewer,
        base,
        import_a_id,
        review,
        decision=decision,
        reason=reason,
        label="导入审核给出结论",
    )

    # --- 导入任务 B：客户端主动取消 ---
    import_b = _create_import(client, base, source_id, sample_limit=1)
    import_b_id = ctx.remember(GROUP, "cancelled_import_id", str(import_b["import_id"]))
    cancelled = _cancel_import(client, base, import_b_id)
    if cancelled.get("finished_at") is None:
        raise SeedFailure("取消导入任务：终态缺少 finished_at")
    ctx.remember(GROUP, "cancelled_import_status", str(cancelled["status"]))

    # --- 列表读一遍，确认两条导入都在项目范围内可见 ---
    listed = _list_all(
        client,
        f"{base}/data-source-imports?data_source_id={source_id}",
        "读取导入任务列表",
    )
    listed_ids = {str(item.get("import_id")) for item in listed}
    missing = {import_a_id, import_b_id} - listed_ids
    if missing:
        raise SeedFailure(f"读取导入任务列表：缺少本次创建的导入 {sorted(missing)}")
