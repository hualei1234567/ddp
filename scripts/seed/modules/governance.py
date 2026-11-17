"""经营治理种子：资源配额与用量报表。

覆盖的路径
----------
- ``PUT /quotas/{kind}`` 新建四类配额（不带 ``If-Match``）；
- 再对 ``EPISODES`` 做一次修改（带 ``If-Match``），验证「新建不需要、修改必须有」
  的前置条件口径；
- ``GET /quotas`` 读回并核对上限；
- ``GET /usage-report`` 读一次并断言 200 与结构，不造任何用量数据。

为什么 ``INFERENCE_TASKS`` 故意不配
------------------------------------
契约把「未配置上限」和「上限为 0」定义为两种状态（``UNLIMITED`` vs
``AT_LIMIT``）。留一类不配置，报表里才有 ``limit=null`` 的真实样本；
把五类全配满会让这个分支在演示数据里永远看不到。

关于数值
--------
上限取得足够宽松（远高于本地演示栈的真实用量），避免种子本身把后续模块的
写入挡住。``EPISODES`` 先写 20 万再调到 30 万，是为了让「修改」路径每次运行
都有一次真实的版本递增，而不是把同值重复提交当成功。
"""

from __future__ import annotations

from typing import Any

from seed.client import Client, Context, SeedFailure, expect, if_match

GROUP = "governance"

# 报表固定顺序 = ResourceKind 声明顺序（backend/app/domain/governance/enums.py）。
RESOURCE_KIND_ORDER = (
    "OBJECT_STORAGE_BYTES",
    "EPISODES",
    "DATASET_VERSIONS",
    "INGESTION_JOBS",
    "INFERENCE_TASKS",
)

EPISODES_INITIAL = 200_000
EPISODES_UPDATED = 300_000

# 最终期望的上限；EPISODES 会被写两次，最终取这个值。
TARGET_LIMITS: dict[str, int] = {
    "OBJECT_STORAGE_BYTES": 10_995_116_277_760,  # 10 TiB
    "EPISODES": EPISODES_UPDATED,
    "DATASET_VERSIONS": 2_000,
    "INGESTION_JOBS": 10_000,
}


def _put(
    client: Client,
    base: str,
    kind: str,
    limit: int,
    version: int | None,
    label: str,
) -> dict[str, Any]:
    headers = if_match(version) if version is not None else None
    payload = expect(
        label,
        client.call("PUT", f"{base}/quotas/{kind}", body={"limit": limit}, headers=headers),
        200,
    )
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _quotas(client: Client, base: str) -> dict[str, dict[str, Any]]:
    payload = expect("读取配额列表", client.call("GET", f"{base}/quotas"), 200)
    if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
        raise SeedFailure(f"读取配额列表：响应形状不符 {payload!r}")
    return {str(item["resource_kind"]): item for item in payload["items"]}


def _set_quotas(client: Client, base: str) -> None:
    existing = _quotas(client, base)
    for kind in ("OBJECT_STORAGE_BYTES", "DATASET_VERSIONS", "INGESTION_JOBS"):
        current = existing.get(kind)
        _put(
            client,
            base,
            kind,
            TARGET_LIMITS[kind],
            int(current["version"]) if current else None,
            f"设置 {kind} 配额",
        )

    current = existing.get("EPISODES")
    first = _put(
        client,
        base,
        "EPISODES",
        EPISODES_INITIAL,
        int(current["version"]) if current else None,
        "新建 EPISODES 配额",
    )
    if first.get("limit") != EPISODES_INITIAL:
        raise SeedFailure(f"新建 EPISODES 配额：limit 不符 {first.get('limit')!r}")
    updated = _put(
        client,
        base,
        "EPISODES",
        EPISODES_UPDATED,
        int(first["version"]),
        "调整 EPISODES 配额（If-Match）",
    )
    if updated.get("limit") != EPISODES_UPDATED:
        raise SeedFailure(f"调整 EPISODES 配额：limit 不符 {updated.get('limit')!r}")
    if int(updated["version"]) <= int(first["version"]):
        raise SeedFailure("调整 EPISODES 配额：版本未递增，If-Match 修改未生效")


def _verify_report(client: Client, base: str, project_id: str) -> None:
    report = expect("读取用量报表", client.call("GET", f"{base}/usage-report"), 200)
    if not isinstance(report, dict):
        raise SeedFailure(f"读取用量报表：响应不是对象：{report!r}")
    if str(report.get("project_id")) != project_id:
        raise SeedFailure(f"读取用量报表：project_id 不符 {report.get('project_id')!r}")
    entries = report.get("entries")
    if not isinstance(entries, list) or len(entries) != len(RESOURCE_KIND_ORDER):
        raise SeedFailure(f"读取用量报表：entries 形状不符 {entries!r}")

    order = tuple(str(entry.get("resource_kind")) for entry in entries)
    if order != RESOURCE_KIND_ORDER:
        raise SeedFailure(f"读取用量报表：条目顺序不符 {order!r}")

    by_kind = {str(entry["resource_kind"]): entry for entry in entries}
    for kind, limit in TARGET_LIMITS.items():
        if by_kind[kind].get("limit") != limit:
            raise SeedFailure(
                f"读取用量报表：{kind} limit 期望 {limit}，实际 {by_kind[kind].get('limit')!r}"
            )
    # 故意不配的一类：limit 必须是 null（未配置上限），不能是 0。
    if by_kind["INFERENCE_TASKS"].get("limit") is not None:
        raise SeedFailure("读取用量报表：INFERENCE_TASKS 不应有配额上限")


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    _set_quotas(client, base)
    _verify_report(client, base, ctx.project_id)

    quotas = _quotas(client, base)
    for kind in RESOURCE_KIND_ORDER:
        if kind in TARGET_LIMITS:
            ctx.remember(GROUP, kind.lower(), str(quotas[kind]["quota_id"]))
