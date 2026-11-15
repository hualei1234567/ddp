"""标注域分页测试的工作链播种夹具（真实 SQLite 约束）。

为什么单独成文件：分页用例本身只关心"翻页不重不漏"，而准备数据需要
满足标注域全部守卫触发器（初始状态 + 逐级迁移 + 复合外键）。按职责再分一层：

- 本文件：schema / sample_set / task / batch / assignment / submission 工作链；
- ``annotation_pagination_quality_support``：质量抽检与 IAA 报告的批量播种，
  以及游标翻页遍历辅助。

所有播种函数都**按守卫要求**先插入初始状态再 UPDATE 迁移，
不使用 ``status="SUBMITTED"`` 直插这类走捷径的写法——那会被触发器拒绝，
也掩盖真实迁移语义。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.infrastructure.db.models.annotation_catalog import AnnotationSampleSet, AnnotationSchema
from app.infrastructure.db.models.annotation_results import AnnotationSubmission
from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment,
    AnnotationBatch,
    AnnotationTask,
)
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

NOW = datetime(2026, 11, 15, 8, 0, tzinfo=UTC)
SCHEMA_SHA = "sha256:" + "a" * 64
RECORDS_DIGEST = "sha256:" + "b" * 64


def url(project_id: object, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}{suffix}"


def _seed_schema_and_sample_set(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
    schema_id: UUID,
    sample_set_id: UUID,
) -> None:
    """按守卫要求先插 DRAFT 再逐级 UPDATE，模拟真实状态机迁移。"""
    session.add(
        AnnotationSchema(
            id=schema_id,
            project_id=project_id,
            schema_lineage_id=uuid4(),
            schema_version=1,
            code=f"schema-{schema_id.hex[:8]}",
            name="分页 Schema",
            status="DRAFT",
            annotation_levels=["EPISODE"],
            labels=[],
            compatibility="BACKWARD_COMPATIBLE",
            schema_sha256=SCHEMA_SHA,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.add(
        AnnotationSampleSet(
            id=sample_set_id,
            project_id=project_id,
            code=f"sample-{sample_set_id.hex[:8]}",
            status="DRAFT",
            selection_rule_version="selection/1",
            strategy="FILTER",
            filters={},
            strata=[],
            dedup_key="EPISODE_LINEAGE",
            candidate_count=1,
            selected_count=1,
            excluded_count=0,
            deduplicated_count=0,
            snapshot_sha256=None,
            snapshot_at=None,
            data_source="REAL",
            exclusion_summary={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    session.execute(
        update(AnnotationSchema)
        .where(AnnotationSchema.id == schema_id)
        .values(status="PENDING_REVIEW", version=2)
    )
    session.execute(
        update(AnnotationSchema)
        .where(AnnotationSchema.id == schema_id)
        .values(status="PUBLISHED", published_at=NOW, version=3)
    )
    session.execute(
        update(AnnotationSampleSet)
        .where(AnnotationSampleSet.id == sample_set_id)
        .values(status="PREVIEW", version=2)
    )
    session.execute(
        update(AnnotationSampleSet)
        .where(AnnotationSampleSet.id == sample_set_id)
        .values(
            status="SNAPSHOTTED",
            snapshot_sha256=RECORDS_DIGEST,
            snapshot_at=NOW,
            version=3,
        )
    )


def _seed_task(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
    task_id: UUID,
    sample_set_id: UUID,
    schema_id: UUID,
    code: str,
) -> None:
    session.add(
        AnnotationTask(
            id=task_id,
            project_id=project_id,
            code=code,
            name=f"任务 {code}",
            status="DRAFT",
            sample_set_id=sample_set_id,
            schema_id=schema_id,
            schema_version=1,
            schema_sha256=SCHEMA_SHA,
            gold_set_id=None,
            iaa_policy={},
            quality_policy={},
            assignment_mode="MANUAL",
            target_item_count=1,
            target_episode_count=1,
            priority="P1",
            progress={},
            quality_summary={},
            iaa_report_id=None,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    for status, version in (
        ("PENDING_REVIEW", 2),
        ("APPROVED", 3),
        ("READY", 4),
        ("ASSIGNED", 5),
        ("IN_PROGRESS", 6),
    ):
        session.execute(
            update(AnnotationTask)
            .where(AnnotationTask.id == task_id)
            .values(status=status, version=version)
        )


def _seed_batch_and_assignment(
    session: Session,
    *,
    project_id: UUID,
    actor_id: UUID,
    task_id: UUID,
    batch_id: UUID,
    assignment_id: UUID,
    assignee_id: UUID,
    code: str,
    sequence_no: int = 1,
) -> None:
    session.add(
        AnnotationBatch(
            id=batch_id,
            project_id=project_id,
            task_id=task_id,
            code=code,
            name=f"批次 {code}",
            sequence_no=sequence_no,
            status="PLANNED",
            item_refs=[],
            planned_item_count=1,
            assignee_ids=[str(assignee_id)],
            reviewer_pool_ids=[],
            submitted_item_count=0,
            approved_item_count=0,
            rejected_item_count=0,
            rework_count=0,
            quality_summary={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    for status, version in (("READY", 2), ("ASSIGNED", 3), ("IN_PROGRESS", 4)):
        session.execute(
            update(AnnotationBatch)
            .where(AnnotationBatch.id == batch_id)
            .values(status=status, version=version)
        )
    session.add(
        AnnotationAssignment(
            id=assignment_id,
            project_id=project_id,
            task_id=task_id,
            batch_id=batch_id,
            assignee_id=assignee_id,
            assignment_role="ANNOTATOR",
            status="ASSIGNED",
            planned_item_count=1,
            claimed_item_count=0,
            submitted_item_count=0,
            approved_item_count=0,
            planned_minutes=30,
            actual_minutes=None,
            lease_expires_at=None,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=actor_id,
            updated_by=actor_id,
        )
    )
    session.flush()
    for status, version, claimed in (
        ("CLAIMED", 2, 1),
        ("IN_PROGRESS", 3, 1),
    ):
        session.execute(
            update(AnnotationAssignment)
            .where(AnnotationAssignment.id == assignment_id)
            .values(status=status, version=version, claimed_item_count=claimed)
        )


def _add_submission(
    session: Session,
    *,
    project_id: UUID,
    task_id: UUID,
    batch_id: UUID,
    assignment_id: UUID,
    annotator_id: UUID,
    schema_id: UUID,
    status: str,
    submission_version: int,
    submission_id: UUID | None = None,
) -> UUID:
    submission_id = submission_id or uuid4()
    session.add(
        AnnotationSubmission(
            id=submission_id,
            project_id=project_id,
            task_id=task_id,
            batch_id=batch_id,
            assignment_id=assignment_id,
            annotator_id=annotator_id,
            submission_version=submission_version,
            status="DRAFT",
            schema_id=schema_id,
            schema_version=1,
            schema_sha256=SCHEMA_SHA,
            item_count=0,
            records_digest=RECORDS_DIGEST,
            data_source="REAL",
            record_refs=[],
            review_id=None,
            arbitration_id=None,
            supersedes_id=None,
            submitted_at=None,
            decided_at=None,
            reason=None,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=annotator_id,
            updated_by=annotator_id,
        )
    )
    session.flush()
    if status != "DRAFT":
        # 守卫要求 DRAFT → SUBMITTED 迁移，不能直接插入终态。
        session.execute(
            update(AnnotationSubmission)
            .where(AnnotationSubmission.id == submission_id)
            .values(status=status, submitted_at=NOW, version=2)
        )
    return submission_id


def _seed_work_chain(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    code: str,
) -> dict[str, UUID]:
    """建齐 schema / sample_set / task / batch / assignment，返回各自的 ID。"""
    ids = {
        "schema_id": uuid4(),
        "sample_set_id": uuid4(),
        "task_id": uuid4(),
        "batch_id": uuid4(),
        "assignment_id": uuid4(),
    }
    with factory() as session:
        _seed_schema_and_sample_set(
            session,
            project_id=project_id,
            actor_id=actor_id,
            schema_id=ids["schema_id"],
            sample_set_id=ids["sample_set_id"],
        )
        _seed_task(
            session,
            project_id=project_id,
            actor_id=actor_id,
            task_id=ids["task_id"],
            sample_set_id=ids["sample_set_id"],
            schema_id=ids["schema_id"],
            code=f"task-{code}",
        )
        _seed_batch_and_assignment(
            session,
            project_id=project_id,
            actor_id=actor_id,
            task_id=ids["task_id"],
            batch_id=ids["batch_id"],
            assignment_id=ids["assignment_id"],
            assignee_id=actor_id,
            code=f"batch-{code}",
        )
        session.commit()
    return ids


def _seed_submissions(
    factory: sessionmaker[Session],
    *,
    project_id: UUID,
    actor_id: UUID,
    chain: dict[str, UUID],
    own_drafts: int = 0,
    submitted: int = 0,
) -> dict[str, list[UUID]]:
    """播种本人草稿与已提交（``SUBMITTED``）提交，返回按 ID 升序的 ID 列表。"""
    created: dict[str, list[UUID]] = {"drafts": [], "submitted": []}
    with factory() as session:
        version = 1
        for _ in range(own_drafts):
            created["drafts"].append(
                _add_submission(
                    session,
                    project_id=project_id,
                    task_id=chain["task_id"],
                    batch_id=chain["batch_id"],
                    assignment_id=chain["assignment_id"],
                    annotator_id=actor_id,
                    schema_id=chain["schema_id"],
                    status="DRAFT",
                    submission_version=version,
                )
            )
            version += 1
        for _ in range(submitted):
            created["submitted"].append(
                _add_submission(
                    session,
                    project_id=project_id,
                    task_id=chain["task_id"],
                    batch_id=chain["batch_id"],
                    assignment_id=chain["assignment_id"],
                    annotator_id=actor_id,
                    schema_id=chain["schema_id"],
                    status="SUBMITTED",
                    submission_version=version,
                )
            )
            version += 1
        session.commit()
    return created
