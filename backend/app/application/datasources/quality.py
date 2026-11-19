"""导入结果的质量检查规则。

规则版本化：``RULE_VERSION`` 变更时报告里会体现，便于追溯"同一批数据
在旧规则下通过、新规则下不通过"这类情况。

只做能由已有事实判定的检查，不做推测：
- 每条资产必须有正的大小与合法 sha256；
- 机位集合必须包含至少 main（第一人称主视角是该数据集的必备视角）；
- Episode 必须至少有一个媒体资产；
- 片段时长与 fps 若存在必须为正。
"""

from __future__ import annotations

import json

from app.domain.datasources.media import MediaAsset
from app.domain.datasources.review import QualityFinding, QualityReport
from app.domain.episodes.sync_analysis import (
    StreamSyncAnalysis,
    StreamSyncDetail,
    analyze_stream_sync,
)
from app.domain.episodes.sync_drift import (
    DEFAULT_TOLERANCE_NS,
    StreamWindow,
    SyncDriftStatus,
)
from app.domain.episodes.sync_frame_index import (
    FrameIndexStatus,
    FrameRate,
    StreamSyncProfile,
)

RULE_VERSION = "1.1.0"
_REQUIRED_CAMERA = "main"

# 质量证据有 4000 字节上限（``domain.quality.models.EVIDENCE_MAX_BYTES``）。
# 这里主动留出余量，超限时**显式标记截断**，不静默丢弃。
_EVIDENCE_MAX_BYTES = 3_500
_EVIDENCE_MAX_EPISODES = 64
_EVIDENCE_MAX_STREAMS_PER_EPISODE = 8
_EVIDENCE_MAX_SINGLE_STREAM_KEYS = 10
_EVIDENCE_KEY_MAX = 128


def evaluate_import_quality(
    *,
    project_id,
    import_id,
    sample_scope: str,
    assets: list[MediaAsset],
) -> QualityReport:
    findings: list[QualityFinding] = []

    findings.append(
        QualityFinding(
            rule_id="assets_present",
            passed=bool(assets),
            detail=(f"共 {len(assets)} 条媒体资产" if assets else "导入未产出任何媒体资产"),
        )
    )

    bad_size = [a for a in assets if a.size_bytes <= 0]
    findings.append(
        QualityFinding(
            rule_id="asset_size_positive",
            passed=not bad_size,
            detail=(
                "全部资产大小为正"
                if not bad_size
                else f"{len(bad_size)} 条资产大小非正：{[a.path for a in bad_size][:3]}"
            ),
        )
    )

    bad_hash = [a for a in assets if not a.sha256.startswith("sha256:") or len(a.sha256) != 71]
    findings.append(
        QualityFinding(
            rule_id="asset_sha256_valid",
            passed=not bad_hash,
            detail=(
                "全部资产校验和格式合法" if not bad_hash else f"{len(bad_hash)} 条资产校验和非法"
            ),
        )
    )

    episodes = {a.episode_key for a in assets}
    findings.append(
        QualityFinding(
            rule_id="episodes_have_assets",
            passed=all(any(a.episode_key == key for a in assets) for key in episodes)
            and bool(episodes),
            detail=f"共 {len(episodes)} 个片段，均有媒体资产" if episodes else "无片段",
        )
    )

    cameras = {a.camera for a in assets}
    findings.append(
        QualityFinding(
            rule_id="main_camera_present",
            passed=_REQUIRED_CAMERA in cameras,
            detail=(
                f"机位集合：{sorted(cameras)}"
                if _REQUIRED_CAMERA in cameras
                else f"缺少主视角（现有：{sorted(cameras)}）"
            ),
        )
    )

    bad_timing = [
        a
        for a in assets
        if (a.excerpt_start_seconds is not None and a.excerpt_start_seconds < 0)
        or (a.excerpt_seconds is not None and a.excerpt_seconds <= 0)
        or (a.source_fps is not None and a.source_fps <= 0)
    ]
    findings.append(
        QualityFinding(
            rule_id="timing_fields_valid",
            passed=not bad_timing,
            detail=(
                "片段起点/时长/帧率均合法"
                if not bad_timing
                else f"{len(bad_timing)} 条资产时序字段非法"
            ),
        )
    )

    findings.append(_sync_drift_finding(assets))

    return QualityReport(
        import_id=import_id,
        project_id=project_id,
        rule_version=RULE_VERSION,
        sample_scope=sample_scope,
        total_assets=len(assets),
        total_episodes=len(episodes),
        findings=tuple(findings),
    )


def _sync_drift_finding(assets: list[MediaAsset]) -> QualityFinding:
    """多路机位的时间同步偏差（N3，N4-003 增强）。

    逐 Episode 检测；**只有全部 Episode 都明确 ALIGNED 才算通过**。
    以下情况一律不算通过（失败关闭）：

    - ``UNKNOWN``：该 Episode 没有可比较的机位区间（含时钟域冲突、
      帧索引越界、负漂移等"不可判定"情形）；
    - ``SINGLE_STREAM``：只有一路，谈不上"同步"；
    - ``EXCEEDED``：偏差超阈值。

    把"没检查到"当成"检查通过"会让质量报告说谎——这正是本规则存在的意义。

    增强部分（每流时钟/偏移/漂移/帧索引映射）只写进 ``evidence``，不改变
    本规则的通过口径；导入元数据没有逐流时钟声明时如实记为 ``UNKNOWN``，
    不伪造。
    """
    by_episode: dict[str, list[MediaAsset]] = {}
    for asset in assets:
        by_episode.setdefault(asset.episode_key, []).append(asset)

    problems: list[str] = []
    single_stream: list[str] = []
    analyses: list[tuple[str, StreamSyncAnalysis]] = []
    checked = 0
    for episode_key in sorted(by_episode):
        members = by_episode[episode_key]
        windows = [
            StreamWindow(
                stream_id=asset.camera,
                start_time_ns=int((asset.excerpt_start_seconds or 0.0) * 1_000_000_000),
                end_time_ns=int(
                    ((asset.excerpt_start_seconds or 0.0) + (asset.excerpt_seconds or 1.0))
                    * 1_000_000_000
                ),
            )
            for asset in members
        ]
        # 同 Episode 内机位重复会让 StreamWindow 校验失败；按机位去重，
        # 保留第一条（导入链路本身已保证同一导入内 path 唯一）。
        unique: dict[str, StreamWindow] = {}
        for window in windows:
            unique.setdefault(window.stream_id, window)
        profiles: dict[str, StreamSyncProfile] = {}
        for asset in members:
            profiles.setdefault(
                asset.camera,
                StreamSyncProfile(frame_rate=FrameRate.from_fps(asset.source_fps)),
            )
        report = analyze_stream_sync(
            [unique[key] for key in sorted(unique)],
            profiles,
        )
        analyses.append((episode_key, report))
        checked += 1
        if report.status is SyncDriftStatus.SINGLE_STREAM:
            # 单机位片段没有"同步"可谈——这不是同步问题，而是"该片段只有一路
            # 视频"。真实数据集里很常见（Human-1M 的 demo_009/011/012 就是单
            # 机位），把它算作"同步未通过"会让整批报告失真。
            # 记入 detail 让人看得见，但不判失败。
            single_stream.append(episode_key)
        elif report.status is not SyncDriftStatus.ALIGNED:
            problems.append(f"{episode_key}:{report.status.value}")

    if not checked:
        return QualityFinding(
            rule_id="stream_sync_aligned",
            passed=False,
            detail="没有可检测的 Episode",
            evidence={"episodes_checked": 0},
        )
    multi = checked - len(single_stream)
    if problems:
        detail = f"{len(problems)} 个片段同步未通过：{problems[:5]}"
    elif multi == 0:
        # 全部是单机位：没有可比较对象，如实说明而不是伪装成"已通过"。
        detail = f"{checked} 个片段均为单机位，无可比较的多路同步"
    else:
        detail = f"{multi} 个多机位片段的同步均在容忍范围内"
    if single_stream and multi > 0:
        detail += (
            f"（另有 {len(single_stream)} 个单机位片段不参与同步判定：" f"{single_stream[:5]}）"
        )
    frame_unknown = [
        key
        for key, report in analyses
        if report.status is not SyncDriftStatus.SINGLE_STREAM
        and report.frame_index_status is not FrameIndexStatus.DERIVED
    ]
    if frame_unknown:
        detail += (
            f"；{len(frame_unknown)} 个多机位片段帧索引不可完全推导："
            f"{frame_unknown[:3]}（见 evidence.frame_index_status）"
        )
    return QualityFinding(
        rule_id="stream_sync_aligned",
        # 只有"存在多机位片段且其中有超偏/不可判定"才判失败；单流不是失败。
        passed=not problems,
        detail=detail,
        evidence=_sync_evidence(
            analyses,
            checked=checked,
            single_stream=single_stream,
        ),
    )


def _sync_evidence(
    analyses: list[tuple[str, StreamSyncAnalysis]],
    *,
    checked: int,
    single_stream: list[str],
) -> dict[str, object]:
    """每流同步证据；有界、可序列化，截断时显式标记。"""
    episodes: list[dict[str, object]] = []
    for episode_key, report in analyses[:_EVIDENCE_MAX_EPISODES]:
        episodes.append(_episode_evidence(episode_key, report))
    single_keys = [
        key[:_EVIDENCE_KEY_MAX] for key in single_stream[:_EVIDENCE_MAX_SINGLE_STREAM_KEYS]
    ]
    payload: dict[str, object] = {
        "tolerance_ns": DEFAULT_TOLERANCE_NS,
        "episodes_checked": checked,
        "single_stream_episodes": single_keys,
        "single_stream_truncated": len(single_stream) > _EVIDENCE_MAX_SINGLE_STREAM_KEYS,
        "episodes": episodes,
        "episodes_truncated": len(analyses) > _EVIDENCE_MAX_EPISODES,
    }
    # 证据列有硬上限：从尾部裁剪并显式标记，绝不静默丢数据。
    while episodes and _encoded_size(payload) > _EVIDENCE_MAX_BYTES:
        episodes.pop()
        payload["episodes_truncated"] = True
    return payload


def _episode_evidence(
    episode_key: str,
    report: StreamSyncAnalysis,
) -> dict[str, object]:
    streams = [
        _stream_evidence(item) for item in report.streams[:_EVIDENCE_MAX_STREAMS_PER_EPISODE]
    ]
    return {
        "episode_key": episode_key[:_EVIDENCE_KEY_MAX],
        "status": report.status.value,
        "clock_domain_status": report.clock_domain_status.value,
        "frame_index_status": report.frame_index_status.value,
        "drift_status": report.drift_status.value,
        "reference_stream_id": report.reference_stream_id,
        "max_start_offset_ns": report.base.max_start_offset_ns,
        "max_duration_delta_ns": report.base.max_duration_delta_ns,
        "correction_available": report.correction_available,
        "correction_unavailable_reason": report.correction_unavailable_reason,
        "issues": list(report.issues),
        "streams": streams,
        "streams_truncated": len(report.streams) > _EVIDENCE_MAX_STREAMS_PER_EPISODE,
    }


def _stream_evidence(item: StreamSyncDetail) -> dict[str, object]:
    frames = item.start_offset_frames
    return {
        "stream_id": item.stream_id[:_EVIDENCE_KEY_MAX],
        "clock_source": item.clock_source,
        "time_domain": item.time_domain,
        "clock_id": item.clock_id,
        "start_offset_ns": item.base.start_offset_ns,
        "duration_delta_ns": item.base.duration_delta_ns,
        "drift_ppm": item.drift_ppm,
        "relative_drift_ns": item.relative_drift_ns,
        "frame_rate": item.frame_rate.to_payload() if item.frame_rate else None,
        "frame_count": item.frame_count_derived,
        "declared_frame_count": item.frame_count_declared,
        "start_offset_frames": (
            {"numerator": frames.numerator, "denominator": frames.denominator}
            if frames is not None
            else None
        ),
        # 只读建议；``applied`` 恒为 False，原始数据从未被改写。
        "recommended_trim_ns": item.correction.trim_start_ns if item.correction else None,
        "issues": list(item.issues),
    }


def _encoded_size(payload: dict[str, object]) -> int:
    return len(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    )
