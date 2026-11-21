"""DatasetVersion 用例：选择预览、构建、校验、冻结与终态动作。"""

from __future__ import annotations

from dataclasses import asdict, replace
from uuid import UUID, uuid4

from app.application.datasets.audit import record_change
from app.application.datasets.commands import (
    ActionCommand,
    LeakageWaiverCommand,
    NewDatasetVersion,
    PreviewSelection,
)
from app.application.datasets.export_ports import ExportObjectStorage
from app.application.datasets.idempotency import IdempotencyGuard, validate_key
from app.application.datasets.lineage import DatasetVersionLineageRecorder
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
from app.application.datasets.quality_gate import EpisodeQualityGate
from app.application.datasets.version_actions import DatasetVersionActionsMixin
from app.application.datasets.version_common import _utcnow, _uuid
from app.domain.datasets.canonical import sha256_text
from app.domain.datasets.enums import (
    DatasetStatus,
    DatasetVersionStatus,
)
from app.domain.datasets.errors import (
    ArchiveBlockedError,
    DomainValidationError,
    ManifestMismatchError,
    ResourceNotFoundError,
)
from app.domain.datasets.manifest import Manifest, build_manifest
from app.domain.datasets.models import Dataset, DatasetItem, DatasetVersion
from app.domain.datasets.pipeline import run_pipeline
from app.domain.datasets.quality import QualityReport
from app.domain.datasets.selection import SelectionFilters
from app.domain.datasets.state_machines import (
    validate_action_name,
)
from app.domain.identity.models import ProjectScope

_VERSION = "DATASET_VERSION"
# 有效性轴动作；与 `_VERSION_RULES` 的动作集合不重叠。
_VALIDITY_ACTIONS = frozenset({"invalidate", "revalidate"})


class DatasetVersionUseCases(DatasetVersionActionsMixin):
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
        storage: ExportObjectStorage,
        lineage: DatasetVersionLineageRecorder | None = None,
        quality_gate: EpisodeQualityGate | None = None,
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
        # 冻结时把 Manifest 写入对象存储并回读校验（V31-040）。生产装配一定
        # 注入；对象存储不可用时冻结失败，而不是只在库里留一个 Hash。
        self._storage = storage
        # 版本转 READY 时写 Episode → DatasetVersion 的 INCLUDED_IN 边。
        # 允许为 None 是为了保持既有纯读/纯策略用例的构造签名兼容；
        # 生产装配一定注入，见 ``api/v1/dataset_dependencies.py``。
        self._lineage = lineage
        # Episode 统一质量门禁读取器；``min_quality_score`` 启用时才真正查询。
        self._quality_gate = quality_gate

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
            # ``path`` 是 apply_action 的必填关键字参数（用于标识这次动作的
            # 幂等作用域）。此前这里漏传，导致 ``build_now=True`` 的建版本请求
            # 在 HTTP 层直接 TypeError 500——「建并立即构建」这条最常用的路径
            # 从未真正可用。
            #
            # **不把这个请求的幂等键传给内部 build**：幂等键绑定"同一请求体"，
            # 而 create 与 build 的 body 不同，复用同一键会立刻撞
            # ``idempotency_key_reused``。整条链路本来就由 create 自身的幂等记录
            # 保护（重放时直接返回已建好的版本，不会走到这里），内部 build 无需
            # 第二层幂等。
            return self.apply_action(
                scope,
                dataset_id,
                created.dataset_version_id,
                "build",
                expected_version=created.row_version,
                reason=None,
                method=method,
                path=path,
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
            if command.quality_policy_id is not None and command.quality_policy_version is not None
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
        candidates = self._episodes.list_candidates(scope, filters)
        result = run_pipeline(
            base_version=preview_base,
            candidates=candidates,
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
            quality_verdicts=self._quality_verdicts(scope, filters, candidates),
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
        # 有效性轴与生命周期轴共用同一个动作端点：两个状态机的动作名互不重叠
        # （invalidate / revalidate 只属于 validity），按名字分派即可，避免为
        # "同一条资源、另一根轴"再开一套路由。
        if isinstance(action, str) and action.strip() in _VALIDITY_ACTIONS:
            return self._validity_transition(
                scope,
                dataset_id,
                version_id,
                action.strip(),
                expected_version=expected_version,
                reason=reason,
                idempotency_key=idempotency_key,
                method=method,
                path=path,
                request_id=request_id,
            )
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

    def quality_report(
        self, scope: ProjectScope, dataset_id: UUID, version_id: UUID
    ) -> QualityReport:
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
    def _persist_manifest(self, manifest: Manifest) -> tuple[str, str]:
        """把 Manifest 规范化字节写入对象存储并回读校验。

        对象键复用 validate 阶段已写入版本的 ``manifest_key``（Manifest 自身
        携带该字段），保证数据库记录、对象存储与下载地址指向同一处；
        返回 ``(object_key, manifest_sha256)``。落库的 Hash 与对象字节必须
        一致：回读不通过就抛错，由外层事务回滚，版本保持 READY。
        """
        object_key = manifest.manifest_key
        if object_key is None:
            raise ManifestMismatchError("版本缺少 manifest_key，无法落盘 Manifest")
        data = manifest.canonical_bytes()
        digest = sha256_text(data.decode("utf-8"))
        self._storage.put_bytes(object_key, data, content_type="application/json")
        readback = b"".join(self._storage.stream_object(object_key, chunk_size=64 * 1024))
        if sha256_text(readback.decode("utf-8")) != digest:
            raise ManifestMismatchError("Manifest 回读校验失败，拒绝冻结")
        return object_key, digest

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
