"""VIDEO_DECODE 阶段处理器测试（V6.1）。

覆盖：
- 无视频输入 → SKIPPED（显式原因，不是静默成功）；
- 有视频输入 → 帧索引写入 ``video_frames``，**原始时间**原样落库；
- 归一化列必须留空（归一化是 TIMESTAMP_NORMALIZE 阶段的职责）；
- 解码失败的帧也留痕（``DECODE_ERROR``），不静默跳过；
- 帧数超上限 → 显式失败（拒绝写入异常大的帧索引）。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.application.processing.video_decode_stage import (
    DecodedFrame,
    VideoDecodeHandler,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.steps import ProcessingStepStatus
from app.infrastructure.db.models.video_frame import VideoFrameModel
from sqlalchemy import select
from sqlalchemy.orm import Session

NOW = datetime(2026, 11, 28, 2, 0, tzinfo=UTC)


class FakeDecoder:
    """假解码器：返回固定帧列表，记录调用。"""

    def __init__(self, frames: tuple[DecodedFrame, ...] = ()) -> None:
        self.frames = frames
        self.calls: list[dict[str, object]] = []

    def decode(
        self,
        *,
        asset_id: object,
        storage_reference: str,
        stream_id: str,
    ) -> tuple[DecodedFrame, ...]:
        self.calls.append(
            {
                "asset_id": asset_id,
                "storage_reference": storage_reference,
                "stream_id": stream_id,
            }
        )
        return self.frames


def _frames() -> tuple[DecodedFrame, ...]:
    return (
        DecodedFrame(
            index=0,
            pts_time=0.0,
            pts_time_raw="0.000000",
            time_base="1/10",
            width=96,
            height=96,
            codec="av1",
            pixel_format="yuv420p",
            is_keyframe=True,
            decode_status="OK",
        ),
        DecodedFrame(
            index=1,
            pts_time=0.1,
            pts_time_raw="0.100000",
            time_base="1/10",
            width=96,
            height=96,
            codec="av1",
            pixel_format="yuv420p",
            is_keyframe=False,
            decode_status="DECODE_ERROR",
        ),
    )


class _FakeRun:
    """最小运行对象：只需 input_asset_ids。"""

    def __init__(self, asset_ids: tuple[object, ...] = ()) -> None:
        self.input_asset_ids = asset_ids


class FakeResolver:
    """假输入解析器：直接给视频输入列表（不碰数据源导入域的三层外键）。"""

    def __init__(self, videos: tuple[tuple[object, str, str], ...] = ()) -> None:
        self.videos = videos

    def resolve(self, run: object) -> tuple[tuple[object, str, str], ...]:
        return self.videos


def _context(run: _FakeRun, project_id: object) -> object:
    """构造阶段上下文。

    **scope 的 project_id 必须与库里那条 project 行一致**——否则写
    ``video_frames`` 会撞外键（实测踩到：scope 用随机 uuid 而库里是另一个）。
    """
    from app.application.processing.stages import StageContext

    return StageContext(
        run=run,  # type: ignore[arg-type]
        scope=ProjectScope(project_id=project_id, actor_id=uuid4()),  # type: ignore[arg-type]
        now=NOW,
    )


@pytest.fixture
def session_and_project() -> tuple[Session, object]:
    """真实 SQLite 会话 + 一个项目行（video_frames 有项目外键）。"""
    # 必须 import 全部模型再 create_all：否则 projects 等表根本没建，
    # 写 video_frames 时撞外键失败（实测踩到）。
    import app.infrastructure.db.models  # noqa: F401
    from app.infrastructure.db.base import Base
    from app.infrastructure.db.models.identity import Project
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    # **StaticPool 必需**：SQLite 的 ``:memory:`` 是**每连接一个独立库**。
    # 不用 StaticPool 时 create_all 用连接 A 建表、后续 session 用连接 B，
    # B 是个空库 → 写 video_frames 撞「no such table」或外键失败
    # （实测踩到，隔离脚本能过而夹具不能过的原因）。
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    # **显式**打开外键检查：SQLite 默认关闭，且 PRAGMA 是**按连接**生效的。
    # 用 StaticPool 保证全程复用同一连接，PRAGMA 只需开一次；
    # 事件钩子（listens_for "connect"）在部分 SQLAlchemy/SQLite 组合下
    # 不触发，会让外键检查处于「有时开有时关」的随机状态——
    # 实测表现为：夹具里外键失败、而同样的代码在独立脚本里通过。
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")
        conn.commit()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    session = factory()
    project = Project(id=uuid4(), code="v61-video", name="V6.1 视频阶段测试")
    session.add(project)
    session.commit()
    # 断言项目行真的在库里：写 video_frames 时外键失败过一次，
    # 根因是夹具里的库与 handler 用的库不是同一个（SQLite 内存库每连接独立）。
    from sqlalchemy import select as _select

    assert session.execute(_select(Project.id)).all(), "项目行未落库"
    try:
        yield session, project
    finally:
        session.close()
        engine.dispose()


def test_no_video_input_is_skipped(session_and_project: tuple) -> None:
    """无视频输入 → 显式 SKIPPED（不是静默 SUCCEEDED）。"""
    session, project = session_and_project
    handler = VideoDecodeHandler(session, FakeDecoder(), resolver=FakeResolver(()))

    outcome = handler(_context(_FakeRun(()), project.id))

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "视频" in outcome.log_summary


def test_frames_are_persisted_with_raw_time(session_and_project: tuple) -> None:
    """帧索引落库，原始时间原样保存，归一化列留空。"""
    session, project = session_and_project
    decoder = FakeDecoder(_frames())
    asset_id = uuid4()
    handler = VideoDecodeHandler(
        session,
        decoder,
        resolver=FakeResolver(((asset_id, "raw/videos/front.mp4", "front"),)),
    )
    context = _context(_FakeRun((asset_id,)), project.id)

    outcome = handler(context)

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    rows = session.scalars(select(VideoFrameModel)).all()
    assert len(rows) == 2

    first = rows[0]
    assert first.project_id == project.id
    assert first.frame_index == 0
    assert first.pts_seconds == Decimal("0.000000")
    assert first.timestamp_raw == "0.000000"
    assert first.time_base == "1/10"
    # 归一化是 TIMESTAMP_NORMALIZE 阶段的职责，此处必须留空
    assert first.timestamp_normalized_seconds is None
    assert first.normalize_method is None
    assert first.width == 96
    assert first.codec == "av1"
    assert first.is_keyframe is True
    assert first.parser_version.startswith("ddp.video_decode/")


def test_decode_error_frames_are_kept(session_and_project: tuple) -> None:
    """解码失败的帧也留痕（DECODE_ERROR），不静默跳过。"""
    session, project = session_and_project
    asset_id = uuid4()
    handler = VideoDecodeHandler(
        session,
        FakeDecoder(_frames()),
        resolver=FakeResolver(((asset_id, "raw/videos/front.mp4", "front"),)),
    )

    handler(_context(_FakeRun((asset_id,)), project.id))

    rows = session.scalars(select(VideoFrameModel)).all()
    statuses = sorted(row.decode_status for row in rows)
    assert statuses == ["DECODE_ERROR", "OK"]


def test_too_many_frames_fails_explicitly(session_and_project: tuple) -> None:
    """帧数超上限 → 显式失败，不写库。"""
    session, project = session_and_project
    many = tuple(
        DecodedFrame(
            index=i,
            pts_time=float(i),
            pts_time_raw=str(i),
            time_base="1/10",
            width=96,
            height=96,
            codec="av1",
            pixel_format="yuv420p",
            is_keyframe=False,
            decode_status="OK",
        )
        for i in range(5)
    )
    asset_id = uuid4()
    handler = VideoDecodeHandler(
        session,
        FakeDecoder(many),
        resolver=FakeResolver(((asset_id, "raw/videos/front.mp4", "front"),)),
        max_frames=3,
    )

    with pytest.raises(ValueError, match="超过上限"):
        handler(_context(_FakeRun((asset_id,)), project.id))

    assert session.scalars(select(VideoFrameModel)).all() == []
