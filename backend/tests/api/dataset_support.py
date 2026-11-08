"""数据集 HTTP 测试夹具：真实建库、Episode/RawAsset/已批准标注链与策略。"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from app.infrastructure.db.models.annotation_catalog import AnnotationSampleSet, AnnotationSchema
from app.infrastructure.db.models.annotation_results import AnnotationRecord, AnnotationSubmission
from app.infrastructure.db.models.annotation_work import (
    AnnotationAssignment,
    AnnotationBatch,
    AnnotationTask,
)
from app.infrastructure.db.models.episodes import EpisodeModel, EpisodeRawAssetModel
from app.infrastructure.db.models.ingestion import RawAssetModel

NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
SHA = "sha256:" + "a" * 64
SCHEMA_SHA = "sha256:" + "b" * 64
RECORD_SHA = "sha256:" + "c" * 64
SUBMISSION_DIGEST = "sha256:" + "d" * 64


def url(project_id: object, suffix: str) -> str:
    return f"/api/v1/projects/{project_id}{suffix}"


def _digest(seed: str) -> str:
    return "sha256:" + (seed * 64)[:64]


def seed_dataset_episodes(
    factory: sessionmaker[Session],
    project_id: UUID,
    actor_id: UUID,
    *,
    count: int = 10,
    prefix: str = "ep",
    scene: str = "kitchen",
    lineage_ids: list[UUID] | None = None,
    manifest_shas: list[str] | None = None,
    raw_hashes: list[str] | None = None,
) -> list[UUID]:
    """创建 count 个 ACCEPTED/PASS/已批准标注 Episode，返回 Episode ID 列表。"""
    episode_ids: list[UUID] = []
    with factory() as session:
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
                code=f"schema-{uuid4().hex[:8]}",
                name="数据集测试 Schema",
                description=None,
                status="DRAFT",
                annotation_levels=["EPISODE"],
                labels=[{"label_key": "action", "label_type": "ENUM", "required": False,
                         "level": "EPISODE", "visibility": "ANNOTATOR",
                         "value_spec": {"enum_values": ["PICK"]}}],
                compatibility="BACKWARD_COMPATIBLE",
                schema_sha256=SCHEMA_SHA,
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
        session.add(
            AnnotationSampleSet(
                id=sample_set_id,
                project_id=project_id,
                code=f"sample-{uuid4().hex[:8]}",
                name=None,
                status="DRAFT",
                selection_rule_version="stage3-sample-selection/1",
                strategy="FILTER",
                filters={"scene_labels": [scene]},
                strata=[],
                dedup_key="EPISODE_LINEAGE",
                candidate_count=count,
                selected_count=count,
                excluded_count=0,
                deduplicated_count=0,
                snapshot_sha256=None,
                snapshot_at=None,
                cancelled_reason=None,
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
            update(AnnotationSampleSet)
            .where(AnnotationSampleSet.id == sample_set_id)
            .values(status="PREVIEW", version=2)
        )
        session.execute(
            update(AnnotationSampleSet)
            .where(AnnotationSampleSet.id == sample_set_id)
            .values(
                status="SNAPSHOTTED",
                snapshot_sha256=SUBMISSION_DIGEST,
                snapshot_at=NOW,
                version=3,
            )
        )
        session.add(
            AnnotationTask(
                id=task_id,
                project_id=project_id,
                code=f"task-{uuid4().hex[:8]}",
                name="数据集测试任务",
                description=None,
                status="DRAFT",
                sample_set_id=sample_set_id,
                schema_id=schema_id,
                schema_version=1,
                schema_sha256=SCHEMA_SHA,
                gold_set_id=None,
                iaa_policy={},
                quality_policy={},
                assignment_mode="MANUAL",
                target_item_count=count,
                target_episode_count=count,
                due_at=None,
                priority="P1",
                progress={},
                quality_summary={},
                iaa_report_id=None,
                accepted_at=None,
                completed_at=None,
                status_reason=None,
                blocked_reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add(
            AnnotationBatch(
                id=batch_id,
                project_id=project_id,
                task_id=task_id,
                code=f"batch-{uuid4().hex[:8]}",
                name=None,
                sequence_no=1,
                status="PLANNED",
                stratum=None,
                item_refs=[],
                planned_item_count=count,
                assignee_ids=[str(actor_id)],
                reviewer_pool_ids=[],
                due_at=None,
                submitted_item_count=0,
                approved_item_count=0,
                rejected_item_count=0,
                rework_count=0,
                quality_summary={},
                status_reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()
        session.add(
            AnnotationAssignment(
                id=assignment_id,
                project_id=project_id,
                task_id=task_id,
                batch_id=batch_id,
                assignee_id=actor_id,
                assignment_role="ANNOTATOR",
                status="ASSIGNED",
                planned_item_count=count,
                claimed_item_count=0,
                submitted_item_count=0,
                approved_item_count=0,
                planned_minutes=30,
                actual_minutes=None,
                lease_expires_at=None,
                claimed_at=None,
                submitted_at=None,
                completed_at=None,
                revoked_reason=None,
                status_reason=None,
                version=1,
                created_at=NOW,
                updated_at=NOW,
                created_by=actor_id,
                updated_by=actor_id,
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
                annotator_id=actor_id,
                submission_version=1,
                status="DRAFT",
                schema_id=schema_id,
                schema_version=1,
                schema_sha256=SCHEMA_SHA,
                item_count=count,
                records_digest=SUBMISSION_DIGEST,
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
                created_by=actor_id,
                updated_by=actor_id,
            )
        )
        session.flush()

        lineage_occurrences: dict[UUID, int] = {}
        for index in range(count):
            episode_id = uuid4()
            asset_id = uuid4()
            lineage_id = (
                lineage_ids[index]
                if lineage_ids is not None and index < len(lineage_ids)
                else uuid4()
            )
            lineage_occurrences[lineage_id] = lineage_occurrences.get(lineage_id, 0) + 1
            episode_version = lineage_occurrences[lineage_id]
            manifest_sha = (
                manifest_shas[index]
                if manifest_shas is not None and index < len(manifest_shas)
                else _digest(f"{index:02d}")
            )
            raw_sha = (
                raw_hashes[index]
                if raw_hashes is not None and index < len(raw_hashes)
                else _digest(f"{index + 50:02d}")
            )
            episode_ids.append(episode_id)
            session.add(
                RawAssetModel(
                    id=asset_id,
                    project_id=project_id,
                    code=f"raw-{prefix}-{index}",
                    source_type="UPLOAD",
                    collection_task_id=None,
                    batch_id=None,
                    upload_session_id=None,
                    original_filename=f"{prefix}-{index}.mcap",
                    object_key=f"raw/{project_id}/{asset_id}/content.mcap",
                    size_bytes=1024 + index,
                    content_type="application/x-mcap",
                    format="MCAP",
                    sha256=raw_sha,
                    status="VALIDATING",
                    captured_at=NOW,
                    ingested_at=NOW,
                    storage_class="STANDARD",
                    retention_policy_id=None,
                    quarantine_reason=None,
                    quarantine_codes=[],
                    scan_report_key=None,
                    verified_at=NOW,
                    archived_at=None,
                    tombstoned_at=None,
                    duplicate_of_asset_id=None,
                    uploaded_by=actor_id,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            session.add(
                EpisodeModel(
                    id=episode_id,
                    project_id=project_id,
                    episode_lineage_id=lineage_id,
                    episode_version=episode_version,
                    code=f"{prefix}-{index}",
                    task_id=None,
                    batch_id=None,
                    robot_id=None,
                    device_ids=[],
                    sensor_ids=[],
                    status="PENDING",
                    quality_status="NOT_CHECKED",
                    annotation_status="NOT_STARTED",
                    success=True,
                    failure_reason=None,
                    scene_labels=[scene],
                    language_instruction=None,
                    start_time_ns=1_000_000 + index * 10_000,
                    end_time_ns=1_010_000 + index * 10_000,
                    duration_ns=10_000,
                    time_domain="UTC",
                    time_base={
                        "time_domain": "UTC",
                        "clock_source": "host",
                        "sync_method": "ptp",
                        "sync_status": "VERIFIED",
                    },
                    canonical_schema_version="ddp.canonical/1.0.0-draft",
                    pipeline_id="parser",
                    pipeline_version="1.0.0",
                    segmentation_rule_version="seg-v1",
                    frame_count=10,
                    calibration_snapshot=[
                        {"calibration_id": str(uuid4()), "status": "ACTIVE"}
                    ],
                    manifest_key=f"canonical/{project_id}/{episode_id}/1/manifest.json",
                    manifest_sha256=manifest_sha,
                    preview_refs=[],
                    supersedes_id=None,
                    attempt=1,
                    max_attempts=3,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            session.flush()
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="VALID", version=2)
            )
            session.add(
                EpisodeRawAssetModel(
                    project_id=project_id,
                    episode_id=episode_id,
                    asset_id=asset_id,
                    role="PRIMARY",
                    start_time_ns=1_000_000 + index * 10_000,
                    end_time_ns=1_010_000 + index * 10_000,
                    sha256_snapshot=raw_sha,
                    size_bytes_snapshot=1024 + index,
                )
            )
            session.flush()
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="PROCESSING")
            )
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="READY")
            )
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="QUALITY_CHECKING", quality_status="PASS")
            )
            session.execute(
                update(EpisodeModel)
                .where(EpisodeModel.id == episode_id)
                .values(status="ACCEPTED")
            )
            session.add(
                AnnotationRecord(
                    id=uuid4(),
                    project_id=project_id,
                    submission_id=submission_id,
                    task_id=task_id,
                    episode_id=episode_id,
                    episode_version=episode_version,
                    annotation_lineage_id=uuid4(),
                    annotation_version=1,
                    schema_id=schema_id,
                    schema_version=1,
                    status="DRAFT",
                    time_range=None,
                    labels=[],
                    evidence_refs=[],
                    record_sha256=RECORD_SHA,
                    supersedes_id=None,
                    approved_by=None,
                    approved_at=None,
                    version=1,
                    created_at=NOW,
                    updated_at=NOW,
                    created_by=actor_id,
                    updated_by=actor_id,
                )
            )
            session.flush()

        record_ids = session.query(AnnotationRecord.id).filter(
            AnnotationRecord.submission_id == submission_id
        ).all()
        session.execute(
            update(AnnotationSubmission)
            .where(AnnotationSubmission.id == submission_id)
            .values(
                status="SUBMITTED",
                submitted_at=NOW,
                record_refs=[str(item[0]) for item in record_ids],
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
        for record_id, in record_ids:
            session.execute(
                update(AnnotationRecord)
                .where(AnnotationRecord.id == record_id)
                .values(status="SUBMITTED", version=2)
            )
            session.execute(
                update(AnnotationRecord)
                .where(AnnotationRecord.id == record_id)
                .values(status="APPROVED", approved_by=actor_id, approved_at=NOW, version=3)
            )
        session.execute(
            update(EpisodeModel)
            .where(EpisodeModel.project_id == project_id)
            .values(annotation_status="APPROVED")
        )
        session.commit()
    return episode_ids


def create_policies(client, project_id: UUID, dataset_id: UUID) -> dict[str, object]:
    dedup = client.post(
        url(project_id, f"/datasets/{dataset_id}/dedup-policies"),
        json={
            "exact_keys": ["EPISODE_LINEAGE", "MANIFEST_SHA256", "RAW_ASSET_SHA256"],
            "near_duplicate_enabled": False,
            "near_duplicate_enforcement": "WARN_ONLY",
            "retain_rule": "NEWEST_ACCEPTED",
        },
    )
    assert dedup.status_code == 201, dedup.text
    split = client.post(
        url(project_id, f"/datasets/{dataset_id}/split-policies"),
        json={
            "strategy": "RATIO",
            "train_ratio": 0.8,
            "validation_ratio": 0.1,
            "test_ratio": 0.1,
            "seed": 42,
            "group_by": ["EPISODE_LINEAGE"],
            "empty_split_policy": "FAIL",
            "min_item_count": 1,
            "min_duration_ns": 0,
            "rounding_rule": "LARGEST_REMAINDER",
        },
    )
    assert split.status_code == 201, split.text
    leakage = client.post(
        url(project_id, f"/datasets/{dataset_id}/leakage-policies"),
        json={
            "hard_rules": [
                "EPISODE_LINEAGE_CROSS_SPLIT",
                "MANIFEST_SHA256_CROSS_SPLIT",
                "RAW_ASSET_SHA256_CROSS_SPLIT",
            ],
            "soft_rules": ["SCENE_GROUP_CROSS_SPLIT", "TEMPORAL_OVERLAP_CROSS_SPLIT"],
            "waiver_allowed_rules": ["SCENE_GROUP_CROSS_SPLIT", "TEMPORAL_OVERLAP_CROSS_SPLIT"],
            "scene_grouping_enabled": True,
        },
    )
    assert leakage.status_code == 201, leakage.text
    quality = client.post(
        url(project_id, f"/datasets/{dataset_id}/quality-policies"),
        json={"min_selected_count": 1, "allow_warn_quality": True},
    )
    assert quality.status_code == 201, quality.text
    return {
        "dedup": dedup.json(),
        "split": split.json(),
        "leakage": leakage.json(),
        "quality": quality.json(),
    }


def version_payload(policies: dict[str, object], **filters: object) -> dict[str, object]:
    return {
        "selection_rule_version": "dataset-selection/1",
        "selection_filters": dict(filters),
        "dedup_policy_id": policies["dedup"]["dedup_policy_id"],
        "dedup_policy_version": policies["dedup"]["version"],
        "split_policy_id": policies["split"]["split_policy_id"],
        "split_policy_version": policies["split"]["version"],
        "leakage_policy_id": policies["leakage"]["leakage_policy_id"],
        "leakage_policy_version": policies["leakage"]["version"],
        "quality_policy_id": policies["quality"]["quality_policy_id"],
        "quality_policy_version": policies["quality"]["version"],
    }


def create_dataset(client, project_id: UUID, owner_id: UUID, code: str) -> dict[str, object]:
    response = client.post(
        url(project_id, "/datasets"),
        json={
            "code": code,
            "name": f"数据集 {code}",
            "purpose": "TRAINING",
            "owner_id": str(owner_id),
            "usage_policy": "TRAINING_ALLOWED",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()
