"""导入结果审核用例：创建审核、给出结论、生成质量报告。

审计与 outbox 复用共享实现；审核结论与质量报告都留痕，
因为"当时凭什么判定通过"是数据可信度的核心证据。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.audit import (
    AuditLogQueryRepository,
    AuditQuery,
    list_audit_logs,
    record_change,
)
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

#: 审核证据存放在审计留痕的 ``details`` 里（现有 JSON 列，无新增迁移）。
#: 读取时按资源时间线取最近一条决策记录回填。
_EVIDENCE_RESOURCE_TYPE = "data_source_review"
_EVIDENCE_AUDIT_ACTION_PREFIX = "decide:"


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
        audit_query: AuditLogQueryRepository | None = None,
    ) -> None:
        self._reviews = reviews
        self._imports = imports
        self._assets = assets
        self._audit = audit
        self._outbox = outbox
        self._uow = uow
        self._audit_query = audit_query

    def _with_evidence(self, scope: ProjectScope, review: DataReview | None) -> DataReview | None:
        """从审计留痕回填审核证据。

        evidence 持久化在 ``audit_logs.details``（现有 JSON 列）。审核结论是
        终态，因此只需取该资源最近一条 ``decide:*`` 记录。未注入查询仓储
        （例如纯领域单测）时原样返回，不伪造空证据。
        """
        if review is None or review.decision is None or self._audit_query is None:
            return review
        entries = list_audit_logs(
            self._audit_query,
            scope,
            AuditQuery(resource_type=_EVIDENCE_RESOURCE_TYPE, resource_id=review.review_id),
            limit=20,
        )
        for entry in entries:
            if not entry.action.startswith(_EVIDENCE_AUDIT_ACTION_PREFIX):
                continue
            raw = dict(entry.details).get("evidence")
            if raw is None:
                return review
            if not isinstance(raw, Mapping):
                # 审计行被外部篡改成非对象：宁可让契约校验拒绝，也不静默当作无证据。
                raise DomainValidationError("审计留痕中的 evidence 不是对象")
            return replace(review, evidence=raw)
        return review

    def get_review(self, scope: ProjectScope, import_id: UUID) -> DataReview | None:
        return self._with_evidence(scope, self._reviews.get_by_import(scope, import_id))

    def ensure_review(
        self, scope: ProjectScope, import_id: UUID, *, request_id: str | None = None
    ) -> DataReview:
        """为导入创建审核记录（幂等：已存在则直接返回）。"""
        existing = self._reviews.get_by_import(scope, import_id)
        if existing is not None:
            return self._with_evidence(scope, existing) or existing
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
        evidence: Mapping[str, object] | None = None,
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
            evidence=evidence,
        )
        saved = self._reviews.save(scope, decided, expected_version=expected_version)
        details: dict[str, object] = {
            "import_id": str(import_id),
            "reviewer_id": str(scope.actor_id),
        }
        if saved.evidence is not None:
            # 审核证据与结论同事务落库到审计留痕；不新增审核表列。
            details["evidence"] = dict(saved.evidence)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_EVIDENCE_RESOURCE_TYPE,
            resource_id=saved.review_id,
            action=f"decide:{saved.decision}",
            from_status=review.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details=details,
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
