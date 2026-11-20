"""真实 DROID-100 元数据 → N4 时间 manifest → 同步分析。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from app.domain.episodes.sync_analysis import ClockDomainStatus, analyze_stream_sync
from app.domain.episodes.sync_drift import SyncDriftStatus
from app.domain.episodes.time_alignment_manifest import parse_time_alignment_manifest
from ddp_data.lerobot import LeRobotParseError, build_v3_time_alignment_manifest

_FIXTURE = Path(__file__).parents[1] / "fixtures/public/lerobot-droid-100/episode-000000.json"


def _source() -> dict[str, object]:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_real_droid_episode_builds_three_stream_aligned_manifest() -> None:
    source = _source()
    payload = build_v3_time_alignment_manifest(
        dataset_id=source["source"]["dataset_id"],  # type: ignore[index]
        info=source["info"],
        episode=source["episode"],
    )

    manifest = parse_time_alignment_manifest(payload)
    assert manifest.groups[0].group_key == "lerobot/droid_100:episode:0"
    assert len(manifest.groups[0].windows) == 3
    assert {item.stream_id for item in manifest.groups[0].windows} == {
        "observation.images.exterior_image_1_left",
        "observation.images.exterior_image_2_left",
        "observation.images.wrist_image_left",
    }
    assert manifest.groups[0].windows[0].end_time_ns == 11_066_667_000
    profiles = manifest.groups[0].profiles
    assert all(profile.time_domain == "UNKNOWN" for profile in profiles.values())
    assert all(profile.clock_source == "UNKNOWN" for profile in profiles.values())
    assert all(profile.declared_frame_count == 166 for profile in profiles.values())
    assert all(profile.frame_rate and profile.frame_rate.num == 15 for profile in profiles.values())

    report = analyze_stream_sync(manifest.groups[0].windows, profiles)
    assert report.status is SyncDriftStatus.ALIGNED
    assert report.clock_domain_status is ClockDomainStatus.UNKNOWN
    assert all(
        detail.correction is None or detail.correction.applied is False for detail in report.streams
    )


def test_real_droid_time_difference_is_preserved_not_forced_aligned() -> None:
    source = _source()
    episode = copy.deepcopy(source["episode"])
    episode["videos/observation.images.wrist_image_left/from_timestamp"] = 0.1
    payload = build_v3_time_alignment_manifest(
        dataset_id="lerobot/droid_100",
        info=source["info"],
        episode=episode,
    )
    group = parse_time_alignment_manifest(payload).groups[0]

    report = analyze_stream_sync(group.windows, group.profiles)

    assert report.status is SyncDriftStatus.EXCEEDED
    assert "START_OFFSET_EXCEEDED" in report.issues


def test_ieee_float_noise_below_one_thousandth_ns_is_normalized() -> None:
    source = _source()
    episode = copy.deepcopy(source["episode"])
    episode["videos/observation.images.wrist_image_left/to_timestamp"] = 1699.0000000000005

    payload = build_v3_time_alignment_manifest(
        dataset_id="lerobot/droid_100",
        info=source["info"],
        episode=episode,
    )

    wrist = payload["groups"][0]["streams"][2]  # type: ignore[index]
    assert wrist["end_time_ns"] == "1699000000000"


def test_missing_video_fps_stays_unknown() -> None:
    source = _source()
    info = copy.deepcopy(source["info"])
    feature = info["features"]["observation.images.wrist_image_left"]
    feature["video_info"].pop("video.fps")
    payload = build_v3_time_alignment_manifest(
        dataset_id="lerobot/droid_100",
        info=info,
        episode=source["episode"],
    )
    group = parse_time_alignment_manifest(payload).groups[0]

    assert group.profiles["observation.images.wrist_image_left"].frame_rate is None


def test_missing_per_video_timestamp_is_rejected() -> None:
    source = _source()
    episode = copy.deepcopy(source["episode"])
    episode.pop("videos/observation.images.wrist_image_left/to_timestamp")

    with pytest.raises(LeRobotParseError, match="to_timestamp"):
        build_v3_time_alignment_manifest(
            dataset_id="lerobot/droid_100",
            info=source["info"],
            episode=episode,
        )


@pytest.mark.parametrize("value", [True, float("nan"), float("inf"), 0.0000000001])
def test_invalid_or_sub_nanosecond_timestamp_is_rejected(value: object) -> None:
    source = _source()
    episode = copy.deepcopy(source["episode"])
    episode["videos/observation.images.wrist_image_left/from_timestamp"] = value

    with pytest.raises(LeRobotParseError, match="timestamp"):
        build_v3_time_alignment_manifest(
            dataset_id="lerobot/droid_100",
            info=source["info"],
            episode=episode,
        )


def test_no_video_features_is_rejected() -> None:
    source = _source()
    info = copy.deepcopy(source["info"])
    info["features"] = {"timestamp": {"dtype": "float32"}}

    with pytest.raises(LeRobotParseError, match="video"):
        build_v3_time_alignment_manifest(
            dataset_id="lerobot/droid_100",
            info=info,
            episode=source["episode"],
        )
