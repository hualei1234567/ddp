"""Canonical Schema v1：版本化只读数据契约（DDP-V21-N3-001）。

用法::

    from ddp_data.canonical import Episode, project_episode
    from ddp_data.adapters import parse_human1m_preview

    legacy = parse_human1m_preview(raw_bytes)[0]
    projection = project_episode(legacy)
    projection.episode.to_dict()

本包不建表、不做 IO、不依赖 backend；缺失字段通过 ``missing_fields`` 显式登记。
"""

from ddp_data.canonical.projection import (
    EpisodeProjection,
    project_episode,
    projection_to_dict,
)
from ddp_data.canonical.v1 import (
    ACTION_FIELDS,
    EPISODE_FIELDS,
    FRAME_CONVENTIONS,
    FRAME_FIELDS,
    LEGACY_EPISODE_FIELD_MAPPING,
    OBSERVATION_FIELDS,
    SCHEMA_VERSION,
    SENSOR_STREAM_FIELDS,
    SUPPORTED_SCHEMA_VERSIONS,
    VIDEO_STREAM_FIELDS,
    Action,
    CanonicalFieldError,
    CanonicalSchemaError,
    CanonicalSchemaVersionError,
    Episode,
    FieldSpec,
    Frame,
    Observation,
    SensorStream,
    VideoStream,
    declared_missing_fields,
    ensure_supported_version,
)

__all__ = [
    "ACTION_FIELDS",
    "EPISODE_FIELDS",
    "FRAME_CONVENTIONS",
    "FRAME_FIELDS",
    "LEGACY_EPISODE_FIELD_MAPPING",
    "OBSERVATION_FIELDS",
    "SCHEMA_VERSION",
    "SENSOR_STREAM_FIELDS",
    "SUPPORTED_SCHEMA_VERSIONS",
    "VIDEO_STREAM_FIELDS",
    "Action",
    "CanonicalFieldError",
    "CanonicalSchemaError",
    "CanonicalSchemaVersionError",
    "Episode",
    "EpisodeProjection",
    "FieldSpec",
    "Frame",
    "Observation",
    "SensorStream",
    "VideoStream",
    "declared_missing_fields",
    "ensure_supported_version",
    "project_episode",
    "projection_to_dict",
]
