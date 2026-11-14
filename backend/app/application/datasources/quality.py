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

RULE_VERSION = "1.0.0"
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

    return QualityReport(
        import_id=import_id,
        project_id=project_id,
        rule_version=RULE_VERSION,
        sample_scope=sample_scope,
        total_assets=len(assets),
        total_episodes=len(episodes),
        findings=tuple(findings),
    )
