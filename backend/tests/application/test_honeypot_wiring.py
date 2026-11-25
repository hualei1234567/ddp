"""黄金题熔断接线测试（V52-008，蓝图 N3 阶段 2）。

覆盖：
- 状态持久化往返（SQLite 真实约束：唯一键、CHECK、audit 列）；
- 提交 → 更新（无行建行 = NORMAL 默认；PAUSED 停止接收新命中）；
- 派单 PAUSED 拦截（422 契约码 + details 原因码；REVIEWER 不受闸口限制）；
- 开关关闭行为不变（不写状态行、派单不查、resume 幂等）；
- resume 清窗口回 NORMAL（幂等：NORMAL 时 resumed=False）。

判定口径与领域单测（``test_honeypot_breaker.py``）一致：窗口大小 10、
连续 2 窗、阈值 85%。
"""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest
from app.application.annotation.honeypot_wiring import (
    WINDOW_SIZE_DEFAULT,
    AnnotatorPausedByHoneypotError,
    record_gold_submission,
    resume,
)
from app.domain.annotation.honeypot_breaker import (
    CONSECUTIVE_WINDOWS,
    HoneypotBreaker,
)
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_honeypot_state import (
    AnnotationHoneypotState as _Model,
)
from app.infrastructure.db.models.identity import Project, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture()
def wiring_db() -> Iterator[tuple[sessionmaker[Session], UUID, UUID, UUID]]:
    """最小身份上下文：1 项目 + 1 标注员 + 1 操作人（audit 外键目标）。"""
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_id, annotator_id, actor_id = uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id, code="hp-proj", name="熔断项目"),
                User(id=annotator_id, display_name="标注员"),
                User(id=actor_id, display_name="操作人"),
            ]
        )
        session.commit()
    try:
        yield factory, project_id, annotator_id, actor_id
    finally:
        engine.dispose()


# 领域默认窗口大小必须与接线常量同源（防止两处口径漂移）。
def test_window_constant_matches_domain_default() -> None:
    assert HoneypotBreaker().window_size == WINDOW_SIZE_DEFAULT
    assert CONSECUTIVE_WINDOWS == 2


def _load_row(session: Session, project_id: object, annotator_id: object) -> _Model | None:
    return session.scalar(
        select(_Model).where(
            _Model.project_id == project_id,
            _Model.annotator_id == annotator_id,
        )
    )


# ── 持久化往返 ───────────────────────────────────────────────────


def test_no_row_means_normal_and_first_submission_creates_row(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """无状态行 = NORMAL 默认；首次提交才建行（零配置项目零写入）。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        state, reason = _load_state(session, project_id, annotator_id)
        assert state == "NORMAL" and reason is None
        assert _load_row(session, project_id, annotator_id) is None

        result = record_gold_submission(
            session,
            project_id,
            annotator_id,
            hit=True,
            actor_id=actor_id,
            enabled=True,
        )
        assert result == "NORMAL"
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "NORMAL"
        assert row.hits == 1 and row.total == 1
        assert row.window_history == "1"
        assert row.paused_reason is None and row.paused_at is None
        assert row.created_by == actor_id and row.updated_by == actor_id
        session.commit()

        # 往返：新 Session 读回同一行（真持久化，不是内存对象）。
    with factory() as session:
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.hits == 1 and row.state == "NORMAL"
        assert row.window_history == "1"


def test_paused_row_roundtrip_keeps_reason_and_timestamp(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """PAUSED 行含原因与时间戳；重开 Session 读回一致。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        _feed(session, project_id, annotator_id, actor_id, [False] * 20)
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "PAUSED"
        assert row.paused_reason and "0%" in row.paused_reason
        assert row.paused_at is not None
        session.commit()
    with factory() as session:
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "PAUSED"
        assert row.paused_reason == "连续 2 个窗口黄金题准确率低于 0.85：0/10=0%, 0/10=0%"
        assert row.hits == 0 and row.total == 20
        assert row.window_history == "0" * 20


def test_unique_constraint_one_row_per_annotator(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """(project_id, annotator_id) 唯一：重复提交只更新既有行。"""
    from sqlalchemy.exc import IntegrityError

    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        record_gold_submission(
            session, project_id, annotator_id, hit=True, actor_id=actor_id, enabled=True
        )
        session.commit()
        duplicate = _Model(
            id=uuid4(),
            project_id=project_id,
            annotator_id=annotator_id,
            hits=0,
            total=0,
            state="NORMAL",
            version=1,
            created_by=actor_id,
            updated_by=actor_id,
        )
        session.add(duplicate)
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


# ── 提交 → 更新 ──────────────────────────────────────────────────


def test_windows_roll_forward_and_pause_on_two_bad_windows(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """窗口滚动：第一窗全错不暂停；连续两窗低准触发 PAUSED。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        # 第一窗 0%：<2 窗，不触发。
        _feed(session, project_id, annotator_id, actor_id, [False] * 10)
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "NORMAL" and row.hits == 0 and row.total == 10
        session.commit()

        # 第二窗 20%：0% + 20% 连续两窗低准 → PAUSED。
        _feed(
            session,
            project_id,
            annotator_id,
            actor_id,
            [False, True, False, True, False, False, False, False, False, False],
        )
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "PAUSED"
        assert row.hits == 2 and row.total == 20
        assert row.window_history == "0" * 10 + "0101000000"
        session.commit()

        # PAUSED 后再提交：不改状态（异常路径由派单闸口在源头拦截）。
        result = record_gold_submission(
            session, project_id, annotator_id, hit=True, actor_id=actor_id, enabled=True
        )
        assert result == "PAUSED"
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "PAUSED" and row.hits == 2 and row.total == 20


def test_good_windows_reach_threshold_and_stay_normal(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """两窗 100% 不触发；窗口序列封顶在滑窗容量（超出滚动丢弃）。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        _feed(session, project_id, annotator_id, actor_id, [True] * 25)
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "NORMAL"
        # 滑窗容量 = window_size * consecutive_windows；序列只留最近 20 条。
        assert row.total == WINDOW_SIZE_DEFAULT * 2
        assert row.hits == WINDOW_SIZE_DEFAULT * 2
        assert row.window_history == "1" * 20


def test_window_order_is_preserved_for_fidelity(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """顺序保真：hits=16/total=20 的两种排布判定不同（重放不靠聚合）。

    实测反例（模块 docstring）：两窗各 80%（PAUSED）与窗1 100% + 窗2 60%
    （NORMAL）聚合同为 hits=16/total=20。序列重放必须区分它们。
    """
    factory, project_id_a, annotator_a, actor_id = wiring_db
    project_id_b, annotator_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_id_b, code="hp-proj-2", name="熔断项目二"),
                User(id=annotator_b, display_name="标注员乙"),
            ]
        )
        session.commit()
        # 排布 A：两窗各 80% → PAUSED。
        _feed(
            session,
            project_id_a,
            annotator_a,
            actor_id,
            [False, False] + [True] * 8 + [False, False] + [True] * 8,
        )
        row_a = _load_row(session, project_id_a, annotator_a)
        assert row_a is not None
        assert row_a.state == "PAUSED"
        # 排布 B：窗1 100% + 窗2 60% → NORMAL。
        _feed(session, project_id_b, annotator_b, actor_id, [True] * 10 + [True] * 6 + [False] * 4)
        row_b = _load_row(session, project_id_b, annotator_b)
        assert row_b is not None
        assert row_b.state == "NORMAL"
        # 两者聚合值完全相同——只有序列能区分。
        assert (row_a.hits, row_a.total) == (row_b.hits, row_b.total) == (16, 20)


# ── 开关关闭：行为不变 ────────────────────────────────────────────


def test_disabled_switch_writes_nothing(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """开关关闭：record/resume 全部短路，行为与旧版一致。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        result = record_gold_submission(
            session,
            project_id,
            annotator_id,
            hit=False,
            actor_id=actor_id,
            enabled=False,
        )
        assert result == "NORMAL"
        assert _load_row(session, project_id, annotator_id) is None
        outcome = resume(
            session,
            project_id,
            annotator_id,
            by=actor_id,
            enabled=False,
        )
        assert outcome == {
            "annotator_id": str(annotator_id),
            "state": "NORMAL",
            "resumed": False,
        }
        assert _load_row(session, project_id, annotator_id) is None


# ── resume ───────────────────────────────────────────────────────


def test_resume_clears_window_back_to_normal(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """resume：PAUSED → NORMAL，窗口清零，原因/时间戳清空。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        _feed(session, project_id, annotator_id, actor_id, [False] * 20)
        session.commit()
        outcome = resume(session, project_id, annotator_id, by=actor_id, enabled=True)
        assert outcome["resumed"] is True
        assert outcome["state"] == "NORMAL"
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "NORMAL"
        assert row.hits == 0 and row.total == 0
        assert row.window_history == ""
        assert row.paused_reason is None and row.paused_at is None
        assert row.updated_by == actor_id

        # 幂等：再 resume 返回 resumed=False，不报错。
        again = resume(session, project_id, annotator_id, by=actor_id, enabled=True)
        assert again["resumed"] is False and again["state"] == "NORMAL"


def test_resume_without_row_is_idempotent_noop(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """无状态行时 resume 是 no-op（NORMAL 语义），不建行。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        outcome = resume(session, project_id, annotator_id, by=actor_id, enabled=True)
        assert outcome["resumed"] is False and outcome["state"] == "NORMAL"
        assert _load_row(session, project_id, annotator_id) is None


def test_resume_then_rebuild_window_from_zero(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """resume 后窗口从零重新累计：旧命中不回放。"""
    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        _feed(session, project_id, annotator_id, actor_id, [False] * 20)
        session.commit()
        resume(session, project_id, annotator_id, by=actor_id, enabled=True)
        _feed(session, project_id, annotator_id, actor_id, [True] * 10)
        row = _load_row(session, project_id, annotator_id)
        assert row is not None
        assert row.state == "NORMAL" and row.hits == 10 and row.total == 10
        assert row.window_history == "1" * 10


# ── 派单闸口 ─────────────────────────────────────────────────────


def test_gatekeeper_blocks_paused_annotator(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """PAUSED → 422 错误码 + details 原因码；NORMAL 静默放行。"""
    from app.application.annotation.honeypot_wiring import SqlHoneypotGatekeeper

    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        gate = SqlHoneypotGatekeeper(session, enabled=True)
        gate.assert_dispatchable(project_id, annotator_id)  # 无行 = 放行

        _feed(session, project_id, annotator_id, actor_id, [False] * 20)
        session.commit()
        with pytest.raises(AnnotatorPausedByHoneypotError) as excinfo:
            gate.assert_dispatchable(project_id, annotator_id)
        assert excinfo.value.code == "annotation_annotator_paused_honeypot"
        assert excinfo.value.details["annotator_id"] == str(annotator_id)
        assert excinfo.value.details["reason"] == "HONEYPOT_PAUSED"

        resume(session, project_id, annotator_id, by=actor_id, enabled=True)
        gate.assert_dispatchable(project_id, annotator_id)  # 解除后放行


def test_gatekeeper_disabled_never_blocks(
    wiring_db: tuple[sessionmaker[Session], UUID, UUID, UUID],
) -> None:
    """开关关闭：即使有 PAUSED 行也放行（闸口先短路）。"""
    from app.application.annotation.honeypot_wiring import SqlHoneypotGatekeeper

    factory, project_id, annotator_id, actor_id = wiring_db
    with factory() as session:
        _feed(session, project_id, annotator_id, actor_id, [False] * 20)
        session.commit()
        gate = SqlHoneypotGatekeeper(session, enabled=False)
        gate.assert_dispatchable(project_id, annotator_id)  # 不抛


def _feed(
    session: Session,
    project_id: object,
    annotator_id: object,
    actor_id: object,
    pattern: list[bool],
) -> None:
    """按给定命中序列连续提交黄金题（不 commit，由调用方控制事务）。"""
    for hit in pattern:
        record_gold_submission(
            session,
            project_id,  # type: ignore[arg-type]
            annotator_id,  # type: ignore[arg-type]
            hit=hit,
            actor_id=actor_id,  # type: ignore[arg-type]
            enabled=True,
        )


def _load_state(
    session: Session, project_id: object, annotator_id: object
) -> tuple[str, str | None]:
    from app.application.annotation.honeypot_wiring import _load_state as _impl

    return _impl(session, project_id, annotator_id)  # type: ignore[arg-type]
