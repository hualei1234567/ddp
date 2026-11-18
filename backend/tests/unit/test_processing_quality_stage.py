"""``QUALITY_PRE_CHECK`` 阶段（N4-005）：解析 Episode、复用质量引擎、失败语义。

覆盖：输入引用解析（Canonical manifest / RawAsset 关联）、无 Episode 时显式
SKIPPED、规则集版本与失败 Episode 写入阶段结果、数据问题不可重试、规则执行
异常不可重试、落库失败回滚且可重试、Episode 数量上限与跨项目引用隔离。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.processing.quality_stage import (
    QualityCheckHandler,
    resolve_episode_ids,
)
from app.application.processing.stages import StageContext, StageFailure, StageOutcome
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.processing.models import ProcessingRun
from app.domain.quality.aggregation import summarize_findings
from app.domain.quality.enums import (
    QualityCategory,
    QualityFindingStatus,
    QualityRunStatus,
    QualitySeverity,
)
from app.domain.quality.errors import (
    DomainValidationError,
    QualityEngineError,
    ResourceNotFoundError,
)
from app.domain.quality.models import QualityFinding, QualityRun

NOW = datetime(2026, 11, 19, 10, 0, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class _Link:
    episode_id: UUID


class FakeEpisodeAssets:
    def __init__(self, links: dict[UUID, list[UUID]] | None = None) -> None:
        self._links = links or {}
        self.calls: list[UUID] = []

    def list_for_asset(self, scope: ProjectScope, asset_id: UUID) -> list[_Link]:
        self.calls.append(asset_id)
        return [_Link(episode_id) for episode_id in self._links.get(asset_id, [])]


class FakeUnitOfWork:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


def _quality_run(
    scope: ProjectScope,
    episode_id: UUID,
    status: QualityRunStatus,
    *,
    rule_set_version: str,
) -> QualityRun:
    run_id = uuid4()
    finding_status = {
        QualityRunStatus.PASSED: QualityFindingStatus.PASS,
        QualityRunStatus.FAILED: QualityFindingStatus.FAIL,
        QualityRunStatus.INCOMPLETE: QualityFindingStatus.NOT_APPLICABLE,
    }[status]
    score = (
        1.0
        if status is QualityRunStatus.PASSED
        else 0.0
        if status is QualityRunStatus.FAILED
        else None
    )
    finding = QualityFinding(
        finding_id=uuid4(),
        project_id=scope.project_id,
        run_id=run_id,
        rule_id="rule",
        rule_version="1",
        category=QualityCategory.INTEGRITY,
        severity=QualitySeverity.ERROR,
        status=finding_status,
        message="判定说明",
        score=score,
        created_at=NOW,
    )
    summary = summarize_findings([finding])
    return QualityRun(
        run_id=run_id,
        project_id=scope.project_id,
        subject_type="EPISODE",
        subject_id=episode_id,
        rule_set_version=rule_set_version,
        status=summary.status,
        score=summary.score,
        finding_count=summary.finding_count,
        error_count=summary.error_count,
        warn_count=summary.warn_count,
        category_summary=summary.category_payload(),
        started_at=NOW,
        finished_at=NOW,
        created_at=NOW,
        created_by=scope.actor_id,
        findings=(finding,),
    )


class FakeQuality:
    def __init__(
        self,
        *,
        statuses: dict[UUID, QualityRunStatus] | None = None,
        rule_set_version: str = "sha256:" + "a" * 64,
        prepare_error: tuple[UUID, Exception] | None = None,
        persist_error: Exception | None = None,
    ) -> None:
        self._statuses = statuses or {}
        self._rule_set_version = rule_set_version
        self._prepare_error = prepare_error
        self._persist_error = persist_error
        self.prepare_calls: list[UUID] = []
        self.persisted: list[tuple[QualityRun, ...]] = []

    def prepare_episode(
        self,
        scope: ProjectScope,
        episode_id: UUID,
        *,
        now: datetime,
    ) -> QualityRun:
        self.prepare_calls.append(episode_id)
        if self._prepare_error is not None and self._prepare_error[0] == episode_id:
            raise self._prepare_error[1]
        return _quality_run(
            scope,
            episode_id,
            self._statuses.get(episode_id, QualityRunStatus.INCOMPLETE),
            rule_set_version=self._rule_set_version,
        )

    def persist_prepared(self, scope: ProjectScope, runs: tuple[QualityRun, ...]) -> None:
        self.persisted.append(runs)
        if self._persist_error is not None:
            raise self._persist_error


def _scope() -> ProjectScope:
    return ProjectScope(project_id=uuid4(), actor_id=uuid4())


def _run(scope: ProjectScope, refs: tuple[str, ...]) -> ProcessingRun:
    return ProcessingRun(
        run_id=uuid4(),
        project_id=scope.project_id,
        pipeline_version="pipeline-v1",
        input_refs=refs,
        created_at=NOW,
        updated_at=NOW,
        created_by=scope.actor_id,
        updated_by=scope.actor_id,
    )


def _handler(
    *,
    assets: FakeEpisodeAssets | None = None,
    quality: FakeQuality | None = None,
    uow: FakeUnitOfWork | None = None,
    max_episodes: int = 200,
) -> tuple[QualityCheckHandler, FakeQuality, FakeUnitOfWork]:
    quality = quality or FakeQuality()
    uow = uow or FakeUnitOfWork()
    return (
        QualityCheckHandler(
            episode_assets=assets or FakeEpisodeAssets(),
            quality=quality,
            uow=uow,
            max_episodes=max_episodes,
        ),
        quality,
        uow,
    )


def _manifest(project_id: UUID, episode_id: UUID) -> str:
    return f"canonical/{project_id}/{episode_id}/1/manifest.json"


def _raw_key(project_id: UUID, asset_id: UUID) -> str:
    return f"raw/{project_id}/2026/11/19/{asset_id}/content.mcap"


def test_unresolvable_inputs_skip_with_reason_and_never_call_engine() -> None:
    scope = _scope()
    handler, quality, _uow = _handler()

    outcome = handler(
        StageContext(
            run=_run(scope, (f"raw/{scope.project_id}/a",)),
            scope=scope,
            now=NOW,
        )
    )

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "未关联任何 Episode" in outcome.log_summary
    assert quality.prepare_calls == []
    assert quality.persisted == []


def test_manifest_and_raw_asset_refs_resolve_in_order_and_dedupe() -> None:
    scope = _scope()
    first, second = uuid4(), uuid4()
    asset_id = uuid4()
    assets = FakeEpisodeAssets({asset_id: [second, first]})
    refs = (
        _manifest(scope.project_id, first),
        _raw_key(scope.project_id, asset_id),
        _manifest(scope.project_id, first),
    )

    resolved = resolve_episode_ids(scope, refs, assets)

    assert resolved == (first, second)
    assert assets.calls == [asset_id]


def test_cross_project_refs_are_not_resolved() -> None:
    scope = _scope()
    other_project = uuid4()
    other_asset = uuid4()
    assets = FakeEpisodeAssets({other_asset: [uuid4()]})
    refs = (
        _manifest(other_project, uuid4()),
        _raw_key(other_project, other_asset),
    )

    assert resolve_episode_ids(scope, refs, assets) == ()
    assert assets.calls == []


def test_stage_reports_counts_rule_version_and_quality_run_refs() -> None:
    scope = _scope()
    passed, failed = uuid4(), uuid4()
    quality = FakeQuality(
        statuses={passed: QualityRunStatus.PASSED, failed: QualityRunStatus.FAILED}
    )
    handler, quality, uow = _handler(quality=quality)

    outcome = handler(
        StageContext(
            run=_run(
                scope,
                (
                    _manifest(scope.project_id, passed),
                    _manifest(scope.project_id, failed),
                ),
            ),
            scope=scope,
            now=NOW,
        )
    )

    assert isinstance(outcome, StageOutcome)
    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert quality.prepare_calls == [passed, failed]
    assert len(quality.persisted) == 1
    assert {run.subject_id for run in quality.persisted[0]} == {passed, failed}
    assert "检查 2 个 Episode" in outcome.log_summary
    assert "通过 1、失败 1、未完成 0" in outcome.log_summary
    assert f"规则集 {quality._rule_set_version}" in outcome.log_summary
    assert str(failed) in outcome.log_summary
    assert outcome.output_refs == tuple(
        f"quality-run/{run.run_id}" for run in quality.persisted[0]
    )
    assert uow.rollbacks == 0


def test_quality_engine_error_is_permanent_and_nothing_persisted() -> None:
    scope = _scope()
    episode_id = uuid4()
    handler, quality, _uow = _handler(
        quality=FakeQuality(prepare_error=(episode_id, QualityEngineError("声明不一致")))
    )

    with pytest.raises(StageFailure) as raised:
        handler(
            StageContext(
                run=_run(scope, (_manifest(scope.project_id, episode_id),)),
                scope=scope,
                now=NOW,
            )
        )

    assert raised.value.error_code == "quality_engine_error"
    assert raised.value.retryable is False
    assert quality.persisted == []


def test_domain_validation_error_is_permanent() -> None:
    scope = _scope()
    episode_id = uuid4()
    handler, quality, _uow = _handler(
        quality=FakeQuality(prepare_error=(episode_id, DomainValidationError("超过上限")))
    )

    with pytest.raises(StageFailure) as raised:
        handler(
            StageContext(
                run=_run(scope, (_manifest(scope.project_id, episode_id),)),
                scope=scope,
                now=NOW,
            )
        )

    assert raised.value.error_code == "quality_subject_invalid"
    assert raised.value.retryable is False
    assert quality.persisted == []


def test_missing_episode_is_permanent() -> None:
    scope = _scope()
    episode_id = uuid4()
    handler, _quality, _uow = _handler(
        quality=FakeQuality(
            prepare_error=(episode_id, ResourceNotFoundError("Episode", episode_id))
        )
    )

    with pytest.raises(StageFailure) as raised:
        handler(
            StageContext(
                run=_run(scope, (_manifest(scope.project_id, episode_id),)),
                scope=scope,
                now=NOW,
            )
        )

    assert raised.value.error_code == "quality_episode_not_found"
    assert raised.value.retryable is False


def test_persist_failure_rolls_back_and_is_retryable() -> None:
    scope = _scope()
    episode_id = uuid4()
    handler, quality, uow = _handler(
        quality=FakeQuality(persist_error=RuntimeError("数据库不可用"))
    )

    with pytest.raises(StageFailure) as raised:
        handler(
            StageContext(
                run=_run(scope, (_manifest(scope.project_id, episode_id),)),
                scope=scope,
                now=NOW,
            )
        )

    assert raised.value.error_code == "quality_persist_failed"
    assert raised.value.retryable is True
    assert uow.rollbacks == 1
    assert len(quality.persisted) == 1


def test_episode_limit_is_enforced_without_truncation() -> None:
    scope = _scope()
    refs = tuple(_manifest(scope.project_id, uuid4()) for _ in range(2))
    handler, quality, _uow = _handler(max_episodes=1)

    with pytest.raises(StageFailure) as raised:
        handler(StageContext(run=_run(scope, refs), scope=scope, now=NOW))

    assert raised.value.error_code == "quality_episode_limit_exceeded"
    assert raised.value.retryable is False
    assert quality.prepare_calls == []
