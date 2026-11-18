"""DatasetVersion 构建、校验、冻结与策略解析。"""

from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.commands import ActionCommand, LeakageWaiverCommand
from app.application.datasets.idempotency import validate_key
from app.application.datasets.version_common import _enum_text, _utcnow, _uuid
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
    QualityGateFailedError,
    ResourceNotFoundError,
    UpstreamInvalidatedError,
    VersionConflictError,
)
from app.domain.datasets.leakage import LeakageWaiver
from app.domain.datasets.models import DatasetItem, DatasetVersion
from app.domain.datasets.pipeline import run_pipeline
from app.domain.datasets.policies import (
    DedupPolicy,
    LeakagePolicy,
    PolicyRef,
    QualityPolicy,
    SplitPolicy,
)
from app.domain.datasets.selection import SelectionFilters
from app.domain.datasets.split import SplitStatus
from app.domain.datasets.state_machines import (
    action_requires_reason,
    allowed_version_actions,
    version_target,
)
from app.domain.identity.models import ProjectScope

_VERSION = "DATASET_VERSION"


class DatasetVersionActionsMixin:
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
            # 构建是"落地产物"的写路径：点名条目全部不合格必须确定性拒绝。
            require_explicit_candidates=True,
        )
        if result.split_status is SplitStatus.INSUFFICIENT_DATA:
            raise InsufficientDataError("候选数据低于最小样本/时长，不能构建")
        if result.split_status is SplitStatus.EMPTY_TEST_SPLIT:
            raise InsufficientDataError("测试划分为空；仅显式 ALLOW_EMPTY_TEST 并记录原因时允许")
        if result.split_status is SplitStatus.INVALID:
            raise InsufficientDataError("划分计数或比例非法")
        if not result.items and not any(
            issue == "DATASET_EMPTY" for issue in result.blocking_issues
        ):
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
            written_items = result.items
            if not existing:
                self._items.add_many(scope, result.items)
            else:
                written_items = tuple(existing)
            updates = dict(result.version_updates)
            updates.update(
                status=DatasetVersionStatus.READY,
                blocking_issues=tuple(blocking),
                updated_at=now,
                updated_by=scope.actor_id,
            )
            updated = replace(current, **updates)
        else:
            written_items = ()
            updated = replace(
                current,
                status=DatasetVersionStatus.VALIDATION_FAILED,
                blocking_issues=tuple(dict.fromkeys(blocking)),
                updated_at=now,
                updated_by=scope.actor_id,
                row_version=current.row_version + 1,
            )
        saved = self._versions.save(scope, updated, expected_version=current.row_version)
        if ready:
            # 版本转 READY 是血缘第四层的唯一写入点：每个入选条目一条
            # Episode → DatasetVersion 的 INCLUDED_IN 边。重复 validate
            # 不会重复写（仓储 ensure_edge 幂等）。
            self._record_version_lineage(
                scope,
                dataset_id=dataset_id,
                version_id=version_id,
                items=written_items,
                now=now,
            )
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
        blocked = current.validity_block_reason
        if blocked is not None:
            raise UpstreamInvalidatedError(
                f"{blocked}，禁止冻结",
                details={"validity_status": current.validity_status.value},
            )
        if current.created_by == scope.actor_id:
            raise FreezeApprovalForbiddenError("构建人不得作为唯一冻结审批人")
        if not bool(command.payload.get("qa_verified")):
            raise FreezeRequiresApprovalError("冻结需要 QA 独立核验")
        quality = self._reports.latest_quality(scope, version_id)
        if (
            quality is None
            or quality.status is not QualityReportStatus.READY
            or quality.blocking_issues
        ):
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

    def _record_version_lineage(
        self,
        scope: ProjectScope,
        *,
        dataset_id: UUID,
        version_id: UUID,
        items: tuple[DatasetItem, ...] | list[DatasetItem],
        now: datetime,
    ) -> None:
        """写 Episode → DatasetVersion 的 INCLUDED_IN 边（幂等）。

        ``dataset_id`` 只用于边的 metadata：一条版本边必须能反查到所属数据集，
        否则从 Episode 反向遍历只能看到 "进了某个 version" 而无法定位数据集。
        """
        if self._lineage is None:
            return
        created = self._lineage.record_inclusion(
            scope,
            dataset_version_id=version_id,
            episode_ids=[item.episode_id for item in items],
            now=now,
            metadata={"dataset_id": str(dataset_id)},
        )
        if created:
            record_change(
                self._audit,
                self._outbox,
                scope,
                resource_type=_VERSION,
                resource_id=version_id,
                action="lineage-included-in",
                from_status=None,
                to_status=None,
                reason=None,
                request_id=None,
                details={"edge_count": created, "dataset_id": str(dataset_id)},
                event_type="dataset.version.lineage_recorded",
                event_payload={
                    "dataset_version_id": str(version_id),
                    "dataset_id": str(dataset_id),
                    "edge_count": created,
                },
                occurred_at=now,
            )

    def _validity_transition(
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
        """有效性迁移：``invalidate``（标记失效）与 ``revalidate``（确认恢复）。

        与生命周期动作共用同一个版本资源与动作端点，但状态机是独立的
        ``validity_status`` 轴：失效不改版本内容、不改生命周期状态，只阻断
        新的导出与交付。每次迁移都强制留原因，并写审计与 outbox。
        """
        key = validate_key(idempotency_key)
        body = {"action": action_name, "expected_version": expected_version, "reason": reason}
        now = _utcnow()
        replay = self._idempotency.lookup(scope, key, method=method, path=path, body=body, now=now)
        if replay is not None:
            return self.get(scope, dataset_id, _uuid(replay.response_ref))
        current = self.get(scope, dataset_id, version_id)
        self._ensure_expected(current, expected_version)
        updated = current.apply_validity_action(
            action_name,
            reason=reason,
            now=now,
            actor_id=scope.actor_id,
        )
        saved = self._versions.save(scope, updated, expected_version=current.row_version)
        created_edges = 0
        if saved.validity_status is ValidityStatus.INVALIDATED and self._lineage is not None:
            # 契约要求失效用血缘边表达而不是改历史：为每个条目写
            # `Episode --INVALIDATES--> DatasetVersion`，幂等（重复失效已被
            # 状态机拦住，这里仍用 ensure_edge 兜底）。
            items = self._items.list_all(scope, version_id)
            created_edges = self._lineage.record_invalidation(
                scope,
                dataset_version_id=version_id,
                episode_ids=[item.episode_id for item in items],
                now=now,
                metadata={"reason": reason, "action": action_name},
            )
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
            details={
                "from_validity": current.validity_status.value,
                "to_validity": saved.validity_status.value,
                "invalidates_edges": created_edges,
            },
            event_type="dataset.version.validity_changed",
            event_payload={
                "dataset_version_id": str(version_id),
                "dataset_id": str(dataset_id),
                "from_validity": current.validity_status.value,
                "to_validity": saved.validity_status.value,
                "action": action_name,
                "invalidates_edges": created_edges,
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
