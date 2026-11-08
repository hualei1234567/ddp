"""数据集策略用例：去重、划分、泄漏与质量策略的创建/查询。"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.commands import (
    NewDedupPolicy,
    NewLeakagePolicy,
    NewQualityPolicy,
    NewSplitPolicy,
)
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    DatasetPolicyRepository,
    DatasetRepository,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasets.errors import ResourceNotFoundError
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.identity.models import ProjectScope


class DatasetPolicyUseCases:
    def __init__(
        self,
        datasets: DatasetRepository,
        policies: DatasetPolicyRepository,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._datasets = datasets
        self._policies = policies
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    def _ensure_dataset(self, scope: ProjectScope, dataset_id: UUID) -> None:
        if self._datasets.get(scope, dataset_id) is None:
            raise ResourceNotFoundError("数据集", dataset_id)

    def create_dedup(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        command: NewDedupPolicy,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DedupPolicy:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get_dedup(scope, dataset_id, _uuid(replay.response_ref))
        self._ensure_dataset(scope, dataset_id)
        policy = DedupPolicy(
            dedup_policy_id=uuid4(),
            project_id=scope.project_id,
            dataset_id=dataset_id,
            version=self._policies.next_dedup_version(scope, dataset_id),
            exact_keys=command.exact_keys,
            near_duplicate_enabled=command.near_duplicate_enabled,
            near_duplicate_enforcement=command.near_duplicate_enforcement,
            retain_rule=command.retain_rule,
            name=command.name,
            similarity_method=command.similarity_method,
            similarity_threshold=command.similarity_threshold,
            similarity_min_cluster_size=command.similarity_min_cluster_size,
            grouping_keys=command.grouping_keys,
            created_at=now,
            created_by=scope.actor_id,
        )
        self._policies.add_dedup(scope, policy)
        self._audit_policy(scope, "DEDUP_POLICY", policy.dedup_policy_id, policy.version, request_id, now)
        self._idempotency.store(
            scope, key, method=method, path=path, body=body, response_status=201,
            response_ref=str(policy.dedup_policy_id), now=now,
        )
        self._uow.commit()
        return policy

    def list_dedup(self, scope: ProjectScope, dataset_id: UUID, *, limit: int = 20) -> list[DedupPolicy]:
        self._ensure_dataset(scope, dataset_id)
        return self._policies.list_dedup(scope, dataset_id=dataset_id, limit=limit)

    def get_dedup(self, scope: ProjectScope, dataset_id: UUID, policy_id: UUID) -> DedupPolicy:
        policy = self._policies.get_dedup(scope, policy_id)
        if policy is None or policy.dataset_id != dataset_id:
            raise ResourceNotFoundError("去重策略", policy_id)
        return policy

    def create_split(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        command: NewSplitPolicy,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> SplitPolicy:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get_split(scope, dataset_id, _uuid(replay.response_ref))
        self._ensure_dataset(scope, dataset_id)
        policy = SplitPolicy(
            split_policy_id=uuid4(),
            project_id=scope.project_id,
            dataset_id=dataset_id,
            version=self._policies.next_split_version(scope, dataset_id),
            strategy=command.strategy,
            seed=command.seed,
            group_by=command.group_by,
            empty_split_policy=command.empty_split_policy,
            min_item_count=command.min_item_count,
            min_duration_ns=command.min_duration_ns,
            rounding_rule=command.rounding_rule,
            name=command.name,
            train_ratio=command.train_ratio,
            validation_ratio=command.validation_ratio,
            test_ratio=command.test_ratio,
            train_count=command.train_count,
            validation_count=command.validation_count,
            test_count=command.test_count,
            stratify_by=command.stratify_by,
            created_at=now,
            created_by=scope.actor_id,
        )
        self._policies.add_split(scope, policy)
        self._audit_policy(scope, "SPLIT_POLICY", policy.split_policy_id, policy.version, request_id, now)
        self._idempotency.store(
            scope, key, method=method, path=path, body=body, response_status=201,
            response_ref=str(policy.split_policy_id), now=now,
        )
        self._uow.commit()
        return policy

    def list_split(self, scope: ProjectScope, dataset_id: UUID, *, limit: int = 20) -> list[SplitPolicy]:
        self._ensure_dataset(scope, dataset_id)
        return self._policies.list_split(scope, dataset_id=dataset_id, limit=limit)

    def get_split(self, scope: ProjectScope, dataset_id: UUID, policy_id: UUID) -> SplitPolicy:
        policy = self._policies.get_split(scope, policy_id)
        if policy is None or policy.dataset_id != dataset_id:
            raise ResourceNotFoundError("划分策略", policy_id)
        return policy

    def create_leakage(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        command: NewLeakagePolicy,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> LeakagePolicy:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get_leakage(scope, dataset_id, _uuid(replay.response_ref))
        self._ensure_dataset(scope, dataset_id)
        policy = LeakagePolicy(
            leakage_policy_id=uuid4(),
            project_id=scope.project_id,
            dataset_id=dataset_id,
            version=self._policies.next_leakage_version(scope, dataset_id),
            hard_rules=command.hard_rules,
            soft_rules=command.soft_rules,
            waiver_allowed_rules=command.waiver_allowed_rules,
            name=command.name,
            similarity_grouping_enabled=command.similarity_grouping_enabled,
            scene_grouping_enabled=command.scene_grouping_enabled,
            batch_grouping_enabled=command.batch_grouping_enabled,
            temporal_overlap_enabled=command.temporal_overlap_enabled,
            created_at=now,
            created_by=scope.actor_id,
        )
        self._policies.add_leakage(scope, policy)
        self._audit_policy(
            scope, "LEAKAGE_POLICY", policy.leakage_policy_id, policy.version, request_id, now
        )
        self._idempotency.store(
            scope, key, method=method, path=path, body=body, response_status=201,
            response_ref=str(policy.leakage_policy_id), now=now,
        )
        self._uow.commit()
        return policy

    def list_leakage(
        self, scope: ProjectScope, dataset_id: UUID, *, limit: int = 20
    ) -> list[LeakagePolicy]:
        self._ensure_dataset(scope, dataset_id)
        return self._policies.list_leakage(scope, dataset_id=dataset_id, limit=limit)

    def get_leakage(self, scope: ProjectScope, dataset_id: UUID, policy_id: UUID) -> LeakagePolicy:
        policy = self._policies.get_leakage(scope, policy_id)
        if policy is None or policy.dataset_id != dataset_id:
            raise ResourceNotFoundError("泄漏策略", policy_id)
        return policy

    def create_quality(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        command: NewQualityPolicy,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> QualityPolicy:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get_quality(scope, dataset_id, _uuid(replay.response_ref))
        self._ensure_dataset(scope, dataset_id)
        policy = QualityPolicy(
            quality_policy_id=uuid4(),
            project_id=scope.project_id,
            dataset_id=dataset_id,
            version=self._policies.next_quality_version(scope, dataset_id),
            min_selected_count=command.min_selected_count,
            allow_warn_quality=command.allow_warn_quality,
            require_leakage_pass=command.require_leakage_pass,
            name=command.name,
            created_at=now,
            created_by=scope.actor_id,
        )
        self._policies.add_quality(scope, policy)
        self._audit_policy(
            scope, "QUALITY_POLICY", policy.quality_policy_id, policy.version, request_id, now
        )
        self._idempotency.store(
            scope, key, method=method, path=path, body=body, response_status=201,
            response_ref=str(policy.quality_policy_id), now=now,
        )
        self._uow.commit()
        return policy

    def list_quality(
        self, scope: ProjectScope, dataset_id: UUID, *, limit: int = 20
    ) -> list[QualityPolicy]:
        self._ensure_dataset(scope, dataset_id)
        return self._policies.list_quality(scope, dataset_id=dataset_id, limit=limit)

    def get_quality(self, scope: ProjectScope, dataset_id: UUID, policy_id: UUID) -> QualityPolicy:
        policy = self._policies.get_quality(scope, policy_id)
        if policy is None or policy.dataset_id != dataset_id:
            raise ResourceNotFoundError("质量策略", policy_id)
        return policy

    def _audit_policy(
        self,
        scope: ProjectScope,
        resource_type: str,
        resource_id: UUID,
        policy_version: int,
        request_id: str | None,
        now: datetime,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=resource_type,
            resource_id=resource_id,
            action="create",
            from_status=None,
            to_status="ACTIVE",
            reason=None,
            request_id=request_id,
            details={"policy_version": policy_version},
            event_type=f"{resource_type.lower()}.created",
            event_payload={
                "resource_id": str(resource_id),
                "policy_version": policy_version,
            },
            occurred_at=now,
        )


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid(value: str | None) -> UUID:
    if not value:
        raise ResourceNotFoundError("策略", value)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError("策略", value) from exc
