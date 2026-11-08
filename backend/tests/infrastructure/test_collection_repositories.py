"""采集任务/批次仓储与数据库兜底测试（SQLite 真实约束）。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.collection.ports import AuditRecord
from app.domain.collection import (
    CollectionBatch,
    CollectionBatchStatus,
    CollectionTask,
    CollectionTaskStatus,
    DuplicateResourceError,
    VersionConflictError,
)
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.collection import (
    AuditLog,
)
from app.infrastructure.db.models.collection import (
    CollectionBatch as CollectionBatchModel,
)
from app.infrastructure.db.models.collection import (
    CollectionTask as CollectionTaskModel,
)
from app.infrastructure.db.models.devices import Device, Robot, Sensor
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import SqlAuditRepository
from app.infrastructure.repositories.collection_batches import SqlCollectionBatchRepository
from app.infrastructure.repositories.collection_tasks import SqlCollectionTaskRepository
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker


@dataclass(frozen=True)
class CollectionIds:
    project_a: UUID
    project_b: UUID
    user_a: UUID
    user_b: UUID
    robot_a: UUID
    robot_b: UUID
    device_a: UUID
    device_b: UUID
    sensor_a: UUID


@pytest.fixture()
def collection_db() -> tuple[sessionmaker[Session], CollectionIds]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    ids = CollectionIds(
        project_a=uuid4(),
        project_b=uuid4(),
        user_a=uuid4(),
        user_b=uuid4(),
        robot_a=uuid4(),
        robot_b=uuid4(),
        device_a=uuid4(),
        device_b=uuid4(),
        sensor_a=uuid4(),
    )
    with factory() as session:
        session.add_all(
            [
                Project(id=ids.project_a, code="project-a", name="项目 A"),
                Project(id=ids.project_b, code="project-b", name="项目 B"),
                User(id=ids.user_a, display_name="甲"),
                User(id=ids.user_b, display_name="乙"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(
                    project_id=ids.project_a,
                    user_id=ids.user_a,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    project_id=ids.project_a,
                    user_id=ids.user_b,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=ids.project_b,
                    user_id=ids.user_b,
                    role=ProjectRole.OWNER.value,
                ),
            ]
        )
        for project_id, robot_id, device_id, actor_id, suffix in (
            (ids.project_a, ids.robot_a, ids.device_a, ids.user_a, "a"),
            (ids.project_b, ids.robot_b, ids.device_b, ids.user_b, "b"),
        ):
            session.add_all(
                [
                    Robot(
                        id=robot_id,
                        project_id=project_id,
                        code=f"robot-{suffix}",
                        name=f"机器人 {suffix}",
                        robot_type="HUMANOID",
                        status="ACTIVE",
                        capability_profile={},
                        created_by=actor_id,
                        updated_by=actor_id,
                    ),
                    Device(
                        id=device_id,
                        project_id=project_id,
                        code=f"device-{suffix}",
                        name=f"设备 {suffix}",
                        device_type="COMPUTE",
                        status="ACTIVE",
                        capability_profile={},
                        created_by=actor_id,
                        updated_by=actor_id,
                    ),
                ]
            )
            session.flush()
        session.add(
            Sensor(
                id=ids.sensor_a,
                project_id=ids.project_a,
                code="sensor-a",
                sensor_type="RGB",
                status="ACTIVE",
                sample_rate_hz=30.0,
                coordinate_frame="camera_link",
                sync_mode="NONE",
                robot_id=ids.robot_a,
                created_by=ids.user_a,
                updated_by=ids.user_a,
            )
        )
        session.commit()
    return factory, ids


def _scope(project_id: UUID, actor_id: UUID) -> ProjectScope:
    return ProjectScope(project_id, actor_id=actor_id)


def _task(
    project_id: UUID,
    *,
    code: str = "task-1",
    target_episode_count: int = 0,
    assigned_device_ids: tuple[UUID, ...] = (),
) -> CollectionTask:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return CollectionTask(
        task_id=uuid4(),
        project_id=project_id,
        code=code,
        name=f"任务 {code}",
        target_episode_count=target_episode_count,
        assigned_device_ids=assigned_device_ids,
        created_at=now,
        updated_at=now,
    )


def test_task_roundtrip_and_project_isolation(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope_a = _scope(ids.project_a, ids.user_a)
    scope_b = _scope(ids.project_b, ids.user_b)
    task_a = _task(ids.project_a, code="task-a")
    task_b = _task(ids.project_b, code="task-b")

    with factory() as session:
        repository = SqlCollectionTaskRepository(session)
        repository.add(scope_a, task_a)
        repository.add(scope_b, task_b)
        session.commit()

    with factory() as session:
        repository = SqlCollectionTaskRepository(session)
        assert repository.get(scope_a, task_a.task_id).status is CollectionTaskStatus.DRAFT
        assert repository.get(scope_b, task_a.task_id) is None
        assert repository.get_by_code(scope_b, task_a.code) is None
        assert [item.task_id for item in repository.list(scope_a)] == [task_a.task_id]


def test_task_code_is_unique_within_project(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope_a = _scope(ids.project_a, ids.user_a)
    scope_b = _scope(ids.project_b, ids.user_b)
    with factory() as session:
        SqlCollectionTaskRepository(session).add(scope_a, _task(ids.project_a, code="dup"))
        session.commit()

    with factory() as session:
        with pytest.raises(DuplicateResourceError):
            SqlCollectionTaskRepository(session).add(
                scope_a,
                _task(ids.project_a, code="dup"),
            )
        session.rollback()

    with factory() as session:
        SqlCollectionTaskRepository(session).add(scope_b, _task(ids.project_b, code="dup"))
        session.commit()


def test_task_optimistic_lock_and_legal_transition(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope = _scope(ids.project_a, ids.user_a)
    task = _task(ids.project_a)
    with factory() as session:
        SqlCollectionTaskRepository(session).add(scope, task)
        session.commit()

    with factory() as session:
        repository = SqlCollectionTaskRepository(session)
        current = repository.get(scope, task.task_id)
        assert current is not None
        saved = repository.save(
            scope,
            current.apply_action("submit-review"),
            expected_version=current.version,
        )
        assert saved.status is CollectionTaskStatus.PENDING_REVIEW
        assert saved.version == 2
        session.commit()

    with factory() as session:
        repository = SqlCollectionTaskRepository(session)
        current = repository.get(scope, task.task_id)
        assert current is not None
        with pytest.raises(VersionConflictError):
            repository.save(
                scope,
                current.apply_action("approve"),
                expected_version=1,
            )
        session.rollback()


def test_cross_project_device_assignment_is_rejected(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope_a = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        with pytest.raises(IntegrityError):
            SqlCollectionTaskRepository(session).add(
                scope_a,
                _task(ids.project_a, code="cross-device", assigned_device_ids=(ids.device_b,)),
            )
        session.rollback()

    with factory() as session:
        assert SqlCollectionTaskRepository(session).get_by_code(scope_a, "cross-device") is None


def test_batch_sequence_counts_and_cross_project_reference(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope_a = _scope(ids.project_a, ids.user_a)
    scope_b = _scope(ids.project_b, ids.user_b)
    task_a = _task(ids.project_a, code="task-a", target_episode_count=10)
    task_b = _task(ids.project_b, code="task-b")
    with factory() as session:
        tasks = SqlCollectionTaskRepository(session)
        tasks.add(scope_a, task_a)
        tasks.add(scope_b, task_b)
        session.commit()

    with factory() as session:
        repository = SqlCollectionBatchRepository(session)
        first = repository.add(
            scope_a,
            _batch(ids.project_a, task_a.task_id, sequence_no=1, code="B-1", expected_count=4),
        )
        assert first.status is CollectionBatchStatus.PLANNED
        assert repository.next_sequence_no(scope_a, task_a.task_id) == 2
        second = repository.add(
            scope_a,
            _batch(ids.project_a, task_a.task_id, sequence_no=2, code="B-2", expected_count=5),
        )
        assert second.sequence_no == 2
        assert repository.sum_expected_count(scope_a, task_a.task_id) == 9
        session.commit()

    with factory() as session:
        repository = SqlCollectionBatchRepository(session)
        with pytest.raises(DuplicateResourceError):
            repository.add(
                scope_a,
                _batch(ids.project_a, task_a.task_id, sequence_no=2, code="B-3", expected_count=1),
            )
        session.rollback()

    with factory() as session:
        repository = SqlCollectionBatchRepository(session)
        with pytest.raises(IntegrityError):
            repository.add(
                scope_a,
                _batch(ids.project_a, task_b.task_id, sequence_no=3, code="B-3", expected_count=1),
            )
        session.rollback()


def test_database_trigger_rejects_illegal_task_and_batch_transitions(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope = _scope(ids.project_a, ids.user_a)
    task = _task(ids.project_a)
    with factory() as session:
        SqlCollectionTaskRepository(session).add(scope, task)
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CollectionTaskModel)
                .where(CollectionTaskModel.id == task.task_id)
                .values(status="APPROVED")
            )
        session.rollback()

    with factory() as session:
        session.execute(
            update(CollectionTaskModel)
            .where(CollectionTaskModel.id == task.task_id)
            .values(status="PENDING_REVIEW")
        )
        session.commit()

    with factory() as session:
        batch = _batch(
            ids.project_a,
            task.task_id,
            sequence_no=1,
            code="B-1",
            expected_count=1,
        )
        SqlCollectionBatchRepository(session).add(scope, batch)
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                update(CollectionBatchModel)
                .where(CollectionBatchModel.id == batch.batch_id)
                .values(status="READY")
            )
        session.rollback()


def test_audit_logs_are_append_only(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope = _scope(ids.project_a, ids.user_a)
    with factory() as session:
        SqlAuditRepository(session).record(
            scope,
            AuditRecord(
                audit_id=uuid4(),
                project_id=ids.project_a,
                actor_user_id=ids.user_a,
                actor_role=None,
                resource_type="COLLECTION_TASK",
                resource_id=uuid4(),
                action="create",
                from_status=None,
                to_status="DRAFT",
                reason=None,
                request_id="test-request",
                result="SUCCESS",
                details={},
                occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
            ),
        )
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(update(AuditLog).values(action="tampered"))
        session.rollback()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(delete(AuditLog))
        session.rollback()


def test_task_delete_is_forbidden(
    collection_db: tuple[sessionmaker[Session], CollectionIds],
) -> None:
    factory, ids = collection_db
    scope = _scope(ids.project_a, ids.user_a)
    task = _task(ids.project_a)
    with factory() as session:
        SqlCollectionTaskRepository(session).add(scope, task)
        session.commit()

    with factory() as session:
        with pytest.raises(IntegrityError):
            session.execute(
                delete(CollectionTaskModel).where(CollectionTaskModel.id == task.task_id)
            )
        session.rollback()


def _batch(
    project_id: UUID,
    task_id: UUID,
    *,
    sequence_no: int,
    code: str,
    expected_count: int,
) -> CollectionBatch:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return CollectionBatch(
        batch_id=uuid4(),
        project_id=project_id,
        task_id=task_id,
        code=code,
        sequence_no=sequence_no,
        expected_count=expected_count,
        created_at=now,
        updated_at=now,
    )
