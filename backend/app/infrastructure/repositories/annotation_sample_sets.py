"""标注样本集与 Episode 引用 SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.sample_set import AnnotationSampleSet, SampleEpisodeRef
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSampleEpisode,
)
from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSampleSet as AnnotationSampleSetModel,
)
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    enum_value,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


class SqlAnnotationSampleSetRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(
        self,
        scope: ProjectScope,
        sample_set: AnnotationSampleSet,
    ) -> AnnotationSampleSet:
        scope.ensure(sample_set.project_id)
        ensure_initial_version(sample_set.version)
        now = datetime.now(UTC)
        row = AnnotationSampleSetModel(
            id=sample_set.sample_set_id,
            project_id=sample_set.project_id,
            code=sample_set.code,
            name=sample_set.name,
            status=enum_value(sample_set.status),
            selection_rule_version=sample_set.selection_rule_version,
            strategy=sample_set.strategy.value,
            filters=dict(sample_set.filters),
            strata=[dict(item) for item in sample_set.strata],
            dedup_key=sample_set.dedup_key.value,
            candidate_count=sample_set.candidate_count,
            selected_count=sample_set.selected_count,
            excluded_count=sample_set.excluded_count,
            deduplicated_count=sample_set.deduplicated_count,
            snapshot_sha256=sample_set.snapshot_sha256,
            snapshot_at=sample_set.snapshot_at,
            cancelled_reason=sample_set.cancelled_reason,
            data_source=sample_set.data_source.value,
            exclusion_summary=dict(sample_set.exclusion_summary),
            version=sample_set.version,
            created_at=sample_set.created_at or now,
            updated_at=sample_set.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return sample_set

    def get(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
    ) -> AnnotationSampleSet | None:
        row = self._session.scalar(
            select(AnnotationSampleSetModel).where(
                AnnotationSampleSetModel.id == sample_set_id,
                AnnotationSampleSetModel.project_id == scope.project_id,
            )
        )
        if row is None:
            return None
        return self._from_row(row, self.list_refs(scope, sample_set_id))

    def get_by_code(self, scope: ProjectScope, code: str) -> AnnotationSampleSet | None:
        row = self._session.scalar(
            select(AnnotationSampleSetModel).where(
                AnnotationSampleSetModel.project_id == scope.project_id,
                AnnotationSampleSetModel.code == code.strip(),
            )
        )
        if row is None:
            return None
        return self._from_row(row, self.list_refs(scope, row.id))

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationSampleSet]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(AnnotationSampleSetModel, scope)
        if status is not None:
            statement = statement.where(AnnotationSampleSetModel.status == status)
        if after_id is not None:
            statement = statement.where(AnnotationSampleSetModel.id > after_id)
        statement = statement.order_by(AnnotationSampleSetModel.id).limit(limit)
        return [
            self._from_row(row, self.list_refs(scope, row.id))
            for row in self._session.scalars(statement)
        ]

    def save(
        self,
        scope: ProjectScope,
        sample_set: AnnotationSampleSet,
        *,
        expected_version: int,
    ) -> AnnotationSampleSet:
        scope.ensure(sample_set.project_id)
        ensure_expected_version(sample_set.version, expected_version)
        now = sample_set.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationSampleSetModel)
            .where(
                AnnotationSampleSetModel.id == sample_set.sample_set_id,
                AnnotationSampleSetModel.project_id == scope.project_id,
                AnnotationSampleSetModel.version == expected_version,
            )
            .values(
                name=sample_set.name,
                status=enum_value(sample_set.status),
                selection_rule_version=sample_set.selection_rule_version,
                strategy=sample_set.strategy.value,
                filters=dict(sample_set.filters),
                strata=[dict(item) for item in sample_set.strata],
                dedup_key=sample_set.dedup_key.value,
                candidate_count=sample_set.candidate_count,
                selected_count=sample_set.selected_count,
                excluded_count=sample_set.excluded_count,
                deduplicated_count=sample_set.deduplicated_count,
                snapshot_sha256=sample_set.snapshot_sha256,
                snapshot_at=sample_set.snapshot_at,
                cancelled_reason=sample_set.cancelled_reason,
                data_source=sample_set.data_source.value,
                exclusion_summary=dict(sample_set.exclusion_summary),
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationSampleSetModel,
                scope,
                sample_set.sample_set_id,
                "标注样本集",
            )
        return replace(
            sample_set,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def replace_refs(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
        refs: tuple[SampleEpisodeRef, ...],
    ) -> None:
        if any(ref.project_id != scope.project_id for ref in refs):
            raise DomainValidationError("样本引用必须与当前项目一致")
        self._session.execute(
            delete(AnnotationSampleEpisode).where(
                AnnotationSampleEpisode.project_id == scope.project_id,
                AnnotationSampleEpisode.sample_set_id == sample_set_id,
            )
        )
        self._session.add_all(
            [
                AnnotationSampleEpisode(
                    ref_id=ref.ref_id or uuid4(),
                    project_id=scope.project_id,
                    sample_set_id=sample_set_id,
                    episode_id=ref.episode_id,
                    episode_lineage_id=ref.episode_lineage_id,
                    episode_version=ref.episode_version,
                    start_time_ns=ref.start_time_ns,
                    end_time_ns=ref.end_time_ns,
                    time_domain=ref.time_domain.value,
                    stratum=ref.stratum,
                    sample_role=ref.sample_role.value,
                    selected_reason=ref.selected_reason,
                )
                for ref in refs
            ]
        )
        self._session.flush()

    def list_refs(
        self,
        scope: ProjectScope,
        sample_set_id: UUID,
    ) -> list[SampleEpisodeRef]:
        rows = self._session.scalars(
            select(AnnotationSampleEpisode)
            .where(
                AnnotationSampleEpisode.project_id == scope.project_id,
                AnnotationSampleEpisode.sample_set_id == sample_set_id,
            )
            .order_by(
                AnnotationSampleEpisode.episode_lineage_id,
                AnnotationSampleEpisode.episode_version,
                AnnotationSampleEpisode.episode_id,
            )
        )
        return [self._from_ref_row(row) for row in rows]

    def _from_row(
        self,
        row: AnnotationSampleSetModel,
        refs: list[SampleEpisodeRef],
    ) -> AnnotationSampleSet:
        return AnnotationSampleSet(
            sample_set_id=row.id,
            project_id=row.project_id,
            code=row.code,
            name=row.name,
            status=row.status,
            selection_rule_version=row.selection_rule_version,
            strategy=row.strategy,
            filters=row.filters or {},
            strata=tuple(row.strata or ()),
            episode_refs=tuple(refs),
            dedup_key=row.dedup_key,
            candidate_count=row.candidate_count,
            selected_count=row.selected_count,
            excluded_count=row.excluded_count,
            deduplicated_count=row.deduplicated_count,
            snapshot_sha256=row.snapshot_sha256,
            snapshot_at=as_utc(row.snapshot_at),
            cancelled_reason=row.cancelled_reason,
            data_source=row.data_source,
            exclusion_summary=row.exclusion_summary or {},
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )

    def _from_ref_row(self, row: AnnotationSampleEpisode) -> SampleEpisodeRef:
        return SampleEpisodeRef(
            ref_id=row.ref_id,
            project_id=row.project_id,
            sample_set_id=row.sample_set_id,
            episode_id=row.episode_id,
            episode_lineage_id=row.episode_lineage_id,
            episode_version=row.episode_version,
            sample_role=row.sample_role,
            selected_reason=row.selected_reason,
            stratum=row.stratum,
            start_time_ns=row.start_time_ns,
            end_time_ns=row.end_time_ns,
            time_domain=row.time_domain,
        )
