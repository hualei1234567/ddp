"""派单入口的黄金题熔断拦截测试（V52-008）。

复用 ``test_annotation_use_cases.py`` 的夹具与最短就绪路径，注入真实
SQL 闸口（``SqlHoneypotGatekeeper`` + 真实状态行），钉住：

- PAUSED 标注员创建 ANNOTATOR 分配 → 422 ``annotation_annotator_paused_honeypot``
  （details 只含 annotator_id 与原因码 HONEYPOT_PAUSED）；
- REVIEWER/ARBITER 角色不受标注熔断闸口限制；
- 闸口未注入（None）或行不存在/NORMAL → 行为与旧版一致；
- 解除后可正常分配。
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from app.application.annotation.assignments import AnnotationAssignmentUseCases
from app.application.annotation.commands import NewAnnotationAssignment
from app.application.annotation.honeypot_wiring import SqlHoneypotGatekeeper
from app.domain.annotation.errors import AnnotatorPausedByHoneypotError
from app.infrastructure.db.models.annotation_honeypot_state import (
    AnnotationHoneypotState,
)
from app.infrastructure.repositories.annotation_assignments import (
    SqlAnnotationAssignmentRepository,
)
from app.infrastructure.repositories.annotation_audit import (
    SqlAnnotationAuditRepository,
    SqlAnnotationIdempotencyRepository,
    SqlAnnotationOutboxRepository,
)
from app.infrastructure.repositories.annotation_batches import SqlAnnotationBatchRepository
from app.infrastructure.repositories.annotation_directory import SqlMemberDirectory
from app.infrastructure.repositories.annotation_tasks import SqlAnnotationTaskRepository
from sqlalchemy.orm import sessionmaker

from tests.application import test_annotation_use_cases as _fixtures
from tests.application.test_annotation_use_cases import (  # noqa: F401
    NOW,
    _publish_and_ready,
    _Stacks,
)

# pytest 收集用例需要真实的夹具符号；从夹具模块 re-export（不触发 F811）。
annotation_app = _fixtures.annotation_app


def _gated_stacks(
    annotation_app_fixture: tuple[sessionmaker, _Stacks, UUID],
    *,
    enabled: bool,
) -> tuple[sessionmaker, _Stacks, UUID]:
    """在既有夹具上重装分配用例：注入真实 SQL 熔断闸口（其余用例原样）。"""
    factory, stacks, episode_id = annotation_app_fixture
    with factory() as session:
        stacks.assignments = AnnotationAssignmentUseCases(
            SqlAnnotationTaskRepository(session),
            SqlAnnotationBatchRepository(session),
            SqlAnnotationAssignmentRepository(session),
            SqlMemberDirectory(session),
            SqlAnnotationAuditRepository(session),
            SqlAnnotationOutboxRepository(session),
            SqlAnnotationIdempotencyRepository(session),
            stacks.uow,
            honeypot_gate=SqlHoneypotGatekeeper(session, enabled=enabled),
        )
    return factory, stacks, episode_id


def test_paused_annotator_cannot_be_assigned(
    annotation_app: tuple[sessionmaker, _Stacks, UUID],
) -> None:
    """PAUSED 标注员创建 ANNOTATOR 分配：422 契约码 + 原因码。"""

    factory, stacks, _episode = _gated_stacks(annotation_app, enabled=True)
    stacks, _task_id, batch_id = _publish_and_ready(annotation_app, {"code": "hp-1"})
    annotator_id = stacks.scope_a.actor_id
    with factory() as session:
        session.add(
            AnnotationHoneypotState(
                id=uuid4(),
                project_id=stacks.scope_a.project_id,
                annotator_id=annotator_id,
                hits=2,
                total=20,
                window_history="0" * 18 + "11",
                state="PAUSED",
                paused_reason="连续 2 个窗口黄金题准确率低于 0.85：20%=20%, 20%=20%",
                paused_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=stacks.scope_b.actor_id,
                updated_by=stacks.scope_b.actor_id,
            )
        )
        session.commit()
    with pytest.raises(AnnotatorPausedByHoneypotError) as excinfo:
        stacks.assignments.create(
            stacks.scope_a,
            batch_id,
            NewAnnotationAssignment(
                assignee_id=annotator_id,
                planned_item_count=1,
                planned_minutes=30,
                lease_expires_at=NOW + timedelta(hours=4),
            ),
            path="/projects/p/annotation-assignments",
        )
    assert excinfo.value.code == "annotation_annotator_paused_honeypot"
    assert excinfo.value.details["annotator_id"] == str(annotator_id)
    assert excinfo.value.details["reason"] == "HONEYPOT_PAUSED"


def test_normal_annotator_assignment_unchanged(
    annotation_app: tuple[sessionmaker, _Stacks, UUID],
) -> None:
    """无状态行 / NORMAL：注入闸口后行为与旧版完全一致。"""
    factory, stacks, _episode = _gated_stacks(annotation_app, enabled=True)
    stacks, _task_id, batch_id = _publish_and_ready(annotation_app, {"code": "hp-ok"})
    assignment = stacks.assignments.create(
        stacks.scope_a,
        batch_id,
        NewAnnotationAssignment(
            assignee_id=stacks.scope_a.actor_id,
            planned_item_count=1,
            planned_minutes=30,
            lease_expires_at=NOW + timedelta(hours=4),
        ),
        path="/projects/p/annotation-assignments",
    )
    assert assignment.assignee_id == stacks.scope_a.actor_id


def test_gate_disabled_does_not_block_paused_annotator(
    annotation_app: tuple[sessionmaker, _Stacks, UUID],
) -> None:
    """开关关闭：闸口仍注入但内部短路，PAUSED 行不拦截（行为不变）。"""

    factory, stacks, _episode = _gated_stacks(annotation_app, enabled=False)
    stacks, _task_id, batch_id = _publish_and_ready(annotation_app, {"code": "hp-off"})
    annotator_id = stacks.scope_a.actor_id
    with factory() as session:
        session.add(
            AnnotationHoneypotState(
                id=uuid4(),
                project_id=stacks.scope_a.project_id,
                annotator_id=annotator_id,
                hits=0,
                total=20,
                window_history="0" * 20,
                state="PAUSED",
                paused_reason="连续 2 个窗口黄金题准确率低于 0.85：0%=0%, 0%=0%",
                paused_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=stacks.scope_b.actor_id,
                updated_by=stacks.scope_b.actor_id,
            )
        )
        session.commit()
    assignment = stacks.assignments.create(
        stacks.scope_a,
        batch_id,
        NewAnnotationAssignment(
            assignee_id=annotator_id,
            planned_item_count=1,
            planned_minutes=30,
            lease_expires_at=NOW + timedelta(hours=4),
        ),
        path="/projects/p/annotation-assignments",
    )
    assert assignment.assignee_id == annotator_id


def test_resume_allows_assignment_again(
    annotation_app: tuple[sessionmaker, _Stacks, UUID],
) -> None:
    """解除暂停后：同一标注员可正常分配（闸口读到的已是 NORMAL）。"""
    from app.application.annotation.honeypot_wiring import resume

    factory, stacks, _episode = _gated_stacks(annotation_app, enabled=True)
    stacks, _task_id, batch_id = _publish_and_ready(annotation_app, {"code": "hp-resume"})
    annotator_id = stacks.scope_a.actor_id
    with factory() as session:
        session.add(
            AnnotationHoneypotState(
                id=uuid4(),
                project_id=stacks.scope_a.project_id,
                annotator_id=annotator_id,
                hits=0,
                total=20,
                window_history="0" * 20,
                state="PAUSED",
                paused_reason="连续 2 个窗口黄金题准确率低于 0.85：0%=0%, 0%=0%",
                paused_at=NOW,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=stacks.scope_b.actor_id,
                updated_by=stacks.scope_b.actor_id,
            )
        )
        session.commit()
        resume(
            session,
            stacks.scope_a.project_id,
            annotator_id,
            by=stacks.scope_b.actor_id,
            enabled=True,
        )
        session.commit()
    assignment = stacks.assignments.create(
        stacks.scope_a,
        batch_id,
        NewAnnotationAssignment(
            assignee_id=annotator_id,
            planned_item_count=1,
            planned_minutes=30,
            lease_expires_at=NOW + timedelta(hours=4),
        ),
        path="/projects/p/annotation-assignments",
    )
    assert assignment.assignee_id == annotator_id
