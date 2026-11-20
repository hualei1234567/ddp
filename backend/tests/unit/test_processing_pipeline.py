"""八阶段编排器（N4-002）：真实 SQLite，不 Mock 仓储。

覆盖：happy path（FILE_VALIDATION 真实校验 + 七阶段显式 SKIPPED）、
对象缺失 / 存储不可用、重试后按新 attempt 续跑、取消导致并发冲突时
残留 RUNNING 步骤被收口。
"""

from __future__ import annotations

from collections.abc import Iterator

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.commands import NewProcessingRun
from app.application.processing.pipeline import (
    ConcurrentRunUpdate,
    FileValidationHandler,
    PipelineOutcomeStatus,
    ProcessingPipeline,
    SkippedStage,
    StageContext,
    StageOutcome,
    default_handlers,
)
from app.application.processing.ports import ObjectInspectionError
from app.application.processing.use_cases import (
    ProcessingRunUseCases,
    ProcessingWorkerUseCases,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import (
    STAGE_SEQUENCE,
    ProcessingRunStatus,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.processing.errors import ResourceNotFoundError
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from app.infrastructure.repositories.collection_audit import (
    SqlAuditRepository,
    SqlOutboxRepository,
)
from app.infrastructure.repositories.processing import (
    SqlProcessingRunRepository,
    SqlProcessingRunStepRepository,
)
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity


class FakeInspector:
    def __init__(self, sizes: dict[str, int] | None = None, *, unavailable: bool = False) -> None:
        self._sizes = sizes or {}
        self._unavailable = unavailable

    def object_size(self, object_key: str) -> int | None:
        if self._unavailable:
            raise ObjectInspectionError("对象存储不可用")
        return self._sizes.get(object_key)


@pytest.fixture()
def factory() -> Iterator[sessionmaker[Session]]:
    engine = create_db_engine(
        "sqlite+pysqlite:///:memory:",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    session_factory = create_session_factory(engine)
    try:
        yield session_factory
    finally:
        engine.dispose()


def _session_services(
    factory: sessionmaker[Session],
) -> tuple[Session, ProcessingRunUseCases, ProcessingWorkerUseCases]:
    session = factory()
    runs = SqlProcessingRunRepository(session)
    use_cases = ProcessingRunUseCases(
        runs,
        SqlProcessingRunStepRepository(session),
        session,
        audit=SqlAuditRepository(session),
        outbox=SqlOutboxRepository(session),
    )
    return (
        session,
        use_cases,
        ProcessingWorkerUseCases(
            runs,
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        ),
    )


def _pipeline(
    session: Session,
    handlers: dict | None = None,
    *,
    inspector: FakeInspector | None = None,
) -> ProcessingPipeline:
    runs = SqlProcessingRunRepository(session)
    return ProcessingPipeline(
        runs,
        SqlProcessingRunStepRepository(session),
        ProcessingWorkerUseCases(
            runs,
            session,
            audit=SqlAuditRepository(session),
            outbox=SqlOutboxRepository(session),
        ),
        handlers
        or default_handlers(
            inspector or FakeInspector(),
            # 本文件只验证编排语义；元数据/格式阶段用占位跳过，真实实现见
            # test_processing_metadata_stage.py 与 test_processing_metadata_pipeline.py。
            metadata_extraction=SkippedStage("编排测试不装配元数据解析"),
            format_parsing=SkippedStage("编排测试不装配格式解析"),
            time_alignment=SkippedStage("编排测试不装配时间对齐"),
            calibration_validation=SkippedStage("编排测试不装配标定校验"),
            quality_check=SkippedStage("编排测试不装配质量引擎"),
        ),
        session,
    )


def test_happy_path_marks_unimplemented_stages_skipped(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(f"raw/{ids.project_a}/a", f"raw/{ids.project_a}/b"),
            ),
        )
        pipeline = _pipeline(
            session,
            inspector=FakeInspector({f"raw/{ids.project_a}/a": 10, f"raw/{ids.project_a}/b": 5}),
        )

        outcome = pipeline.execute(scope, run.run_id)

        assert outcome.status == PipelineOutcomeStatus.SUCCEEDED.value
        assert outcome.run.status == ProcessingRunStatus.SUCCEEDED.value
        assert outcome.run.stage == ProcessingStage.QUALITY_PRE_CHECK.value
        assert outcome.run.attempt == 1

        steps = use_cases.list_steps(scope, run.run_id)
        assert [step.stage for step in steps] == [stage.value for stage in STAGE_SEQUENCE]
        assert steps[0].status == ProcessingStepStatus.SUCCEEDED.value
        assert "2 个输入对象" in (steps[0].log_summary or "")
        skipped = steps[1:]
        assert all(step.status == ProcessingStepStatus.SKIPPED.value for step in skipped)
        # SKIPPED 必须解释原因，不能是空字符串。
        assert all(step.log_summary and "跳过" in step.log_summary for step in skipped)
        assert all(step.finished_at is not None for step in steps)
    finally:
        session.close()


def test_input_ref_outside_project_is_rejected_without_storage_access(
    factory: sessionmaker[Session],
) -> None:
    """输入引用必须落在本项目下：不能借 object_missing/大小日志探测别的项目。"""

    class NoStorageInspector:
        def object_size(self, object_key: str) -> int | None:
            raise AssertionError(f"不应访问对象存储：{object_key}")

    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1",
                input_refs=(f"raw/{ids.project_b}/secret",),
            ),
        )
        pipeline = _pipeline(session, inspector=NoStorageInspector())

        outcome = pipeline.execute(scope, run.run_id)

        assert outcome.status == PipelineOutcomeStatus.FAILED.value
        assert outcome.error_code == "input_ref_not_project_scoped"
        step = use_cases.list_steps(scope, run.run_id)[0]
        assert step.error_code == "input_ref_not_project_scoped"
        # 错误摘要不得回显 key。
        assert "secret" not in (step.error_summary or "")
        assert "secret" not in (step.log_summary or "")
    finally:
        session.close()


def test_missing_input_object_fails_and_retry_uses_new_attempt(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1", input_refs=(f"raw/{ids.project_a}/missing",)
            ),
        )
        pipeline = _pipeline(session, inspector=FakeInspector({}))

        first = pipeline.execute(scope, run.run_id)
        assert first.status == PipelineOutcomeStatus.FAILED.value
        assert first.error_code == "object_missing"
        assert first.run.status == ProcessingRunStatus.FAILED.value
        assert first.run.stage == ProcessingStage.FILE_VALIDATION.value

        step = use_cases.list_steps(scope, run.run_id)[0]
        assert step.status == ProcessingStepStatus.FAILED.value
        assert step.error_code == "object_missing"
        assert step.attempt == 1

        # FAILED 不是终态：再次执行会经 worker-retry 重新 claim，attempt 递增，
        # 步骤历史保留第一次失败（不会被覆盖）。
        second = pipeline.execute(scope, run.run_id)
        assert second.status == PipelineOutcomeStatus.FAILED.value
        assert second.run.attempt == 2
        attempts = [
            item.attempt
            for item in use_cases.list_steps(scope, run.run_id)
            if item.stage == "FILE_VALIDATION"
        ]
        assert attempts == [1, 2]
    finally:
        session.close()


def test_storage_unavailable_is_distinct_from_missing_object(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1", input_refs=(f"raw/{ids.project_a}/a",)
            ),
        )
        pipeline = _pipeline(session, inspector=FakeInspector(unavailable=True))

        outcome = pipeline.execute(scope, run.run_id)

        assert outcome.status == PipelineOutcomeStatus.FAILED.value
        assert outcome.error_code == "object_storage_unavailable"
    finally:
        session.close()


class _CancelDuringStage:
    """阶段执行期间取消运行，模拟客户端与 Worker 的竞态。"""

    def __init__(self, use_cases: ProcessingRunUseCases) -> None:
        self._use_cases = use_cases

    def __call__(self, context: StageContext) -> StageOutcome:
        current = self._use_cases.get(context.scope, context.run.run_id)
        self._use_cases.cancel(
            context.scope,
            current.run_id,
            expected_version=current.row_version,
        )
        return StageOutcome(
            status=ProcessingStepStatus.SUCCEEDED.value,
            log_summary="取消后仍返回成功，用于验证运行侧冲突",
        )


def test_cancel_during_stage_aborts_step_without_leaving_running(
    factory: sessionmaker[Session],
) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1", input_refs=(f"raw/{ids.project_a}/a",)
            ),
        )
        handlers = default_handlers(
            FakeInspector({f"raw/{ids.project_a}/a": 1}),
            metadata_extraction=SkippedStage("编排测试不装配元数据解析"),
            format_parsing=SkippedStage("编排测试不装配格式解析"),
            time_alignment=SkippedStage("编排测试不装配时间对齐"),
            calibration_validation=SkippedStage("编排测试不装配标定校验"),
            quality_check=SkippedStage("编排测试不装配质量引擎"),
        )
        handlers[ProcessingStage.FILE_VALIDATION] = _CancelDuringStage(use_cases)
        pipeline = _pipeline(session, handlers=handlers)

        with pytest.raises(ConcurrentRunUpdate):
            pipeline.execute(scope, run.run_id)

        current = use_cases.get(scope, run.run_id)
        assert current.status == ProcessingRunStatus.CANCELLED.value
        step = use_cases.list_steps(scope, run.run_id)[0]
        assert step.status == ProcessingStepStatus.FAILED.value
        assert step.error_code == "run_state_changed"
        assert step.log_summary is not None
    finally:
        session.close()


def test_default_handlers_cover_every_stage(factory: sessionmaker[Session]) -> None:
    metadata_extraction = SkippedStage("测试占位：元数据")
    format_parsing = SkippedStage("测试占位：格式解析")
    time_alignment = SkippedStage("测试占位：时间对齐")
    calibration_validation = SkippedStage("测试占位：标定校验")
    quality_check = SkippedStage("测试占位：质量")
    handlers = default_handlers(
        FakeInspector(),
        metadata_extraction=metadata_extraction,
        format_parsing=format_parsing,
        time_alignment=time_alignment,
        calibration_validation=calibration_validation,
        quality_check=quality_check,
    )
    assert set(handlers) == set(STAGE_SEQUENCE)
    assert isinstance(handlers[ProcessingStage.FILE_VALIDATION], FileValidationHandler)
    assert handlers[ProcessingStage.METADATA_EXTRACTION] is metadata_extraction
    assert handlers[ProcessingStage.FORMAT_PARSING] is format_parsing
    assert handlers[ProcessingStage.TIME_ALIGNMENT] is time_alignment
    assert handlers[ProcessingStage.CALIBRATION_VALIDATION] is calibration_validation
    assert handlers[ProcessingStage.QUALITY_PRE_CHECK] is quality_check
    # 剩余两个阶段必须是显式 SKIPPED，且原因写明依赖。
    for stage in (
        ProcessingStage.EPISODE_SEGMENTATION,
        ProcessingStage.DATA_CLEANING,
    ):
        assert isinstance(handlers[stage], SkippedStage)


def test_terminal_run_is_noop(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope,
            NewProcessingRun(
                pipeline_version="pipeline-v1", input_refs=(f"raw/{ids.project_a}/a",)
            ),
        )
        use_cases.cancel(scope, run.run_id, expected_version=run.row_version)
        outcome = _pipeline(session).execute(scope, run.run_id)
        assert outcome.status == PipelineOutcomeStatus.NOOP.value
        assert use_cases.list_steps(scope, run.run_id) == []
    finally:
        session.close()


def test_scope_is_enforced_for_cross_project_run(factory: sessionmaker[Session]) -> None:
    ids = seed_identity(factory)
    scope_a = ProjectScope(project_id=ids.project_a, actor_id=ids.editor_a)
    scope_b = ProjectScope(project_id=ids.project_b, actor_id=ids.owner_a)
    session, use_cases, _worker = _session_services(factory)
    try:
        run = use_cases.create(
            scope_a,
            NewProcessingRun(
                pipeline_version="pipeline-v1", input_refs=(f"raw/{ids.project_a}/a",)
            ),
        )
        with pytest.raises(ResourceNotFoundError):
            _pipeline(session).execute(scope_b, run.run_id)
    finally:
        session.close()
