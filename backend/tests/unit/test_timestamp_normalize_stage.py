"""TIMESTAMP_NORMALIZE 阶段处理器测试（V6.1）。

覆盖：
- 三类输入分别得到正确的 ``normalize_method``（纯函数层）；
- 处理器端到端落库：``SECONDS_IDENTITY`` / ``RAW_PARSED`` / ``NONE``；
- 两类原始时间都缺（NULL 输入）不崩，归一化值保持 NULL 且**留痕**；
- 时间基非法（缺失 / 格式错误 / 分母非正）→ 显式 ``StageFailure``，行不被改写；
- ``default_handlers`` 接线：未注入时仍 SKIPPED 并保留原因，注入后生效。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.application.processing.ports import InputObjectInspector
from app.application.processing.stages import (
    STAGE_SKIP_REASONS,
    StageFailure,
    StageHandler,
    default_handlers,
)
from app.application.processing.timestamp_normalize_stage import (
    METHOD_NONE,
    METHOD_RAW_PARSED,
    METHOD_SECONDS_IDENTITY,
    TimestampNormalizeHandler,
    normalize_frame_timestamp,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
from app.infrastructure.db.models.video_frame import VideoFrameModel
from sqlalchemy.orm import Session

NOW = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)


# ---------------------------------------------------------------- 纯函数层


def test_pts_seconds_present_yields_seconds_identity() -> None:
    """有 pts_seconds：归一化值 = pts，方法显式声明"什么都没做"。"""
    result = normalize_frame_timestamp(Decimal("0.125"), "0.125", "1/10")

    assert result.method == METHOD_SECONDS_IDENTITY
    assert result.normalized_seconds == Decimal("0.125")


def test_pts_seconds_null_with_parseable_raw_yields_raw_parsed() -> None:
    """pts 为 NULL 但原文可解析：按时间基换算成秒，记 RAW_PARSED。"""
    # 原文是 1500 个 tick，时间基 1/1000（每 tick 1ms）→ 1.5 秒
    result = normalize_frame_timestamp(None, "1500", "1/1000")

    assert result.method == METHOD_RAW_PARSED
    assert result.normalized_seconds == Decimal("1.5")


def test_both_timestamps_missing_yields_none() -> None:
    """两类原始时间都缺：方法 NONE，归一化值保持 NULL。"""
    result = normalize_frame_timestamp(None, None, "1/10")

    assert result.method == METHOD_NONE
    assert result.normalized_seconds is None


def test_raw_unparseable_or_empty_is_not_silently_skipped() -> None:
    """原文为空 / 不可解析：不得悄悄归一化，一律如实报错或留痕。"""
    # 空串等同缺失 → NONE 留痕，不崩
    empty = normalize_frame_timestamp(None, "  ", "1/10")
    assert empty.method == METHOD_NONE and empty.normalized_seconds is None
    # 非数值原文 → 显式报错，绝不按 0 或跳过处理
    with pytest.raises(ValueError):
        normalize_frame_timestamp(None, "not-a-number", "1/10")


def test_invalid_time_base_raises_explicitly() -> None:
    """时间基缺失 / 格式非法 / 分母非正 → 显式报错（不猜兜底值）。"""
    for bad_time_base in (None, "1000", "1/0", "abc/1"):
        with pytest.raises(ValueError):
            normalize_frame_timestamp(None, "1500", bad_time_base)


# ---------------------------------------------------------------- 处理器层


class _FakeRun:
    """最小运行对象：只需 input_asset_ids。"""

    def __init__(self, asset_ids: tuple[object, ...] = ()) -> None:
        self.input_asset_ids = asset_ids


def _context(run: _FakeRun, project_id: object) -> object:
    """构造阶段上下文（scope 的 project_id 必须与库里那条 project 行一致）。"""
    from app.application.processing.stages import StageContext

    return StageContext(
        run=run,  # type: ignore[arg-type]
        scope=ProjectScope(project_id=project_id, actor_id=uuid4()),  # type: ignore[arg-type]
        now=NOW,
    )


def _add_frame(
    session: Session,
    project_id: object,
    asset_id: object,
    frame_index: int,
    *,
    pts_seconds: Decimal | None = None,
    timestamp_raw: str | None = None,
    time_base: str | None = "1/1000",
) -> VideoFrameModel:
    row = VideoFrameModel(
        id=uuid4(),
        project_id=project_id,
        asset_id=asset_id,
        stream_id="front",
        frame_index=frame_index,
        pts_seconds=pts_seconds,
        timestamp_raw=timestamp_raw,
        time_base=time_base,
        # 初始归一化列为 NULL（与 VIDEO_DECODE 落库后的形态一致）
        timestamp_normalized_seconds=None,
        normalize_method=None,
        is_keyframe=True,
        decode_status="OK",
        parser_version="ddp.video_decode/1.0.0",
    )
    session.add(row)
    session.commit()
    return row


@pytest.fixture
def session_and_project() -> tuple[Session, object]:
    """真实 SQLite 会话 + 一个项目行（video_frames 有项目外键）。

    **StaticPool 必需**：SQLite 的 ``:memory:`` 是每连接一个独立库，
    不用 StaticPool 时夹具建表用的连接与后续 session 不是同一个。
    """
    import app.infrastructure.db.models  # noqa: F401
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.models.identity import Project
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    project = Project(id=uuid4(), code="v61-ts", name="V6.1 时间归一化测试")
    session.add(project)
    session.commit()
    try:
        yield session, project
    finally:
        session.close()
        engine.dispose()


def test_handler_persists_all_three_methods(session_and_project: tuple) -> None:
    """三类行一次处理：各自得到正确的 normalize_method 与归一化值。"""
    session, project = session_and_project
    asset_id = uuid4()
    rows = {
        0: _add_frame(session, project.id, asset_id, 0, pts_seconds=Decimal("0.25")),
        1: _add_frame(
            session,
            project.id,
            asset_id,
            1,
            timestamp_raw="1500",
            time_base="1/1000",
        ),
        2: _add_frame(session, project.id, asset_id, 2),  # 两类原始时间都缺
    }
    handler = TimestampNormalizeHandler(session)

    outcome = handler(_context(_FakeRun((asset_id,)), project.id))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "SECONDS_IDENTITY" in outcome.log_summary and "RAW_PARSED" in outcome.log_summary
    session.refresh(rows[0])
    session.refresh(rows[1])
    session.refresh(rows[2])
    assert rows[0].normalize_method == METHOD_SECONDS_IDENTITY
    assert rows[0].timestamp_normalized_seconds == Decimal("0.25")
    assert rows[1].normalize_method == METHOD_RAW_PARSED
    assert rows[1].timestamp_normalized_seconds == Decimal("1.5")
    assert rows[2].normalize_method == METHOD_NONE
    assert rows[2].timestamp_normalized_seconds is None


def test_handler_null_inputs_do_not_crash_and_leave_trace(session_and_project: tuple) -> None:
    """NULL 输入不崩：NONE 行保持 NULL 归一化值，且汇总里留痕、不静默跳过。"""
    session, project = session_and_project
    asset_id = uuid4()
    none_row = _add_frame(session, project.id, asset_id, 0)
    handler = TimestampNormalizeHandler(session)

    outcome = handler(_context(_FakeRun((asset_id,)), project.id))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert METHOD_NONE in outcome.log_summary
    assert "NULL" in outcome.log_summary
    session.refresh(none_row)
    assert none_row.normalize_method == METHOD_NONE
    assert none_row.timestamp_normalized_seconds is None


def test_handler_invalid_time_base_fails_explicitly_and_leaves_rows_untouched(
    session_and_project: tuple,
) -> None:
    """时间基非法 → 显式 StageFailure，帧行不被改写（不猜兜底值）。"""
    session, project = session_and_project
    asset_id = uuid4()
    bad_row = _add_frame(
        session,
        project.id,
        asset_id,
        0,
        timestamp_raw="1500",
        time_base="garbage",
    )
    handler = TimestampNormalizeHandler(session)

    with pytest.raises(StageFailure) as exc_info:
        handler(_context(_FakeRun((asset_id,)), project.id))

    assert exc_info.value.error_code == "timestamp_time_base_invalid"
    assert exc_info.value.retryable is False
    session.refresh(bad_row)
    assert bad_row.normalize_method is None
    assert bad_row.timestamp_normalized_seconds is None


def test_handler_skips_already_normalized_and_other_assets(session_and_project: tuple) -> None:
    """只处理"未归一化且属于本次运行"的行：已归一化的行与别的资产的行不动。"""
    session, project = session_and_project
    asset_id = uuid4()
    other_asset = uuid4()
    done_row = _add_frame(session, project.id, asset_id, 0, pts_seconds=Decimal("1.0"))
    done_row.timestamp_normalized_seconds = Decimal("1.0")
    done_row.normalize_method = METHOD_SECONDS_IDENTITY
    session.commit()
    pending_row = _add_frame(session, project.id, asset_id, 1, pts_seconds=Decimal("2.0"))
    foreign_row = _add_frame(session, project.id, other_asset, 0, pts_seconds=Decimal("3.0"))

    handler = TimestampNormalizeHandler(session)
    outcome = handler(_context(_FakeRun((asset_id,)), project.id))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    session.refresh(done_row)
    session.refresh(pending_row)
    session.refresh(foreign_row)
    assert done_row.timestamp_normalized_seconds == Decimal("1.0")  # 未被重复改写
    assert pending_row.timestamp_normalized_seconds == Decimal("2.0")
    assert foreign_row.normalize_method is None  # 别的资产的行不碰


def test_handler_skips_when_no_pending_rows(session_and_project: tuple) -> None:
    """没有待归一化行（未登记资产 / 均已归一化）→ 显式 SKIPPED 而非静默成功。"""
    session, project = session_and_project
    handler = TimestampNormalizeHandler(session)

    outcome = handler(_context(_FakeRun(()), project.id))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "跳过" in outcome.log_summary


# ---------------------------------------------------------------- 接线层


class _RecordingHandler:
    """记录被调用的假处理器。"""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, context: object) -> object:
        from app.application.processing.stages import StageOutcome

        self.calls.append(context)
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary="注入的处理器被调用",
        )


def _stub_handler() -> StageHandler:
    return _RecordingHandler()


def test_default_handlers_timestamp_normalize_skipped_when_not_injected() -> None:
    """未注入时：TIMESTAMP_NORMALIZE 仍 SKIPPED，且原因与 STAGE_SKIP_REASONS 一致。"""

    handlers = default_handlers(
        _StubInspector(),
        metadata_extraction=_stub_handler(),
        format_parsing=_stub_handler(),
        time_alignment=_stub_handler(),
        calibration_validation=_stub_handler(),
        episode_segmentation=_stub_handler(),
        quality_check=_stub_handler(),
    )

    stage_handler = handlers[ProcessingStage.TIMESTAMP_NORMALIZE]
    assert isinstance(stage_handler, type(handlers[ProcessingStage.VIDEO_DECODE]))
    outcome = stage_handler(_context(_FakeRun(()), uuid4()))
    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert STAGE_SKIP_REASONS[ProcessingStage.TIMESTAMP_NORMALIZE] in outcome.log_summary


def test_default_handlers_timestamp_normalize_injected_takes_effect() -> None:
    """注入后：TIMESTAMP_NORMALIZE 用注入的处理器，不再走 SkippedStage。"""

    injected = _RecordingHandler()
    handlers = default_handlers(
        _StubInspector(),
        metadata_extraction=_stub_handler(),
        format_parsing=_stub_handler(),
        time_alignment=_stub_handler(),
        calibration_validation=_stub_handler(),
        episode_segmentation=_stub_handler(),
        quality_check=_stub_handler(),
        timestamp_normalize=injected,
    )

    outcome = handlers[ProcessingStage.TIMESTAMP_NORMALIZE](_context(_FakeRun(()), uuid4()))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert injected.calls, "注入的处理器未被调用"


class _StubInspector(InputObjectInspector):
    """不触碰对象存储的桩。"""

    def object_size(self, ref: str) -> int | None:  # pragma: no cover - 不会被调用
        return 0
