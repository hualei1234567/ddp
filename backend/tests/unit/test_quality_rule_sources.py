"""规则源适配器测试：验证三套既有规则被正确接入，不重写判定。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from app.application.quality.facts import QualitySubjectFacts
from app.application.quality.registry import default_registry
from app.application.quality.sources.annotations import AnnotationQualityRuleSource
from app.application.quality.sources.datasets import DatasetQualityRuleSource
from app.application.quality.sources.imports import ImportQualityRuleSource
from app.domain.annotation.submission import AnnotationRecord, LabelValue
from app.domain.datasources.media import MediaAsset
from app.domain.identity.models import ProjectScope
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySubjectType,
    coerce_subject_type,
)
from app.domain.quality.rules import is_sha256_version

NOW = datetime(2026, 11, 18, tzinfo=UTC)
SHA = "sha256:" + "a" * 64


def _scope() -> ProjectScope:
    return ProjectScope(project_id=uuid4(), actor_id=uuid4())


def _asset(
    scope: ProjectScope,
    *,
    import_id: UUID | None = None,
    camera: str = "main",
) -> MediaAsset:
    return MediaAsset(
        asset_id=uuid4(),
        project_id=scope.project_id,
        data_source_id=uuid4(),
        import_id=import_id or uuid4(),
        episode_key="ep-1",
        path="videos/ep-1-main.mp4",
        camera=camera,
        object_key=f"data-sources/{scope.project_id}/v.mp4",
        size_bytes=1024,
        sha256=SHA,
        content_type="video/mp4",
        task_name="demo",
        created_at=NOW,
    )


def _record(scope: ProjectScope) -> AnnotationRecord:
    return AnnotationRecord(
        annotation_id=uuid4(),
        annotation_lineage_id=uuid4(),
        annotation_version=1,
        project_id=scope.project_id,
        submission_id=uuid4(),
        episode_id=uuid4(),
        episode_version=1,
        task_id=uuid4(),
        schema_id=uuid4(),
        schema_version=1,
        labels=(LabelValue(label_key="action", value="PICK"),),
    )


def _import_facts(
    scope: ProjectScope,
    assets: tuple[MediaAsset, ...],
    *,
    import_id: UUID,
) -> QualitySubjectFacts:
    job = SimpleNamespace(scope=SimpleNamespace(value="SAMPLE"))
    return QualitySubjectFacts(
        subject_type=QualitySubjectType.DATASOURCE_IMPORT,
        subject_id=import_id,
        import_job=job,  # type: ignore[arg-type]
        assets=assets,
    )


def test_import_source_maps_existing_seven_rules() -> None:
    scope = _scope()
    import_id = uuid4()
    asset = _asset(scope, import_id=import_id)
    facts = _import_facts(scope, (asset,), import_id=import_id)
    source = ImportQualityRuleSource()

    drafts = source.evaluate(scope, facts)

    assert len(drafts) == 7
    assert {draft.rule_id for draft in drafts} == {item.rule_id for item in source.declarations}
    assert all(draft.status is QualityFindingStatus.PASS for draft in drafts)
    assert {draft.category for draft in drafts} == {
        QualityCategory.INTEGRITY,
        QualityCategory.TEMPORAL,
        QualityCategory.VISUAL,
    }
    assert all(draft.score == 1.0 for draft in drafts)
    # N4-003：每流同步证据必须随统一质量结论落库，而不是只留在临时报告里。
    sync_draft = next(draft for draft in drafts if draft.rule_id == "stream_sync_aligned")
    assert sync_draft.evidence is not None
    assert sync_draft.evidence["episodes_checked"] == 1


def test_episode_without_import_assets_is_not_applicable_not_pass() -> None:
    scope = _scope()
    facts = QualitySubjectFacts(
        subject_type=QualitySubjectType.EPISODE,
        subject_id=uuid4(),
    )

    drafts = ImportQualityRuleSource().evaluate(scope, facts)

    assert len(drafts) == 7
    assert all(draft.status is QualityFindingStatus.NOT_APPLICABLE for draft in drafts)
    assert all(draft.score is None for draft in drafts)
    assert all(
        draft.evidence == {"reason": "episode_has_no_import_media_assets"} for draft in drafts
    )


def test_annotation_source_runs_existing_rules_without_context() -> None:
    scope = _scope()
    record = _record(scope)
    facts = QualitySubjectFacts(
        subject_type=QualitySubjectType.EPISODE,
        subject_id=record.episode_id,
        annotation_records=(record,),
        episode_bounds={record.episode_id: (0, 1_000_000)},
    )

    drafts = AnnotationQualityRuleSource().evaluate(scope, facts)

    assert len(drafts) == 4
    statuses = {(draft.rule_id, draft.status) for draft in drafts}
    assert ("required_labels_present", QualityFindingStatus.NOT_APPLICABLE) in statuses
    assert ("time_range_within_episode", QualityFindingStatus.NOT_APPLICABLE) in statuses
    assert ("ai_label_confidence_low", QualityFindingStatus.NOT_APPLICABLE) in statuses
    assert ("duplicate_label_keys", QualityFindingStatus.PASS) in statuses
    assert all(draft.subject_ref == str(record.annotation_id) for draft in drafts)
    assert all(draft.category is QualityCategory.ANNOTATION for draft in drafts)


def test_annotation_source_accepts_nanosecond_evidence() -> None:
    scope = _scope()
    record = _record(scope)
    record = replace(
        record,
        time_range={
            "start_time_ns": 1_500_000_000_000_000_000,
            "end_time_ns": 1_600_000_000_000_000_000,
        },
        record_sha256=None,
    )
    facts = QualitySubjectFacts(
        subject_type=QualitySubjectType.EPISODE,
        subject_id=record.episode_id,
        annotation_records=(record,),
        episode_bounds={record.episode_id: (0, 2_000_000_000_000_000_000)},
    )

    drafts = AnnotationQualityRuleSource().evaluate(scope, facts)

    time_draft = next(item for item in drafts if item.rule_id == "time_range_within_episode")
    assert time_draft.status is QualityFindingStatus.PASS
    assert time_draft.evidence["record_start_time_ns"] == 1_500_000_000_000_000_000


def test_annotation_source_without_records_is_not_applicable() -> None:
    scope = _scope()
    facts = QualitySubjectFacts(
        subject_type=QualitySubjectType.EPISODE,
        subject_id=uuid4(),
    )
    drafts = AnnotationQualityRuleSource().evaluate(scope, facts)
    assert len(drafts) == 4
    assert all(draft.status is QualityFindingStatus.NOT_APPLICABLE for draft in drafts)


class _Reports:
    def __init__(self, report: object | None) -> None:
        self._report = report

    def latest_quality(self, scope: ProjectScope, dataset_version_id: object) -> object | None:
        return self._report


def _dataset_facts() -> QualitySubjectFacts:
    return QualitySubjectFacts(
        subject_type=QualitySubjectType.DATASET_VERSION,
        subject_id=uuid4(),
    )


def test_dataset_source_maps_ready_report() -> None:
    report = SimpleNamespace(
        is_ready=True,
        blocking_issues=(),
        warnings=("W-1",),
        generator_version="dataset-quality/1.0.0",
        report_sha256=SHA,
        candidate_count=10,
        selected_count=8,
        status=SimpleNamespace(value="READY"),
    )
    drafts = DatasetQualityRuleSource(_Reports(report)).evaluate(_scope(), _dataset_facts())  # type: ignore[arg-type]
    assert len(drafts) == 1
    assert drafts[0].status is QualityFindingStatus.PASS
    assert drafts[0].score == 1.0
    assert drafts[0].category is QualityCategory.METADATA
    assert drafts[0].evidence["selected_count"] == 8


def test_dataset_source_failed_report_and_missing_report() -> None:
    failed = SimpleNamespace(
        is_ready=False,
        blocking_issues=("DATASET_EMPTY",),
        warnings=(),
        generator_version="dataset-quality/0.9.0",
        report_sha256=SHA,
        candidate_count=0,
        selected_count=0,
        status=SimpleNamespace(value="FAILED"),
    )
    drafts = DatasetQualityRuleSource(_Reports(failed)).evaluate(_scope(), _dataset_facts())  # type: ignore[arg-type]
    assert drafts[0].status is QualityFindingStatus.FAIL
    assert drafts[0].rule_version == "dataset-quality/0.9.0"

    missing = DatasetQualityRuleSource(_Reports(None)).evaluate(_scope(), _dataset_facts())  # type: ignore[arg-type]
    assert missing[0].status is QualityFindingStatus.ERROR
    assert missing[0].score is None


def test_default_registry_versions_are_subject_specific_content_hashes() -> None:
    registry = default_registry(_Reports(None))  # type: ignore[arg-type]

    episode = registry.declarations_for(QualitySubjectType.EPISODE)
    import_only = registry.declarations_for(QualitySubjectType.DATASOURCE_IMPORT)
    dataset = registry.declarations_for(QualitySubjectType.DATASET_VERSION)

    assert {item.category for item in episode} >= {
        QualityCategory.INTEGRITY,
        QualityCategory.ANNOTATION,
    }
    versions = {item: registry.rule_set_version_for(item) for item in QualitySubjectType}
    assert all(is_sha256_version(value) for value in versions.values())
    assert registry.rule_set_version_for(
        QualitySubjectType.EPISODE
    ) != registry.rule_set_version_for(QualitySubjectType.DATASOURCE_IMPORT)
    assert [item.rule_id for item in dataset] == ["dataset_quality_gate"]
    assert {item.rule_id for item in import_only} == {
        item.rule_id for item in ImportQualityRuleSource.declarations
    }
    assert coerce_subject_type("EPISODE") is QualitySubjectType.EPISODE
