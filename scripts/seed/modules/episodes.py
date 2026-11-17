"""Episode 模块：创建 / 状态动作 / 修订。

覆盖的状态路径
--------------
素材来自导入链路已落库的 RawAsset：按 ``raw_metadata.episode_key`` 分组，
一个 Episode 用一整组机位（与导入物化口径一致），切片窗口取该组机位
``excerpt_start_seconds`` / ``excerpt_seconds`` 的并集。

- E1：``PENDING`` →（``ready`` 内含 ``system-start``）``PROCESSING`` → ``READY``
  → ``QUALITY_CHECKING`` →（``quality-result`` PASS）→ ``ACCEPTED`` → ``COMPLETED``
- E2：``READY`` → ``QUALITY_CHECKING`` →（``quality-result`` FAIL）→ ``REJECTED``
- E3：``READY`` → ``INVALIDATED``
- E4：保持 ``PENDING``（界面里「待就绪」的代表）
- E5：``READY`` → 修订（新 ``episode_id``、``episode_version=2``、
  ``supersedes_id`` 指向旧版）→ 新版再次 ``READY``

走不到的终态
------------
- ``ARCHIVED``：``archive`` 不在 HTTP ``EpisodeAction`` 白名单里
  （``backend/app/api/v1/episodes.py:41-49``），只存在于领域迁移表；
- ``FAILED``：只能由 ``system-fail`` 产生，该动作同样未暴露；``retry`` 也未暴露；
- ``PROCESSING`` 只能作为 ``ready`` 的中间态被瞬时跳过，没有独立动作。

关于 Manifest 与退化量
----------------------
``ready`` 需要客户端提交 Canonical Manifest。这里用**库里已有事实**组装：
机位取 RawAsset 的 ``object_key`` / ``sha256`` / ``size_bytes`` / ``content_type`` /
``format`` / ``camera``；时间范围取 Episode 自身的 ``start_time_ns`` /
``end_time_ns``；``frame_count`` 按各机位声明的 ``source_fps`` 与切片时长推算
（``round(duration_ns * fps / 1e9)``），不是逐帧解码的结果。
公开数据集没有时钟同步与标定声明，因此 ``time_base`` 如实填 ``DEGRADED`` +
``NONE``、``calibration_snapshot`` 为空——不伪造标定。

重复运行
--------
同一份导入内容会命中去重，第二次运行起素材被既有 Episode 占用，
``POST /episodes`` 返回 409 ``episode_overlap_conflict``。此时按契约显式改用
``allow_overlap=true`` + ``overlap_reason`` 再建一次：重叠事实由服务端记录在库，
不是静默绕过。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "episodes"

# 一个 Episode 最多用几条；顺序即角色，见模块文档。
ROLE_ORDER = ("complete", "reject", "invalidate", "pending", "revision")
MIN_EPISODES = 3

CANONICAL_SCHEMA_VERSION = "ddp.canonical/1.0.0-draft"
PIPELINE_ID = "seed_scripts"
PIPELINE_VERSION = "1.0.0"
MANUAL_RULE_VERSION = "manual-seed/1.0.0"
# 公开数据集不含时钟同步声明：如实标注退化，不声称 VERIFIED。
TIME_BASE: dict[str, object] = {
    "time_domain": "UTC",
    "clock_source": "HOST_CLOCK",
    "sync_method": "NONE",
    "sync_status": "DEGRADED",
}
OVERLAP_REASON = "种子复跑：同一份已导入素材的同一片段再次切片，用于演示 Episode 状态流转"
NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class Group:
    """一个 ``episode_key`` 下的全部机位及其切片事实。"""

    key: str
    assets: tuple[dict[str, Any], ...]
    start_ns: int
    end_ns: int
    fps: float
    instruction: str | None

    @property
    def duration_ns(self) -> int:
        return self.end_ns - self.start_ns


def _as_dict(label: str, payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _get(client: Client, path: str, label: str) -> dict[str, Any]:
    return _as_dict(label, expect(label, client.call("GET", path), 200))


def _list_all(client: Client, path: str, label: str) -> list[dict[str, Any]]:
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


def _error_code(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    code = error.get("code")
    return code if isinstance(code, str) else None


def _metadata(asset: dict[str, Any]) -> dict[str, Any]:
    raw = asset.get("raw_metadata")
    return dict(raw) if isinstance(raw, dict) else {}


def _media_index(
    client: Client, base: str, import_ids: set[str]
) -> dict[tuple[str, str], dict[str, Any]]:
    """``(episode_key, camera) → 媒体资产``。

    同一原始资产会被多次导入复用，每次导入都会各写一行媒体资产；多行并存时
    以 ``created_at`` 最新的一行为准（重复导入的元数据本应一致，这里不做断言，
    以免同一素材被不同数据集重复导入时把整次播种卡死）。
    """
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for import_id in sorted(import_ids):
        page = _get(
            client,
            f"{base}/data-source-imports/{import_id}/assets?limit=500",
            "读取导入产出的媒体资产",
        )
        for item in page.get("items") or []:
            if not isinstance(item, dict):
                continue
            key = (str(item.get("episode_key")), str(item.get("camera")))
            previous = index.get(key)
            if previous is None or str(item.get("created_at")) >= str(previous.get("created_at")):
                index[key] = item
    return index


def _build_groups(client: Client, base: str) -> list[Group]:
    assets = _list_all(client, f"{base}/raw-assets?status=VALID", "读取原始资产列表")
    if not assets:
        raise SeedFailure(
            "BLOCKED: 项目内没有 VALID 原始资产；Episode 只能引用真实的 RawAsset。"
            "先运行 `run.py datasources` 让导入链路产出素材"
        )

    bucketed: dict[str, list[dict[str, Any]]] = {}
    import_ids: set[str] = set()
    for asset in assets:
        meta = _metadata(asset)
        episode_key = meta.get("episode_key")
        import_id = meta.get("import_id")
        if not isinstance(episode_key, str) or not episode_key:
            # 上传链路来的素材没有 episode_key，不猜测它的分组，直接跳过。
            continue
        if not isinstance(import_id, str) or not import_id:
            continue
        bucketed.setdefault(episode_key, []).append(asset)
        import_ids.add(import_id)

    if len(bucketed) < MIN_EPISODES:
        raise SeedFailure(
            f"BLOCKED: 可用于创建 Episode 的素材分组只有 {len(bucketed)} 组，"
            f"至少需要 {MIN_EPISODES} 组；先运行 `run.py datasources` 导入新的样本"
        )

    media = _media_index(client, base, import_ids)
    groups: list[Group] = []
    for key in sorted(bucketed):
        members = sorted(bucketed[key], key=lambda item: str(item.get("asset_id")))
        starts: list[int] = []
        ends: list[int] = []
        rates: set[float] = set()
        instruction: str | None = None
        for asset in members:
            camera = _metadata(asset).get("camera")
            if not isinstance(camera, str) or not camera:
                raise SeedFailure(f"BLOCKED: 分组 {key} 的素材缺少 camera 元数据")
            fact = media.get((key, camera))
            if fact is None:
                raise SeedFailure(
                    f"BLOCKED: 分组 {key} 的机位 {camera} 在导入产物里找不到对应媒体资产，"
                    "无法确定切片窗口"
                )
            start_seconds = fact.get("excerpt_start_seconds")
            duration_seconds = fact.get("excerpt_seconds")
            rate = fact.get("source_fps")
            if start_seconds is None or duration_seconds is None or not rate:
                raise SeedFailure(f"BLOCKED: 分组 {key} 的机位 {camera} 缺少起始/时长/帧率元数据")
            start_ns = int(round(float(start_seconds) * NS_PER_SECOND))
            starts.append(start_ns)
            end_seconds = float(start_seconds) + float(duration_seconds)
            ends.append(int(round(end_seconds * NS_PER_SECOND)))
            rates.add(float(rate))
            if instruction is None:
                task_name = fact.get("task_name")
                if isinstance(task_name, str) and task_name.strip():
                    instruction = task_name.strip()
        if len(rates) != 1:
            raise SeedFailure(f"BLOCKED: 分组 {key} 的各机位帧率不一致：{sorted(rates)}")
        group = Group(
            key=key,
            assets=tuple(members),
            start_ns=min(starts),
            end_ns=max(ends),
            fps=rates.pop(),
            instruction=instruction,
        )
        if group.duration_ns <= 0:
            raise SeedFailure(f"BLOCKED: 分组 {key} 的切片窗口时长为 0，无法创建 Episode")
        groups.append(group)
    return groups


def _frame_count(group: Group) -> int:
    """按声明帧率与切片时长推算帧数；这是估算，不是逐帧解码结果。"""
    return int(round(group.duration_ns * group.fps / NS_PER_SECOND))


def _create_episode(
    client: Client, base: str, ctx: Context, group: Group, role: str
) -> dict[str, Any]:
    code = f"{ctx.stamp}-{group.key}"[:64]
    segmentation: dict[str, Any] = {
        "method": "MANUAL_TIME_RANGE",
        "rule_version": MANUAL_RULE_VERSION,
        "start_time_ns": group.start_ns,
        "end_time_ns": group.end_ns,
        "time_domain": "UTC",
    }
    body: dict[str, Any] = {
        "raw_asset_ids": [str(asset["asset_id"]) for asset in group.assets],
        "segmentation": segmentation,
        "time_base": dict(TIME_BASE),
        "calibration_snapshot": [],
        "canonical_schema_version": CANONICAL_SCHEMA_VERSION,
        "pipeline_id": PIPELINE_ID,
        "pipeline_version": PIPELINE_VERSION,
        "code": code,
        "scene_labels": [],
        "language_instruction": group.instruction,
    }

    status, payload = client.call(
        "POST",
        f"{base}/episodes",
        body=body,
        headers={"Idempotency-Key": idempotency_key()},
    )
    if status == 409 and _error_code(payload) == "episode_overlap_conflict":
        # 复跑时素材已被占用：按契约显式声明重叠后再建（重叠事实由服务端留痕）。
        body["segmentation"] = {
            **segmentation,
            "allow_overlap": True,
            "overlap_reason": OVERLAP_REASON,
        }
        status, payload = client.call(
            "POST",
            f"{base}/episodes",
            body=body,
            headers={"Idempotency-Key": idempotency_key()},
        )
    if status != 201:
        raise SeedFailure(
            f"创建 Episode（{role}/{group.key}）：期望 201，实际 {status}\n  响应：{payload}"
        )

    episode = _as_dict("创建 Episode", payload)
    if episode.get("status") != "PENDING":
        raise SeedFailure(f"创建 Episode：初始状态应为 PENDING，实际 {episode.get('status')}")
    if episode.get("code") != code:
        raise SeedFailure(f"创建 Episode：code 与请求不符（{episode.get('code')} != {code}）")
    return episode


def _create_revision(
    client: Client, base: str, ctx: Context, episode: dict[str, Any], suffix: str
) -> dict[str, Any]:
    code = f"{ctx.stamp}-{suffix}"[:64]
    revised = _as_dict(
        "创建 Episode 修订",
        expect(
            "创建 Episode 修订",
            client.call(
                "POST",
                f"{base}/episodes/{episode['episode_id']}/revisions",
                body={"code": code},
                headers={
                    **if_match(int(episode["version"])),
                    "Idempotency-Key": idempotency_key(),
                },
            ),
            201,
        ),
    )
    if revised.get("episode_version") != int(episode["episode_version"]) + 1:
        raise SeedFailure(
            f"创建 Episode 修订：episode_version 应自增，"
            f"{episode.get('episode_version')} -> {revised.get('episode_version')}"
        )
    if revised.get("supersedes_id") != episode["episode_id"]:
        raise SeedFailure("创建 Episode 修订：supersedes_id 未指向旧版本")
    if revised.get("status") != "PENDING":
        raise SeedFailure(
            f"创建 Episode 修订：新版本状态应为 PENDING，实际 {revised.get('status')}"
        )
    return revised


def _manifest(episode: dict[str, Any], group: Group) -> dict[str, Any]:
    """用库里已有事实组装 Canonical Manifest；不写入任何伪造的标定或同步声明。"""
    streams = []
    for asset in sorted(group.assets, key=lambda item: str(_metadata(item).get("camera") or "")):
        meta = _metadata(asset)
        streams.append(
            {
                "stream_id": str(asset["asset_id"]),
                "camera": meta.get("camera"),
                "object_key": asset["object_key"],
                "sha256": asset["sha256"],
                "size_bytes": int(asset["size_bytes"]),
                "content_type": asset["content_type"],
                "format": asset["format"],
            }
        )
    return {
        "schema_version": episode["canonical_schema_version"],
        "episode_id": episode["episode_id"],
        "episode_lineage_id": episode["episode_lineage_id"],
        "episode_version": episode["episode_version"],
        "project_id": episode["project_id"],
        "code": episode["code"],
        "task_id": episode["task_id"],
        "batch_id": episode["batch_id"],
        "robot_id": episode["robot_id"],
        "device_ids": list(episode["device_ids"]),
        "sensor_ids": list(episode["sensor_ids"]),
        # 纳秒整数以字符串承载，跨 JSON/JS 不丢精度（与领域生成器一致）。
        "start_time": str(episode["start_time_ns"]),
        "end_time": str(episode["end_time_ns"]),
        "duration_ns": str(episode["duration_ns"]),
        "time_base": dict(episode["time_base"]),
        "time_alignment": {
            "clock_source": episode["time_base"].get("clock_source"),
            "sync_method": episode["time_base"].get("sync_method"),
            "sync_status": episode["time_base"].get("sync_status"),
        },
        "calibration_snapshot": list(episode["calibration_snapshot"]),
        "streams": streams,
        "raw_asset_ids": [str(asset["asset_id"]) for asset in group.assets],
        "provenance": {
            "pipeline_id": episode["pipeline_id"],
            "pipeline_version": episode["pipeline_version"],
            "source": PIPELINE_ID,
        },
        "quality": {"status": episode["quality_status"], "issues": []},
        "frame_count": _frame_count(group),
    }


def _act(
    client: Client,
    episode: dict[str, Any],
    action: str,
    expected_status: str,
    label: str,
    body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    path = f"/api/v1/projects/{episode['project_id']}/episodes/{episode['episode_id']}"
    # 统一显式发对象：契约允许省略 body，但显式 `{}` 对两边都成立。
    payload_body = {} if body is None else body
    updated = _as_dict(
        label,
        expect(
            label,
            client.call(
                "POST",
                f"{path}/actions/{action}",
                body=payload_body,
                headers={
                    **if_match(int(episode["version"])),
                    "Idempotency-Key": idempotency_key(),
                },
            ),
            200,
        ),
    )
    if updated.get("status") != expected_status:
        raise SeedFailure(f"{label}：期望状态 {expected_status}，实际 {updated.get('status')}")
    return updated


def _ready(client: Client, episode: dict[str, Any], group: Group) -> dict[str, Any]:
    manifest = _manifest(episode, group)
    frame_count = _frame_count(group)
    ready = _act(
        client,
        episode,
        "ready",
        "READY",
        "Episode 提交 Manifest 就绪",
        {"manifest": manifest, "frame_count": frame_count},
    )
    if ready.get("frame_count") != frame_count:
        raise SeedFailure(
            "Episode 提交 Manifest 就绪：frame_count 未被记录"
            f"（{ready.get('frame_count')} != {frame_count}）"
        )
    if not ready.get("manifest_sha256"):
        raise SeedFailure("Episode 提交 Manifest 就绪：服务端未记录 manifest_sha256")
    return ready


def _chain_complete(client: Client, episode: dict[str, Any], group: Group) -> dict[str, Any]:
    current = _ready(client, episode, group)
    current = _act(client, current, "quality-check", "QUALITY_CHECKING", "Episode 进入质检")
    current = _act(
        client,
        current,
        "quality-result",
        "QUALITY_CHECKING",
        "Episode 写入质检结论 PASS",
        {"quality_status": "PASS"},
    )
    if current.get("quality_status") != "PASS":
        raise SeedFailure(f"Episode 写入质检结论：期望 PASS，实际 {current.get('quality_status')}")
    current = _act(client, current, "accept", "ACCEPTED", "Episode 质检通过")
    return _act(client, current, "complete", "COMPLETED", "Episode 完成")


def _chain_reject(client: Client, episode: dict[str, Any], group: Group) -> dict[str, Any]:
    current = _ready(client, episode, group)
    current = _act(client, current, "quality-check", "QUALITY_CHECKING", "Episode 进入质检")
    current = _act(
        client,
        current,
        "quality-result",
        "QUALITY_CHECKING",
        "Episode 写入质检结论 FAIL",
        {"quality_status": "FAIL"},
    )
    if current.get("quality_status") != "FAIL":
        raise SeedFailure(f"Episode 写入质检结论：期望 FAIL，实际 {current.get('quality_status')}")
    return _act(
        client,
        current,
        "reject",
        "REJECTED",
        "Episode 质检驳回",
        {"reason": "手部遮挡导致关键动作不可见，片段不满足标注要求"},
    )


def _chain_invalidate(client: Client, episode: dict[str, Any], group: Group) -> dict[str, Any]:
    current = _ready(client, episode, group)
    return _act(
        client,
        current,
        "invalidate",
        "INVALIDATED",
        "Episode 失效",
        {"reason": "上游数据源 revision 回滚，片段来源不再可信"},
    )


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"

    groups = _build_groups(client, base)
    selected = groups[: len(ROLE_ORDER)]

    for role, group in zip(ROLE_ORDER, selected, strict=False):
        episode = _create_episode(client, base, ctx, group, role)
        ctx.remember(GROUP, f"{role}_group", group.key)
        ctx.remember(GROUP, f"{role}_code", str(episode["code"]))

        if role == "complete":
            episode = _chain_complete(client, episode, group)
        elif role == "reject":
            episode = _chain_reject(client, episode, group)
        elif role == "invalidate":
            episode = _chain_invalidate(client, episode, group)
        elif role == "pending":
            # 保持 PENDING：这条就是界面里「待就绪」的代表，不再推进。
            pass
        elif role == "revision":
            episode = _ready(client, episode, group)
            original_id = str(episode["episode_id"])
            ctx.remember(GROUP, "ready_episode_id", original_id)
            revised = _create_revision(client, base, ctx, episode, f"{group.key}-v2")
            ctx.remember(GROUP, "revision_episode_id", str(revised["episode_id"]))
            revised = _ready(client, revised, group)
            episode = revised

        ctx.remember(GROUP, f"{role}_episode_id", str(episode["episode_id"]))
        ctx.remember(GROUP, f"{role}_status", str(episode["status"]))

    # 读一遍列表，确认本次创建的 Episode 都在项目范围内可见。
    listed = _list_all(client, f"{base}/episodes", "读取 Episode 列表")
    listed_codes = {str(item.get("code")) for item in listed}
    expected = {
        str(ctx.recall(GROUP, f"{role}_code"))
        for role, _ in zip(ROLE_ORDER, selected, strict=False)
    }
    missing = expected - listed_codes
    if missing:
        raise SeedFailure(f"读取 Episode 列表：缺少本次创建的 Episode {sorted(missing)}")
