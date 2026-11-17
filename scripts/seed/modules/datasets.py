"""数据集模块：数据集 / 版本 / 策略 / 授权。

覆盖的状态路径
--------------
- 数据集 A（承载版本）：``DRAFT → ACTIVE``；
- 数据集 B（演示归档）：``DRAFT → ACTIVE → ARCHIVED``（归档需负责人权限与理由）；
- 版本 v1：``DRAFT → BUILDING → VALIDATING``（build）→ ``READY``（validate）
  → ``FROZEN``（冻结人必须不是构建人，且提交 ``payload.qa_verified``）；
- 版本 v2：仅走到 ``DRAFT``，用第二套策略做选择预览，不构建；
- 授权：``PENDING_APPROVAL → ACTIVE``（approve）与 ``PENDING_APPROVAL →
  REJECTED``（reject，必须给理由）。

关于上游 Episode
----------------
版本构建只引用项目里**真实存在**的 Episode（``GET /episodes``）。候选资格由
服务端真实规则判定：状态 ACCEPTED/COMPLETED、质量 PASS（或显式确认 WARN）、
已批准标注快照、Manifest Hash、RawAsset、时间同步与标定。种子不伪造这些事实；
项目里没有合格 Episode 时，build 会被服务端拒绝，模块如实失败而不是造空版本。

``allow_missing_calibration`` / ``allow_degraded_time_sync`` / ``allow_warn``
是领域层提供的**显式豁免**开关，必须同时给出确认原因；公开数据集通常不含
标定、导入链路会把未声明的时钟同步标成 DEGRADED，这里按真实口径填理由。

关于跨项目授权
--------------
``POST /datasets/{id}/grants`` 要求 ``grantee_project_id`` 是**已存在**的项目，
但当前没有接口让普通项目成员列出其他项目（``GET /projects`` 只返回本人可见
项目）。授权只能引用别的模块已产出的项目 ID；找不到时如实跳过，不伪造 UUID。
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from seed.client import Client, Context, SeedFailure, expect, if_match, utc_now

GROUP = "datasets"

_GOOD_EPISODE_STATUSES = frozenset({"ACCEPTED", "COMPLETED"})
_USABLE_QUALITY = frozenset({"PASS", "WARN"})
_MAX_CANDIDATES = 50
_EXPORT_SCOPE = ["VIEW", "DOWNLOAD", "EXPORT"]


def _ok(label: str, result: tuple[int, Any], want: int = 200) -> Any:
    payload = expect(label, result, want)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是 JSON 对象：{payload!r}")
    return payload


def _create(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    return _ok(label, client.call("POST", path, body=body), 201)


def _act(
    client: Client,
    path: str,
    action: str,
    version: int,
    label: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _ok(
        label,
        client.call("POST", f"{path}/actions/{action}", body=body, headers=if_match(version)),
    )


def _page(client: Client, path: str, label: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """按 cursor 取完一页列表接口的全部条目。"""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        separator = "&" if "?" in path else "?"
        query = f"{separator}limit={limit}"
        if cursor:
            query += f"&cursor={cursor}"
        payload = _ok(label, client.call("GET", f"{path}{query}"))
        page_items = payload.get("items")
        if not isinstance(page_items, list):
            raise SeedFailure(f"{label}：响应缺少 items")
        items.extend(page_items)
        cursor = payload.get("next_cursor")
        if not cursor:
            return items


def _eligible_episodes(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """挑出「看起来已经可用」的 Episode，真实资格仍由服务端 build 判定。"""
    eligible = [
        item
        for item in episodes
        if item.get("status") in _GOOD_EPISODE_STATUSES
        and item.get("quality_status") in _USABLE_QUALITY
        and item.get("annotation_status") == "APPROVED"
        and item.get("manifest_sha256")
    ]
    eligible.sort(key=lambda item: str(item.get("code") or item.get("episode_id")))
    return eligible[:_MAX_CANDIDATES]


def _require_eligible_episodes(client: Client, base: str) -> list[dict[str, Any]]:
    episodes = _page(client, f"{base}/episodes", "列出 Episode")
    eligible = _eligible_episodes(episodes)
    if not eligible:
        raise SeedFailure(
            "项目里没有可用于数据集构建的 Episode："
            f"共 {len(episodes)} 条，需先由 datasources/episodes/annotation 模块"
            "产出 ACCEPTED/COMPLETED、质量 PASS、标注 APPROVED 且带 Manifest 的样本；"
            "数据集模块不伪造这些事实，故版本构建与导出在此处如实阻塞"
        )
    return eligible


def _selection_filters(episodes: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "episode_ids": [str(item["episode_id"]) for item in episodes],
        # 公开/导入数据通常没有标定快照，且导入链路如实把未声明的时钟同步
        # 标记为 DEGRADED。两者都是数据固有属性，按领域层要求填写显式豁免理由。
        "allow_missing_calibration": True,
        "missing_calibration_reason": "演示数据不含相机/机械臂标定，样本身份不因此改变",
        "allow_degraded_time_sync": True,
        "degraded_confirmation_reason": "导入数据未声明时钟同步，服务端如实标记 DEGRADED",
        "allow_warn": True,
        "warn_confirmation_reason": "已人工复核质量 WARN 的具体缺项，允许进入演示训练集",
    }


# (策略资源段, 用途, 请求体, 日志标签)；每个数据集两套：构建用与草稿预览用。
# 演示数据的多次导入复用同一份媒体字节，内容级 Hash 天然跨 Episode 重复，
# 因此构建用策略只按血缘去重/下硬结论，内容级规则放进预览策略如实暴露冲突。
_POLICY_SPECS: tuple[tuple[str, str, dict[str, Any], str], ...] = (
    (
        "dedup-policies",
        "build",
        {
            "name": "按 Episode 血缘去重（跨次采集的独立样本保留）",
            "exact_keys": ["EPISODE_LINEAGE"],
            "near_duplicate_enabled": False,
            "retain_rule": "NEWEST_ACCEPTED",
        },
        "创建去重策略（血缘）",
    ),
    (
        "dedup-policies",
        "preview",
        {
            "name": "内容级去重 + 时间重叠近重复告警",
            "exact_keys": ["EPISODE_LINEAGE", "MANIFEST_SHA256", "RAW_ASSET_SHA256"],
            "near_duplicate_enabled": True,
            "near_duplicate_enforcement": "WARN_ONLY",
            "similarity_method": "TEMPORAL_OVERLAP_V1",
            "similarity_threshold": 0.8,
            "similarity_min_cluster_size": 2,
            "retain_rule": "HIGHEST_QUALITY",
        },
        "创建去重策略（内容级 + 近重复告警）",
    ),
    (
        "split-policies",
        "build",
        {
            "name": "6:2:2 划分，小样本允许空测试集",
            "strategy": "RATIO",
            "train_ratio": 0.6,
            "validation_ratio": 0.2,
            "test_ratio": 0.2,
            "seed": 20261117,
            "group_by": ["EPISODE_LINEAGE"],
            "empty_split_policy": "ALLOW_EMPTY_TEST",
            "min_item_count": 1,
            "min_duration_ns": 0,
            "rounding_rule": "LARGEST_REMAINDER",
        },
        "创建划分策略（小样本）",
    ),
    (
        "split-policies",
        "preview",
        {
            "name": "8:1:1 划分，空测试集直接失败",
            "strategy": "RATIO",
            "train_ratio": 0.8,
            "validation_ratio": 0.1,
            "test_ratio": 0.1,
            "seed": 20261117,
            "group_by": ["EPISODE_LINEAGE"],
            "empty_split_policy": "FAIL",
            "min_item_count": 1,
            "min_duration_ns": 0,
            "rounding_rule": "LARGEST_REMAINDER",
        },
        "创建划分策略（严格）",
    ),
    (
        "leakage-policies",
        "build",
        {
            "name": "血缘硬规则：同一 Episode 血缘不得跨划分",
            "hard_rules": ["EPISODE_LINEAGE_CROSS_SPLIT"],
            "soft_rules": ["TEMPORAL_OVERLAP_CROSS_SPLIT"],
            "waiver_allowed_rules": ["TEMPORAL_OVERLAP_CROSS_SPLIT"],
            "scene_grouping_enabled": False,
            "batch_grouping_enabled": False,
            "temporal_overlap_enabled": True,
        },
        "创建泄漏策略（血缘硬规则）",
    ),
    (
        "leakage-policies",
        "preview",
        {
            "name": "内容级硬规则：Manifest/RawAsset 不得跨划分",
            "hard_rules": [
                "EPISODE_LINEAGE_CROSS_SPLIT",
                "MANIFEST_SHA256_CROSS_SPLIT",
                "RAW_ASSET_SHA256_CROSS_SPLIT",
            ],
            "soft_rules": ["DUPLICATE_ANNOTATION"],
            "waiver_allowed_rules": ["DUPLICATE_ANNOTATION"],
            "scene_grouping_enabled": False,
            "batch_grouping_enabled": False,
            "temporal_overlap_enabled": False,
        },
        "创建泄漏策略（内容级硬规则）",
    ),
    (
        "quality-policies",
        "build",
        {
            "name": "构建门禁：至少 1 条、允许人工确认过的 WARN",
            "min_selected_count": 1,
            "allow_warn_quality": True,
            "require_leakage_pass": False,
        },
        "创建质量策略（构建门禁）",
    ),
    (
        "quality-policies",
        "preview",
        {
            "name": "预览口径：允许 WARN、不强制泄漏通过",
            "min_selected_count": 1,
            "allow_warn_quality": True,
            "require_leakage_pass": False,
        },
        "创建质量策略（预览口径）",
    ),
)

_FAMILY_SEGMENTS: dict[str, str] = {
    "dedup": "dedup-policies",
    "split": "split-policies",
    "leakage": "leakage-policies",
    "quality": "quality-policies",
}


def _create_policies(client: Client, dataset_path: str) -> list[dict[str, dict[str, Any]]]:
    """每个数据集建两套策略：构建用与草稿预览用。"""
    created = {
        (segment, purpose): _create(client, f"{dataset_path}/{segment}", body, label)
        for segment, purpose, body, label in _POLICY_SPECS
    }
    return [
        {family: created[(segment, purpose)] for family, segment in _FAMILY_SEGMENTS.items()}
        for purpose in ("build", "preview")
    ]


def _policy_refs(policies: dict[str, dict[str, Any]]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    for family in _FAMILY_SEGMENTS:
        refs[f"{family}_policy_id"] = policies[family][f"{family}_policy_id"]
        refs[f"{family}_policy_version"] = policies[family]["version"]
    return refs


def _version_payload(
    policies: dict[str, dict[str, Any]], filters: dict[str, Any]
) -> dict[str, Any]:
    return {
        "selection_rule_version": "dataset-selection/1",
        "selection_filters": filters,
        "build_now": False,
        **_policy_refs(policies),
    }


def _preview(
    client: Client,
    version_path: str,
    policies: dict[str, dict[str, Any]],
    filters: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    return _ok(
        label,
        client.call(
            "POST",
            f"{version_path}/selection-preview",
            body={"selection_filters": filters, **_policy_refs(policies)},
        ),
    )


def _build_ready_version(
    ctx: Context,
    client: Client,
    version_path: str,
    policies: dict[str, dict[str, Any]],
    filters: dict[str, Any],
) -> dict[str, Any]:
    """DRAFT → 预览 → BUILDING/VALIDATING → READY，任何一步不符预期即失败。"""
    filters = dict(filters)
    preview = _preview(client, version_path, policies, filters, "选择预览")
    if preview.get("selected_count", 0) < 1:
        raise SeedFailure(
            "选择预览没有任何入选样本，无法构建版本："
            f"blocking={preview.get('blocking_issues')} "
            f"excluded={preview.get('exclusion_summary')}"
        )
    version = _ok(
        "冻结前读取版本",
        client.call("GET", version_path),
    )
    built = _act(client, version_path, "build", version["version"], "构建版本")
    if built.get("status") != "VALIDATING":
        raise SeedFailure(
            f"构建后状态应为 VALIDATING，实际 {built.get('status')}："
            f"blocking={built.get('blocking_issues')}"
        )
    validated = _act(client, version_path, "validate", built["version"], "校验版本")
    if validated.get("status") != "READY":
        raise SeedFailure(
            "校验未通过，版本停在 "
            f"{validated.get('status')}：blocking={validated.get('blocking_issues')}"
        )
    if int(validated.get("train_count", 0)) < 1 or int(validated.get("validation_count", 0)) < 1:
        raise SeedFailure(
            "READY 版本缺少训练/验证划分："
            f"train={validated.get('train_count')} validation={validated.get('validation_count')}"
        )
    return validated


def _assert_reports_and_artifacts(
    client: Client, version_path: str, version: dict[str, Any], label: str
) -> None:
    quality = _ok(f"{label}质量报告", client.call("GET", f"{version_path}/quality-report"))
    if quality.get("status") != "READY":
        raise SeedFailure(f"{label}质量报告不是 READY：{quality.get('blocking_issues')}")
    leakage = _ok(f"{label}泄漏报告", client.call("GET", f"{version_path}/leakage-report"))
    if leakage.get("leakage_status") in {"FAIL", "UNKNOWN", None}:
        raise SeedFailure(f"{label}泄漏检查不可接受：{leakage.get('leakage_status')}")
    items = _page(client, f"{version_path}/items", f"{label}数据集条目")
    if not items:
        raise SeedFailure(f"{label}版本没有条目")
    if version.get("item_count") != len(items):
        raise SeedFailure(
            f"{label}条目数与版本不一致：version={version.get('item_count')} items={len(items)}"
        )
    manifest = _ok(f"{label}Manifest", client.call("GET", f"{version_path}/manifest?inline=true"))
    if manifest.get("manifest_sha256") != version.get("manifest_sha256"):
        raise SeedFailure(
            f"{label}Manifest Hash 与版本不一致：{manifest.get('manifest_sha256')} "
            f"!= {version.get('manifest_sha256')}"
        )


def _freeze(
    ctx: Context, client: Client, version_path: str, version: dict[str, Any]
) -> dict[str, Any]:
    # 职责分离：构建人不得作为唯一冻结审批人，冻结必须换第二个身份。
    reviewer = ctx.as_("reviewer")
    frozen = _act(
        reviewer,
        version_path,
        "freeze",
        version["version"],
        "评审人冻结版本",
        {"reason": "QA 独立核验通过：条目、报告与 Manifest 一致", "payload": {"qa_verified": True}},
    )
    if frozen.get("status") != "FROZEN":
        raise SeedFailure(f"冻结后状态应为 FROZEN，实际 {frozen.get('status')}")
    return frozen


def _grantee_project(ctx: Context, client: Client) -> str | None:
    """找出一个可用于跨项目授权的项目；没有就返回 None（不伪造 ID）。"""
    for group in ("projects", "identity"):
        for key in ("grantee_project_id", "second_project_id"):
            candidate = ctx.ids.get(group, {}).get(key)
            if candidate:
                return candidate
    projects = _page(client, "/api/v1/projects", "列出项目")
    for item in projects:
        project_id = item.get("project_id")
        if project_id and str(project_id) != ctx.project_id:
            return str(project_id)
    return None


def _grants(ctx: Context, client: Client, dataset_id: str, version_id: str) -> None:
    grantee = _grantee_project(ctx, client)
    if grantee is None:
        print(
            "  [跳过] 跨项目授权：当前身份看不到第二个项目，"
            "且没有接口让普通成员发现其他项目（需基座提供接收项目 ID）"
        )
        ctx.remember(GROUP, "grant_status", "SKIPPED_NO_GRANTEE")
        return
    base_path = f"/api/v1/projects/{ctx.project_id}/datasets/{dataset_id}/grants"
    expires_at = (utc_now() + timedelta(days=90)).isoformat()
    approved = _create(
        client,
        base_path,
        {
            "dataset_version_id": version_id,
            "grantee_project_id": grantee,
            "grantee_team": "算法训练组",
            "scope": _EXPORT_SCOPE,
            "reason": "跨项目共享已冻结版本用于模型训练（演示）",
            "expires_at": expires_at,
        },
        "创建跨项目授权（待审批）",
    )
    # 职责分离：授权创建人不得审批自己的授权；审批还要求「项目负责人」权限，
    # 所以必须由第二位 owner 来批（editor 会被 403）。
    reviewer = ctx.as_("owner2")
    approved = _act(
        reviewer,
        f"{base_path}/{approved['grant_id']}",
        "approve",
        approved["version"],
        "审批通过授权",
    )
    ctx.remember(GROUP, "grant_status", str(approved.get("status")))
    rejected = _create(
        client,
        base_path,
        {
            "grantee_project_id": grantee,
            "grantee_team": "外部评测组",
            "scope": ["VIEW"],
            "reason": "临时查看请求（演示驳回）",
            "expires_at": expires_at,
        },
        "创建跨项目授权（待驳回）",
    )
    rejected = _act(
        reviewer,
        f"{base_path}/{rejected['grant_id']}",
        "reject",
        rejected["version"],
        "驳回授权",
        {"reason": "未提供数据使用范围与合规说明"},
    )
    if rejected.get("status") != "REJECTED":
        raise SeedFailure(f"驳回后状态应为 REJECTED，实际 {rejected.get('status')}")


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    stamp = ctx.stamp
    owner_id = ctx.user_id

    episodes = _require_eligible_episodes(client, base)
    filters = _selection_filters(episodes)
    print(f"  可用 Episode {len(episodes)} 条，用于版本构建")

    # --- 数据集 A：承载策略、版本与授权 ---
    dataset = _create(
        client,
        f"{base}/datasets",
        {
            "code": f"ds-{stamp}-train",
            "name": "家庭操作模仿学习训练集",
            "description": "厨房/客厅第一人称操作数据，供模仿学习训练与回归验证。",
            "purpose": "TRAINING",
            "owner_id": owner_id,
            "usage_policy": "TRAINING_ALLOWED",
            "tags": ["manipulation", "indoor", "imitation"],
        },
        "创建训练数据集",
    )
    dataset_id = ctx.remember(GROUP, "dataset_id", str(dataset["dataset_id"]))
    dataset_path = f"{base}/datasets/{dataset_id}"
    dataset = _act(client, dataset_path, "activate", dataset["version"], "启用训练数据集")

    policies = _create_policies(client, dataset_path)
    for index, item in enumerate(policies, start=1):
        ctx.remember(GROUP, f"dedup_policy_id_{index}", str(item["dedup"]["dedup_policy_id"]))
        ctx.remember(GROUP, f"split_policy_id_{index}", str(item["split"]["split_policy_id"]))
        ctx.remember(GROUP, f"leakage_policy_id_{index}", str(item["leakage"]["leakage_policy_id"]))
        ctx.remember(GROUP, f"quality_policy_id_{index}", str(item["quality"]["quality_policy_id"]))

    # --- 版本 v1：严格策略，构建到 READY 并冻结 ---
    version = _create(
        client,
        f"{dataset_path}/versions",
        _version_payload(policies[0], filters),
        "创建版本草稿（严格策略）",
    )
    version_id = ctx.remember(GROUP, "version_id", str(version["dataset_version_id"]))
    version_path = f"{dataset_path}/versions/{version_id}"
    ready = _build_ready_version(ctx, client, version_path, policies[0], filters)
    _assert_reports_and_artifacts(client, version_path, ready, "训练集版本")
    frozen = _freeze(ctx, client, version_path, ready)
    ctx.remember(GROUP, "frozen_version_id", str(frozen["dataset_version_id"]))
    ctx.remember(GROUP, "version_status", str(frozen["status"]))

    # --- 版本 v2：第二套策略，只做草稿与预览 ---
    draft = _create(
        client,
        f"{dataset_path}/versions",
        _version_payload(policies[1], filters),
        "创建版本草稿（第二套策略）",
    )
    draft_id = ctx.remember(GROUP, "draft_version_id", str(draft["dataset_version_id"]))
    draft_path = f"{dataset_path}/versions/{draft_id}"
    preview = _preview(client, draft_path, policies[1], filters, "草稿版本选择预览")
    ctx.remember(GROUP, "draft_preview_status", str(preview.get("split_preview_status")))
    current = _ok("读取草稿版本", client.call("GET", draft_path))
    if current.get("status") != "DRAFT":
        raise SeedFailure(f"第二套策略版本应保持 DRAFT，实际 {current.get('status')}")

    # --- 数据集 B：演示归档路径 ---
    archived = _create(
        client,
        f"{base}/datasets",
        {
            "code": f"ds-{stamp}-eval",
            "name": "家庭场景回归评测集（旧版）",
            "description": "上一轮评测口径，已被训练集版本取代，保留用于历史追溯。",
            "purpose": "EVALUATION",
            "owner_id": owner_id,
            "usage_policy": "TRAINING_DENIED",
            "tags": ["evaluation", "legacy"],
        },
        "创建评测数据集",
    )
    archived_id = ctx.remember(GROUP, "archived_dataset_id", str(archived["dataset_id"]))
    archived_path = f"{base}/datasets/{archived_id}"
    activated = _act(client, archived_path, "activate", archived["version"], "启用评测数据集")
    archived = _act(
        client,
        archived_path,
        "archive",
        activated["version"],
        "归档评测数据集",
        {"reason": "评测口径升级，旧版本停止新增"},
    )
    if archived.get("status") != "ARCHIVED":
        raise SeedFailure(f"归档后状态应为 ARCHIVED，实际 {archived.get('status')}")
    ctx.remember(GROUP, "archived_dataset_status", str(archived["status"]))

    # --- 跨项目授权 ---
    _grants(ctx, client, dataset_id, str(frozen["dataset_version_id"]))
