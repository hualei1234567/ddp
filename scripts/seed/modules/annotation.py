"""标注模块：Schema / 样本集 / 任务 / 批次 / 分配 / 提交 / 复核 / 仲裁 /
黄金集 / 质检抽检 / 质量检查 / IAA / AI 建议。

覆盖的状态路径
--------------
- Schema：``DRAFT → PENDING_REVIEW → PUBLISHED``；另一条走完
  ``DRAFT → PENDING_REVIEW → DRAFT → PENDING_REVIEW → PUBLISHED →
  DEPRECATED → REVOKED``；
- 样本集：``DRAFT → PREVIEW → SNAPSHOTTED``，任务审批时级联 ``CONSUMED``；
  另建 ``DRAFT → PREVIEW → CANCELLED`` 与停在 ``PREVIEW`` 的一条；
- 标注任务：
  * 验收链 ``DRAFT → PENDING_REVIEW → APPROVED → READY → ASSIGNED →
    IN_PROGRESS →(pause/resume、block/unblock)→ SUBMITTED → REVIEWING →
    ACCEPTED → COMPLETED → ARCHIVED``；
  * 驳回链 ``DRAFT → … → SUBMITTED → REVIEWING → REJECTED → REWORK →
    IN_PROGRESS → SUBMITTED → REVIEWING → ACCEPTED → COMPLETED``；
  * 升级链 ``DRAFT → … → REVIEWING → ARBITRATION → ACCEPTED → COMPLETED``；
  * 取消链 ``DRAFT → PENDING_REVIEW → DRAFT → CANCELLED``；另一条
    ``DRAFT → PENDING_REVIEW → APPROVED → READY → CANCELLED``（批次取消链）；
- 批次：``PLANNED → READY → ASSIGNED → IN_PROGRESS →(pause/resume、
  block/unblock)→ SUBMITTED → REVIEWING → ACCEPTED``；另建
  ``PLANNED → CANCELLED``（批次取消链任务，该任务不再提交验收）；
- 分配：``ASSIGNED → CLAIMED → ASSIGNED（release）→ CLAIMED → IN_PROGRESS →
  SUBMITTED → COMPLETED``；另建 ``ASSIGNED → REVOKED``；驳回链上
  ``SUBMITTED → IN_PROGRESS``（返工级联）；
- 提交：``DRAFT → SUBMITTED → IN_REVIEW → APPROVED``；``DRAFT → WITHDRAWN
  →(创建新版本级联)→ SUPERSEDED``；``SUBMITTED → IN_REVIEW → REJECTED →
  REWORK_REQUIRED``；``SUBMITTED → IN_REVIEW → ESCALATED → ARBITRATING →
  RESOLVED_APPROVED``；
- 复核：``PENDING → IN_REVIEW → APPROVED / REJECTED / ESCALATED``；另建
  ``PENDING → CANCELLED``（同一提交的第二条复核：claim 会重复推进提交，
  见 ``reviews.py:233``）；
- 仲裁：``PENDING → IN_ARBITRATION →(vote)→ RESOLVED_APPROVED``。``recuse`` 走不通：
  退回 PENDING 后重新 claim 会再次对提交执行 ``arbitrate-start``，而提交已处于
  ``ARBITRATING``，直接 409（``arbitrations.py:222`` + ``state_rules.py:281``）；
- 黄金集：``DRAFT → PENDING_REVIEW → ACTIVE → RETIRED``；另一条
  ``DRAFT → REVOKED``；
- 质检抽检：``PLANNED → SAMPLED → ASSIGNED → IN_REVIEW →
  PASSED/FAILED/ESCALATED → CLOSED``；另建 ``PLANNED → CANCELLED``；
- 质量检查：提交时自动执行一次，再显式触发一次并读取历史与 findings；
- IAA：验收任务算出 ``SUCCEEDED``（两名标注者重叠标注 2 个 Episode），
  驳回任务因只有一名标注者如实返回 ``INSUFFICIENT_DATA``；
- AI 建议：登记 3 条 → 人工确认（采纳 1 / 修订 1 / 留待处理 1）→ 按模型版本
  聚合采纳率。

走不到的状态
------------
- 分配 ``EXPIRED``：只由 Worker 入口 ``expire_due()`` 推进
  （``backend/app/application/annotation/assignments.py:318``），HTTP 没有系统
  触发点；本脚本不伪造系统身份。
- IAA 的 ``QUEUED/RUNNING/FAILED`` 执行态：用例在一次同步调用内落终态
  （``backend/app/application/annotation/iaa.py:141-176``），客户端看不到中间态。
- 提交侧的 ``system-review-start / approve / reject / escalate / resolve-* /
  system-supersede``：提交资源只开放 ``submit / withdraw / require-rework``
  （``backend/app/api/v1/annotation_results.py:50``），裁决一律走复核或仲裁
  资源；``system-supersede`` 只由「创建新版本」级联触发。
- 批次的 ``escalate / resolve-accepted / resolve-rejected``：业务上没有"批次级
  升级"入口，升级只在任务维度发生（本脚本覆盖任务级 ARBITRATION /
  resolve-accepted）。
- 已取消批次会让任务再也无法 ``submit``：数据库守卫把 ``CANCELLED`` 当成
  "未进入验收的批次"（``annotation_work_guards.py:111-121``），而应用层把
  ``CANCELLED`` 当终态（``repositories/annotation_batches.py:28``），删除批次又被
  触发器禁止。本脚本因此把"取消批次"放在不再提交验收的任务上。

前置数据
--------
样本集只接受 ``ACCEPTED/COMPLETED``、质量 ``PASS/WARN`` 且 manifest 已物化
的 Episode（``backend/app/domain/annotation/selection.py:23-24,145``）。本模块
依赖同项目的 Episode，没有可用 Episode 时直接失败并说明缺口，不造 Mock。

另外两个身份
------------
- 标注员：每轮新加入一个 editor。分配容量按人累计
  （``annotation_max_open_items`` 默认 50，``core/config.py:121``），复用同一个人
  多轮运行必然撞上"分配超过人员容量"。
- 仲裁人：必须独立于标注人与首轮复核人
  （``backend/app/domain/annotation/arbitration.py:170``）。

平台没有用户注册入口，因此按 ``seed/identity.py`` 的手法准备身份：只读查询一个
尚未加入项目的用户，经 ``POST /projects/{pid}/members`` 加入并本地签发令牌；
仅当库里没有可复用用户时才插入一条 users 记录（与 identity.py 一致）。
"""

from __future__ import annotations

import datetime
import time
import urllib.parse
import uuid
from dataclasses import dataclass, field
from typing import Any

import jwt
import psycopg

from seed.client import REPO_ROOT, Client, Context, SeedFailure, expect, if_match

GROUP = "annotation"
# 与 domain/annotation/selection.py 的标注输入门禁保持一致。
_ELIGIBLE_STATUSES = ("ACCEPTED", "COMPLETED")
_ELIGIBLE_QUALITY = frozenset({"PASS", "WARN"})
_DEFAULT_RULES = frozenset(
    {
        "required_labels_present",
        "time_range_within_episode",
        "ai_label_confidence_low",
        "duplicate_label_keys",
    }
)
# 复核「通过」要求非空且全部通过的检查项（domain/annotation/review.py:263）。
_APPROVE_CHECKLIST = [
    {"key": "labels_complete", "passed": True},
    {"key": "boundary_checked", "passed": True},
]
_LABELS: list[dict[str, Any]] = [
    {
        "label_key": "episode_outcome",
        "label_type": "ENUM",
        "required": True,
        "level": "EPISODE",
        "visibility": "ANNOTATOR",
        "description": "该 Episode 内任务是否成功完成",
        "value_spec": {"enum_values": ["success", "failure"]},
        "quality_rules": ["required_labels_present"],
    },
    {
        "label_key": "action_segment",
        "label_type": "ENUM",
        "required": True,
        "level": "TIME_RANGE",
        "visibility": "ANNOTATOR",
        "description": "该片段的主要操作类型",
        "value_spec": {"enum_values": ["reach", "grasp", "lift", "place", "release"]},
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
    {
        "label_key": "scene_notes",
        "label_type": "FREE_TEXT",
        "required": False,
        "level": "EPISODE",
        "visibility": "ANNOTATOR",
        "description": "现场备注",
        "value_spec": {"max_length": 500},
        "quality_rules": [],
    },
]


# --------------------------------------------------------------------------
# HTTP 基础操作
# --------------------------------------------------------------------------
_MAX_ATTEMPTS = 8


def _call(
    client: Client,
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, Any]:
    """带退避重试的 HTTP 调用。

    两层保护，都不吞错：
    - 连接级瞬时错误（本地栈重启、连接被重置）按指数退避重试；
    - 写限流 120 次/60 秒、限流键是客户端 IP（``core/config.py:238``），
      ``Client`` 已按 Retry-After 重试，这里再做一层兜底，重试耗尽即失败。
    """
    delay = 0.5
    last_error: str = ""
    for _ in range(_MAX_ATTEMPTS):
        try:
            status, payload = client.call(method, path, body=body, headers=headers)
        except OSError as exc:  # URLError / RemoteDisconnected 均属 OSError
            last_error = f"{type(exc).__name__}: {exc}"
            time.sleep(min(delay, 30.0))
            delay *= 2
            continue
        if status != 429:
            return status, payload
        last_error = "HTTP 429"
        time.sleep(min(delay, 30.0))
        delay *= 2
    raise SeedFailure(f"请求持续失败：{method} {path}（最后一次：{last_error}）")


def _expect(
    label: str,
    client: Client,
    method: str,
    path: str,
    want: int | tuple[int, ...],
    *,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    return expect(label, _call(client, method, path, body=body, headers=headers), want)


def _create(client: Client, path: str, body: dict[str, Any], label: str = "") -> dict[str, Any]:
    payload = _expect(label or path, client, "POST", path, 201, body=body)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label or path}：期望对象响应，实际 {type(payload).__name__}")
    return payload


def _create_many(client: Client, path: str, body: dict[str, Any], label: str) -> list[Any]:
    payload = _expect(label, client, "POST", path, 201, body=body)
    if not isinstance(payload, list):
        raise SeedFailure(f"{label}：期望数组响应，实际 {type(payload).__name__}")
    return payload


def _get(client: Client, path: str, label: str = "") -> dict[str, Any]:
    payload = _expect(label or path, client, "GET", path, 200)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label or path}：期望对象响应，实际 {type(payload).__name__}")
    return payload


def _act(
    client: Client,
    path: str,
    action: str,
    version: int,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _expect(
        action,
        client,
        "POST",
        f"{path}/actions/{action}",
        200,
        body=body,
        headers=if_match(version),
    )
    if not isinstance(payload, dict):
        raise SeedFailure(f"{action}：期望对象响应，实际 {type(payload).__name__}")
    return payload


def _status(entity: dict[str, Any], want: str, label: str = "") -> None:
    actual = entity.get("status")
    if actual != want:
        raise SeedFailure(f"{label or '状态校验'}：期望 {want}，实际 {actual}")


def _step(
    client: Client,
    path: str,
    action: str,
    current: dict[str, Any],
    want: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行状态动作：断言 200 与目标状态，返回带新 version 的实体。"""
    result = _act(client, path, action, int(current["version"]), body)
    _status(result, want, action)
    return result


def _due(days: int) -> str:
    moment = datetime.datetime.now(datetime.UTC) + datetime.timedelta(days=days)
    return moment.replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# 第三个身份（仲裁人）
# --------------------------------------------------------------------------
def _load_env() -> dict[str, str]:
    path = REPO_ROOT / ".env"
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _sign(env: dict[str, str], user_id: str) -> str:
    now = datetime.datetime.now(datetime.UTC)
    token = jwt.encode(
        {
            "sub": user_id,
            "iss": env["DDP_JWT_ISSUER"],
            "aud": env["DDP_JWT_AUDIENCE"],
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(days=1)).timestamp()),
        },
        env["DDP_JWT_SECRET"],
        algorithm="HS256",
    )
    return str(token)


def _ensure_identity(ctx: Context, client: Client, role: str, label: str) -> str:
    """准备一个 editor 身份并签发令牌，返回 user_id。

    为什么每轮都用新身份做标注员：分配容量按人累计（``annotation_max_open_items``
    默认 50，``core/config.py:121``），复用同一个人的话，重复运行几轮就会撞上
    "分配超过人员容量"。仲裁人则必须独立于标注人与首轮复核人
    （``domain/annotation/arbitration.py:170``）。
    """
    env = _load_env()
    missing = [
        key
        for key in ("DDP_DATABASE_URL", "DDP_JWT_SECRET", "DDP_JWT_ISSUER", "DDP_JWT_AUDIENCE")
        if not env.get(key)
    ]
    if missing:
        raise SeedFailure(f".env 缺少 {missing}，无法准备{label}身份")
    user_id = _pick_member_candidate(ctx, env, f"演示{label}")
    _expect(
        f"把{label}加入项目",
        client,
        "POST",
        f"/api/v1/projects/{ctx.project_id}/members",
        (200, 201),
        body={"user_id": user_id, "role": "editor"},
    )
    ctx.remember("identity", role, user_id)
    ctx.tokens[role] = _sign(env, user_id)
    return user_id


def _pick_member_candidate(ctx: Context, env: dict[str, str], display_name: str) -> str:
    dsn = env["DDP_DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(dsn, connect_timeout=10) as conn:
        row = conn.execute(
            """
            select u.id from users u
            where not exists (
                select 1 from project_members pm
                where pm.project_id = %s and pm.user_id = u.id
            )
            order by u.created_at
            limit 1
            """,
            (ctx.project_id,),
        ).fetchone()
        if row is not None:
            return str(row[0])
        user_id = str(uuid.uuid4())
        conn.execute(
            "insert into users (id, display_name) values (%s, %s)",
            (user_id, display_name),
        )
        conn.commit()
        return user_id


def _owner_client(ctx: Context, client: Client) -> Client:
    """项目负责人的客户端：黄金集答案视图只对 OWNER 开放。

    ``run.py`` 的主身份取成员列表第一条，可能是 editor；答案视图的授权规则是
    "仅项目 OWNER"（``infrastructure/repositories/annotation_directory.py``），
    因此这里按角色取真实负责人，不改变主身份，也不放宽断言。
    """
    user_id = _member_with_role(ctx, client, "owner")
    if user_id == ctx.user_id:
        return client
    return Client(ctx.base_url, _sign(_load_env(), user_id))


def _member_with_role(ctx: Context, client: Client, role: str) -> str:
    cursor: str | None = None
    while True:
        query = f"/api/v1/projects/{ctx.project_id}/members?limit=100"
        if cursor:
            query += f"&cursor={urllib.parse.quote(cursor)}"
        page = _get(client, query)
        for item in page.get("items") or []:
            if item.get("role") == role:
                return str(item["user_id"])
        cursor = page.get("next_cursor")
        if not cursor:
            raise SeedFailure(f"项目里没有角色为 {role} 的成员")


def _member_role(ctx: Context, client: Client, user_id: str) -> str | None:
    cursor: str | None = None
    while True:
        query = f"/api/v1/projects/{ctx.project_id}/members?limit=100"
        if cursor:
            query += f"&cursor={urllib.parse.quote(cursor)}"
        page = _get(client, query)
        for item in page.get("items") or []:
            if str(item["user_id"]) == user_id:
                return str(item.get("role"))
        cursor = page.get("next_cursor")
        if not cursor:
            return None


# --------------------------------------------------------------------------
# 共享引用
# --------------------------------------------------------------------------
@dataclass
class _Refs:
    ctx: Context
    client: Client
    base: str
    reviewer: Client
    reviewer_id: str
    annotator: Client
    annotator_id: str
    arbiter: Client
    arbiter_id: str
    schema_id: str
    episodes: list[dict[str, Any]] = field(default_factory=list)

    @property
    def stamp(self) -> str:
        return self.ctx.stamp

    def episode_ids(self, count: int) -> list[str]:
        return [str(item["episode_id"]) for item in self.episodes[:count]]


# --------------------------------------------------------------------------
# Schema
# --------------------------------------------------------------------------
def _schemas(ref: _Refs) -> None:
    client, base, stamp = ref.client, ref.base, ref.ctx.stamp
    schema = _create(
        client,
        f"{base}/annotation-schemas",
        {
            "code": f"ann-schema-{stamp}",
            "name": "Episode 片段级操作标注 Schema",
            "description": "家庭抓取任务：Episode 级成功判定 + 片段级动作类型 + 质量分。",
            "annotation_levels": ["EPISODE", "TIME_RANGE"],
            "labels": _LABELS,
            "compatibility": "BACKWARD_COMPATIBLE",
            "schema_version": 1,
        },
        "创建标注 Schema",
    )
    ref.schema_id = ref.ctx.remember(GROUP, "schema_id", str(schema["schema_id"]))
    path = f"{base}/annotation-schemas/{ref.schema_id}"
    schema = _step(client, path, "submit-review", schema, "PENDING_REVIEW")
    schema = _step(ref.reviewer, path, "publish", schema, "PUBLISHED")
    if not schema.get("published_at"):
        raise SeedFailure("Schema 发布后缺少 published_at")

    legacy = _create(
        client,
        f"{base}/annotation-schemas",
        {
            "code": f"ann-schema-{stamp}-legacy",
            "name": "旧版整段标注 Schema（已撤销）",
            "description": "只做整段成功/失败标注，粒度不足，保留用于历史追溯。",
            "annotation_levels": ["EPISODE"],
            "labels": [_LABELS[0]],
            "compatibility": "BREAKING",
            "schema_version": 1,
        },
        "创建待撤销 Schema",
    )
    legacy_id = ref.ctx.remember(GROUP, "schema_legacy", legacy["schema_id"])
    lpath = f"{base}/annotation-schemas/{legacy_id}"
    legacy = _step(client, lpath, "submit-review", legacy, "PENDING_REVIEW")
    legacy = _step(
        ref.reviewer,
        lpath,
        "return-draft",
        legacy,
        "DRAFT",
        {"reason": "缺少片段级动作标签，补充后再提交"},
    )
    legacy = _step(client, lpath, "submit-review", legacy, "PENDING_REVIEW")
    legacy = _step(ref.reviewer, lpath, "publish", legacy, "PUBLISHED")
    legacy = _step(
        client,
        lpath,
        "deprecate",
        legacy,
        "DEPRECATED",
        {"reason": "已被片段级 Schema 取代"},
    )
    _step(client, lpath, "revoke", legacy, "REVOKED", {"reason": "整段标注结论不可用于训练"})


# --------------------------------------------------------------------------
# Episode 前置数据
# --------------------------------------------------------------------------
def _eligible_episodes(client: Client, base: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for status in _ELIGIBLE_STATUSES:
        page = _get(client, f"{base}/episodes?status={status}&limit=100")
        for item in page.get("items") or []:
            if item.get("quality_status") in _ELIGIBLE_QUALITY:
                found[str(item["episode_id"])] = item
    eligible = list(found.values())
    ready = [item for item in eligible if item.get("manifest_sha256")]
    if len(ready) < 3:
        raise SeedFailure(
            f"标注输入不足：项目内可用 Episode {len(ready)} 个（至少需要 3 个，"
            f"另有 {len(eligible) - len(ready)} 个 manifest 未物化）。样本集只接受 "
            "ACCEPTED/COMPLETED 且质量 PASS/WARN 且 manifest 已物化的 Episode"
            "（domain/annotation/selection.py:23-24,145）；请先在同一个项目里跑通 "
            "episodes 模块，或换一个有可用 Episode 的项目。"
        )
    return ready[:3]


# --------------------------------------------------------------------------
# 样本集 / 任务 / 批次 / 分配 / 提交 / 复核 构造
# --------------------------------------------------------------------------
def _sample_set(
    client: Client,
    base: str,
    *,
    code: str,
    name: str,
    episode_ids: list[str],
    stop: str = "snapshot",
) -> dict[str, Any]:
    """创建样本集并推进到 ``stop``：``preview`` / ``cancel`` / ``snapshot``。"""
    sample_set = _create(
        client,
        f"{base}/annotation-sample-sets",
        {
            "code": code,
            "name": name,
            "strategy": "FILTER",
            "selection_rule_version": "stage3-sample-selection/1",
            "filters": {"include_episode_ids": episode_ids},
            "dedup_key": "EPISODE_LINEAGE",
            "data_source": "REAL",
            "warn_confirmed": True,
        },
        f"创建样本集 {code}",
    )
    path = f"{base}/annotation-sample-sets/{sample_set['sample_set_id']}"
    sample_set = _step(client, path, "preview", sample_set, "PREVIEW", {"warn_confirmed": True})
    if int(sample_set.get("selected_count", 0)) < len(episode_ids):
        raise SeedFailure(
            f"样本集 {code} 入选 {sample_set.get('selected_count')} 个 / 期望 "
            f"{len(episode_ids)} 个，排除明细：{sample_set.get('exclusion_summary')}"
        )
    if stop == "preview":
        return sample_set
    if stop == "cancel":
        return _step(
            client,
            path,
            "cancel",
            sample_set,
            "CANCELLED",
            {"reason": "筛选口径与最新场景划分不一致，重新抽样"},
        )
    sample_set = _step(client, path, "snapshot", sample_set, "SNAPSHOTTED")
    if not sample_set.get("snapshot_sha256"):
        raise SeedFailure(f"样本集 {code} 快照缺少 snapshot_sha256")
    return sample_set


def _task(
    client: Client,
    base: str,
    *,
    code: str,
    name: str,
    sample_set_id: str,
    schema_id: str,
    episodes: int,
    items: int,
    priority: str = "P1",
) -> dict[str, Any]:
    return _create(
        client,
        f"{base}/annotation-tasks",
        {
            "code": code,
            "name": name,
            "description": "演示数据：覆盖标注任务全生命周期。",
            "sample_set_id": sample_set_id,
            "schema_id": schema_id,
            "target_episode_count": episodes,
            "target_item_count": items,
            "due_at": _due(7),
            "priority": priority,
            "assignment_mode": "MIXED",
            "iaa_policy": {"metric_definitions_version": "ddp.stage3.iaa/1"},
            "quality_policy": {"max_rework_rounds": 2, "ai_confidence_threshold": 0.7},
        },
        f"创建标注任务 {code}",
    )


def _batch(
    client: Client,
    base: str,
    *,
    task_id: str,
    code: str,
    sequence_no: int,
    planned: int,
    item_refs: list[str],
    name: str,
) -> dict[str, Any]:
    return _create(
        client,
        f"{base}/annotation-tasks/{task_id}/batches",
        {
            "code": code,
            "sequence_no": sequence_no,
            "name": name,
            "planned_item_count": planned,
            "item_refs": item_refs,
            "due_at": _due(7),
        },
        f"创建标注批次 {code}",
    )


def _assignment(
    client: Client,
    base: str,
    *,
    batch_id: str,
    assignee_id: str,
    planned: int,
    minutes: int,
    label: str,
) -> dict[str, Any]:
    return _create(
        client,
        f"{base}/annotation-assignments",
        {
            "batch_id": batch_id,
            "assignee_id": assignee_id,
            "assignment_role": "ANNOTATOR",
            "planned_item_count": planned,
            "planned_minutes": minutes,
            "lease_expires_at": _due(1),
        },
        label,
    )


def _record(episode: dict[str, Any], note: str) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "episode_id": str(episode["episode_id"]),
        "episode_version": int(episode["episode_version"]),
        "labels": [
            {"label_key": "episode_outcome", "value": "success"},
            {"label_key": "action_segment", "value": "grasp"},
            {"label_key": "quality_score", "value": 0.86},
            {"label_key": "scene_notes", "value": note},
        ],
    }
    for label in payload["labels"]:
        label["source"] = "HUMAN"
        label["label_status"] = "CONFIRMED"
    start, end = int(episode["start_time_ns"]), int(episode["end_time_ns"])
    if end > start:
        payload["time_range"] = {"start_time_ns": start, "end_time_ns": end, "time_domain": "UTC"}
    return payload


def _submission(
    client: Client,
    base: str,
    *,
    assignment_id: str,
    episodes: list[dict[str, Any]],
    note: str,
    label: str,
) -> dict[str, Any]:
    return _create(
        client,
        f"{base}/annotation-submissions",
        {
            "assignment_id": assignment_id,
            "records": [_record(item, note) for item in episodes],
            "data_source": "REAL",
        },
        label,
    )


def _review(
    client: Client,
    base: str,
    *,
    submission_id: str,
    reviewer_id: str,
    label: str,
) -> dict[str, Any]:
    return _create(
        client,
        f"{base}/annotation-submissions/{submission_id}/reviews",
        {"reviewer_id": reviewer_id},
        label,
    )


def _approve_to_ready(ref: _Refs, task: dict[str, Any], task_path: str) -> dict[str, Any]:
    """任务：PENDING_REVIEW → APPROVED（换身份）→ READY。"""
    task = _step(ref.client, task_path, "submit-review", task, "PENDING_REVIEW")
    task = _step(ref.reviewer, task_path, "approve", task, "APPROVED")
    return _step(ref.client, task_path, "ready", task, "READY")


# --------------------------------------------------------------------------
# 验收链（任务 1）
# --------------------------------------------------------------------------
def _accept_open(ref: _Refs, sample_set: dict[str, Any]) -> None:
    """任务 → APPROVED → READY → 批次/分配到齐 → IN_PROGRESS，并做暂停/阻塞往返。"""
    client, base, stamp = ref.client, ref.base, ref.stamp
    task = _task(
        client,
        base,
        code=f"ann-task-{stamp}-accept",
        name="家庭抓取标注（验收链）",
        sample_set_id=str(sample_set["sample_set_id"]),
        schema_id=ref.schema_id,
        episodes=3,
        items=3,
        priority="P0",
    )
    task_id = ref.ctx.remember(GROUP, "task_accept", task["task_id"])
    task_path = f"{base}/annotation-tasks/{task_id}"
    task = _step(client, task_path, "submit-review", task, "PENDING_REVIEW")
    task = _step(ref.reviewer, task_path, "approve", task, "APPROVED")
    consumed = _get(client, f"{base}/annotation-sample-sets/{sample_set['sample_set_id']}")
    _status(consumed, "CONSUMED", "任务审批后样本集状态")
    task = _step(client, task_path, "ready", task, "READY")

    batch = _batch(
        client,
        base,
        task_id=task_id,
        code=f"ann-batch-{stamp}-a1",
        sequence_no=1,
        planned=3,
        item_refs=ref.episode_ids(3),
        name="主批次",
    )
    batch_id = ref.ctx.remember(GROUP, "batch_accept", batch["batch_id"])
    batch_path = f"{base}/annotation-batches/{batch_id}"
    batch = _step(client, batch_path, "ready", batch, "READY")
    batch = _step(client, batch_path, "assign", batch, "ASSIGNED")

    first = _assignment(
        client,
        base,
        batch_id=batch_id,
        assignee_id=ref.annotator_id,
        planned=3,
        minutes=90,
        label="分配主标注员",
    )
    first_id = ref.ctx.remember(GROUP, "assignment_accept_first", first["assignment_id"])
    first_path = f"{base}/annotation-assignments/{first_id}"
    second = _assignment(
        client,
        base,
        batch_id=batch_id,
        assignee_id=ref.reviewer_id,
        planned=3,
        minutes=90,
        label="分配第二标注员",
    )
    second_id = ref.ctx.remember(GROUP, "assignment_accept_second", second["assignment_id"])
    second_path = f"{base}/annotation-assignments/{second_id}"

    # 领取前任务必须已 ASSIGNED（数据库守卫 annotation_work_guards.py:72-84）。
    task = _step(client, task_path, "assign", task, "ASSIGNED")
    first = _step(ref.annotator, first_path, "claim", first, "CLAIMED")
    second = _step(ref.reviewer, second_path, "claim", second, "CLAIMED")
    second = _step(
        ref.reviewer,
        second_path,
        "release",
        second,
        "ASSIGNED",
        {"reason": "先处理验收侧工作"},
    )
    second = _step(ref.reviewer, second_path, "claim", second, "CLAIMED")
    task = _step(client, task_path, "start", task, "IN_PROGRESS")
    # 分配的 IN_PROGRESS 要求任务与批次都已 IN_PROGRESS，批次必须先开始。
    batch = _step(client, batch_path, "start", batch, "IN_PROGRESS")
    first = _step(ref.annotator, first_path, "start", first, "IN_PROGRESS")
    second = _step(ref.reviewer, second_path, "start", second, "IN_PROGRESS")
    task = _step(client, task_path, "pause", task, "PAUSED", {"reason": "等待相机标定复核"})
    task = _step(client, task_path, "resume", task, "IN_PROGRESS", {"reason": "标定复核通过"})
    task = _step(client, task_path, "block", task, "BLOCKED", {"reason": "标注工具快捷键冲突"})
    task = _step(client, task_path, "unblock", task, "IN_PROGRESS", {"reason": "工具已修复"})
    batch = _step(client, batch_path, "pause", batch, "PAUSED", {"reason": "与任务同步暂停"})
    batch = _step(client, batch_path, "resume", batch, "IN_PROGRESS", {"reason": "任务已恢复"})
    batch = _step(client, batch_path, "block", batch, "BLOCKED", {"reason": "审看节点排满"})
    _step(client, batch_path, "unblock", batch, "IN_PROGRESS", {"reason": "审看节点空闲"})


def _accept_results(ref: _Refs) -> None:
    """两条提交（含撤回与新版本）+ 各自复核通过 + 质量检查 / IAA 输入。"""
    client, base = ref.client, ref.base
    task_id = ref.ctx.recall(GROUP, "task_accept")
    batch_path = f"{base}/annotation-batches/{ref.ctx.recall(GROUP, 'batch_accept')}"
    first_id = ref.ctx.recall(GROUP, "assignment_accept_first")
    second_id = ref.ctx.recall(GROUP, "assignment_accept_second")
    first_path = f"{base}/annotation-assignments/{first_id}"
    second_path = f"{base}/annotation-assignments/{second_id}"
    episodes = ref.episodes[:2]

    main_sub = _submission(
        ref.annotator,
        base,
        assignment_id=ref.ctx.recall(GROUP, "assignment_accept_first"),
        episodes=episodes,
        note="主标注员首批",
        label="主标注员创建提交",
    )
    main_id = ref.ctx.remember(GROUP, "submission_accept_main", main_sub["submission_id"])
    main_path = f"{base}/annotation-submissions/{main_id}"
    _step(ref.annotator, main_path, "submit", main_sub, "SUBMITTED")
    review = _review(
        ref.reviewer,
        base,
        submission_id=main_id,
        reviewer_id=ref.reviewer_id,
        label="为主标注员提交创建复核",
    )
    review_path = f"{base}/annotation-reviews/{review['review_id']}"
    review = _step(ref.reviewer, review_path, "claim", review, "IN_REVIEW")
    _status(_get(client, main_path), "IN_REVIEW", "复核领取后提交状态")
    review = _step(
        ref.reviewer,
        review_path,
        "approve",
        review,
        "APPROVED",
        {"review_duration_sec": 420, "checklist": _APPROVE_CHECKLIST},
    )
    _status(_get(client, main_path), "APPROVED", "复核通过后提交状态")

    draft = _submission(
        ref.reviewer,
        base,
        assignment_id=ref.ctx.recall(GROUP, "assignment_accept_second"),
        episodes=episodes[:1],
        note="第二标注员试标",
        label="第二标注员创建试标草稿",
    )
    draft_path = f"{base}/annotation-submissions/{draft['submission_id']}"
    withdrawn = _step(
        ref.reviewer,
        draft_path,
        "withdraw",
        draft,
        "WITHDRAWN",
        {"reason": "试标内容不完整，重新提交"},
    )
    second_sub = _submission(
        ref.reviewer,
        base,
        assignment_id=ref.ctx.recall(GROUP, "assignment_accept_second"),
        episodes=episodes,
        note="第二标注员正式提交",
        label="第二标注员创建正式提交",
    )
    second_id = ref.ctx.remember(GROUP, "submission_accept_second", second_sub["submission_id"])
    if second_sub.get("supersedes_id") != withdrawn["submission_id"]:
        raise SeedFailure("新版本提交未取代已撤回的旧版本")
    _step(
        ref.reviewer,
        f"{base}/annotation-submissions/{second_id}",
        "submit",
        second_sub,
        "SUBMITTED",
    )
    review2 = _review(
        client,
        base,
        submission_id=second_id,
        reviewer_id=ref.ctx.user_id,
        label="为第二标注员提交创建复核",
    )
    review2_path = f"{base}/annotation-reviews/{review2['review_id']}"
    review2 = _step(client, review2_path, "claim", review2, "IN_REVIEW")
    _step(
        client,
        review2_path,
        "approve",
        review2,
        "APPROVED",
        {"checklist": _APPROVE_CHECKLIST},
    )

    task = _get(client, f"{base}/annotation-tasks/{task_id}")
    batch = _get(client, batch_path)
    batch = _step(client, batch_path, "submit", batch, "SUBMITTED")
    task = _step(client, f"{base}/annotation-tasks/{task_id}", "submit", task, "SUBMITTED")
    task_path = f"{base}/annotation-tasks/{task_id}"
    _step(client, task_path, "system-review", task, "REVIEWING")
    batch = _step(client, batch_path, "system-review", batch, "REVIEWING")
    _step(client, batch_path, "accept", batch, "ACCEPTED")

    task = _get(client, task_path)
    _step(client, task_path, "accept", task, "ACCEPTED")
    # 提交动作已把分配推进到 SUBMITTED（version 变化），完成前必须重读。
    first = _get(client, first_path)
    _step(
        ref.annotator,
        first_path,
        "complete",
        first,
        "COMPLETED",
        {"actual_minutes": 75, "approved_item_count": 2},
    )
    second = _get(client, second_path)
    _step(
        ref.reviewer,
        second_path,
        "complete",
        second,
        "COMPLETED",
        {"actual_minutes": 65, "approved_item_count": 2},
    )
    task = _get(client, task_path)
    # progress.approved_items 目前没有任何写入点（tasks.py:477 只读），
    # 因此 COMPLETE 只能按契约显式豁免，不伪造验收数量。
    task = _step(
        client,
        task_path,
        "complete",
        task,
        "COMPLETED",
        {"waiver_reason": "演示数据：task.progress.approved_items 无写入点，按契约显式豁免"},
    )
    _step(client, task_path, "archive", task, "ARCHIVED")


# --------------------------------------------------------------------------
# 驳回 / 返工链（任务 2）
# --------------------------------------------------------------------------
def _rework_chain(ref: _Refs, sample_set: dict[str, Any]) -> None:
    client, base, stamp = ref.client, ref.base, ref.stamp
    task = _task(
        client,
        base,
        code=f"ann-task-{stamp}-rework",
        name="家庭抓取标注（驳回返工链）",
        sample_set_id=str(sample_set["sample_set_id"]),
        schema_id=ref.schema_id,
        episodes=2,
        items=2,
    )
    task_id = ref.ctx.remember(GROUP, "task_rework", task["task_id"])
    task_path = f"{base}/annotation-tasks/{task_id}"
    task = _approve_to_ready(ref, task, task_path)

    batch = _batch(
        client,
        base,
        task_id=task_id,
        code=f"ann-batch-{stamp}-r1",
        sequence_no=1,
        planned=2,
        item_refs=ref.episode_ids(2),
        name="返工批次",
    )
    batch_path = f"{base}/annotation-batches/{batch['batch_id']}"
    batch = _step(client, batch_path, "ready", batch, "READY")
    batch = _step(client, batch_path, "assign", batch, "ASSIGNED")
    assignment = _assignment(
        client,
        base,
        batch_id=str(batch["batch_id"]),
        assignee_id=ref.annotator_id,
        planned=2,
        minutes=60,
        label="返工任务分配标注员",
    )
    assignment_path = f"{base}/annotation-assignments/{assignment['assignment_id']}"
    task = _step(client, task_path, "assign", task, "ASSIGNED")
    assignment = _step(ref.annotator, assignment_path, "claim", assignment, "CLAIMED")
    task = _step(client, task_path, "start", task, "IN_PROGRESS")
    batch = _step(client, batch_path, "start", batch, "IN_PROGRESS")
    assignment = _step(ref.annotator, assignment_path, "start", assignment, "IN_PROGRESS")

    first = _submission(
        ref.annotator,
        base,
        assignment_id=str(assignment["assignment_id"]),
        episodes=ref.episodes[:2],
        note="第一版（被驳回）",
        label="返工标注员创建第一版提交",
    )
    first_path = f"{base}/annotation-submissions/{first['submission_id']}"
    _step(ref.annotator, first_path, "submit", first, "SUBMITTED")
    batch = _step(client, batch_path, "submit", batch, "SUBMITTED")
    task = _step(client, task_path, "submit", task, "SUBMITTED")
    task = _step(client, task_path, "system-review", task, "REVIEWING")
    # 批次先进入 REVIEWING：返工级联会把任务先推到 REWORK，若批次仍停在
    # SUBMITTED，批次 SUBMITTED→REVIEWING 会被数据库守卫拒绝。
    batch = _step(client, batch_path, "system-review", batch, "REVIEWING")

    review = _review(
        ref.reviewer,
        base,
        submission_id=str(first["submission_id"]),
        reviewer_id=ref.reviewer_id,
        label="为第一版提交创建复核",
    )
    review_path = f"{base}/annotation-reviews/{review['review_id']}"
    review = _step(ref.reviewer, review_path, "claim", review, "IN_REVIEW")
    _step(
        ref.reviewer,
        review_path,
        "reject",
        review,
        "REJECTED",
        {
            "reason": "action_segment 与片段边界不一致，需重标",
            "issues": [
                {
                    "code": "action_segment_boundary",
                    "severity": "BLOCKER",
                    "message": "片段边界与动作类型不一致",
                    "label_key": "action_segment",
                }
            ],
        },
    )
    _status(_get(client, first_path), "REJECTED", "驳回后提交状态")
    _step(
        ref.reviewer,
        first_path,
        "require-rework",
        _get(client, first_path),
        "REWORK_REQUIRED",
        {"reason": "片段边界需按 SOP 重标"},
    )
    # 返工要求由用例内部级联推进任务/批次/分配（version 变化），必须先重读。
    _status(_get(client, task_path), "REWORK", "返工要求后任务状态")
    _status(_get(client, batch_path), "REWORK", "返工要求后批次状态")
    _status(_get(client, assignment_path), "IN_PROGRESS", "返工要求后分配状态")
    task = _step(client, task_path, "resume-work", _get(client, task_path), "IN_PROGRESS")
    batch = _step(client, batch_path, "resume-work", _get(client, batch_path), "IN_PROGRESS")

    second = _submission(
        ref.annotator,
        base,
        assignment_id=str(assignment["assignment_id"]),
        episodes=ref.episodes[:2],
        note="第二版（返工后）",
        label="返工标注员创建第二版提交",
    )
    second_id = ref.ctx.remember(GROUP, "submission_rework", second["submission_id"])
    if int(second.get("submission_version", 0)) != 2:
        raise SeedFailure("返工后的提交版本应为 2")
    if second.get("supersedes_id") != first["submission_id"]:
        raise SeedFailure("返工后的提交未取代第一版")
    second_path = f"{base}/annotation-submissions/{second_id}"
    _step(ref.annotator, second_path, "submit", second, "SUBMITTED")
    review2 = _review(
        ref.reviewer,
        base,
        submission_id=second_id,
        reviewer_id=ref.reviewer_id,
        label="为第二版提交创建复核",
    )
    review2_path = f"{base}/annotation-reviews/{review2['review_id']}"
    review2 = _step(ref.reviewer, review2_path, "claim", review2, "IN_REVIEW")
    _step(
        ref.reviewer,
        review2_path,
        "approve",
        review2,
        "APPROVED",
        {"checklist": _APPROVE_CHECKLIST},
    )

    batch = _step(client, batch_path, "submit", batch, "SUBMITTED")
    task = _step(client, task_path, "submit", task, "SUBMITTED")
    task = _step(client, task_path, "system-review", task, "REVIEWING")
    batch = _step(client, batch_path, "system-review", batch, "REVIEWING")
    batch = _step(client, batch_path, "accept", batch, "ACCEPTED")
    task = _step(client, task_path, "accept", task, "ACCEPTED")
    _step(
        client,
        task_path,
        "complete",
        task,
        "COMPLETED",
        {"waiver_reason": "演示数据：progress.approved_items 无写入点，按契约显式豁免"},
    )

    iaa = _create(
        client,
        f"{base}/iaa-reports",
        {
            "task_id": task_id,
            "metric_definitions_version": "ddp.stage3.iaa/1",
            "iou_threshold": 0.5,
        },
        "为单标注者任务计算 IAA",
    )
    _status(iaa, "INSUFFICIENT_DATA", "单标注者任务 IAA")


# --------------------------------------------------------------------------
# 取消链（任务 3）
# --------------------------------------------------------------------------
def _cancel_chain(ref: _Refs, sample_set: dict[str, Any]) -> None:
    client, base, stamp = ref.client, ref.base, ref.stamp
    task = _task(
        client,
        base,
        code=f"ann-task-{stamp}-cancel",
        name="家庭抓取标注（取消链）",
        sample_set_id=str(sample_set["sample_set_id"]),
        schema_id=ref.schema_id,
        episodes=1,
        items=1,
        priority="P2",
    )
    task_path = f"{base}/annotation-tasks/{ref.ctx.remember(GROUP, 'task_cancel', task['task_id'])}"
    task = _step(client, task_path, "submit-review", task, "PENDING_REVIEW")
    task = _step(
        ref.reviewer,
        task_path,
        "return-draft",
        task,
        "DRAFT",
        {"reason": "目标 Episode 数偏少，先补齐样本"},
    )
    _step(
        client,
        task_path,
        "cancel",
        task,
        "CANCELLED",
        {"reason": "本轮不排期，样本转给验收链任务"},
    )


def _closed_batch_chain(ref: _Refs, sample_set: dict[str, Any]) -> None:
    """任务：APPROVED → READY → 批次/分配就位 → 撤销分配 → 取消批次 → 取消任务。

    已取消的批次必须留在"永不提交验收"的任务上：任务的数据库守卫把
    ``CANCELLED`` 也算作"未进入验收的批次"（``annotation_work_guards.py:111``），
    而删除批次又被禁止（同文件 ``trg_annotation_batches_no_delete``），
    因此这类任务在现有实现下无法再 submit。
    """
    client, base, stamp = ref.client, ref.base, ref.stamp
    task = _task(
        client,
        base,
        code=f"ann-task-{stamp}-closed",
        name="家庭抓取标注（批次取消链）",
        sample_set_id=str(sample_set["sample_set_id"]),
        schema_id=ref.schema_id,
        episodes=1,
        items=1,
        priority="P3",
    )
    task_id = ref.ctx.remember(GROUP, "task_closed", task["task_id"])
    task_path = f"{base}/annotation-tasks/{task_id}"
    task = _approve_to_ready(ref, task, task_path)

    batch = _batch(
        client,
        base,
        task_id=task_id,
        code=f"ann-batch-{stamp}-c1",
        sequence_no=1,
        planned=1,
        item_refs=ref.episode_ids(1),
        name="待取消批次",
    )
    assignment = _assignment(
        client,
        base,
        batch_id=str(batch["batch_id"]),
        assignee_id=ref.annotator_id,
        planned=1,
        minutes=30,
        label="分配待取消批次",
    )
    _step(
        client,
        f"{base}/annotation-assignments/{assignment['assignment_id']}",
        "revoke",
        assignment,
        "REVOKED",
        {"reason": "排期调整，改由验收链任务承担"},
    )
    _step(
        client,
        f"{base}/annotation-batches/{batch['batch_id']}",
        "cancel",
        batch,
        "CANCELLED",
        {"reason": "样本转给验收链任务"},
    )
    _step(client, task_path, "cancel", task, "CANCELLED", {"reason": "无人力承接，本轮取消"})


# --------------------------------------------------------------------------
# 升级 / 仲裁链（任务 4）
# --------------------------------------------------------------------------
def _arbitration_chain(ref: _Refs, sample_set: dict[str, Any]) -> None:
    client, base, stamp = ref.client, ref.base, ref.stamp
    task = _task(
        client,
        base,
        code=f"ann-task-{stamp}-arbitration",
        name="家庭抓取标注（升级仲裁链）",
        sample_set_id=str(sample_set["sample_set_id"]),
        schema_id=ref.schema_id,
        episodes=2,
        items=2,
    )
    task_id = ref.ctx.remember(GROUP, "task_arbitration", task["task_id"])
    task_path = f"{base}/annotation-tasks/{task_id}"
    task = _approve_to_ready(ref, task, task_path)

    batch = _batch(
        client,
        base,
        task_id=task_id,
        code=f"ann-batch-{stamp}-x1",
        sequence_no=1,
        planned=2,
        item_refs=ref.episode_ids(2),
        name="仲裁批次",
    )
    batch_path = f"{base}/annotation-batches/{batch['batch_id']}"
    batch = _step(client, batch_path, "ready", batch, "READY")
    batch = _step(client, batch_path, "assign", batch, "ASSIGNED")
    assignment = _assignment(
        client,
        base,
        batch_id=str(batch["batch_id"]),
        assignee_id=ref.annotator_id,
        planned=2,
        minutes=60,
        label="仲裁链分配标注员",
    )
    assignment_path = f"{base}/annotation-assignments/{assignment['assignment_id']}"
    task = _step(client, task_path, "assign", task, "ASSIGNED")
    assignment = _step(ref.annotator, assignment_path, "claim", assignment, "CLAIMED")
    task = _step(client, task_path, "start", task, "IN_PROGRESS")
    batch = _step(client, batch_path, "start", batch, "IN_PROGRESS")
    _step(ref.annotator, assignment_path, "start", assignment, "IN_PROGRESS")

    submission = _submission(
        ref.annotator,
        base,
        assignment_id=str(assignment["assignment_id"]),
        episodes=ref.episodes[:2],
        note="仲裁链提交",
        label="仲裁链创建提交",
    )
    submission_id = ref.ctx.remember(GROUP, "submission_arbitration", submission["submission_id"])
    submission_path = f"{base}/annotation-submissions/{submission_id}"
    _step(ref.annotator, submission_path, "submit", submission, "SUBMITTED")

    # 同一提交先建两条复核：一条由仲裁人撤回（覆盖复核 CANCELLED），
    # 一条由复核人升级（覆盖复核 ESCALATED）。
    first_review = _review(
        ref.reviewer,
        base,
        submission_id=submission_id,
        reviewer_id=ref.reviewer_id,
        label="为仲裁链提交创建首轮复核",
    )
    extra_review = _review(
        ref.arbiter,
        base,
        submission_id=submission_id,
        reviewer_id=ref.arbiter_id,
        label="为仲裁链提交创建第二条复核",
    )
    extra_path = f"{base}/annotation-reviews/{extra_review['review_id']}"
    # 不能先 claim 再 cancel：claim 会无条件把提交推进到 IN_REVIEW
    # （reviews.py:233-239），第二条复核再 claim 会因重复的 system-review-start
    # 抛 state_transition_invalid。PENDING 状态本身允许 cancel，直接取消。
    _step(
        ref.arbiter,
        extra_path,
        "cancel",
        extra_review,
        "CANCELLED",
        {"reason": "重复创建，由首轮复核继续"},
    )
    review_path = f"{base}/annotation-reviews/{first_review['review_id']}"
    first_review = _step(ref.reviewer, review_path, "claim", first_review, "IN_REVIEW")
    _step(
        ref.reviewer,
        review_path,
        "escalate",
        first_review,
        "ESCALATED",
        {
            "reason": "片段边界与 SOP 存在争议，升级仲裁",
            "issues": [
                {
                    "code": "sop_boundary_dispute",
                    "severity": "MAJOR",
                    "message": "片段边界与 SOP 规定的切分点不一致",
                    "label_key": "action_segment",
                }
            ],
        },
    )
    _status(_get(client, submission_path), "ESCALATED", "升级后提交状态")

    arbitration = _create(
        client,
        f"{base}/annotation-submissions/{submission_id}/arbitrations",
        {
            "arbiter_ids": [ref.arbiter_id],
            "quorum": 1,
            "trigger": "REVIEWER_DISAGREEMENT",
            "review_id": str(first_review["review_id"]),
        },
        "创建仲裁",
    )
    arbitration_id = ref.ctx.remember(GROUP, "arbitration", arbitration["arbitration_id"])
    arbitration_path = f"{base}/annotation-arbitrations/{arbitration_id}"
    arbitration = _step(ref.arbiter, arbitration_path, "claim", arbitration, "IN_ARBITRATION")
    _status(_get(client, submission_path), "ARBITRATING", "仲裁受理后提交状态")
    voted = _expect(
        "仲裁投票",
        ref.arbiter,
        "POST",
        f"{arbitration_path}/votes",
        200,
        body={"decision": "APPROVE", "reason": "复核意见成立但记录本身可用"},
        headers=if_match(int(arbitration["version"])),
    )
    if int(voted.get("effective_vote_count", 0)) != 1:
        raise SeedFailure(f"仲裁有效票数应为 1，实际 {voted.get('effective_vote_count')}")
    resolved = _step(
        ref.arbiter,
        arbitration_path,
        "resolve-approved",
        voted,
        "RESOLVED_APPROVED",
    )
    if resolved.get("final_decision") != "APPROVE":
        raise SeedFailure(f"仲裁终局决议应为 APPROVE，实际 {resolved.get('final_decision')}")
    _status(_get(client, submission_path), "RESOLVED_APPROVED", "仲裁通过后提交状态")

    batch = _step(client, batch_path, "submit", batch, "SUBMITTED")
    task = _step(client, task_path, "submit", task, "SUBMITTED")
    task = _step(client, task_path, "system-review", task, "REVIEWING")
    batch = _step(client, batch_path, "system-review", batch, "REVIEWING")
    task = _step(
        client,
        task_path,
        "escalate",
        task,
        "ARBITRATION",
        {"reason": "任务级存在与采样口径相关的争议"},
    )
    task = _step(client, task_path, "resolve-accepted", task, "ACCEPTED")
    batch = _step(client, batch_path, "accept", batch, "ACCEPTED")
    _step(
        client,
        task_path,
        "complete",
        task,
        "COMPLETED",
        {"waiver_reason": "演示数据：progress.approved_items 无写入点，按契约显式豁免"},
    )


# --------------------------------------------------------------------------
# 黄金集
# --------------------------------------------------------------------------
def _gold_sets(ref: _Refs) -> None:
    client, base, stamp = ref.client, ref.base, ref.stamp
    items = [
        {
            "episode_id": ref.episode_ids(2)[0],
            "expected_labels": [{"label_key": "episode_outcome", "value": "success"}],
            "difficulty": "EASY",
        },
        {
            "episode_id": ref.episode_ids(2)[1],
            "expected_labels": [{"label_key": "episode_outcome", "value": "failure"}],
            "difficulty": "HARD",
        },
    ]
    gold = _create(
        client,
        f"{base}/gold-sets",
        {
            "code": f"gold-{stamp}-main",
            "name": "家庭抓取黄金集",
            "schema_id": ref.schema_id,
            "items": items,
            "exposure_policy": "HIDDEN",
            "max_exposure_per_annotator": 1,
            "rotation_policy": "PER_TASK",
        },
        "创建黄金集",
    )
    gold_id = ref.ctx.remember(GROUP, "gold_set", gold["gold_set_id"])
    gold_path = f"{base}/gold-sets/{gold_id}"
    gold = _step(client, gold_path, "submit-review", gold, "PENDING_REVIEW")
    gold = _step(ref.reviewer, gold_path, "activate", gold, "ACTIVE")

    owner = _owner_client(ref.ctx, ref.client)
    answers = _get(owner, f"{gold_path}/answers", "读取黄金集授权答案")
    expected = answers.get("items") or []
    if len(expected) != 2 or not expected[0].get("expected_labels"):
        raise SeedFailure("黄金集授权答案视图缺少 expected_labels")
    # 用仲裁人（本模块刚以 editor 加入）验证授权边界：评审人可能被共享环境里
    # 的其他会话提升为 owner，用它做否定用例会随环境漂移。
    arbiter_role = _member_role(ref.ctx, ref.client, ref.arbiter_id)
    if arbiter_role != "editor":
        raise SeedFailure(f"仲裁人角色应为 editor，实际 {arbiter_role}，无法验证答案视图授权边界")
    _expect(
        "非项目负责人读取黄金集答案应被拒绝",
        ref.arbiter,
        "GET",
        f"{gold_path}/answers",
        403,
    )

    injection = _expect(
        "向标注员注入黄金项",
        ref.reviewer,
        "POST",
        f"{gold_path}/injections",
        201,
        body={
            "task_id": ref.ctx.recall(GROUP, "task_accept"),
            "annotator_id": ref.annotator_id,
            "item_ids": [str(item["gold_item_id"]) for item in gold.get("items") or []],
        },
    )
    if len(injection.get("exposure_ids") or []) != 2:
        raise SeedFailure("黄金项注入条数与请求不一致")
    if any("expected_labels" in item for item in injection.get("items") or []):
        raise SeedFailure("注入响应泄漏了黄金集期望答案")

    evaluation = _expect(
        "按黄金集评估提交",
        client,
        "POST",
        f"{gold_path}/evaluations",
        201,
        body={
            "task_id": ref.ctx.recall(GROUP, "task_accept"),
            "submission_id": ref.ctx.recall(GROUP, "submission_accept_main"),
        },
    )
    if int(evaluation.get("evaluated_count", 0)) != 2:
        raise SeedFailure(f"黄金集评估计数异常：{evaluation.get('evaluated_count')}")
    if evaluation.get("accuracy") != 0.5:
        raise SeedFailure(f"黄金集准确率应为 0.5（1/2），实际 {evaluation.get('accuracy')}")
    _step(
        client,
        gold_path,
        "retire",
        gold,
        "RETIRED",
        {"reason": "本轮评估完成，下一轮更换黄金项"},
    )

    revoked = _create(
        client,
        f"{base}/gold-sets",
        {
            "code": f"gold-{stamp}-revoked",
            "name": "待撤销黄金集",
            "schema_id": ref.schema_id,
            "items": [items[0]],
            "exposure_policy": "HIDDEN",
            "max_exposure_per_annotator": 1,
            "rotation_policy": "NONE",
        },
        "创建待撤销黄金集",
    )
    _step(
        client,
        f"{base}/gold-sets/{revoked['gold_set_id']}",
        "revoke",
        revoked,
        "REVOKED",
        {"reason": "期望答案与最新 Schema 语义不一致，撤销后重建"},
    )


# --------------------------------------------------------------------------
# 质检抽检
# --------------------------------------------------------------------------
def _quality_samples(ref: _Refs) -> None:
    client, base = ref.client, ref.base
    task_id = ref.ctx.recall(GROUP, "task_accept")
    main_id = ref.ctx.recall(GROUP, "submission_accept_main")
    second_id = ref.ctx.recall(GROUP, "submission_accept_second")
    policy = "stage3-quality-sample/1"

    planned = _create_many(
        ref.reviewer,
        f"{base}/quality-samples/plan",
        {
            "task_id": task_id,
            "candidates": [{"submission_id": main_id, "stratum": "batch-a", "risk_score": 0.35}],
            "sampling_policy_version": policy,
            "sample_type": "RANDOM",
            "sample_size": 1,
            "sample_reason": "首批提交按固定比例随机抽检",
        },
        "生成抽检计划（主标注员提交）",
    )
    sample = _require_single(planned, "随机抽检计划")[0]
    sample_path = f"{base}/quality-samples/{sample['quality_sample_id']}"
    sample = _step(ref.reviewer, sample_path, "sample", sample, "SAMPLED")
    sample = _step(
        ref.reviewer,
        sample_path,
        "assign",
        sample,
        "ASSIGNED",
        {"reviewer_id": ref.reviewer_id},
    )
    sample = _step(ref.reviewer, sample_path, "start", sample, "IN_REVIEW")
    sample = _step(ref.reviewer, sample_path, "pass", sample, "PASSED")
    _step(ref.reviewer, sample_path, "close", sample, "CLOSED", {"reason": "抽检通过，归档"})

    risky = _create_many(
        client,
        f"{base}/quality-samples/plan",
        {
            "task_id": task_id,
            "candidates": [{"submission_id": second_id, "stratum": "batch-a", "risk_score": 0.92}],
            "sampling_policy_version": policy,
            "sample_type": "RISK_BASED",
            "sample_size": 1,
            "sample_reason": "高风险提交按风险阈值抽检",
            "risk_threshold": 0.5,
        },
        "生成抽检计划（高风险）",
    )
    sample = _require_single(risky, "风险抽检计划")[0]
    sample_path = f"{base}/quality-samples/{sample['quality_sample_id']}"
    sample = _step(client, sample_path, "sample", sample, "SAMPLED")
    sample = _step(
        client,
        sample_path,
        "assign",
        sample,
        "ASSIGNED",
        {"reviewer_id": ref.ctx.user_id},
    )
    sample = _step(client, sample_path, "start", sample, "IN_REVIEW")
    sample = _step(
        client,
        sample_path,
        "fail",
        sample,
        "FAILED",
        {"reason": "片段边界偏移超过 200ms，需返工"},
    )
    _step(client, sample_path, "close", sample, "CLOSED", {"reason": "已开具返工单"})

    single = _create(
        client,
        f"{base}/quality-samples",
        {
            "task_id": task_id,
            "submission_id": second_id,
            "annotator_id": ref.reviewer_id,
            "sampling_policy_version": policy,
            "sample_type": "RANDOM",
            "sample_reason": "针对第二标注员的定向抽检",
            "item_refs": [],
        },
        "创建单条抽检",
    )
    single_path = f"{base}/quality-samples/{single['quality_sample_id']}"
    single = _step(client, single_path, "sample", single, "SAMPLED")
    single = _step(
        client,
        single_path,
        "assign",
        single,
        "ASSIGNED",
        {"reviewer_id": ref.ctx.user_id},
    )
    single = _step(client, single_path, "start", single, "IN_REVIEW")
    single = _step(
        client,
        single_path,
        "escalate",
        single,
        "ESCALATED",
        {"reason": "争议涉及标签语义，升级质检负责人"},
    )
    _step(client, single_path, "close", single, "CLOSED", {"reason": "升级结论已归档"})

    cancelled = _create_many(
        ref.reviewer,
        f"{base}/quality-samples/plan",
        {
            "task_id": task_id,
            "candidates": [{"submission_id": main_id, "stratum": "batch-a", "risk_score": 0.2}],
            "sampling_policy_version": policy,
            "sample_type": "RANDOM",
            "sample_size": 1,
            "sample_reason": "本轮不再抽检，计划作废",
        },
        "生成抽检计划（待取消）",
    )
    sample = _require_single(cancelled, "待取消抽检计划")[0]
    _step(
        ref.reviewer,
        f"{base}/quality-samples/{sample['quality_sample_id']}",
        "cancel",
        sample,
        "CANCELLED",
        {"reason": "任务已验收，本轮抽检取消"},
    )


def _require_single(payload: list[Any], label: str) -> list[dict[str, Any]]:
    if len(payload) != 1 or not isinstance(payload[0], dict):
        raise SeedFailure(f"{label} 应返回 1 条，实际 {len(payload)}")
    return [payload[0]]


# --------------------------------------------------------------------------
# 质量检查与 IAA
# --------------------------------------------------------------------------
def _quality_checks_and_iaa(ref: _Refs) -> None:
    client, base = ref.client, ref.base
    task_id = ref.ctx.recall(GROUP, "task_accept")
    main_id = ref.ctx.recall(GROUP, "submission_accept_main")
    second_id = ref.ctx.recall(GROUP, "submission_accept_second")
    main_path = f"{base}/annotation-submissions/{main_id}"
    second_path = f"{base}/annotation-submissions/{second_id}"

    run = _expect("显式触发质量检查", client, "POST", f"{main_path}/quality-checks", 201)
    _status(run, "PASSED", "质量检查结论")
    rules = {item["rule_id"] for item in run.get("findings") or []}
    if rules != _DEFAULT_RULES:
        raise SeedFailure(f"质量检查命中的规则集与默认规则集不一致：{sorted(rules)}")
    runs = _get(client, f"{main_path}/quality-checks", "读取质量检查历史")
    if len(runs.get("items") or []) < 2:
        raise SeedFailure("提交时自动检查与显式检查都应留下执行记录")
    _expect("第二标注员提交质量检查", client, "POST", f"{second_path}/quality-checks", 201)

    report = _create(
        client,
        f"{base}/iaa-reports",
        {
            "task_id": task_id,
            "metric_definitions_version": "ddp.stage3.iaa/1",
            "iou_threshold": 0.5,
        },
        "计算 IAA 报告",
    )
    _status(report, "SUCCEEDED", "IAA 报告状态")
    if int(report.get("overlap_item_count", 0)) < 2 or int(report.get("annotator_count", 0)) < 2:
        raise SeedFailure(
            f"IAA 需要 2 个重叠 Episode 与 2 名标注者，实际 "
            f"{report.get('overlap_item_count')}/{report.get('annotator_count')}"
        )
    if not report.get("metrics"):
        raise SeedFailure("IAA 报告缺少指标")
    ref.ctx.remember(GROUP, "iaa_report", report["report_id"])
    _get(client, f"{base}/iaa-reports?task_id={task_id}", "读取 IAA 报告历史")


# --------------------------------------------------------------------------
# AI 建议
# --------------------------------------------------------------------------
def _ai_suggestions(ref: _Refs) -> None:
    client, base = ref.annotator, ref.base
    path = f"{base}/annotation-submissions/{ref.ctx.recall(GROUP, 'submission_accept_main')}"
    records = _get(client, path, "读取提交详情（含记录）").get("records") or []
    if len(records) < 2:
        raise SeedFailure("AI 建议需要至少两条标注记录")
    model = {
        "model_id": "ddp-vlm-annotator",
        "model_version": "1.3.0",
        "prompt_version": "episode-outcome/2",
    }
    first, second = str(records[0]["annotation_id"]), str(records[1]["annotation_id"])
    created = _expect(
        "登记 AI 预标注建议",
        client,
        "POST",
        f"{path}/ai-suggestions",
        201,
        body={
            "suggestions": [
                {
                    "annotation_id": first,
                    "label_key": "episode_outcome",
                    "suggested_value": "success",
                    "confidence": 0.91,
                    **model,
                },
                {
                    "annotation_id": second,
                    "label_key": "episode_outcome",
                    "suggested_value": "failure",
                    "confidence": 0.66,
                    **model,
                },
                {
                    "annotation_id": first,
                    "label_key": "quality_score",
                    "suggested_value": 0.92,
                    "confidence": 0.74,
                    **model,
                },
            ]
        },
    )
    items = created.get("items") or []
    if len(items) != 3:
        raise SeedFailure(f"AI 建议应返回 3 条，实际 {len(items)}")
    by_key = {(item["annotation_id"], item["label_key"]): item for item in items}

    accepted = by_key[(first, "episode_outcome")]
    confirmed = _expect(
        "采纳 AI 建议",
        client,
        "POST",
        f"{base}/annotation-ai-suggestions/{accepted['suggestion_id']}/confirm",
        200,
        body={"human_value": "success"},
        headers=if_match(int(accepted["version"])),
    )
    if not confirmed.get("human_confirmed"):
        raise SeedFailure("采纳 AI 建议后 human_confirmed 应为 true")

    revised = by_key[(second, "episode_outcome")]
    confirmed_revised = _expect(
        "修订 AI 建议",
        client,
        "POST",
        f"{base}/annotation-ai-suggestions/{revised['suggestion_id']}/confirm",
        200,
        body={"human_value": "success"},
        headers=if_match(int(revised["version"])),
    )
    if confirmed_revised.get("human_value") != "success":
        raise SeedFailure("修订后的 AI 建议应保留人工值 success")

    agreement = _get(client, f"{path}/ai-suggestions/agreement", "读取 AI 建议采纳率")
    stats = (agreement.get("items") or [{}])[0]
    actual = (stats.get("total"), stats.get("accepted"), stats.get("revised"), stats.get("pending"))
    if actual != (3, 1, 1, 1):
        raise SeedFailure(f"AI 采纳统计应为 (3,1,1,1)，实际 {actual}")
    if stats.get("acceptance_rate") != 0.5:
        raise SeedFailure(f"AI 采纳率应为 0.5，实际 {stats.get('acceptance_rate')}")
    ref.ctx.remember(GROUP, "ai_model_version", str(stats.get("model_version")))


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    annotator_id = _ensure_identity(ctx, client, "annotator", "标注员")
    _ensure_identity(ctx, client, "arbiter", "仲裁人")
    ref = _Refs(
        ctx=ctx,
        client=client,
        base=base,
        reviewer=ctx.as_("reviewer"),
        reviewer_id=ctx.recall("identity", "reviewer"),
        annotator=ctx.as_("annotator"),
        annotator_id=annotator_id,
        arbiter=ctx.as_("arbiter"),
        arbiter_id=ctx.recall("identity", "arbiter"),
        schema_id="",
    )

    _schemas(ref)
    ref.episodes = _eligible_episodes(client, base)
    ctx.remember(GROUP, "episode_ids", ",".join(item["episode_id"] for item in ref.episodes))

    accept_set = _sample_set(
        client,
        base,
        code=f"ss-{ctx.stamp}-accept",
        name="验收链样本集",
        episode_ids=ref.episode_ids(3),
    )
    ctx.remember(GROUP, "sample_set_accept", accept_set["sample_set_id"])
    _sample_set(
        client,
        base,
        code=f"ss-{ctx.stamp}-cancelled",
        name="待取消样本集",
        episode_ids=ref.episode_ids(2),
        stop="cancel",
    )
    _sample_set(
        client,
        base,
        code=f"ss-{ctx.stamp}-preview",
        name="仅预览样本集",
        episode_ids=ref.episode_ids(2),
        stop="preview",
    )

    _accept_open(ref, accept_set)
    _accept_results(ref)
    _gold_sets(ref)
    _quality_samples(ref)
    _quality_checks_and_iaa(ref)
    _ai_suggestions(ref)

    rework_set = _sample_set(
        client,
        base,
        code=f"ss-{ctx.stamp}-rework",
        name="返工链样本集",
        episode_ids=ref.episode_ids(2),
    )
    cancel_set = _sample_set(
        client,
        base,
        code=f"ss-{ctx.stamp}-cancel",
        name="取消链样本集",
        episode_ids=ref.episode_ids(1),
    )
    arbitration_set = _sample_set(
        client,
        base,
        code=f"ss-{ctx.stamp}-arbitration",
        name="仲裁链样本集",
        episode_ids=ref.episode_ids(2),
    )

    _rework_chain(ref, rework_set)
    _cancel_chain(ref, cancel_set)
    # 批次取消链复用取消链的样本集：取消链任务未审批，样本集仍是 SNAPSHOTTED。
    _closed_batch_chain(ref, cancel_set)
    _arbitration_chain(ref, arbitration_set)
