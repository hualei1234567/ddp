"""质量检查仓储的项目隔离与键集分页（真实 SQLite 约束 + 真实业务链路）。

为什么直接测仓储而不是只靠 API 用例：API 路径上"跨项目"会在用例层先被
``submissions.get`` 挡成 404，仓储的项目过滤即使被删掉也测不出来。这里用
真实 HTTP 链路造出两个项目各自的提交，再**直接**对仓储喂 ``ProjectScope``，
删掉 ``project_id`` 过滤就会立刻失败。

刻意不手写 Schema/Task 的裸 SQL：标注域有触发器要求任务绑定的样本集已快照、
Schema 已发布，硬造行只会得到 ``annotation_task_binding_invalid``。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from app.domain.annotation.enums import QualityCheckStatus, QualityRuleSeverity
from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.quality_check import QualityCheckRun, QualityFinding
from app.domain.identity.errors import CrossProjectAccessError
from app.domain.identity.models import ProjectRole, ProjectScope
from app.infrastructure.db.base import Base
from app.infrastructure.db.models.annotation_quality_rules import (
    AnnotationQualityFinding,
    AnnotationQualityRun,
)
from app.infrastructure.db.models.identity import Project, ProjectMember, User
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.annotation_quality_checks import SqlQualityCheckRepository
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, sessionmaker

from tests.api.annotation_support import (
    create_published_schema,
    create_ready_work,
    create_snapshot_set,
    create_submitted,
    seed_episode,
)
from tests.api.conftest import make_client

NOW = datetime.now(UTC).replace(microsecond=0)
RULE_SET = "sha256:" + "b" * 64


@dataclass(frozen=True)
class _World:
    project_a: UUID
    project_b: UUID
    editor_a: UUID
    user_b: UUID
    submission_a: UUID
    submission_b: UUID
    record_a: UUID
    record_b: UUID


@pytest.fixture()
def world() -> Iterator[tuple[sessionmaker[Session], _World]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    project_a, project_b = uuid4(), uuid4()
    editor_a, owner, user_b = uuid4(), uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                Project(id=project_a, code="project-a", name="项目 A"),
                Project(id=project_b, code="project-b", name="项目 B"),
                User(id=editor_a, display_name="标注员甲"),
                User(id=owner, display_name="负责人"),
                User(id=user_b, display_name="标注员乙"),
            ]
        )
        session.flush()
        session.add_all(
            [
                ProjectMember(
                    project_id=project_a,
                    user_id=editor_a,
                    role=ProjectRole.EDITOR.value,
                ),
                ProjectMember(
                    project_id=project_a,
                    user_id=owner,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=project_b,
                    user_id=owner,
                    role=ProjectRole.OWNER.value,
                ),
                ProjectMember(
                    project_id=project_b,
                    user_id=user_b,
                    role=ProjectRole.EDITOR.value,
                ),
            ]
        )
        session.commit()

    editor_client = make_client(factory, editor_a)
    owner_client = make_client(factory, owner)
    user_b_client = make_client(factory, user_b)

    def _submit(
        project_id: UUID,
        annotator_client,
        annotator_id: UUID,
        code: str,
    ) -> dict[str, object]:
        episode_id = seed_episode(factory, project_id, annotator_id, f"EP-{code}")
        schema = create_published_schema(
            annotator_client,
            owner_client,
            project_id,
            f"schema-{code}",
        )
        sample_set = create_snapshot_set(annotator_client, project_id, f"sample-{code}")
        work = create_ready_work(
            annotator_client,
            owner_client,
            project_id,
            schema,
            sample_set,
            episode_id,
            annotator_id,
            f"task-{code}",
        )
        return create_submitted(
            annotator_client,
            project_id,
            work["assignment"]["assignment_id"],
            episode_id,
        )

    submission_a = _submit(project_a, editor_client, editor_a, "a")
    submission_b = _submit(project_b, user_b_client, user_b, "b")

    ids = _World(
        project_a=project_a,
        project_b=project_b,
        editor_a=editor_a,
        user_b=user_b,
        submission_a=UUID(str(submission_a["submission_id"])),
        submission_b=UUID(str(submission_b["submission_id"])),
        record_a=UUID(str(submission_a["records"][0]["annotation_id"])),
        record_b=UUID(str(submission_b["records"][0]["annotation_id"])),
    )
    # 提交动作会在应用层自动触发一次质量检查，留下基线记录。
    # 本文件测的是**仓储层**行为（分页、跨项目隔离、拒绝非法写入），
    # 需要从干净状态起算——否则断言里的绝对条数会被应用层副作用带偏。
    # 显式清空而不是绕过检查：让"这个文件不依赖应用层"成为事实。
    with factory() as session:
        session.execute(delete(AnnotationQualityFinding))
        session.execute(delete(AnnotationQualityRun))
        session.commit()

    try:
        yield factory, ids
    finally:
        engine.dispose()


def _scope(project_id: UUID, actor_id: UUID) -> ProjectScope:
    return ProjectScope(project_id, actor_id=actor_id)


def _run(
    *,
    project_id: UUID,
    submission_id: UUID,
    record_id: UUID,
    created_at: datetime,
    findings: tuple[QualityFinding, ...] | None = None,
) -> QualityCheckRun:
    if findings is None:
        findings = (
            QualityFinding(
                annotation_id=record_id,
                rule_id="required_labels_present",
                rule_version=1,
                severity=QualityRuleSeverity.ERROR,
                passed=True,
                message="必填标签齐全",
            ),
        )
    error_count = sum(
        1
        for item in findings
        if item.severity is QualityRuleSeverity.ERROR and item.passed is False
    )
    warn_count = sum(
        1 for item in findings if item.severity is QualityRuleSeverity.WARN and item.passed is False
    )
    return QualityCheckRun(
        run_id=uuid4(),
        project_id=project_id,
        submission_id=submission_id,
        rule_set_version=RULE_SET,
        status=QualityCheckStatus.FAILED if error_count else QualityCheckStatus.PASSED,
        error_count=error_count,
        warn_count=warn_count,
        started_at=created_at,
        finished_at=created_at,
        findings=findings,
        created_at=created_at,
    )


def test_repository_never_returns_other_projects_rows(
    world: tuple[sessionmaker[Session], _World],
) -> None:
    """项目 A 的执行记录用项目 B 的作用域查不到；反之亦然。"""
    factory, ids = world
    with factory() as session:
        repo = SqlQualityCheckRepository(session)
        run_a = repo.add(
            _scope(ids.project_a, ids.editor_a),
            _run(
                project_id=ids.project_a,
                submission_id=ids.submission_a,
                record_id=ids.record_a,
                created_at=NOW,
            ),
        )
        run_b = repo.add(
            _scope(ids.project_b, ids.user_b),
            _run(
                project_id=ids.project_b,
                submission_id=ids.submission_b,
                record_id=ids.record_b,
                created_at=NOW,
            ),
        )
        session.commit()

    scope_a = _scope(ids.project_a, ids.editor_a)
    scope_b = _scope(ids.project_b, ids.user_b)
    with factory() as session:
        repo = SqlQualityCheckRepository(session)
        assert repo.get(scope_a, run_a.run_id) is not None
        assert repo.get(scope_a, run_b.run_id) is None
        assert repo.get(scope_b, run_a.run_id) is None
        assert repo.list_for_submission(scope_a, submission_id=ids.submission_b) == []
        listed = repo.list_for_submission(scope_a, submission_id=ids.submission_a)
        assert [item.run_id for item in listed] == [run_a.run_id]


def test_repository_rejects_add_with_foreign_project_scope(
    world: tuple[sessionmaker[Session], _World],
) -> None:
    """``add`` 必须拒绝"作用域与记录 project_id 不一致"的写入。"""
    factory, ids = world
    foreign_run = _run(
        project_id=ids.project_a,
        submission_id=ids.submission_a,
        record_id=ids.record_a,
        created_at=NOW,
    )

    with factory() as session:
        repo = SqlQualityCheckRepository(session)
        with pytest.raises(CrossProjectAccessError):
            repo.add(_scope(ids.project_b, ids.user_b), foreign_run)

    with factory() as session:
        assert session.scalar(select(func.count()).select_from(AnnotationQualityRun)) == 0


def test_list_for_submission_orders_newest_first_and_paginates(
    world: tuple[sessionmaker[Session], _World],
) -> None:
    factory, ids = world
    scope = _scope(ids.project_a, ids.editor_a)
    timestamps = [NOW + timedelta(seconds=offset) for offset in range(3)]
    with factory() as session:
        repo = SqlQualityCheckRepository(session)
        runs = [
            repo.add(
                scope,
                _run(
                    project_id=ids.project_a,
                    submission_id=ids.submission_a,
                    record_id=ids.record_a,
                    created_at=ts,
                ),
            )
            for ts in timestamps
        ]
        session.commit()

    with factory() as session:
        repo = SqlQualityCheckRepository(session)
        first_page = repo.list_for_submission(scope, submission_id=ids.submission_a, limit=2)
        assert [item.run_id for item in first_page] == [runs[2].run_id, runs[1].run_id]

        second_page = repo.list_for_submission(
            scope,
            submission_id=ids.submission_a,
            after_id=first_page[-1].run_id,
            limit=2,
        )
        assert [item.run_id for item in second_page] == [runs[0].run_id]


def test_repository_rejects_out_of_range_limit(
    world: tuple[sessionmaker[Session], _World],
) -> None:
    factory, ids = world
    scope = _scope(ids.project_a, ids.editor_a)
    with factory() as session:
        repo = SqlQualityCheckRepository(session)
        for bad in (0, 102):
            with pytest.raises(DomainValidationError):
                repo.list_for_submission(scope, submission_id=ids.submission_a, limit=bad)


def test_findings_are_written_for_every_rule_outcome(
    world: tuple[sessionmaker[Session], _World],
) -> None:
    """``passed=None``（没有结论）必须原样落库为 NULL，而不是 True/False。"""
    factory, ids = world
    scope = _scope(ids.project_a, ids.editor_a)
    findings = (
        QualityFinding(
            annotation_id=ids.record_a,
            rule_id="ai_label_confidence_low",
            rule_version=1,
            severity=QualityRuleSeverity.WARN,
            passed=None,
            message="任务未配置阈值",
        ),
        QualityFinding(
            annotation_id=ids.record_a,
            rule_id="required_labels_present",
            rule_version=1,
            severity=QualityRuleSeverity.ERROR,
            passed=True,
            message="齐全",
        ),
    )
    run = _run(
        project_id=ids.project_a,
        submission_id=ids.submission_a,
        record_id=ids.record_a,
        created_at=NOW,
        findings=findings,
    )

    with factory() as session:
        SqlQualityCheckRepository(session).add(scope, run)
        session.commit()

    with factory() as session:
        loaded = SqlQualityCheckRepository(session).get(scope, run.run_id)
        assert loaded is not None
        by_rule = {item.rule_id: item for item in loaded.findings}
        assert by_rule["ai_label_confidence_low"].passed is None
        assert by_rule["required_labels_present"].passed is True
        assert by_rule["ai_label_confidence_low"].severity is QualityRuleSeverity.WARN
        raw = session.scalars(
            select(AnnotationQualityFinding.passed).where(
                AnnotationQualityFinding.run_id == run.run_id
            )
        ).all()
        assert sorted(raw, key=lambda item: (item is not None, item)) == [None, True]
