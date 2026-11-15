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

from app.domain.datasources.media import MediaAsset
from app.domain.datasources.review import QualityFinding, QualityReport
from app.domain.episodes.sync_drift import (
    StreamWindow,
    SyncDriftStatus,
    detect_sync_drift,
)

RULE_VERSION = "1.1.0"
_REQUIRED_CAMERA = "main"


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
    """多路机位的时间同步偏差（N3）。

    逐 Episode 检测；**只有全部 Episode 都明确 ALIGNED 才算通过**。
    以下情况一律不算通过（失败关闭）：

    - ``UNKNOWN``：该 Episode 没有可比较的机位区间；
    - ``SINGLE_STREAM``：只有一路，谈不上"同步"；
    - ``EXCEEDED``：偏差超阈值。

    把"没检查到"当成"检查通过"会让质量报告说谎——这正是本规则存在的意义。
    """
    by_episode: dict[str, list[MediaAsset]] = {}
    for asset in assets:
        by_episode.setdefault(asset.episode_key, []).append(asset)

    problems: list[str] = []
    checked = 0
    for episode_key in sorted(by_episode):
        windows = [
            StreamWindow(
                stream_id=asset.camera,
                start_time_ns=int((asset.excerpt_start_seconds or 0.0) * 1_000_000_000),
                end_time_ns=int(
                    ((asset.excerpt_start_seconds or 0.0) + (asset.excerpt_seconds or 1.0))
                    * 1_000_000_000
                ),
            )
            for asset in by_episode[episode_key]
        ]
        # 同 Episode 内机位重复会让 StreamWindow 校验失败；按机位去重，
        # 保留第一条（导入链路本身已保证同一导入内 path 唯一）。
        unique: dict[str, StreamWindow] = {}
        for window in windows:
            unique.setdefault(window.stream_id, window)
        report = detect_sync_drift(list(unique.values()))
        checked += 1
        if report.status is not SyncDriftStatus.ALIGNED:
            problems.append(f"{episode_key}:{report.status.value}")

    if not checked:
        return QualityFinding(
            rule_id="stream_sync_aligned",
            passed=False,
            detail="没有可检测的 Episode",
        )
    return QualityFinding(
        rule_id="stream_sync_aligned",
        passed=not problems,
        detail=(
            f"{checked} 个片段的机位时间同步均在容忍范围内"
            if not problems
            else f"{len(problems)} 个片段同步未通过：{problems[:5]}"
        ),
    )
