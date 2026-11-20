"""N4-011 多流时间 manifest：严格解析，不用默认值伪造事实。"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from app.domain.episodes.sync_drift import SyncDriftError
from app.domain.episodes.time_alignment_manifest import (
    TIME_ALIGNMENT_INPUT_SCHEMA_VERSION,
    parse_time_alignment_manifest,
)
from jsonschema import Draft202012Validator


def _valid() -> dict[str, object]:
    return {
        "schema_version": TIME_ALIGNMENT_INPUT_SCHEMA_VERSION,
        "time_unit": "ns",
        "groups": [
            {
                "group_key": "episode-001",
                "streams": [
                    {
                        "stream_id": "camera-main",
                        "start_time_ns": "1730000000000000000",
                        "end_time_ns": "1730000005000000000",
                        "frame_rate": {"num": 30000, "den": 1001},
                        "declared_frame_count": 150,
                        "time_domain": "UTC",
                        "clock_source": "PTP",
                        "clock_id": "ptp0",
                        "drift_ppm": 1.5,
                    }
                ],
            }
        ],
    }


def test_parses_exact_nanoseconds_and_reuses_sync_value_objects() -> None:
    manifest = parse_time_alignment_manifest(_valid())

    assert manifest.schema_version == TIME_ALIGNMENT_INPUT_SCHEMA_VERSION
    assert manifest.time_unit == "ns"
    group = manifest.groups[0]
    assert group.group_key == "episode-001"
    assert group.windows[0].start_time_ns == 1_730_000_000_000_000_000
    assert group.windows[0].end_time_ns == 1_730_000_005_000_000_000
    profile = group.profiles["camera-main"]
    assert profile.frame_rate is not None
    assert profile.frame_rate.num == 30000
    assert profile.frame_rate.den == 1001
    assert profile.clock_source == "PTP"
    assert profile.drift_ppm == 1.5


def test_empty_groups_is_valid_and_does_not_invent_a_group() -> None:
    payload = _valid()
    payload["groups"] = []

    manifest = parse_time_alignment_manifest(payload)

    assert manifest.groups == ()


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), "ddp.processing.time-alignment-input/2.0.0"),
        (("time_unit",), "ms"),
        (("groups", 0, "streams", 0, "start_time_ns"), 1730000000000000000),
        (("groups", 0, "streams", 0, "start_time_ns"), "01"),
        (("groups", 0, "streams", 0, "end_time_ns"), "9223372036854775808"),
        (("groups", 0, "streams", 0, "time_domain"), "GPS"),
        (("groups", 0, "streams", 0, "clock_source"), "GPS_CLOCK"),
        (("groups", 0, "streams", 0, "frame_rate", "num"), 0),
        (("groups", 0, "streams", 0, "declared_frame_count"), -1),
        (("groups", 0, "streams", 0, "drift_ppm"), float("inf")),
    ],
)
def test_rejects_non_contract_values(path: tuple[object, ...], value: object) -> None:
    payload = _valid()
    target: object = payload
    for key in path[:-1]:
        target = target[key]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]

    with pytest.raises(SyncDriftError):
        parse_time_alignment_manifest(payload)


def test_missing_optional_facts_remain_unknown() -> None:
    payload = _valid()
    stream = payload["groups"][0]["streams"][0]  # type: ignore[index]
    for key in (
        "frame_rate",
        "declared_frame_count",
        "time_domain",
        "clock_source",
        "clock_id",
        "drift_ppm",
    ):
        stream.pop(key)

    group = parse_time_alignment_manifest(payload).groups[0]
    profile = group.profiles["camera-main"]
    assert profile.frame_rate is None
    assert profile.declared_frame_count is None
    assert profile.time_domain is None
    assert profile.clock_source is None
    assert profile.clock_id is None
    assert profile.drift_ppm is None


def test_rejects_duplicate_group_and_stream_keys() -> None:
    duplicate_group = _valid()
    duplicate_group["groups"].append(copy.deepcopy(duplicate_group["groups"][0]))  # type: ignore[union-attr,index]
    with pytest.raises(SyncDriftError, match="group_key"):
        parse_time_alignment_manifest(duplicate_group)

    duplicate_stream = _valid()
    streams = duplicate_stream["groups"][0]["streams"]  # type: ignore[index]
    streams.append(copy.deepcopy(streams[0]))
    with pytest.raises(SyncDriftError, match="stream_id"):
        parse_time_alignment_manifest(duplicate_stream)


def test_rejects_non_increasing_window_and_unknown_properties() -> None:
    invalid_window = _valid()
    stream = invalid_window["groups"][0]["streams"][0]  # type: ignore[index]
    stream["end_time_ns"] = stream["start_time_ns"]
    with pytest.raises(SyncDriftError, match="start_time_ns"):
        parse_time_alignment_manifest(invalid_window)

    extra = _valid()
    extra["groups"][0]["streams"][0]["start_time"] = 0  # type: ignore[index]
    with pytest.raises(SyncDriftError, match="未知字段"):
        parse_time_alignment_manifest(extra)


def test_contract_schema_is_closed_and_matches_parser_constants() -> None:
    schema_path = (
        Path(__file__).parents[3]
        / "packages/contracts/schemas/processing-time-alignment-input.v1.schema.json"
    )
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    assert schema["properties"]["schema_version"]["const"] == TIME_ALIGNMENT_INPUT_SCHEMA_VERSION
    assert schema["properties"]["time_unit"]["const"] == "ns"
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["group"]["additionalProperties"] is False
    assert schema["$defs"]["stream"]["additionalProperties"] is False
    assert schema["$defs"]["nanoseconds"]["type"] == "string"

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    assert list(validator.iter_errors(_valid())) == []

    unsafe_number = _valid()
    unsafe_number["groups"][0]["streams"][0]["start_time_ns"] = 1_730_000_000_000_000_000  # type: ignore[index]
    assert list(validator.iter_errors(unsafe_number))
