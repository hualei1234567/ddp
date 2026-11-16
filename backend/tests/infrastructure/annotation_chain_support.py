"""缺口覆盖统计的 LABEL 轴测试构造器：把 Episode 标注链推进到 APPROVED。

为什么单独成模块：这条链跨 Schema / 样本集 / 任务 / 批次 / 分配 / 提交 / 记录
七张表，且有数据库触发器强制状态迁移（初始状态固定、非 DRAFT 时标签等字段不可
变）。它与 Episode 构造、传感器/动作库构造是两类完全不同的夹具，放在一起会把
``coverage_support.py`` 推过 500 行阈值。

状态推进顺序与生产写路径一致：插入 DRAFT → 沿触发器允许的迁移推进到 APPROVED，
``labels`` 只在 DRAFT 阶段写入。
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID, uuid4

from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSampleSet,
    AnnotationSchema,
)
from app.infrastructure.db.models.annotation_results import (
    AnnotationRecord,
    AnnotationSubmission,
)
from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment,
    AnnotationBatch,
    AnnotationTask,
)
from app.infrastructure.db.models.episodes import EpisodeModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from tests.infrastructure.coverage_support import NOW, SHA


def approve_episodes(
    session: Session,
    project_id: UUID,
    user_id: UUID,
    *,
    labels_by_episode: Mapping[UUID, list[dict[str, object]]],
) -> None:
    """为给定 Episode 建一条最小可用的标注链并推进到 APPROVED。

    调用方需先建好 ``project_members``（受让人外键）；``labels`` 只在 DRAFT 阶段
    写入，之后沿触发器允许的迁移推进，避免"直接插 APPROVED"绕过状态机。
    """
    episode_ids = list(labels_by_episode)
    schema_id = uuid4()
    sample_set_id = uuid4()
    task_id = uuid4()
    batch_id = uuid4()
    assignment_id = uuid4()
    submission_id = uuid4()
    session.add(
        AnnotationSchema(
            id=schema_id,
            project_id=project_id,
            schema_lineage_id=uuid4(),
            schema_version=1,
            code=f"schema-{schema_id.hex[:8]}",
            name="覆盖测试 Schema",
            status="DRAFT",
            annotation_levels=["EPISODE"],
            labels=[
                {
                    "label_key": "action",
                    "label_type": "ENUM",
                    "required": False,
                    "level": "EPISODE",
                    "value_spec": {"enum_values": ["PICK"]},
                }
            ],
            compatibility="BACKWARD_COMPATIBLE",
            schema_sha256=SHA,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
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
    session.add(
        AnnotationSampleSet(
            id=sample_set_id,
            project_id=project_id,
            code=f"sample-{sample_set_id.hex[:8]}",
            status="DRAFT",
            selection_rule_version="stage3-sample-selection/1",
            strategy="FILTER",
            filters={},
            strata=[],
            dedup_key="EPISODE_LINEAGE",
            candidate_count=len(episode_ids),
            selected_count=len(episode_ids),
            excluded_count=0,
            deduplicated_count=0,
            data_source="REAL",
            exclusion_summary={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
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
            snapshot_sha256=SHA,
            snapshot_at=NOW,
            version=3,
        )
    )
    session.add(
        AnnotationTask(
            id=task_id,
            project_id=project_id,
            code=f"task-{task_id.hex[:8]}",
            name="覆盖测试标注任务",
            status="DRAFT",
            sample_set_id=sample_set_id,
            schema_id=schema_id,
            schema_version=1,
            schema_sha256=SHA,
            iaa_policy={},
            quality_policy={},
            assignment_mode="MANUAL",
            target_item_count=len(episode_ids),
            target_episode_count=len(episode_ids),
            priority="P1",
            progress={},
            quality_summary={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    session.add(
        AnnotationBatch(
            id=batch_id,
            project_id=project_id,
            task_id=task_id,
            code=f"batch-{batch_id.hex[:8]}",
            sequence_no=1,
            status="PLANNED",
            item_refs=[],
            planned_item_count=len(episode_ids),
            assignee_ids=[str(user_id)],
            reviewer_pool_ids=[],
            submitted_item_count=0,
            approved_item_count=0,
            rejected_item_count=0,
            rework_count=0,
            quality_summary={},
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    session.add(
        AnnotationAssignment(
            id=assignment_id,
            project_id=project_id,
            task_id=task_id,
            batch_id=batch_id,
            assignee_id=user_id,
            assignment_role="ANNOTATOR",
            status="ASSIGNED",
            planned_item_count=len(episode_ids),
            claimed_item_count=0,
            submitted_item_count=0,
            approved_item_count=0,
            planned_minutes=30,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    session.add(
        AnnotationSubmission(
            id=submission_id,
            project_id=project_id,
            task_id=task_id,
            batch_id=batch_id,
            assignment_id=assignment_id,
            annotator_id=user_id,
            submission_version=1,
            status="DRAFT",
            schema_id=schema_id,
            schema_version=1,
            schema_sha256=SHA,
            item_count=len(episode_ids),
            records_digest=SHA,
            data_source="REAL",
            record_refs=[],
            version=1,
            created_at=NOW,
            updated_at=NOW,
            created_by=user_id,
            updated_by=user_id,
        )
    )
    session.flush()
    record_ids: list[UUID] = []
    for episode_id in episode_ids:
        record_id = uuid4()
        record_ids.append(record_id)
        session.add(
            AnnotationRecord(
                id=record_id,
                project_id=project_id,
                submission_id=submission_id,
                task_id=task_id,
                episode_id=episode_id,
                episode_version=1,
                annotation_lineage_id=uuid4(),
                annotation_version=1,
                schema_id=schema_id,
                schema_version=1,
                status="DRAFT",
                labels=labels_by_episode[episode_id],
                evidence_refs=[],
                record_sha256=SHA,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=user_id,
                updated_by=user_id,
            )
        )
    session.flush()
    session.execute(
        update(AnnotationSubmission)
        .where(AnnotationSubmission.id == submission_id)
        .values(
            status="SUBMITTED",
            submitted_at=NOW,
            record_refs=[str(item) for item in record_ids],
            version=2,
        )
    )
    session.execute(
        update(AnnotationSubmission)
        .where(AnnotationSubmission.id == submission_id)
        .values(status="IN_REVIEW", version=3)
    )
    session.execute(
        update(AnnotationSubmission)
        .where(AnnotationSubmission.id == submission_id)
        .values(status="APPROVED", decided_at=NOW, version=4)
    )
    for record_id in record_ids:
        session.execute(
            update(AnnotationRecord)
            .where(AnnotationRecord.id == record_id)
            .values(status="SUBMITTED", version=2)
        )
        session.execute(
            update(AnnotationRecord)
            .where(AnnotationRecord.id == record_id)
            .values(
                status="APPROVED",
                approved_by=user_id,
                approved_at=NOW,
                version=3,
            )
        )
    session.execute(
        update(EpisodeModel)
        .where(
            EpisodeModel.project_id == project_id,
            EpisodeModel.id.in_(episode_ids),
        )
        .values(annotation_status="APPROVED")
    )
    session.flush()


__all__ = ["approve_episodes"]
