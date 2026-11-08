"""DatasetVersion 用例：选择预览、构建、校验、冻结与终态动作。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.commands import (
    ActionCommand,
    LeakageWaiverCommand,
    NewDatasetVersion,
    PreviewSelection,
)
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.ports import (
    AuditRepository,
    DatasetItemRepository,
    DatasetPolicyRepository,
    DatasetReportRepository,
    DatasetRepository,
    DatasetVersionRepository,
    EpisodeCandidateDirectory,
    IdempotencyRepository,
    OutboxRepository,
    UnitOfWork,
)
from app.domain.datasets.enums import (
    DatasetStatus,
    DatasetVersionStatus,
    LeakageStatus,
    QualityReportStatus,
    ValidityStatus,
)
from app.domain.datasets.errors import (
    ArchiveBlockedError,
    DatasetEmptyError,
    DatasetNotReadyError,
    DatasetVersionImmutableError,
    DomainValidationError,
    FreezeApprovalForbiddenError,
    FreezeRequiresApprovalError,
    InsufficientDataError,
    InvalidStateTransitionError,
    LeakageCheckFailedError,
    LeakageCheckUnknownError,
    ManifestMismatchError,
    QualityGateFailedError,
    ResourceNotFoundError,
    UpstreamInvalidatedError,
    VersionConflictError,
)
from app.domain.datasets.leakage import LeakageWaiver
from app.domain.datasets.manifest import Manifest, build_manifest
from app.domain.datasets.models import Dataset, DatasetItem, DatasetVersion
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    PolicyRef,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.pipeline import run_pipeline
from app.domain.datasets.quality import QualityReport
from app.domain.datasets.selection import SelectionFilters
from app.domain.datasets.split import SplitStatus
from app.domain.datasets.state_machines import (
    action_requires_reason,
    allowed_version_actions,
    validate_action_name,
    version_target,
)
from app.domain.identity.models import ProjectScope

_VERSION = "DATASET_VERSION"


class DatasetVersionUseCases:
    def __init__(
        self,
        datasets: DatasetRepository,
        versions: DatasetVersionRepository,
        items: DatasetItemRepository,
        policies: DatasetPolicyRepository,
        reports: DatasetReportRepository,
        episodes: EpisodeCandidateDirectory,
        audit: AuditRepository,
        outbox: OutboxRepository,
        idempotency: IdempotencyRepository,
        uow: UnitOfWork,
    ) -> None:
        self._datasets = datasets
        self._versions = versions
        self._items = items
        self._policies = policies
        self._reports = reports
        self._episodes = episodes
        self._audit = audit
        self._outbox = outbox
        self._idempotency = IdempotencyGuard(idempotency)
        self._uow = uow

    # ---- 读取 ----
    def get_dataset(self, scope: ProjectScope, dataset_id: UUID) -> Dataset:
        dataset = self._datasets.get(scope, dataset_id)
        if dataset is None:
            raise ResourceNotFoundError("数据集", dataset_id)
        return dataset

    def get(self, scope: ProjectScope, dataset_id: UUID, version_id: UUID) -> DatasetVersion:
        version = self._versions.get_for_dataset(scope, dataset_id, version_id)
        if version is None:
            raise ResourceNotFoundError("数据集版本", version_id)
        return version

    def list(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        *,
        status: str | None = None,
        validity_status: str | None = None,
        leakage_status: str | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetVersion]:
        self.get_dataset(scope, dataset_id)
        return self._versions.list(
            scope,
            dataset_id=dataset_id,
            status=status,
            validity_status=validity_status,
            leakage_status=leakage_status,
            after_id=after_id,
            limit=limit,
        )

    def list_items(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        *,
        split: str | None = None,
        sample_role: str | None = None,
        episode_id: UUID | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[DatasetItem]:
        self.get(scope, dataset_id, version_id)
        return self._items.list(
            scope,
            dataset_version_id=version_id,
            split=split,
            sample_role=sample_role,
            episode_id=episode_id,
            after_id=after_id,
            limit=limit,
        )

    def get_item(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        item_id: UUID,
    ) -> DatasetItem:
        self.get(scope, dataset_id, version_id)
        item = self._items.get(scope, dataset_version_id=version_id, item_id=item_id)
        if item is None:
            raise ResourceNotFoundError("数据集条目", item_id)
        return item

    # ---- 创建与预览 ----
    def create(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        command: NewDatasetVersion,
        *,
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DatasetVersion:
        key = validate_key(idempotency_key)
        body = asdict(command)
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get(scope, dataset_id, _uuid(replay.response_ref))
        dataset = self.get_dataset(scope, dataset_id)
        if dataset.status is DatasetStatus.ARCHIVED:
            raise ArchiveBlockedError("已归档数据集不能创建新版本")
        dedup = self._resolve_dedup(
            scope,
            dataset_id,
            command.dedup_policy_id,
            command.dedup_policy_version,
        )
        split = self._resolve_split(
            scope,
            dataset_id,
            command.split_policy_id,
            command.split_policy_version,
        )
        leakage = self._resolve_leakage(
            scope,
            dataset_id,
            command.leakage_policy_id,
            command.leakage_policy_version,
        )
        quality = self._resolve_quality(
            scope,
            dataset_id,
            command.quality_policy_id,
            command.quality_policy_version,
        )
        if command.supersedes_version_id is not None:
            previous = self.get(scope, dataset_id, command.supersedes_version_id)
            if previous.status is not DatasetVersionStatus.FROZEN:
                raise DomainValidationError("只能取代已冻结版本")
        version = DatasetVersion(
            dataset_version_id=uuid4(),
            project_id=scope.project_id,
            dataset_id=dataset_id,
            dataset_lineage_id=dataset.dataset_lineage_id,
            dataset_version=self._versions.next_version_number(scope, dataset_id),
            selection_rule_version=command.selection_rule_version,
            selection_filters=dict(command.selection_filters),
            selection_snapshot_at=now,
            dedup_policy_ref=dedup.ref,
            split_policy_ref=split.ref,
            leakage_policy_ref=leakage.ref,
            quality_policy_ref=quality.ref,
            source_snapshot_at=now,
            supersedes_version_id=command.supersedes_version_id,
            created_at=now,
            updated_at=now,
            created_by=scope.actor_id,
            updated_by=scope.actor_id,
        )
        created = self._versions.add(scope, version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=created.dataset_version_id,
            action="create",
            from_status=None,
            to_status=created.status.value,
            reason=None,
            request_id=request_id,
            details={
                "dataset_id": str(dataset_id),
                "dataset_version": created.dataset_version,
            },
            event_type="dataset.version.created",
            event_payload={
                "dataset_version_id": str(created.dataset_version_id),
                "dataset_id": str(dataset_id),
                "dataset_version": created.dataset_version,
                "status": created.status.value,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=201,
            response_ref=str(created.dataset_version_id),
            now=now,
        )
        self._uow.commit()
        if command.build_now:
            return self.apply_action(
                scope,
                dataset_id,
                created.dataset_version_id,
                "build",
                expected_version=created.row_version,
                reason=None,
                request_id=request_id,
            )
        return created

    def preview(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        command: PreviewSelection,
        *,
        request_id: str | None = None,
    ):
        version = self.get(scope, dataset_id, version_id)
        dedup = self._resolve_dedup(
            scope, dataset_id, command.dedup_policy_id, command.dedup_policy_version
        )
        split = self._resolve_split(
            scope, dataset_id, command.split_policy_id, command.split_policy_version
        )
        leakage = self._resolve_leakage(
            scope, dataset_id, command.leakage_policy_id, command.leakage_policy_version
        )
        quality = (
            self._resolve_quality(
                scope,
                dataset_id,
                command.quality_policy_id,
                command.quality_policy_version,
            )
            if command.quality_policy_id is not None
            and command.quality_policy_version is not None
            else self._resolve_quality_version(scope, dataset_id, version.quality_policy_ref)
        )
        now = _utcnow()
        filters = SelectionFilters.from_mapping(dict(command.selection_filters))
        preview_base = replace(
            version,
            selection_filters=dict(command.selection_filters),
            dedup_policy_ref=dedup.ref,
            split_policy_ref=split.ref,
            leakage_policy_ref=leakage.ref,
            quality_policy_ref=quality.ref,
        )
        result = run_pipeline(
            base_version=preview_base,
            candidates=self._episodes.list_candidates(scope, filters),
            filters=filters,
            usage_policy=self.get_dataset(scope, dataset_id).usage_policy,
            dedup_policy=dedup,
            split_policy=split,
            leakage_policy=leakage,
            quality_policy=quality,
            leakage_report_id=uuid4(),
            quality_report_id=uuid4(),
            now=now,
            source_snapshot_at=now,
            sample_limit=command.sample_limit,
        )
        return result.preview

    # ---- 动作 ----
    def apply_action(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        action: str,
        *,
        expected_version: int,
        reason: str | None = None,
        command: ActionCommand | None = None,
        waivers: tuple[LeakageWaiverCommand, ...] = (),
        idempotency_key: str | None = None,
        method: str = "POST",
        path: str,
        request_id: str | None = None,
    ) -> DatasetVersion:
        action_name = validate_action_name(action, family="version")
        if action_name == "build":
            return self._build(
                scope,
                dataset_id,
                version_id,
                expected_version=expected_version,
                reason=reason,
                waivers=waivers,
                idempotency_key=idempotency_key,
                method=method,
                path=path,
                request_id=request_id,
            )
        if action_name in {"validate", "ready"}:
            return self._validate(
                scope,
                dataset_id,
                version_id,
                action_name,
                expected_version=expected_version,
                waivers=waivers,
                idempotency_key=idempotency_key,
                method=method,
                path=path,
                request_id=request_id,
            )
        if action_name == "freeze":
            return self._freeze(
                scope,
                dataset_id,
                version_id,
                expected_version=expected_version,
                command=command or ActionCommand(),
                idempotency_key=idempotency_key,
                method=method,
                path=path,
                request_id=request_id,
            )
        return self._simple_transition(
            scope,
            dataset_id,
            version_id,
            action_name,
            expected_version=expected_version,
            reason=reason,
            idempotency_key=idempotency_key,
            method=method,
            path=path,
            request_id=request_id,
        )

    def _build(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
        reason: str | None,
        waivers: tuple[LeakageWaiverCommand, ...],
        idempotency_key: str | None,
        method: str,
        path: str,
        request_id: str | None,
    ) -> DatasetVersion:
        key = validate_key(idempotency_key)
        body = {
            "action": "build",
            "expected_version": expected_version,
            "reason": reason,
            "waivers": [asdict(item) for item in waivers],
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get(scope, dataset_id, _uuid(replay.response_ref))
        dataset = self.get_dataset(scope, dataset_id)
        if dataset.status is DatasetStatus.ARCHIVED:
            raise ArchiveBlockedError("已归档数据集不能构建新版本")
        current = self.get(scope, dataset_id, version_id)
        self._ensure_expected(current, expected_version)
        if "build" not in allowed_version_actions(current.status):
            raise InvalidStateTransitionError("数据集版本", current.status.value, "build")
        target = version_target(current.status, "build")
        building = replace(
            current,
            status=target,
            source_snapshot_at=now,
            updated_at=now,
            updated_by=scope.actor_id,
            row_version=current.row_version + 1,
        )
        building = self._versions.save(scope, building, expected_version=current.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=version_id,
            action="build-start",
            from_status=current.status.value,
            to_status=building.status.value,
            reason=reason,
            request_id=request_id,
            details={"build_attempt": current.build_attempt + 1},
            event_type="dataset.version.building",
            event_payload={"dataset_version_id": str(version_id), "status": building.status.value},
            occurred_at=now,
        )
        dedup, split, leakage, quality = self._resolve_all(scope, dataset_id, current)
        filters = SelectionFilters.from_mapping(dict(current.selection_filters))
        leakage_report_id = uuid4()
        quality_report_id = uuid4()
        result = run_pipeline(
            base_version=building,
            candidates=self._episodes.list_candidates(scope, filters),
            filters=filters,
            usage_policy=dataset.usage_policy,
            dedup_policy=dedup,
            split_policy=split,
            leakage_policy=leakage,
            quality_policy=quality,
            leakage_report_id=leakage_report_id,
            quality_report_id=quality_report_id,
            now=now,
            source_snapshot_at=now,
            waivers=self._waivers(scope, leakage, waivers, now=now, request_id=request_id),
        )
        if result.split_status is SplitStatus.INSUFFICIENT_DATA:
            raise InsufficientDataError("候选数据低于最小样本/时长，不能构建")
        if result.split_status is SplitStatus.EMPTY_TEST_SPLIT:
            raise InsufficientDataError("测试划分为空；仅显式 ALLOW_EMPTY_TEST 并记录原因时允许")
        if result.split_status is SplitStatus.INVALID:
            raise InsufficientDataError("划分计数或比例非法")
        if not result.items and not any(issue == "DATASET_EMPTY" for issue in result.blocking_issues):
            raise DatasetEmptyError("入选样本为空，不能创建可交付版本")
        self._reports.add_leakage(scope, result.leakage_report)
        self._reports.add_quality(scope, result.quality_report)
        updates = dict(result.version_updates)
        updates.update(
            status=DatasetVersionStatus.VALIDATING,
            build_attempt=current.build_attempt + 1,
            updated_at=now,
            updated_by=scope.actor_id,
        )
        validating = replace(building, **updates)
        saved = self._versions.save(scope, validating, expected_version=building.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=version_id,
            action="build-complete",
            from_status=building.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={
                "candidate_count": saved.candidate_count,
                "selected_count": saved.selected_count,
                "duplicated_count": saved.duplicated_count,
                "leakage_status": _enum_text(saved.leakage_status),
            },
            event_type="dataset.version.built",
            event_payload={
                "dataset_version_id": str(version_id),
                "status": saved.status.value,
                "leakage_status": _enum_text(saved.leakage_status),
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.dataset_version_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _validate(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        action_name: str,
        *,
        expected_version: int,
        waivers: tuple[LeakageWaiverCommand, ...],
        idempotency_key: str | None,
        method: str,
        path: str,
        request_id: str | None,
    ) -> DatasetVersion:
        key = validate_key(idempotency_key)
        body = {
            "action": action_name,
            "expected_version": expected_version,
            "waivers": [asdict(item) for item in waivers],
        }
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get(scope, dataset_id, _uuid(replay.response_ref))
        current = self.get(scope, dataset_id, version_id)
        self._ensure_expected(current, expected_version)
        if action_name not in allowed_version_actions(current.status):
            raise InvalidStateTransitionError("数据集版本", current.status.value, action_name)
        dedup, split, leakage, quality = self._resolve_all(scope, dataset_id, current)
        filters = SelectionFilters.from_mapping(dict(current.selection_filters))
        checked_at = current.source_snapshot_at or now
        stored_leakage = self._reports.latest_leakage(scope, version_id)
        if waivers:
            waiver_objects = self._waivers(
                scope,
                leakage,
                waivers,
                now=checked_at,
                request_id=request_id,
            )
        elif stored_leakage is not None:
            waiver_objects = stored_leakage.waivers
        else:
            waiver_objects = ()
        result = run_pipeline(
            base_version=current,
            candidates=self._episodes.list_candidates(scope, filters),
            filters=filters,
            usage_policy=self.get_dataset(scope, dataset_id).usage_policy,
            dedup_policy=dedup,
            split_policy=split,
            leakage_policy=leakage,
            quality_policy=quality,
            leakage_report_id=current.leakage_report_id or uuid4(),
            quality_report_id=current.quality_report_id or uuid4(),
            now=checked_at,
            source_snapshot_at=checked_at,
            waivers=waiver_objects,
        )
        computed_manifest_sha = (
            result.manifest.manifest_sha256 if result.manifest is not None else None
        )
        hashes_match = (
            result.quality_report.report_sha256 == current.quality_report_sha256
            and result.leakage_report.report_sha256 == current.leakage_report_sha256
            and computed_manifest_sha == current.manifest_sha256
        )
        blocking = list(result.blocking_issues)
        if not hashes_match:
            blocking.append("SOURCE_CHANGED_OR_MANIFEST_MISMATCH")
        ready = (
            hashes_match
            and not blocking
            and result.split_status is SplitStatus.OK
            and result.manifest is not None
        )
        if ready:
            existing = self._items.list_all(scope, version_id)
            if not existing:
                self._items.add_many(scope, result.items)
            updates = dict(result.version_updates)
            updates.update(
                status=DatasetVersionStatus.READY,
                blocking_issues=tuple(blocking),
                updated_at=now,
                updated_by=scope.actor_id,
            )
            updated = replace(current, **updates)
        else:
            updated = replace(
                current,
                status=DatasetVersionStatus.VALIDATION_FAILED,
                blocking_issues=tuple(dict.fromkeys(blocking)),
                updated_at=now,
                updated_by=scope.actor_id,
                row_version=current.row_version + 1,
            )
        saved = self._versions.save(scope, updated, expected_version=current.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=version_id,
            action="validate" if action_name == "validate" else "ready",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=None,
            request_id=request_id,
            details={"blocking_issues": list(saved.blocking_issues)},
            event_type="dataset.version.validated",
            event_payload={
                "dataset_version_id": str(version_id),
                "status": saved.status.value,
                "leakage_status": _enum_text(saved.leakage_status),
                "blocking_issues": list(saved.blocking_issues),
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.dataset_version_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _freeze(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
        command: ActionCommand,
        idempotency_key: str | None,
        method: str,
        path: str,
        request_id: str | None,
    ) -> DatasetVersion:
        key = validate_key(idempotency_key)
        body = {"action": "freeze", "expected_version": expected_version, **asdict(command)}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get(scope, dataset_id, _uuid(replay.response_ref))
        dataset = self.get_dataset(scope, dataset_id)
        if dataset.status is DatasetStatus.ARCHIVED:
            raise ArchiveBlockedError("已归档数据集不能冻结新版本")
        current = self.get(scope, dataset_id, version_id)
        self._ensure_expected(current, expected_version)
        if "freeze" not in allowed_version_actions(current.status):
            raise DatasetNotReadyError("只有 READY 版本可以冻结")
        if current.validity_status is not ValidityStatus.VALID:
            raise UpstreamInvalidatedError("版本有效性不是 VALID，禁止冻结")
        if current.created_by == scope.actor_id:
            raise FreezeApprovalForbiddenError("构建人不得作为唯一冻结审批人")
        if not bool(command.payload.get("qa_verified")):
            raise FreezeRequiresApprovalError("冻结需要 QA 独立核验")
        quality = self._reports.latest_quality(scope, version_id)
        if quality is None or quality.status is not QualityReportStatus.READY or quality.blocking_issues:
            raise QualityGateFailedError("质量报告未就绪或存在阻断项")
        if current.leakage_status is LeakageStatus.FAIL:
            raise LeakageCheckFailedError("泄漏硬规则失败，禁止冻结")
        if current.leakage_status in {LeakageStatus.UNKNOWN, None}:
            raise LeakageCheckUnknownError("泄漏检查未知，按失败关闭")
        if current.train_count < 1 or current.validation_count < 1:
            raise InsufficientDataError("训练或验证划分为空")
        self._verify_manifest(scope, current)
        previous = self._versions.latest_frozen(scope, dataset_id)
        if previous is not None and previous.dataset_version_id != version_id:
            superseded = replace(
                previous,
                status=DatasetVersionStatus.SUPERSEDED,
                superseded_by_version_id=version_id,
                updated_at=now,
                updated_by=scope.actor_id,
                row_version=previous.row_version + 1,
            )
            self._versions.save(scope, superseded, expected_version=previous.row_version)
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_VERSION,
                resource_id=previous.dataset_version_id,
                action="system-supersede",
                from_status=previous.status.value,
                to_status=DatasetVersionStatus.SUPERSEDED.value,
                reason=None,
                request_id=request_id,
                details={"superseded_by_version_id": str(version_id)},
                event_type="dataset.version.superseded",
                event_payload={
                    "dataset_version_id": str(previous.dataset_version_id),
                    "superseded_by_version_id": str(version_id),
                },
                occurred_at=now,
            )
        supersedes_id = current.supersedes_version_id or (
            previous.dataset_version_id if previous is not None else None
        )
        frozen = replace(
            current,
            status=DatasetVersionStatus.FROZEN,
            supersedes_version_id=supersedes_id,
            freeze_approval_id=uuid4(),
            frozen_by=scope.actor_id,
            frozen_at=now,
            updated_at=now,
            updated_by=scope.actor_id,
            row_version=current.row_version + 1,
        )
        saved = self._versions.save(scope, frozen, expected_version=current.row_version)
        updated_dataset = replace(
            dataset,
            status=DatasetStatus.ACTIVE,
            current_version_id=version_id,
            frozen_version_count=dataset.frozen_version_count + 1,
            updated_at=now,
            updated_by=scope.actor_id,
            row_version=dataset.row_version + 1,
        )
        self._datasets.save(scope, updated_dataset, expected_version=dataset.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=version_id,
            action="freeze",
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=command.reason,
            request_id=request_id,
            details={
                "manifest_sha256": saved.manifest_sha256,
                "quality_report_sha256": saved.quality_report_sha256,
                "leakage_report_sha256": saved.leakage_report_sha256,
                "freeze_approval_id": str(saved.freeze_approval_id),
            },
            event_type="dataset.version.frozen",
            event_payload={
                "dataset_version_id": str(version_id),
                "dataset_id": str(dataset_id),
                "manifest_sha256": saved.manifest_sha256,
                "frozen_at": now.isoformat(),
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.dataset_version_id),
            now=now,
        )
        self._uow.commit()
        return saved

    def _simple_transition(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version_id: UUID,
        action_name: str,
        *,
        expected_version: int,
        reason: str | None,
        idempotency_key: str | None,
        method: str,
        path: str,
        request_id: str | None,
    ) -> DatasetVersion:
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version, "reason": reason}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get(scope, dataset_id, _uuid(replay.response_ref))
        current = self.get(scope, dataset_id, version_id)
        self._ensure_expected(current, expected_version)
        if action_name not in allowed_version_actions(current.status):
            raise InvalidStateTransitionError("数据集版本", current.status.value, action_name)
        if action_requires_reason("version", action_name) and not reason:
            raise DomainValidationError("该动作必须填写原因")
        if action_name == "return-draft" and self._items.list_all(scope, version_id):
            raise DatasetVersionImmutableError("已有条目的版本不可回到草稿；请创建新版本")
        target = version_target(current.status, action_name)
        changes: dict[str, object] = {
            "status": target,
            "updated_at": now,
            "updated_by": scope.actor_id,
            "row_version": current.row_version + 1,
        }
        if target is DatasetVersionStatus.ARCHIVED:
            changes.update(archived_at=now, archive_reason=reason)
        updated = replace(current, **changes)
        saved = self._versions.save(scope, updated, expected_version=current.row_version)
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_VERSION,
            resource_id=version_id,
            action=action_name,
            from_status=current.status.value,
            to_status=saved.status.value,
            reason=reason,
            request_id=request_id,
            details={},
            event_type="dataset.version.status_changed",
            event_payload={
                "dataset_version_id": str(version_id),
                "from_status": current.status.value,
                "to_status": saved.status.value,
                "action": action_name,
            },
            occurred_at=now,
        )
        self._idempotency.store(
            scope,
            key,
            method=method,
            path=path,
            body=body,
            response_status=200,
            response_ref=str(saved.dataset_version_id),
            now=now,
        )
        self._uow.commit()
        return saved

    # ---- 报告与 Manifest ----
    def quality_report(self, scope: ProjectScope, dataset_id: UUID, version_id: UUID) -> QualityReport:
        self.get(scope, dataset_id, version_id)
        report = self._reports.latest_quality(scope, version_id)
        if report is None:
            raise ResourceNotFoundError("质量报告", version_id)
        return report

    def leakage_report(self, scope: ProjectScope, dataset_id: UUID, version_id: UUID):
        self.get(scope, dataset_id, version_id)
        report = self._reports.latest_leakage(scope, version_id)
        if report is None:
            raise ResourceNotFoundError("泄漏报告", version_id)
        return report

    def manifest(self, scope: ProjectScope, dataset_id: UUID, version_id: UUID) -> Manifest:
        version = self.get(scope, dataset_id, version_id)
        if version.manifest_sha256 is None or version.source_snapshot_at is None:
            raise ResourceNotFoundError("Manifest", version_id)
        items = self._items.list_all(scope, version_id)
        if not items:
            raise ResourceNotFoundError("Manifest", version_id)
        return self._verify_manifest(scope, version, items=tuple(items))

    # ---- 内部 ----
    def _verify_manifest(
        self,
        scope: ProjectScope,
        version: DatasetVersion,
        *,
        items: tuple[DatasetItem, ...] | None = None,
    ) -> Manifest:
        if version.source_snapshot_at is None or version.episode_version_digest is None:
            raise ManifestMismatchError("版本缺少 Manifest 快照信息")
        resolved_items = items or tuple(self._items.list_all(scope, version.dataset_version_id))
        if not resolved_items:
            raise ManifestMismatchError("版本没有条目，无法校验 Manifest")
        try:
            return build_manifest(
                version,
                resolved_items,
                episode_version_digest=version.episode_version_digest,
                annotation_submission_refs_digest=version.annotation_submission_refs_digest,
                annotation_schema_versions=version.annotation_schema_versions,
                created_at=version.source_snapshot_at,
            )
        except ManifestMismatchError:
            raise
        except ValueError as exc:
            raise ManifestMismatchError("Manifest 复算失败") from exc

    def _resolve_all(
        self,
        scope: ProjectScope,
        dataset_id: UUID,
        version: DatasetVersion,
    ) -> tuple[DedupPolicy, SplitPolicy, LeakagePolicy, QualityPolicy]:
        return (
            self._resolve_dedup_version(scope, dataset_id, version.dedup_policy_ref),
            self._resolve_split_version(scope, dataset_id, version.split_policy_ref),
            self._resolve_leakage_version(scope, dataset_id, version.leakage_policy_ref),
            self._resolve_quality_version(scope, dataset_id, version.quality_policy_ref),
        )

    def _resolve_dedup(self, scope, dataset_id, policy_id, policy_version) -> DedupPolicy:
        policy = self._policies.get_dedup_version(
            scope,
            dataset_id=dataset_id,
            policy_id=policy_id,
            version=policy_version,
        )
        if policy is None:
            raise ResourceNotFoundError("去重策略", policy_id)
        return policy

    def _resolve_split(self, scope, dataset_id, policy_id, policy_version) -> SplitPolicy:
        policy = self._policies.get_split_version(
            scope,
            dataset_id=dataset_id,
            policy_id=policy_id,
            version=policy_version,
        )
        if policy is None:
            raise ResourceNotFoundError("划分策略", policy_id)
        return policy

    def _resolve_leakage(self, scope, dataset_id, policy_id, policy_version) -> LeakagePolicy:
        policy = self._policies.get_leakage_version(
            scope,
            dataset_id=dataset_id,
            policy_id=policy_id,
            version=policy_version,
        )
        if policy is None:
            raise ResourceNotFoundError("泄漏策略", policy_id)
        return policy

    def _resolve_quality(self, scope, dataset_id, policy_id, policy_version) -> QualityPolicy:
        policy = self._policies.get_quality_version(
            scope,
            dataset_id=dataset_id,
            policy_id=policy_id,
            version=policy_version,
        )
        if policy is None:
            raise ResourceNotFoundError("质量策略", policy_id)
        return policy

    def _resolve_dedup_version(self, scope, dataset_id, ref: PolicyRef) -> DedupPolicy:
        return self._resolve_dedup(scope, dataset_id, ref.policy_id, ref.version)

    def _resolve_split_version(self, scope, dataset_id, ref: PolicyRef) -> SplitPolicy:
        return self._resolve_split(scope, dataset_id, ref.policy_id, ref.version)

    def _resolve_leakage_version(self, scope, dataset_id, ref: PolicyRef) -> LeakagePolicy:
        return self._resolve_leakage(scope, dataset_id, ref.policy_id, ref.version)

    def _resolve_quality_version(self, scope, dataset_id, ref: PolicyRef) -> QualityPolicy:
        return self._resolve_quality(scope, dataset_id, ref.policy_id, ref.version)

    def _waivers(
        self,
        scope: ProjectScope,
        policy: LeakagePolicy,
        commands: tuple[LeakageWaiverCommand, ...],
        *,
        now: datetime,
        request_id: str | None,
    ) -> tuple[LeakageWaiver, ...]:
        waivers: list[LeakageWaiver] = []
        for command in commands:
            waiver = LeakageWaiver(
                rule=command.rule,
                approved_by=scope.actor_id,
                reason=command.reason,
                impact=command.impact,
                approved_at=now,
                expires_at=command.expires_at,
                audit_ref=f"dataset-leakage-waiver:{request_id or 'n/a'}",
            )
            waivers.append(waiver)
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type="DATASET_LEAKAGE_WAIVER",
                resource_id=uuid4(),
                action="waive",
                from_status=None,
                to_status="WAIVED",
                reason=command.reason,
                request_id=request_id,
                details={"rule": command.rule, "policy_id": str(policy.leakage_policy_id)},
                event_type="dataset.leakage.waived",
                event_payload={"rule": command.rule, "reason": command.reason},
                occurred_at=now,
            )
        return tuple(waivers)

    @staticmethod
    def _ensure_expected(version: DatasetVersion, expected_version: int) -> None:
        if version.row_version != expected_version:
            raise VersionConflictError("数据集版本")


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _uuid(value: str | None) -> UUID:
    if not value:
        raise ResourceNotFoundError("数据集版本", value)
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError("数据集版本", value) from exc


def _enum_text(value: object) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


__all__ = ["DatasetVersionUseCases"]
