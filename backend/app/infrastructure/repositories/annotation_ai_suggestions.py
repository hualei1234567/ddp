"""AI 预标注建议 SQLAlchemy 仓储。

与 ``annotation_results.py`` 分开成文件：后者已 706 行，逼近 800 行拆分阈值；
建议表的读写与提交/记录/复核/仲裁没有共享状态，独立成模块更易审查。
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.annotation.ai_suggestion import AiSuggestion
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_ai import AnnotationAiSuggestion as _Model
from app.infrastructure.db.models.annotation_results import AnnotationRecord as _RecordModel
from app.infrastructure.repositories._annotation_sql import (
    as_utc,
    ensure_expected_version,
    ensure_initial_version,
    flush_or_duplicate,
    raise_update_failure,
    scoped_select,
)


class SqlAnnotationAiSuggestionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(
        self,
        scope: ProjectScope,
        suggestions: tuple[AiSuggestion, ...],
    ) -> None:
        now = datetime.now(UTC)
        for suggestion in suggestions:
            scope.ensure(suggestion.project_id)
            ensure_initial_version(suggestion.version)
            self._session.add(
                _Model(
                    id=suggestion.suggestion_id,
                    project_id=suggestion.project_id,
                    annotation_id=suggestion.annotation_id,
                    label_key=suggestion.label_key,
                    suggested_value=suggestion.suggested_value,
                    model_id=suggestion.model_id,
                    model_version=suggestion.model_version,
                    prompt_version=suggestion.prompt_version,
                    confidence=suggestion.confidence,
                    generated_at=suggestion.generated_at,
                    human_confirmed=suggestion.human_confirmed,
                    human_value=suggestion.human_value,
                    decided_by=suggestion.decided_by,
                    decided_at=suggestion.decided_at,
                    version=suggestion.version,
                    created_at=suggestion.created_at or now,
                    updated_at=suggestion.updated_at or now,
                    created_by=scope.actor_id,
                    updated_by=scope.actor_id,
                )
            )
        flush_or_duplicate(self._session)

    def get(self, scope: ProjectScope, suggestion_id: UUID) -> AiSuggestion | None:
        row = self._session.scalar(
            select(_Model).where(
                _Model.id == suggestion_id,
                _Model.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def list_for_annotation(
        self,
        scope: ProjectScope,
        annotation_id: UUID,
    ) -> list[AiSuggestion]:
        rows = self._session.scalars(
            scoped_select(_Model, scope)
            .where(_Model.annotation_id == annotation_id)
            .order_by(_Model.id)
        )
        return [self._from_row(row) for row in rows]

    def list_for_submission(
        self,
        scope: ProjectScope,
        submission_id: UUID,
    ) -> list[AiSuggestion]:
        """按提交查询：经 ``annotation_records`` 关联到提交，两次都带项目过滤。"""
        rows = self._session.scalars(
            scoped_select(_Model, scope)
            .join(
                _RecordModel,
                (_Model.project_id == _RecordModel.project_id)
                & (_Model.annotation_id == _RecordModel.id),
            )
            .where(_RecordModel.submission_id == submission_id)
            .order_by(_Model.annotation_id, _Model.label_key, _Model.model_id, _Model.id)
        )
        return [self._from_row(row) for row in rows]

    def save(
        self,
        scope: ProjectScope,
        suggestion: AiSuggestion,
        *,
        expected_version: int,
    ) -> AiSuggestion:
        scope.ensure(suggestion.project_id)
        ensure_expected_version(suggestion.version, expected_version)
        now = suggestion.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(_Model)
            .where(
                _Model.id == suggestion.suggestion_id,
                _Model.project_id == scope.project_id,
                _Model.version == expected_version,
            )
            .values(
                human_confirmed=suggestion.human_confirmed,
                human_value=suggestion.human_value,
                decided_by=suggestion.decided_by,
                decided_at=suggestion.decided_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                _Model,
                scope,
                suggestion.suggestion_id,
                "AI 建议",
            )
        return replace(
            suggestion,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: _Model) -> AiSuggestion:
        return AiSuggestion(
            suggestion_id=row.id,
            project_id=row.project_id,
            annotation_id=row.annotation_id,
            label_key=row.label_key,
            suggested_value=row.suggested_value,
            model_id=row.model_id,
            model_version=row.model_version,
            prompt_version=row.prompt_version,
            confidence=row.confidence,
            generated_at=as_utc(row.generated_at),
            human_confirmed=bool(row.human_confirmed),
            human_value=row.human_value,
            decided_by=row.decided_by,
            decided_at=as_utc(row.decided_at),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
