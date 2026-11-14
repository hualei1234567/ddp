"""LeRobot 预览元数据解析测试：用真实 Human-1M 结构，不联网。"""

from __future__ import annotations

import pytest
from ddp_data.lerobot import LeRobotParseError, parse_preview_metadata


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "file_name": "videos/demo_001_0b4a67b0_main.mp4",
        "example_id": "demo_001",
        "task_name": "Sew lace fabric",
        "instruction": "The worker repeatedly sews white lace fabric.",
        "view": "Egocentric main",
        "setting": "itw",
        "modality": "stereo",
        "has_wrist": False,
        "environment": "Industrial workspace",
        "episode_seconds": 237.77,
        "source_fps": 30.0,
        "excerpt_start_seconds": 69.0,
        "excerpt_seconds": 12.0,
    }
    base.update(overrides)
    return base


def test_groups_multiple_cameras_into_one_episode() -> None:
    rows = [
        _row(),
        _row(file_name="videos/demo_001_right.mp4", view="Egocentric right"),
        _row(file_name="videos/demo_001_wl.mp4", view="Left wrist"),
    ]

    episodes = parse_preview_metadata(rows)

    assert len(episodes) == 1
    assert [a.camera for a in episodes[0].media_assets] == ["main", "right", "wrist_left"]


def test_distinct_example_ids_become_distinct_episodes() -> None:
    rows = [_row(), _row(example_id="demo_002", file_name="videos/demo_002_main.mp4")]

    assert len(parse_preview_metadata(rows)) == 2


def test_unknown_view_is_preserved_as_slug_not_dropped() -> None:
    """已知机位之外的新机位不能静默丢失。"""
    episodes = parse_preview_metadata([_row(view="Overhead Ceiling")])

    assert episodes[0].media_assets[0].camera == "overhead_ceiling"
    assert episodes[0].media_assets[0].view_raw == "Overhead Ceiling"


def test_duplicate_path_in_same_episode_is_rejected() -> None:
    """重复会让审看列表出现重复项，很难在 UI 上排查。"""
    with pytest.raises(LeRobotParseError, match="重复出现"):
        parse_preview_metadata([_row(), _row()])


def test_missing_example_id_is_rejected() -> None:
    row = _row()
    del row["example_id"]

    with pytest.raises(LeRobotParseError, match="example_id"):
        parse_preview_metadata([row])


def test_missing_file_name_is_rejected() -> None:
    row = _row()
    del row["file_name"]

    with pytest.raises(LeRobotParseError, match="file_name"):
        parse_preview_metadata([row])


def test_blank_task_name_is_rejected() -> None:
    with pytest.raises(LeRobotParseError, match="task_name"):
        parse_preview_metadata([_row(task_name="   ")])


def test_negative_seconds_is_rejected() -> None:
    with pytest.raises(LeRobotParseError, match="不能为负"):
        parse_preview_metadata([_row(excerpt_start_seconds=-1.0)])


def test_bool_where_number_expected_is_rejected() -> None:
    """Python 里 True == 1，不显式排除会让布尔混进时长。"""
    with pytest.raises(LeRobotParseError, match="布尔"):
        parse_preview_metadata([_row(source_fps=True)])


def test_missing_optional_fields_become_none_not_defaults() -> None:
    """元数据没有的字段必须留空，不能编默认值。"""
    row = _row()
    for key in ("environment", "setting", "episode_seconds"):
        row.pop(key, None)

    episode = parse_preview_metadata([row])[0]

    assert episode.environment is None
    assert episode.setting is None
    assert episode.episode_seconds is None


def test_raw_row_is_kept_verbatim() -> None:
    """按 docs/ddp/07 §4：保留原始元数据，不因映射而丢弃来源信息。"""
    row = _row(custom_field="keep-me")

    assert parse_preview_metadata([row])[0].raw["custom_field"] == "keep-me"


def test_empty_metadata_is_rejected() -> None:
    with pytest.raises(LeRobotParseError, match="为空"):
        parse_preview_metadata([])


def test_preserves_first_appearance_order() -> None:
    rows = [
        _row(example_id="b_demo", file_name="v/b.mp4"),
        _row(example_id="a_demo", file_name="v/a.mp4"),
    ]

    assert [e.episode_key for e in parse_preview_metadata(rows)] == ["b_demo", "a_demo"]
