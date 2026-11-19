"""质量规则测试：报告必须带规则版本、样本范围与失败原因。"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from app.application.datasources.quality import RULE_VERSION, evaluate_import_quality
from app.domain.datasources.media import MediaAsset

_NOW = datetime(2026, 11, 15, tzinfo=UTC)


def _asset(**overrides: object) -> MediaAsset:
    base: dict[str, object] = {
        "asset_id": uuid4(),
        "project_id": uuid4(),
        "data_source_id": uuid4(),
        "import_id": uuid4(),
        "episode_key": "demo_001",
        "path": "videos/a.mp4",
        "camera": "main",
        "object_key": "k/a.mp4",
        "size_bytes": 1024,
        "sha256": "sha256:" + "a" * 64,
        "content_type": "video/mp4",
        "task_name": "任务",
        "created_at": _NOW,
        "excerpt_start_seconds": 1.0,
        "excerpt_seconds": 12.0,
        "source_fps": 30.0,
    }
    base.update(overrides)
    return MediaAsset(**base)  # type: ignore[arg-type]


def _report(assets: list[MediaAsset]):
    return evaluate_import_quality(
        project_id=uuid4(),
        import_id=uuid4(),
        sample_scope="SAMPLE",
        assets=assets,
    )


def test_healthy_import_passes_all_rules() -> None:
    report = _report(
        [
            _asset(),
            _asset(camera="right", path="videos/b.mp4"),
        ]
    )

    assert report.passed
    assert report.rule_version == RULE_VERSION
    assert report.sample_scope == "SAMPLE"
    assert report.total_assets == 2
    assert report.total_episodes == 1


def test_empty_import_fails_with_reason() -> None:
    report = _report([])

    assert not report.passed
    assert report.failures
    # 失败原因必须是可读事实，不是空数组。
    assert "未产出任何媒体资产" in report.failures[0].detail


def test_missing_main_camera_is_reported() -> None:
    report = _report([_asset(camera="wrist_left")])

    assert not report.passed
    rule = next(f for f in report.failures if f.rule_id == "main_camera_present")
    assert "缺少主视角" in rule.detail


def test_rule_version_is_always_present() -> None:
    """docs/ddp/07 §7：报告必须说明规则版本，否则无法复现判定。

    1.1.0 起新增 stream_sync_aligned（多路同步偏差）规则；规则集变化必须
    升版本，否则「同一批数据在旧规则下通过、新规则下不通过」无从追溯。
    """
    assert _report([_asset()]).rule_version == "1.1.0"


def test_payload_includes_failures_not_empty_array() -> None:
    payload = _report([]).to_payload()

    assert payload["passed"] is False
    assert payload["failures"] != []
    assert payload["rule_version"] == RULE_VERSION


# --------------------------------------------------------------------------
# N3：多路同步偏差规则 stream_sync_aligned
# --------------------------------------------------------------------------


def _sync_rule(report):
    return next(f for f in report.findings if f.rule_id == "stream_sync_aligned")


def test_aligned_cameras_pass_sync_rule() -> None:
    report = _report(
        [
            _asset(camera="main", path="a.mp4"),
            _asset(camera="wrist", path="b.mp4"),
        ]
    )
    assert _sync_rule(report).passed is True


def test_offset_beyond_tolerance_fails_sync_rule() -> None:
    report = _report(
        [
            _asset(camera="main", path="a.mp4", excerpt_start_seconds=1.0),
            _asset(camera="wrist", path="b.mp4", excerpt_start_seconds=1.5),
        ]
    )
    rule = _sync_rule(report)
    assert rule.passed is False
    assert "demo_001" in rule.detail


def test_single_camera_is_not_a_sync_failure() -> None:
    """单机位片段不是同步问题——它是"这个片段只有一路视频"。

    真实数据集里很常见（Human-1M 的 demo_009/011/012 就是单机位）。把它
    算作"同步未通过"会让整批报告失真。但文案必须如实说明"无可比较的多路
    同步"，不能伪装成"已通过检查"。
    """
    rule = _sync_rule(_report([_asset(camera="main")]))
    assert rule.passed is True
    assert "单机位" in rule.detail
    assert "无可比较" in rule.detail


def test_mixed_single_and_multi_stream_reports_both() -> None:
    """多机位对齐 + 单机位混合时，通过且文案点明单机位数量。"""
    report = _report(
        [
            _asset(camera="main", path="a.mp4"),
            _asset(camera="wrist_left", path="b.mp4"),
            _asset(episode_key="demo_002", camera="main", path="c.mp4"),
        ]
    )
    rule = _sync_rule(report)
    assert rule.passed is True
    assert "单机位" in rule.detail


def test_multi_stream_misaligned_still_fails_when_others_single() -> None:
    """单机位不判失败，但多机位超偏仍必须判失败。"""
    report = _report(
        [
            _asset(camera="main", path="a.mp4", excerpt_start_seconds=1.0),
            _asset(camera="wrist_left", path="b.mp4", excerpt_start_seconds=2.0),
            _asset(episode_key="demo_002", camera="main", path="c.mp4"),
        ]
    )
    rule = _sync_rule(report)
    assert rule.passed is False
    assert "demo_001" in rule.detail


def test_empty_assets_fails_sync_rule() -> None:
    rule = _sync_rule(_report([]))
    assert rule.passed is False


def test_sync_rule_is_versioned_with_other_rules() -> None:
    """新增规则必须随之升 RULE_VERSION，否则历史判定不可追溯。"""
    assert RULE_VERSION == "1.1.0"


# --------------------------------------------------------------------------
# N4-003：每流时钟/偏移/漂移/帧索引映射写进质量证据（不新增迁移）
# --------------------------------------------------------------------------


def _sync_evidence(report):
    rule = _sync_rule(report)
    assert rule.evidence is not None
    return rule.evidence


def test_sync_evidence_carries_per_stream_frame_index_mapping() -> None:
    report = _report(
        [
            _asset(camera="main", path="a.mp4", source_fps=30.0),
            _asset(camera="wrist", path="b.mp4", source_fps=30.0),
        ]
    )

    evidence = _sync_evidence(report)
    assert evidence["episodes_checked"] == 1
    episode = evidence["episodes"][0]
    assert episode["frame_index_status"] == "DERIVED"
    # 导入元数据没有逐流时钟声明：必须如实 UNKNOWN，不能伪造 PTP/NTP。
    assert episode["clock_domain_status"] == "UNKNOWN"
    assert episode["drift_status"] == "UNKNOWN"
    streams = {item["stream_id"]: item for item in episode["streams"]}
    assert streams["main"]["frame_rate"] == {"num": 30, "den": 1}
    # excerpt_seconds=12.0 @ 30fps = 360 帧
    assert streams["main"]["frame_count"] == 360
    assert streams["main"]["start_offset_frames"] == {
        "numerator": 0,
        "denominator": 1,
    }
    assert streams["main"]["recommended_trim_ns"] == 0


def test_sync_evidence_marks_legacy_assets_without_fps_as_unknown() -> None:
    """历史导入行没有 source_fps：帧索引显式 UNKNOWN，不拖垮既有同步结论。"""
    report = _report(
        [
            _asset(camera="main", path="a.mp4", source_fps=None),
            _asset(camera="wrist", path="b.mp4", source_fps=None),
        ]
    )

    rule = _sync_rule(report)
    assert rule.passed is True
    assert "帧索引不可完全推导" in rule.detail
    episode = _sync_evidence(report)["episodes"][0]
    assert episode["frame_index_status"] == "UNKNOWN"
    assert all(item["frame_count"] is None for item in episode["streams"])


def test_sync_evidence_is_bounded_and_marks_truncation() -> None:
    """证据列有 4000 字节上限：超限必须显式标记，不能静默丢弃。"""
    assets = [
        _asset(
            episode_key=f"demo_{index:03d}",
            camera=camera,
            path=f"demo_{index:03d}-{camera}.mp4",
            source_fps=30.0,
        )
        for index in range(40)
        for camera in ("main", "right", "wrist_left", "stereo")
    ]

    evidence = _sync_evidence(_report(assets))
    encoded = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    assert len(encoded.encode("utf-8")) <= 4000
    assert evidence["episodes_truncated"] is True
    assert evidence["episodes_checked"] == 40
