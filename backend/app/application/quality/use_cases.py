"""质量运行用例：加载事实 → 调既有规则源 → 聚合 → 落库。

边界：

- 规则判定不在本模块（见 ``domain.annotation.quality_rules`` /
  ``application.datasources.quality`` / ``domain.datasets``）；
- 本模块只负责编排、项目作用域、版本固化、六类聚合与事务边界；
- 运行是纯内存计算（导入规则 O(资产数)，标注规则 O(记录数 × 规则数)），
  与标注质量检查同一口径，不占用 Worker；资产数量设硬上限，超限显式失败，
  不静默截断。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from app.application.annotation.ports import (
    AnnotationRecordRepository,
    AnnotationSchemaRepository,
    AnnotationTaskRepository,
)
from app.application.audit import (
    AuditRepository,
    OutboxRepository,
    record_change,
)
from app.application.datasources.ports import (
    DataSourceImportRepository,
    MediaAssetRepository,
)
from app.application.episodes.ports import (
    EpisodeRawAssetRepository,
    EpisodeRepository,
)
from app.application.quality.commands import NewQualityRun
from app.application.quality.facts import QualitySubjectFacts
from app.application.quality.ports import (
    QualityRunRepository,
    RuleSourceRegistry,
    UnitOfWork,
)
from app.domain.annotation.enums import AnnotationRecordStatus
from app.domain.annotation.schema import AnnotationSchema
from app.domain.common.ids import new_id
from app.domain.identity.models import ProjectScope
from app.domain.quality.aggregation import summarize_findings
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualitySubjectType,
)
from app.domain.quality.errors import (
    DomainValidationError,
    QualityEngineError,
    ResourceNotFoundError,
)
from app.domain.quality.models import QualityFinding, QualityFindingDraft, QualityRun
from app.domain.quality.rules import compute_rule_set_version

_RESOURCE_TYPE = "QUALITY_RUN"
_EVENT_TYPE = "quality.run.completed"
#: 单个对象的资产硬上限：超过即拒绝，避免 HTTP 线程处理无界数据，也避免
#: 分页截断后把"只检查了前 1000 条"当成"全部通过"。
MAX_SUBJECT_ASSETS = 1000
#: 单个 Episode 参与质检的标注记录上限；超限拒绝而不是截断后宣称通过。
MAX_SUBJECT_RECORDS = 2000


def _utcnow() -> datetime:
    return datetime.now(UTC)


class QualityRunUseCases:
    def __init__(
        self,
        runs: QualityRunRepository,
        imports: DataSourceImportRepository,
        assets: MediaAssetRepository,
        episodes: EpisodeRepository,
        episode_assets: EpisodeRawAssetRepository,
        annotation_records: AnnotationRecordRepository,
        schemas: AnnotationSchemaRepository,
        tasks: AnnotationTaskRepository,
        registry: RuleSourceRegistry,
        audit: AuditRepository,
        outbox: OutboxRepository,
        uow: UnitOfWork,
    ) -> None:
        self._runs = runs
        self._imports = imports
        self._assets = assets
        self._episodes = episodes
        self._episode_assets = episode_assets
        self._records = annotation_records
        self._schemas = schemas
        self._tasks = tasks
        self._registry = registry
        self._audit = audit
        self._outbox = outbox
        self._uow = uow

    def run(
        self,
        scope: ProjectScope,
        command: NewQualityRun,
        *,
        request_id: str | None = None,
        now: datetime | None = None,
    ) -> QualityRun:
        """一次性完成"计算 + 落库 + 提交"，供 HTTP 单次质检使用。"""
        run = self.prepare(scope, command, now=now)
        created = self.persist(scope, run, request_id=request_id)
        self._uow.commit()
        return created

    def prepare(
        self,
        scope: ProjectScope,
        command: NewQualityRun,
        *,
        now: datetime | None = None,
    ) -> QualityRun:
        """只读取事实并计算结论，**不写库、不提交**。

        处理流水线的 QUALITY 阶段要在同一事务里为多个 Episode 落结论，
        并把阶段推进一起提交；因此把"计算"与"落库"拆开，由调用方决定
        事务边界（见 :meth:`persist`）。规则、版本固化与聚合口径与
        :meth:`run` 完全一致，不存在第二套实现。
        """
        started_at = now or _utcnow()
        facts = self._load_facts(scope, command.subject_type, command.subject_id)
        rule_set_version, drafts = self._evaluate(scope, facts, command.categories)

        run_id = new_id()
        created_at = _utcnow()
        findings = tuple(
            QualityFinding.from_draft(
                finding_id=new_id(),
                project_id=scope.project_id,
                run_id=run_id,
                draft=draft,
                created_at=created_at,
            )
            for draft in drafts
        )
        summary = summarize_findings(findings)
        finished_at = max(created_at, started_at)
        return QualityRun(
            run_id=run_id,
            project_id=scope.project_id,
            subject_type=command.subject_type,
            subject_id=command.subject_id,
            rule_set_version=rule_set_version,
            status=summary.status,
            score=summary.score,
            finding_count=summary.finding_count,
            error_count=summary.error_count,
            warn_count=summary.warn_count,
            category_summary=summary.category_payload(),
            started_at=started_at,
            finished_at=finished_at,
            created_at=created_at,
            created_by=scope.actor_id,
            findings=findings,
        )

    def persist(
        self,
        scope: ProjectScope,
        run: QualityRun,
        *,
        request_id: str | None = None,
    ) -> QualityRun:
        """落库并写审计/outbox，**不提交事务**。

        SQLAlchemy 的 flush 可能失败；调用方必须把本方法放在自己的事务边界内，
        失败时回滚整个事务——否则会留下"有运行、没结论"或"部分 Episode 已检查"
        的半成品。
        """
        created = self._runs.add(scope, run)
        self._record(scope, created, request_id=request_id, occurred_at=run.finished_at)
        return created

    def get(self, scope: ProjectScope, run_id: UUID) -> QualityRun:
        run = self._runs.get(scope, run_id)
        if run is None:
            raise ResourceNotFoundError("质量运行", run_id)
        return run

    def list_runs(
        self,
        scope: ProjectScope,
        *,
        subject_type: QualitySubjectType,
        subject_id: UUID,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityRun]:
        """按对象列历史运行，创建时间/ID 倒序。

        对象不存在（或跨项目）时返回空列表而不是 404：本接口是列表过滤，
        没有可定位的单条资源；调用方需要确认对象存在性时用对象自身的详情端点。
        """
        return self._runs.list(
            scope,
            subject_type=subject_type,
            subject_id=subject_id,
            after_id=after_id,
            limit=limit,
        )

    def list_findings(
        self,
        scope: ProjectScope,
        run_id: UUID,
        *,
        category: QualityCategory | None = None,
        status: QualityFindingStatus | None = None,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[QualityFinding]:
        # 先确认运行可见：跨项目/不存在的运行返回 404，而不是空列表。
        self.get(scope, run_id)
        return self._runs.list_findings(
            scope,
            run_id,
            category=category,
            status=status,
            after_id=after_id,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # 事实加载
    # ------------------------------------------------------------------

    def _load_facts(
        self,
        scope: ProjectScope,
        subject_type: QualitySubjectType,
        subject_id: UUID,
    ) -> QualitySubjectFacts:
        if subject_type is QualitySubjectType.EPISODE:
            return self._episode_facts(scope, subject_id)
        if subject_type is QualitySubjectType.DATASOURCE_IMPORT:
            return self._import_facts(scope, subject_id)
        # 数据集版本入口本批次不开放；如果未来放开，必须显式实现而不是
        # 落到"空事实"后静默给出 INCOMPLETE。
        raise DomainValidationError(f"本批次不支持检查对象：{subject_type.value}")

    def _episode_facts(self, scope: ProjectScope, episode_id: UUID) -> QualitySubjectFacts:
        episode = self._episodes.get(scope, episode_id)
        if episode is None:
            raise ResourceNotFoundError("Episode", episode_id)
        links = self._episode_assets.list_for_episode(scope, episode_id)
        if len(links) > MAX_SUBJECT_ASSETS:
            raise DomainValidationError(
                f"Episode 原始资产超过 {MAX_SUBJECT_ASSETS} 条，统一质检暂不支持"
            )
        raw_asset_ids = tuple(link.asset_id for link in links)
        assets = (
            tuple(self._assets.list_by_raw_asset_ids(scope, raw_asset_ids)) if raw_asset_ids else ()
        )
        if len(assets) > MAX_SUBJECT_ASSETS:
            raise DomainValidationError(
                f"Episode 媒体资产超过 {MAX_SUBJECT_ASSETS} 条，统一质检暂不支持"
            )
        records = tuple(
            self._records.list_for_episode(
                scope,
                episode_id,
                statuses=frozenset(
                    {
                        AnnotationRecordStatus.SUBMITTED.value,
                        AnnotationRecordStatus.APPROVED.value,
                    }
                ),
                limit=MAX_SUBJECT_RECORDS + 1,
            )
        )
        if len(records) > MAX_SUBJECT_RECORDS:
            raise DomainValidationError(
                f"Episode 标注记录超过 {MAX_SUBJECT_RECORDS} 条，统一质检暂不支持"
            )
        # Schema 按版本独立成行（schema_id 指向具体版本），因此按记录上的
        # schema_id 读取就是"标注当时的定义"，不会被后续升级改写。
        schemas: dict[UUID, AnnotationSchema] = {}
        for record in records:
            if record.schema_id in schemas:
                continue
            schema = self._schemas.get(scope, record.schema_id)
            if schema is not None:
                schemas[record.schema_id] = schema
        policies: dict[UUID, dict[str, object]] = {}
        for record in records:
            if record.task_id in policies:
                continue
            task = self._tasks.get(scope, record.task_id)
            if task is not None:
                policies[record.task_id] = dict(task.quality_policy)
        return QualitySubjectFacts(
            subject_type=QualitySubjectType.EPISODE,
            subject_id=episode_id,
            assets=assets,
            annotation_records=records,
            schemas=schemas,
            task_policies=policies,
            episode_bounds={episode_id: (episode.start_time_ns, episode.end_time_ns)},
        )

    def _import_facts(self, scope: ProjectScope, import_id: UUID) -> QualitySubjectFacts:
        job = self._imports.get(scope, import_id)
        if job is None:
            raise ResourceNotFoundError("导入任务", import_id)
        count = self._assets.count_by_import(scope, import_id)
        if count > MAX_SUBJECT_ASSETS:
            raise DomainValidationError(
                f"导入资产 {count} 条超过上限 {MAX_SUBJECT_ASSETS}，统一质检暂不支持"
            )
        assets = (
            tuple(self._assets.list_by_import(scope, import_id, limit=MAX_SUBJECT_ASSETS))
            if count
            else ()
        )
        return QualitySubjectFacts(
            subject_type=QualitySubjectType.DATASOURCE_IMPORT,
            subject_id=import_id,
            import_job=job,
            assets=assets,
        )

    # ------------------------------------------------------------------
    # 规则执行与版本
    # ------------------------------------------------------------------

    def _evaluate(
        self,
        scope: ProjectScope,
        facts: QualitySubjectFacts,
        categories: tuple[QualityCategory, ...] | None,
    ) -> tuple[str, tuple[QualityFindingDraft, ...]]:
        selected_categories = None if categories is None else frozenset(categories)
        declarations = tuple(
            item
            for item in self._registry.declarations_for(facts.subject_type)
            if selected_categories is None or item.category in selected_categories
        )
        if not declarations:
            # 没有任何规则适用于该对象：仍然生成一次 INCOMPLETE 运行，
            # 让调用方看到"检查过但无结论"，而不是 404 或假通过。
            return compute_rule_set_version(()), ()
        selected_keys = {(item.source, item.rule_id) for item in declarations}
        rule_set_version = compute_rule_set_version(declarations)

        drafts: list[QualityFindingDraft] = []
        for source in self._registry.sources_for(facts.subject_type):
            declared = {item.rule_id: item for item in source.declarations}
            keep = {rule_id for rule_id in declared if (source.source_id, rule_id) in selected_keys}
            if not keep:
                continue
            produced = source.evaluate(scope, facts)
            returned: set[str] = set()
            for draft in produced:
                declaration = declared.get(draft.rule_id)
                if declaration is None:
                    raise QualityEngineError(
                        f"规则源 {source.source_id} 返回了未声明的规则 {draft.rule_id}"
                    )
                if declaration.category is not draft.category:
                    raise QualityEngineError(f"规则 {draft.rule_id} 的类别与声明不一致")
                if declaration.severity is not draft.severity:
                    raise QualityEngineError(f"规则 {draft.rule_id} 的严重级别与声明不一致")
                if draft.rule_id in keep:
                    returned.add(draft.rule_id)
                    drafts.append(draft)
            missing = sorted(keep - returned)
            if missing:
                names = "、".join(f"{source.source_id}/{rule_id}" for rule_id in missing)
                raise QualityEngineError(f"规则源 {source.source_id} 未返回已声明规则：{names}")
        return rule_set_version, tuple(drafts)

    # ------------------------------------------------------------------
    # 留痕
    # ------------------------------------------------------------------

    def _record(
        self,
        scope: ProjectScope,
        run: QualityRun,
        *,
        request_id: str | None,
        occurred_at: datetime,
    ) -> None:
        record_change(
            self._audit,
            self._outbox,
            scope,
            resource_type=_RESOURCE_TYPE,
            resource_id=run.run_id,
            action="run",
            from_status=None,
            to_status=run.status.value,
            reason=None,
            request_id=request_id,
            details={
                "subject_type": run.subject_type.value,
                "subject_id": str(run.subject_id),
                "rule_set_version": run.rule_set_version,
                "finding_count": run.finding_count,
                "error_count": run.error_count,
                "warn_count": run.warn_count,
                "score": run.score,
                "category_status": {
                    key: value["status"] for key, value in run.category_summary.items()
                },
            },
            event_type=_EVENT_TYPE,
            event_payload={
                "run_id": str(run.run_id),
                "subject_type": run.subject_type.value,
                "subject_id": str(run.subject_id),
                "rule_set_version": run.rule_set_version,
                "status": run.status.value,
                "finding_count": run.finding_count,
                "error_count": run.error_count,
                "warn_count": run.warn_count,
            },
            occurred_at=occurred_at,
        )


__all__ = ["MAX_SUBJECT_ASSETS", "MAX_SUBJECT_RECORDS", "QualityRunUseCases"]
