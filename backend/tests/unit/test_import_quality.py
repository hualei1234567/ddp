"""质量规则测试：报告必须带规则版本、样本范围与失败原因。"""

from __future__ import annotations

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


def test_single_camera_does_not_count_as_aligned() -> None:
    """单机位谈不上"同步"，不能报通过——否则质量报告把"没检查"说成"通过"。"""
    rule = _sync_rule(_report([_asset(camera="main")]))
    assert rule.passed is False
    assert "SINGLE_STREAM" in rule.detail


def test_empty_assets_fails_sync_rule() -> None:
    rule = _sync_rule(_report([]))
    assert rule.passed is False


def test_sync_rule_is_versioned_with_other_rules() -> None:
    """新增规则必须随之升 RULE_VERSION，否则历史判定不可追溯。"""
    assert RULE_VERSION == "1.1.0"
