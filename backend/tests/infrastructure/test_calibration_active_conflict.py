"""`calibration_active_conflict` 的语义证据测试（契约 §8.6 / G-20 补充）。

契约里这一条写的是：

    | 409 | calibration_active_conflict | 同目标/类型已有 ACTIVE，**且无法原子替代** |

同一份文档的 VALIDATED→ACTIVE 行又要求「原子替代旧 ACTIVE」。因此这个错误码
不是"已有 ACTIVE 就报错"，而是"**替代失败**才报错"。本文件用三组用例把两种
情形分开证明：

1. 能替代（正常路径）：旧 ACTIVE → SUPERSEDED，新记录 → ACTIVE，**不抛错**；
2. 无法替代（并发/约束拦截）：抛 `calibration_active_conflict`，且事务回滚后
   旧记录仍是 ACTIVE、新记录仍是 VALIDATED（不留部分写入）；
3. 任意时刻同目标 ACTIVITY 唯一：由 `uq_calibration_records_active_target`
   部分唯一索引保证，不依赖应用层"看起来没问题"。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.domain.devices import (
    CalibrationActiveConflictError,
    CalibrationRecord,
    CalibrationStatus,
)
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.devices import CalibrationRecord as CalibrationRecordModel
from app.infrastructure.repositories.devices import SqlCalibrationRecordRepository
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from tests.infrastructure.calibration_support import (
    NOW,
    add_profile,
    seed_active,
    seed_validated,
)
from tests.infrastructure.calibration_support import (
    profile as make_profile,
)
from tests.infrastructure.conftest import DeviceIds


def _active_count(factory: sessionmaker[Session], project_id: UUID, target_id: UUID) -> int:
    with factory() as session:
        return (
            session.scalar(
                select(func.count())
                .select_from(CalibrationRecordModel)
                .where(
                    CalibrationRecordModel.project_id == project_id,
                    CalibrationRecordModel.target_id == target_id,
                    CalibrationRecordModel.status == CalibrationStatus.ACTIVE.value,
                )
            )
            or 0
        )


def test_atomic_supersede_is_the_normal_path_and_does_not_conflict(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    """能原子替代时**不报错**：旧 ACTIVE 变 SUPERSEDED，新记录变 ACTIVE。

    这条是判断"错误码该不该在正常路径抛出"的直接证据：如果实现会在
    "已有 ACTIVE" 时一律报错，本用例立刻失败，也就说明契约 §8.6 的
    "且无法原子替代"被实现曲解了。
    """
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    first = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        activated = repository.activate(
            scope_a,
            stored,
            expected_version=stored.version,
            now=NOW,
        )
        session.commit()
        assert activated.status is CalibrationStatus.ACTIVE
        assert activated.supersedes_id == first.calibration_id

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        assert old is not None
        assert old.status is CalibrationStatus.SUPERSEDED

    assert _active_count(factory, ids.project_a, ids.sensor_a) == 1


def test_conflict_when_supersede_cannot_be_applied(
    device_db: tuple[sessionmaker[Session], DeviceIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无法原子替代时抛 `calibration_active_conflict`，且不留部分写入。"""
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    first = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    def _raise_unique_conflict(
        self: SqlCalibrationRecordRepository,
        scope: ProjectScope,
        activated: CalibrationRecord,
        old_active_id: UUID | None,
        expected_version: int,
    ) -> None:
        # 模拟并发：旧 ACTIVE 已被另一事务顶替成 SUPERSEDED 之后，
        # 本事务再写 ACTIVE 就会撞上部分唯一索引。
        raise IntegrityError(
            "UPDATE calibration_records",
            {},
            Exception("UNIQUE constraint failed: uq_calibration_records_active_target"),
        )

    monkeypatch.setattr(
        SqlCalibrationRecordRepository,
        "_activate_current",
        _raise_unique_conflict,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, second.calibration_id)
        assert stored is not None
        with pytest.raises(CalibrationActiveConflictError) as excinfo:
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version,
                now=NOW,
            )
        session.rollback()

    details = excinfo.value.details or {}
    assert details["target_type"] == "SENSOR"
    assert details["calibration_type"] == "INTRINSICS"

    # savepoint 回滚后两边都保持原状——不存在"旧的已顶替、新的没激活"的中间态。
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        old = repository.get(scope_a, first.calibration_id)
        pending = repository.get(scope_a, second.calibration_id)
        assert old is not None
        assert pending is not None
        assert old.status is CalibrationStatus.ACTIVE
        assert pending.status is CalibrationStatus.VALIDATED

    assert _active_count(factory, ids.project_a, ids.sensor_a) == 1


def test_database_rejects_second_active_row_directly(
    device_db: tuple[sessionmaker[Session], DeviceIds],
) -> None:
    """绕过用例层直写第二条 ACTIVE：数据库必须拒绝，而不是靠应用层自觉。

    这是"任意时刻同目标 ACTIVITY 唯一"的**硬证据**：
    `uq_calibration_records_active_target` 是部分唯一索引（WHERE status='ACTIVE'），
    PG 与 SQLite 都支持，因此并发窗口里两条都想变 ACTIVE 时，后提交的一方
    必然失败。应用层据此把它翻译成 `calibration_active_conflict`。
    """
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    active = seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    other = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )

    with factory() as session:
        row = session.get(CalibrationRecordModel, other.calibration_id)
        assert row is not None
        row.status = CalibrationStatus.ACTIVE.value
        with pytest.raises(IntegrityError) as excinfo:
            session.flush()
        # SQLite 报列组合、PostgreSQL 报索引名，两者都指向同一个
        # uq_calibration_records_active_target（WHERE status='ACTIVE'）。
        message = str(excinfo.value.orig).lower()
        assert "uq_calibration_records_active_target" in message or (
            "calibration_records.project_id" in message
            and "calibration_records.calibration_type" in message
        ), message
        session.rollback()

    # 第一条仍是唯一的 ACTIVE。
    assert _active_count(factory, ids.project_a, ids.sensor_a) == 1
    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        still_active = repository.get(scope_a, active.calibration_id)
        assert still_active is not None
        assert still_active.status is CalibrationStatus.ACTIVE


def test_conflict_details_do_not_leak_record_ids(
    device_db: tuple[sessionmaker[Session], DeviceIds],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """details 只回显目标类型与标定类型，不含任何记录 ID。"""
    factory, ids = device_db
    scope_a = ProjectScope(ids.project_a, actor_id=ids.user_a)
    profile = make_profile(ids.project_a)
    add_profile(factory, scope_a, profile)
    seed_active(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    second = seed_validated(
        factory,
        scope_a,
        profile,
        target_id=ids.sensor_a,
        performer=ids.user_a,
        validator=ids.user_b,
    )
    victim_id = second.calibration_id

    def _raise_unique_conflict(
        self: SqlCalibrationRecordRepository,
        scope: ProjectScope,
        activated: CalibrationRecord,
        old_active_id: UUID | None,
        expected_version: int,
    ) -> None:
        raise IntegrityError(
            "UPDATE calibration_records",
            {},
            Exception("UNIQUE constraint failed: uq_calibration_records_active_target"),
        )

    monkeypatch.setattr(
        SqlCalibrationRecordRepository,
        "_activate_current",
        _raise_unique_conflict,
    )

    with factory() as session:
        repository = SqlCalibrationRecordRepository(session)
        stored = repository.get(scope_a, victim_id)
        assert stored is not None
        with pytest.raises(CalibrationActiveConflictError) as excinfo:
            repository.activate(
                scope_a,
                stored,
                expected_version=stored.version,
                now=NOW,
            )
        session.rollback()

    rendered = repr(excinfo.value.details or {})
    assert str(victim_id) not in rendered
    assert str(uuid4()) not in rendered
