"""Episode 自动就绪判定与 Canonical Manifest 生成（纯函数，无 IO）。

**为什么需要这一层**

``Episode.mark_ready`` 只接受调用方提交的 Manifest，而平台里没有任何组件从
**已落库事实**生成它：导入链路的产物（RawAsset + EpisodeRawAsset + time_base +
calibration_snapshot）一直在，却没有人把 Manifest 组装出来，于是 Episode 永远停在
``PENDING``，「导入 → 标注 → 数据集」全部要人工补一步。本模块就是那个缺失的
canonicalization 规则：**只根据事实推导，不编造结论**。

**边界（重要）**

- 本模块是纯函数：不读库、不写库、不调对象存储、不看时间；同样的输入永远得到
  同样的 Manifest（键排序、stream 排序、整数时间戳），因此 Hash 可复现。
- Manifest 的结构、规范化与 Hash 计算**不在这里重写**：复用
  ``app.domain.episodes.manifest`` 的 ``MANIFEST_REQUIRED_KEYS`` /
  ``validate_manifest_identity`` / ``compute_manifest_sha256``。
- 状态机、``manifest_key`` 生成、写入与审计都不在这里：本模块只回答
  「能不能就绪、Manifest 长什么样、为什么不能」。

**失败关闭（fail-closed）**

判不了就是判不了，绝不当成通过：任何一条未确认的事实都会变成一条
``ReadinessBlocker``，调用方据此让 Episode 留在 ``PENDING``。宁可停在
``PENDING`` 并留下可读原因，也不能把未校验数据送进标注与数据集。

**帧数与标定：两条需要产品确认的判定口径**

1. ``frame_count`` 取「Episode 时间范围（纳秒，权威） × 机位声明的
   ``source_fps``」，不跨机位求和（求和会把同一时间轴的帧重复计数）。机位声明
   的帧率缺失或不一致时无法确定帧率，直接判为阻塞——解码视频数帧才是真值，
   平台当前不解码，因此这里只承认**声明的帧率**，并在 Manifest 的 provenance
   之外不做任何"看起来精确"的补充。
2. 标定快照为空：**多机位**必然阻塞（没有标定就没有机位间几何关系，多路流
   无法构成一个可融合的样本）；**单机位**不阻塞，但记一条显式 notice 并写进
   审计——单路流不需要跨机位几何关系，而"没有标定"这件事本来就由数据集准入
   （``domain/datasets/selection.py`` 的 ``CALIBRATION_MISSING`` /
   ``allow_missing_calibration``）如实反映；在 Episode 上伪造一份标定才是造假。
   这与 ``validate_calibration_snapshot`` 既有的门禁划分一致：能否建 Episode
   只看字段结构，标定是否可接受由数据准入判断。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from app.domain.episodes.errors import DomainValidationError, TimeBaseInvalidError
from app.domain.episodes.manifest import validate_manifest_identity
from app.domain.episodes.readiness_facts import EpisodeManifestFacts, EpisodeStream
from app.domain.episodes.readiness_result import (
    MAX_BLOCKERS,
    MULTI_STREAM_THRESHOLD,
    ReadinessBlocker,
    ReadinessBlockerCode,
    ReadinessDecision,
    ReadinessNotice,
    ReadinessNoticeCode,
)
from app.domain.episodes.validation import (
    SYNC_STATUSES_UNVERIFIED,
    validate_sha256,
    validate_time_base,
)
from app.domain.ingestion.enums import RAW_ASSET_STREAM_USABLE_STATUSES


def evaluate_readiness(
    facts: EpisodeManifestFacts,
    streams: Sequence[EpisodeStream],
    *,
    provenance_source: str,
) -> ReadinessDecision:
    """按已落库事实判定能否置 READY，并在可置时产出 Canonical Manifest。

    ``provenance_source`` 写入 Manifest 的 ``provenance.source``，用来标识产出方
    （例如导入链路传 ``data_source_import``）；它不做任何校验决策。
    """
    blockers: list[ReadinessBlocker] = []
    notices: list[ReadinessNotice] = []

    time_base = _check_time_base(facts.time_base, blockers)
    ordered = _check_streams(streams, blockers)
    _check_calibration(facts, ordered, blockers, notices)

    frame_count: int | None = None
    if facts.duration_ns <= 0:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.DURATION_UNKNOWN,
                "Episode 时间范围无法确定（时长不大于 0），不能推导帧数",
                {"start_time_ns": facts.start_time_ns, "end_time_ns": facts.end_time_ns},
            )
        )
    else:
        frame_rate = _check_frame_rate(ordered, blockers)
        if frame_rate is not None:
            frame_count = int(round(facts.duration_ns * frame_rate / 1_000_000_000))
            if frame_count <= 0:
                frame_count = None
                blockers.append(
                    ReadinessBlocker(
                        ReadinessBlockerCode.FRAME_COUNT_UNDETERMINED,
                        "按声明帧率推导出的帧数为 0，无法确定帧数",
                        {"duration_ns": facts.duration_ns, "source_fps": frame_rate},
                    )
                )

    if blockers:
        return ReadinessDecision(
            manifest=None,
            frame_count=frame_count,
            blockers=tuple(blockers[:MAX_BLOCKERS]),
            notices=tuple(notices),
        )

    # 走到这里说明前四项事实都已确认；缺少任一前置时才可能为 None。
    assert time_base is not None and frame_count is not None
    manifest = _build_manifest(
        facts,
        ordered,
        time_base=time_base,
        frame_count=frame_count,
        provenance_source=provenance_source,
    )
    # 自检：生成的 Manifest 必须通过领域自身的身份校验。这里不做"如果失败就跳过"
    # 的兜底——做不到就是实现缺陷，必须立刻炸出来，而不是悄悄放行。
    validate_manifest_identity(
        manifest,
        episode_id=facts.episode_id,
        episode_lineage_id=facts.episode_lineage_id,
        episode_version=facts.episode_version,
        project_id=facts.project_id,
        code=facts.code,
        start_time_ns=facts.start_time_ns,
        end_time_ns=facts.end_time_ns,
        duration_ns=facts.duration_ns,
    )
    return ReadinessDecision(
        manifest=manifest,
        frame_count=frame_count,
        blockers=(),
        notices=tuple(notices),
    )


def _check_time_base(
    time_base: Mapping[str, object],
    blockers: list[ReadinessBlocker],
) -> dict[str, object] | None:
    """时间基准必须通过既有 ``validate_time_base``（fail-closed 的权威口径）。"""
    if not isinstance(time_base, Mapping):
        blockers.append(
            ReadinessBlocker(ReadinessBlockerCode.TIME_BASE_INVALID, "time_base 不是对象")
        )
        return None
    sync_status = time_base.get("sync_status")
    if sync_status in SYNC_STATUSES_UNVERIFIED:
        # 先判同步状态，才能给出比"字段非法"更准确的 code；判定集合与
        # validate_time_base 共用同一份权威定义，不另写一套。
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.TIME_SYNC_UNVERIFIED,
                f"时间同步状态为 {sync_status}，未通过时间基准校验，不能自动就绪",
                {"sync_status": str(sync_status)},
            )
        )
        return None
    try:
        return validate_time_base(dict(time_base))
    except TimeBaseInvalidError as exc:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.TIME_BASE_INVALID,
                f"time_base 未通过校验：{exc}",
            )
        )
        return None


def _check_streams(
    streams: Sequence[EpisodeStream],
    blockers: list[ReadinessBlocker],
) -> tuple[EpisodeStream, ...]:
    """校验每条流的事实完整性，并返回确定性排序后的流序列。"""
    if not streams:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.STREAMS_MISSING,
                "Episode 没有关联任何原始资产流，无法生成 Canonical Manifest",
            )
        )
        return ()

    unusable: list[dict[str, object]] = []
    unnamed: list[str] = []
    invalid: list[dict[str, object]] = []
    mismatched: list[str] = []
    cameras: dict[str, int] = {}
    valid_streams: list[EpisodeStream] = []

    for stream in streams:
        asset = str(stream.asset_id)
        if not stream.usable:
            unusable.append({"asset_id": asset, "reason": "资产不可取"})
            continue
        if stream.status is not None and stream.status not in RAW_ASSET_STREAM_USABLE_STATUSES:
            unusable.append({"asset_id": asset, "reason": f"资产状态为 {stream.status}"})
            continue
        camera = (stream.camera or "").strip()
        if not camera:
            unnamed.append(asset)
            continue
        if not stream.object_key or not stream.content_type or not stream.format:
            invalid.append({"asset_id": asset, "reason": "缺少 object_key/content_type/format"})
            continue
        try:
            validate_sha256(stream.sha256, "stream.sha256")
        except DomainValidationError as exc:
            invalid.append({"asset_id": asset, "reason": str(exc)})
            continue
        if stream.size_bytes <= 0:
            invalid.append({"asset_id": asset, "reason": "size_bytes 必须大于 0"})
            continue
        if stream.sha256_snapshot is not None and (
            stream.sha256_snapshot,
            stream.size_bytes_snapshot,
        ) != (stream.sha256, stream.size_bytes):
            mismatched.append(asset)
            continue
        cameras[camera] = cameras.get(camera, 0) + 1
        valid_streams.append(stream)

    if unusable:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.STREAM_INCOMPLETE,
                "Episode 关联的原始资产不可用于生成 Manifest",
                {"streams": unusable},
            )
        )
    if unnamed:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.STREAM_INCOMPLETE,
                "机位缺失：无法确定流的机位标识，机位集合不完整",
                {"asset_ids": unnamed},
            )
        )
    if invalid:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.STREAM_INCOMPLETE,
                "流事实不合法：object_key/sha256/size_bytes/format 存在缺失或非法值",
                {"streams": invalid},
            )
        )
    if mismatched:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.STREAM_SNAPSHOT_MISMATCH,
                "流与 Episode 关联时记录的 Hash/大小快照不一致，Manifest 无法引用",
                {"asset_ids": mismatched},
            )
        )
    duplicated = sorted(camera for camera, count in cameras.items() if count > 1)
    if duplicated:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.STREAM_INCOMPLETE,
                "同一机位出现多条流，机位与流的对应关系不唯一",
                {"cameras": duplicated},
            )
        )
    return tuple(sorted(valid_streams, key=lambda item: ((item.camera or ""), str(item.asset_id))))


def _check_calibration(
    facts: EpisodeManifestFacts,
    streams: Sequence[EpisodeStream],
    blockers: list[ReadinessBlocker],
    notices: list[ReadinessNotice],
) -> None:
    """缺标定只记 notice，不阻塞就绪——**能否用于训练由数据集准入判断**。

    这条口径在 2026-11-18 调整。原实现要求"多机位必须有标定快照"，看似更严，
    实际效果是：公开第一人称数据集（多机位、不带标定）永远无法进入 READY，
    整条链路停在 PENDING，只能靠 HTTP ``ready`` 绕过——而那正是"ready 不检查
    就绪门禁"这个缺陷的成因。

    标定是否可接受本来就有权威判定：数据集选择器的 ``CALIBRATION_MISSING``
    排除码与 ``allow_missing_calibration`` 显式确认。就绪的职责是"能否构造出
    可信的 Canonical Manifest"，不是"这份数据能否用于训练"。两件事分开，
    既不伪造标定，也不让缺失的标定悄悄通过。
    """
    if facts.calibration_snapshot:
        return
    if not streams:
        return
    single = len(streams) < MULTI_STREAM_THRESHOLD
    notices.append(
        ReadinessNotice(
            (
                ReadinessNoticeCode.SINGLE_STREAM_CALIBRATION_ABSENT
                if single
                else ReadinessNoticeCode.MULTI_STREAM_CALIBRATION_ABSENT
            ),
            (
                "单机位 Episode 没有标定快照：单路流无需跨机位几何关系，"
                if single
                else "多机位 Episode 没有标定快照：机位间几何关系未声明，"
            )
            + "标定是否可接受由数据集准入（CALIBRATION_MISSING）判断",
        )
    )


def _check_frame_rate(
    streams: Sequence[EpisodeStream],
    blockers: list[ReadinessBlocker],
) -> float | None:
    """帧率必须对每一路流都可确定，且各机位一致（否则无法给出单一 frame_count）。"""
    if not streams:
        # 流缺失已单独登记；没有流就没有帧率，不再重复报同一条事实。
        return None
    unknown = [
        str(stream.asset_id)
        for stream in streams
        if stream.source_fps is None or stream.source_fps <= 0
    ]
    if unknown:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.FRAME_RATE_UNKNOWN,
                "机位没有声明有效帧率（source_fps），无法确定帧数",
                {"asset_ids": unknown},
            )
        )
        return None
    rates = sorted({float(stream.source_fps) for stream in streams if stream.source_fps})
    if len(rates) > 1:
        blockers.append(
            ReadinessBlocker(
                ReadinessBlockerCode.FRAME_RATE_INCONSISTENT,
                "各机位声明的帧率不一致，无法给出单一的 frame_count",
                {"source_fps": rates},
            )
        )
        return None
    return rates[0]


def _build_manifest(
    facts: EpisodeManifestFacts,
    streams: Sequence[EpisodeStream],
    *,
    time_base: Mapping[str, object],
    frame_count: int,
    provenance_source: str,
) -> dict[str, object]:
    """按 ``MANIFEST_REQUIRED_KEYS`` 组装 Manifest；顺序与取值必须确定。"""
    if not provenance_source.strip():
        raise DomainValidationError("provenance_source 必须是非空字符串")
    return {
        "schema_version": facts.canonical_schema_version,
        "episode_id": str(facts.episode_id),
        "episode_lineage_id": str(facts.episode_lineage_id),
        "episode_version": facts.episode_version,
        "project_id": str(facts.project_id),
        "code": facts.code,
        "task_id": None if facts.task_id is None else str(facts.task_id),
        "batch_id": None if facts.batch_id is None else str(facts.batch_id),
        "robot_id": None if facts.robot_id is None else str(facts.robot_id),
        "device_ids": [str(item) for item in facts.device_ids],
        "sensor_ids": [str(item) for item in facts.sensor_ids],
        # 时间统一纳秒整数，以字符串承载：跨 JSON/JS 无精度损失。
        "start_time": str(facts.start_time_ns),
        "end_time": str(facts.end_time_ns),
        "duration_ns": str(facts.duration_ns),
        "time_base": dict(time_base),
        "time_alignment": {
            "clock_source": time_base.get("clock_source"),
            "sync_method": time_base.get("sync_method"),
            "sync_status": time_base.get("sync_status"),
        },
        "calibration_snapshot": [dict(item) for item in facts.calibration_snapshot],
        "streams": [
            {
                "stream_id": str(stream.asset_id),
                "camera": stream.camera,
                "object_key": stream.object_key,
                "sha256": stream.sha256,
                "size_bytes": stream.size_bytes,
                "content_type": stream.content_type,
                "format": stream.format,
            }
            for stream in streams
        ],
        "raw_asset_ids": [str(stream.asset_id) for stream in streams],
        "provenance": {
            "pipeline_id": facts.pipeline_id,
            "pipeline_version": facts.pipeline_version,
            "source": provenance_source,
        },
        "quality": {"status": facts.quality_status, "issues": []},
        "frame_count": frame_count,
    }
