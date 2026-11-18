"""EpisodeListFilters 与 JSON 数组谓词的单元测试（不碰数据库）。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.domain.episodes.enums import EpisodeAnnotationStatus, EpisodeQualityStatus
from app.domain.episodes.errors import DomainValidationError
from app.domain.episodes.filters import EpisodeListFilters
from app.infrastructure.db.models.episodes import EpisodeModel
from app.infrastructure.repositories._json_array_predicates import json_array_any_of
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)


def test_empty_filters_report_is_empty() -> None:
    assert EpisodeListFilters().is_empty
    assert EpisodeListFilters(scene_labels=()).is_empty
    assert not EpisodeListFilters(quality_status="PASS").is_empty
    assert not EpisodeListFilters(scene_labels=("kitchen",)).is_empty


def test_enum_values_are_normalized_and_rejected() -> None:
    filters = EpisodeListFilters(quality_status="PASS", annotation_status="APPROVED")
    assert filters.quality_status is EpisodeQualityStatus.PASS
    assert filters.annotation_status is EpisodeAnnotationStatus.APPROVED

    with pytest.raises(DomainValidationError):
        EpisodeListFilters(quality_status="UNKNOWN")
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(annotation_status="DONE")


def test_scene_labels_reject_empty_and_oversized_input() -> None:
    assert EpisodeListFilters(scene_labels="kitchen").scene_labels == ("kitchen",)
    assert EpisodeListFilters(scene_labels=["a", "a", "b"]).scene_labels == ("a", "b")
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(scene_labels=[""])
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(scene_labels=["x" * 101])
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(scene_labels=[f"s{index}" for index in range(21)])
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(scene_labels=[1])  # type: ignore[list-item]


def test_uuid_fields_normalize_from_strings() -> None:
    device = uuid4()
    filters = EpisodeListFilters(device_id=str(device), robot_id=device)
    assert filters.device_id == device
    assert filters.robot_id == device
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(device_id="not-a-uuid")


def test_camera_is_trimmed_validated_and_counted_as_filter() -> None:
    assert EpisodeListFilters().is_empty
    assert EpisodeListFilters(camera="  main  ").camera == "main"
    # 大小写是机位标识本身的一部分，不做静默折叠。
    assert EpisodeListFilters(camera="Main").camera == "Main"
    assert not EpisodeListFilters(camera="main").is_empty

    with pytest.raises(DomainValidationError):
        EpisodeListFilters(camera="")
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(camera="   ")
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(camera="x" * 65)
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(camera=1)  # type: ignore[arg-type]


def test_captured_range_requires_timezone_and_ordered_bounds() -> None:
    naive = datetime(2026, 11, 16, 12, 0)
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(captured_from=naive)
    with pytest.raises(DomainValidationError):
        EpisodeListFilters(captured_from=NOW, captured_to=NOW - timedelta(days=1))
    # 相等边界合法（闭区间）。
    assert EpisodeListFilters(captured_from=NOW, captured_to=NOW).captured_to == NOW


def test_json_array_any_of_renders_expansion_per_dialect() -> None:
    def compiled(dialect: str, sqlalchemy_dialect: object) -> str:
        statement = select(EpisodeModel.id).where(
            json_array_any_of(EpisodeModel.scene_labels, ("kitchen", "street"), dialect=dialect)
        )
        compiled = statement.compile(
            dialect=sqlalchemy_dialect, compile_kwargs={"literal_binds": True}
        )
        return " ".join(str(compiled).split()).lower()

    sqlite_sql = compiled("sqlite", sqlite.dialect())
    assert "json_each(episodes.scene_labels)" in sqlite_sql
    assert "exists" in sqlite_sql
    assert "'kitchen'" in sqlite_sql and "'street'" in sqlite_sql

    pg_sql = compiled("postgresql", postgresql.dialect())
    assert "jsonb_array_elements_text(episodes.scene_labels)" in pg_sql
    assert "exists" in pg_sql


def test_json_array_any_of_rejects_empty_values_and_unknown_dialect() -> None:
    with pytest.raises(ValueError):
        json_array_any_of(EpisodeModel.scene_labels, (), dialect="sqlite")
    with pytest.raises(RuntimeError):
        json_array_any_of(EpisodeModel.scene_labels, ("a",), dialect="mysql")
