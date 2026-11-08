"""标签 Schema SQLAlchemy 仓储。"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.schema import AnnotationSchema, LabelDefinition
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_catalog import (
    AnnotationSchema as AnnotationSchemaModel,
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


class SqlAnnotationSchemaRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, scope: ProjectScope, schema: AnnotationSchema) -> AnnotationSchema:
        scope.ensure(schema.project_id)
        ensure_initial_version(schema.version)
        now = datetime.now(UTC)
        row = AnnotationSchemaModel(
            id=schema.schema_id,
            project_id=schema.project_id,
            schema_lineage_id=schema.schema_lineage_id,
            schema_version=schema.schema_version,
            code=schema.code,
            name=schema.name,
            description=schema.description,
            status=enum_value(schema.status),
            annotation_levels=[item.value for item in schema.annotation_levels],
            labels=[item.to_payload() for item in schema.labels],
            compatibility=schema.compatibility.value,
            schema_sha256=schema.schema_sha256,
            supersedes_id=schema.supersedes_id,
            published_at=schema.published_at,
            deprecated_at=schema.deprecated_at,
            revoked_at=schema.revoked_at,
            version=schema.version,
            created_at=schema.created_at or now,
            updated_at=schema.updated_at or now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        self._session.add(row)
        flush_or_duplicate(self._session)
        return schema

    def get(self, scope: ProjectScope, schema_id: UUID) -> AnnotationSchema | None:
        row = self._session.scalar(
            select(AnnotationSchemaModel).where(
                AnnotationSchemaModel.id == schema_id,
                AnnotationSchemaModel.project_id == scope.project_id,
            )
        )
        return self._from_row(row) if row is not None else None

    def get_by_lineage_version(
        self,
        scope: ProjectScope,
        *,
        schema_lineage_id: UUID,
        schema_version: int,
    ) -> AnnotationSchema | None:
        row = self._session.scalar(
            select(AnnotationSchemaModel).where(
                AnnotationSchemaModel.project_id == scope.project_id,
                AnnotationSchemaModel.schema_lineage_id == schema_lineage_id,
                AnnotationSchemaModel.schema_version == schema_version,
            )
        )
        return self._from_row(row) if row is not None else None

    def list(
        self,
        scope: ProjectScope,
        *,
        status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AnnotationSchema]:
        if limit < 1 or limit > 101:
            raise DomainValidationError("limit 必须在 1 到 101 之间")
        statement = scoped_select(AnnotationSchemaModel, scope)
        if status is not None:
            statement = statement.where(AnnotationSchemaModel.status == status)
        if after_id is not None:
            statement = statement.where(AnnotationSchemaModel.id > after_id)
        statement = statement.order_by(AnnotationSchemaModel.id).limit(limit)
        return [self._from_row(row) for row in self._session.scalars(statement)]

    def save(
        self,
        scope: ProjectScope,
        schema: AnnotationSchema,
        *,
        expected_version: int,
    ) -> AnnotationSchema:
        scope.ensure(schema.project_id)
        ensure_expected_version(schema.version, expected_version)
        now = schema.updated_at or datetime.now(UTC)
        result = self._session.execute(
            update(AnnotationSchemaModel)
            .where(
                AnnotationSchemaModel.id == schema.schema_id,
                AnnotationSchemaModel.project_id == scope.project_id,
                AnnotationSchemaModel.version == expected_version,
            )
            .values(
                name=schema.name,
                description=schema.description,
                status=enum_value(schema.status),
                annotation_levels=[item.value for item in schema.annotation_levels],
                labels=[item.to_payload() for item in schema.labels],
                compatibility=schema.compatibility.value,
                schema_sha256=schema.schema_sha256,
                published_at=schema.published_at,
                deprecated_at=schema.deprecated_at,
                revoked_at=schema.revoked_at,
                updated_at=now,
                updated_by=scope.actor_id,
                version=expected_version + 1,
            )
        )
        if result.rowcount == 0:
            raise_update_failure(
                self._session,
                AnnotationSchemaModel,
                scope,
                schema.schema_id,
                "标注 Schema",
            )
        return replace(
            schema,
            version=expected_version + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )

    def _from_row(self, row: AnnotationSchemaModel) -> AnnotationSchema:
        labels = tuple(
            LabelDefinition(**item) for item in (row.labels or []) if isinstance(item, dict)
        )
        return AnnotationSchema(
            schema_id=row.id,
            project_id=row.project_id,
            code=row.code,
            name=row.name,
            description=row.description,
            schema_lineage_id=row.schema_lineage_id,
            schema_version=row.schema_version,
            status=row.status,
            annotation_levels=tuple(row.annotation_levels or ()),
            labels=labels,
            compatibility=row.compatibility,
            schema_sha256=row.schema_sha256,
            supersedes_id=row.supersedes_id,
            published_at=as_utc(row.published_at),
            deprecated_at=as_utc(row.deprecated_at),
            revoked_at=as_utc(row.revoked_at),
            version=row.version,
            created_at=as_utc(row.created_at),
            updated_at=as_utc(row.updated_at),
            created_by=row.created_by,
            updated_by=row.updated_by,
        )
