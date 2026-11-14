"""导入结果审核用例：创建审核、给出结论、生成质量报告。

审计与 outbox 复用共享实现；审核结论与质量报告都留痕，
因为"当时凭什么判定通过"是数据可信度的核心证据。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.audit import record_change
from app.application.datasources.ports import (
    AuditRepository,
    DataReviewRepository,
    DataSourceImportRepository,
    MediaAssetRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.application.datasources.quality import evaluate_import_quality
from app.domain.datasources.errors import (
    DomainValidationError,
    ResourceNotFoundError,
    VersionConflictError,
)
from app.domain.datasources.review import DataReview, QualityReport, ReviewDecision
from app.domain.identity.models import ProjectScope


def _utcnow() -> datetime:
    return datetime.now(UTC)


class DataReviewUseCases:
    def __init__(
        self,
        reviews: DataReviewRepository,
        imports: DataSourceImportRepository,
        assets: MediaAssetRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._reviews = reviews
        self._imports = imports
        self._assets = assets
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def get_review(self, scope: ProjectScope, import_id: UUID) -> DataReview | None:
        return self._reviews.get_by_import(scope, import_id)

    def ensure_review(
        self, scope: ProjectScope, import_id: UUID, *, request_id: str | None = None
    ) -> DataReview:
        """为导入创建审核记录（幂等：已存在则直接返回）。"""
        existing = self._reviews.get_by_import(scope, import_id)
        if existing is not None:
            return existing
        job = self._imports.get(scope, import_id)
        if job is None:
            raise ResourceNotFoundError("导入任务", import_id)
        now = _utcnow()
        review = self._reviews.add(
            scope,
            DataReview(
                review_id=uuid4(),
                project_id=scope.project_id,
                import_id=import_id,
                status="PENDING",
                decision=None,
                reason=None,
                reviewer_id=None,
                # 提交人 = 创建审核的人；后续审核人不得与之相同。
                submitted_by=scope.actor_id,
                created_at=now,
            ),
        )
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="data_source_review",
            resource_id=review.review_id,
            action="create",
            from_status=None,
            to_status=review.status.value,
            reason=None,
            request_id=request_id,
            details={"import_id": str(import_id)},
            event_type="datasource.review.created",
            event_payload={"review_id": str(review.review_id), "import_id": str(import_id)},
            occurred_at=now,
        )
        self._uow.commit()
        return review

    def decide(
        self,
        scope: ProjectScope,
        import_id: UUID,
        decision: ReviewDecision | str,
        *,
        reason: str | None,
        expected_version: int,
        request_id: str | None = None,
    ) -> DataReview:
        review = self._reviews.get_by_import(scope, import_id)
        if review is None:
            raise ResourceNotFoundError("审核记录", import_id)
        if review.row_version != expected_version:
            raise VersionConflictError("审核记录")
        now = _utcnow()
        decided = review.decide(
            decision=decision,
            reviewer_id=scope.actor_id,
            reason=reason,
            now=now,
        )
        saved = self._reviews.save(scope, decided, expected_version=expected_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type="data_source_review",
            resource_id=saved.review_id,
            action=f"decide:{saved.decision}",
            from_status=review.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={"import_id": str(import_id), "reviewer_id": str(scope.actor_id)},
            event_type="datasource.review.decided",
            event_payload={
                "review_id": str(saved.review_id),
                "import_id": str(import_id),
                "decision": saved.decision.value if saved.decision else None,
            },
            occurred_at=now,
        )
        self._uow.commit()
        return saved

    def quality_report(self, scope: ProjectScope, import_id: UUID) -> QualityReport:
        """生成质量报告；只基于库里已有事实，不做推测。"""
        job = self._imports.get(scope, import_id)
        if job is None:
            raise ResourceNotFoundError("导入任务", import_id)
        assets = self._assets.list_by_import(scope, import_id, limit=1000)
        if not assets and job.status.value != "SUCCEEDED":
            raise DomainValidationError(f"导入任务状态为 {job.status.value}，尚无可评估的媒体资产")
        return evaluate_import_quality(
            project_id=scope.project_id,
            import_id=import_id,
            sample_scope=job.scope.value,
            assets=assets,
        )
