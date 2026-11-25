"""``DATA_CLEANING`` 确定性清洗阶段单元测试（V51-006，纯内存/SQLite，零外部 PG 依赖）。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.cleaning_stage import (
    CLEANING_REPORT_SCHEMA_VERSION,
    DataCleaningHandler,
    cleaning_report_key,
)
from app.application.processing.commands import NewProcessingRun
from app.application.processing.ports import ObjectInspectionError
from app.application.processing.stages import StageContext, StageFailure
from app.application.processing.stream_extraction_stage import STREAM_SCHEMA_VERSION
from app.application.processing.use_cases import ProcessingRunUseCases
from app.core.config import Settings
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifact, ProcessingArtifactKind
from app.domain.processing.cleaning_rules import (
    CLEANING_RULESET_VERSION,
    RULE_ACTION_STEP_JUMP,
    RULE_CONSTANT_CHANNEL,
    RULE_NON_FINITE_OR_EMPTY_VECTOR,
    RULE_NON_MONOTONIC_TIMESTAMP,
    RULE_OBS_ACTION_COUNT_MISMATCH,
    RULE_SHORT_EPISODE_FRAGMENT,
    CleaningAction,
    CleaningFrameSample,
    CleaningStreamSlice,
    CleaningThresholds,
    evaluate_stream_records,
)
from app.domain.processing.enums import (
    ProcessingRunStatus,
    ProcessingStage,
    ProcessingStepStatus,
)
from app.domain.processing.errors import DomainValidationError
from app.domain.processing.models import ProcessingRun
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
from app.infrastructure.repositories.processing_artifacts import (
    SqlProcessingArtifactRepository,
)
from app.infrastructure.repositories.raw_assets import SqlRawAssetRepository
from app.workers.processing import ProcessingRunWorker, RunProcessingStatus

from tests.api.conftest import seed_identity
from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


class _InMemoryArtifactCatalog:
    def __init__(self, items: list[ProcessingArtifact] | None = None) -> None:
        self._items = list(items or [])

    def add(self, scope: ProjectScope, artifact: ProcessingArtifact) -> ProcessingArtifact:
        self._items.append(artifact)
        return artifact

    def list(
        self,
        scope: ProjectScope,
        *,
        run_id: UUID,
        stage: str | None = None,
        attempt: int | None = None,
        kind: str | None = None,
    ) -> list[ProcessingArtifact]:
        return [
            item
            for item in self._items
            if item.project_id == scope.project_id
            and item.run_id == run_id
            and (stage is None or item.stage == stage)
            and (attempt is None or item.attempt == attempt)
            and (kind is None or item.kind == kind)
        ]

    def get(self, scope: ProjectScope, artifact_id: UUID) -> ProcessingArtifact | None:
        return next((i for i in self._items if i.artifact_id == artifact_id), None)


def _make_clean_slice(
    *,
    target_ref: str = "processing-run/p/r/streams/robomimic_hdf5_v1.5/0000.ndjson",
    frames: int = 6,
    action_source: str = "PRESENT",
) -> CleaningStreamSlice:
    base = 1_700_000_000_000_000_000
    obs = tuple(
        CleaningFrameSample(
            f"demo_0/obs/{i}", i, (0.1 * i, -0.2 * i + 0.05, 1.0 + 0.01 * i), base + i * 20_000_000
        )
        for i in range(frames)
    )
    acts = (
        tuple(
            CleaningFrameSample(f"demo_0/act/{i}", i, (0.05 * i, -0.05 * i), base + i * 20_000_000)
            for i in range(frames)
        )
        if action_source == "PRESENT"
        else ()
    )
    return CleaningStreamSlice(target_ref, "raw/p/demo.hdf5", action_source, obs, acts)


def _build_running_context(project_id: UUID, run_id: UUID, *, now: datetime = NOW) -> StageContext:
    actor = uuid4()
    run = ProcessingRun(
        run_id,
        project_id,
        "pipeline-v5.1",
        (f"raw/{project_id}/demo.hdf5",),
        status=ProcessingRunStatus.RUNNING.value,
        stage=ProcessingStage.DATA_CLEANING.value,
        attempt=1,
        started_at=now,
        row_version=2,
        created_at=now,
        updated_at=now,
        created_by=actor,
        updated_by=actor,
    )
    return StageContext(run=run, scope=ProjectScope(project_id, actor_id=actor), now=now)


def _stream_artifact(
    ctx: StageContext,
    object_key: str,
    *,
    size_bytes: int = 128,
    checksum_sha256: str = "a" * 64,
    source_ref: str | None = None,
) -> ProcessingArtifact:
    return ProcessingArtifact(
        uuid4(),
        ctx.run.project_id,
        ctx.run.run_id,
        ProcessingStage.STREAM_EXTRACTION.value,
        1,
        ProcessingArtifactKind.STREAM_EXTRACTION.value,
        object_key,
        STREAM_SCHEMA_VERSION,
        "application/x-ndjson",
        size_bytes,
        checksum_sha256=checksum_sha256,
        source_ref=source_ref,
        created_at=NOW,
        created_by=ctx.scope.actor_id,
    )


def _make_ndjson_bytes(
    *, observations: list[dict[str, object]], actions: list[dict[str, object]] | None
) -> bytes:
    header = {
        "_meta": {
            "schema_version": STREAM_SCHEMA_VERSION,
            "action_source": "PRESENT" if actions is not None else "ACTION_SOURCE_ABSENT",
            "streams": [],
            "limitations": [],
        }
    }
    lines = [json.dumps(header, ensure_ascii=False, sort_keys=True)]
    lines.extend(
        json.dumps({"type": "observation", **obs}, ensure_ascii=False, sort_keys=True)
        for obs in observations
    )
    lines.extend(
        json.dumps({"type": "action", **act}, ensure_ascii=False, sort_keys=True)
        for act in actions or []
    )
    return ("\n".join(lines) + "\n").encode("utf-8")


def test_clean_slice_passes_with_zero_hits_and_deterministic_sha256() -> None:
    """无异常流切片命中为 0，且多次评估及逆序切片输入的 SHA256 完全一致。"""
    slice_a = _make_clean_slice(target_ref="processing-run/p/r/streams/l/0000.ndjson")
    slice_b = _make_clean_slice(target_ref="processing-run/p/r/streams/l/0001.ndjson")

    report_1 = evaluate_stream_records([slice_a, slice_b])
    report_2 = evaluate_stream_records([slice_b, slice_a])

    assert report_1.hits == ()
    assert report_1.raw_asset_mutated is False
    assert report_1.derived_data_mutated is False
    assert report_1.ruleset_version == CLEANING_RULESET_VERSION
    assert report_1.deterministic_sha256() == report_2.deterministic_sha256()
    assert report_1.to_payload() == report_2.to_payload()


def test_non_monotonic_timestamp_hits_time_rule_without_precision_loss() -> None:
    """重复或回退纳秒时间戳精准触发 CLN-TIME-001，大整数以字符串保留完整精度。"""
    big_ns = (1 << 55) + 123456789
    samples = tuple(
        CleaningFrameSample(f"obs/{i}", i, (float(i),), timestamp_ns=ts)
        for i, ts in enumerate([big_ns, big_ns + 10, big_ns + 10, big_ns + 5, big_ns + 30])
    )
    acts = tuple(CleaningFrameSample(f"act/{i}", i, (0.1 * i,)) for i in range(5))
    item = CleaningStreamSlice(
        "processing-run/p/r/streams/l/0000.ndjson", None, "PRESENT", samples, acts
    )

    report = evaluate_stream_records([item])
    assert [hit.rule_id for hit in report.hits] == [RULE_NON_MONOTONIC_TIMESTAMP]
    hit = report.hits[0]
    assert hit.action == CleaningAction.FLAG.value
    assert hit.frame_range == (1, 3)
    assert hit.field_path == "observations.timestamp_ns"
    assert hit.observed_value == {
        "violation_count": 2,
        "first_prev_timestamp_ns": str(big_ns + 10),
        "first_curr_timestamp_ns": str(big_ns + 10),
    }


def test_nan_inf_and_empty_vector_hit_numeric_rule_with_strict_json() -> None:
    """含 NaN/Inf 或空向量精准触发 CLN-NUM-001，且序列化严格兼容 RFC 8259 JSON。"""
    obs = tuple(
        CleaningFrameSample(f"obs/{i}", i, (float("nan") if i == 2 else float(i), 1.0 + i))
        for i in range(5)
    )
    acts = tuple(
        CleaningFrameSample(
            f"act/{i}", i, () if i == 4 else (0.1 * i, float("inf") if i == 1 else -0.1 * i)
        )
        for i in range(5)
    )
    item = CleaningStreamSlice(
        "processing-run/p/r/streams/l/0000.ndjson", None, "PRESENT", obs, acts
    )

    report = evaluate_stream_records([item])
    num_hits = [h for h in report.hits if h.rule_id == RULE_NON_FINITE_OR_EMPTY_VECTOR]
    assert len(num_hits) == 2
    assert num_hits[0].frame_range == (2, 2)
    assert num_hits[0].field_path == "observations[*].values[0]"
    assert num_hits[1].frame_range == (1, 4)
    assert len(report.deterministic_sha256()) == 64


def test_constant_channel_and_action_jump_hit_respective_rules() -> None:
    """常量通道（CLN-NUM-002）与相邻帧动作突变（CLN-NUM-003）精准定位通道与帧区间。"""
    obs = tuple(
        CleaningFrameSample(f"obs/{i}", i, (float(i), 42.0, -7.5, 0.2 * i)) for i in range(6)
    )
    acts = tuple(
        CleaningFrameSample(f"act/{i}", i, (0.1 * i, 3.5 if i == 3 else 0.01 * i)) for i in range(6)
    )
    item = CleaningStreamSlice(
        "processing-run/p/r/streams/l/0000.ndjson", None, "PRESENT", obs, acts
    )

    report = evaluate_stream_records(
        [item], thresholds=CleaningThresholds(max_action_step_delta=1.5)
    )
    by_rule = {hit.rule_id: hit for hit in report.hits}

    assert set(by_rule) == {RULE_CONSTANT_CHANNEL, RULE_ACTION_STEP_JUMP}
    assert by_rule[RULE_CONSTANT_CHANNEL].observed_value == {
        "constant_dims": [1, 2],
        "constant_dim_count": 2,
        "total_dims": 4,
    }
    assert by_rule[RULE_ACTION_STEP_JUMP].frame_range == (2, 4)
    assert by_rule[RULE_ACTION_STEP_JUMP].field_path == "actions[*].values[1]"


def test_short_episode_and_obs_action_mismatch_rules_are_orthogonal() -> None:
    """过短片段命中 CLN-EPISODE-001；动作源存在但帧数不等命中 CLN-SEM-001；无动作源不误报。"""
    short_slice = _make_clean_slice(target_ref="processing-run/p/r/streams/l/0000.ndjson", frames=3)
    base = _make_clean_slice(target_ref="processing-run/p/r/streams/l/0001.ndjson", frames=6)
    mismatch_slice = CleaningStreamSlice(
        base.target_ref, base.source_ref, "PRESENT", base.observations, base.actions[:4]
    )
    absent_slice = _make_clean_slice(
        target_ref="processing-run/p/r/streams/l/0002.ndjson",
        frames=6,
        action_source="ACTION_SOURCE_ABSENT",
    )

    report = evaluate_stream_records([short_slice, mismatch_slice, absent_slice])
    hits_by_target = {(h.target_ref, h.rule_id): h for h in report.hits}

    assert (short_slice.target_ref, RULE_SHORT_EPISODE_FRAGMENT) in hits_by_target
    assert (mismatch_slice.target_ref, RULE_OBS_ACTION_COUNT_MISMATCH) in hits_by_target
    assert (mismatch_slice.target_ref, RULE_SHORT_EPISODE_FRAGMENT) not in hits_by_target
    assert not any(h.target_ref == absent_slice.target_ref for h in report.hits)


def test_disabled_rule_ids_suppress_evaluation_and_reject_unknown_ids() -> None:
    """关闭指定规则后不再产生该规则命中，且报告显式记录已关闭规则；未知 ID 报错。"""
    short_slice = _make_clean_slice(frames=2)

    report = evaluate_stream_records([short_slice], disabled_rule_ids={RULE_SHORT_EPISODE_FRAGMENT})
    assert report.hits == ()
    assert report.disabled_rule_ids == (RULE_SHORT_EPISODE_FRAGMENT,)
    assert RULE_SHORT_EPISODE_FRAGMENT not in report.evaluated_rule_ids

    with pytest.raises(DomainValidationError, match="未知规则 ID"):
        evaluate_stream_records([short_slice], disabled_rule_ids={"CLN-UNKNOWN-999"})


def test_handler_skips_honestly_when_no_stream_extraction_artifacts_exist() -> None:
    """当前运行没有 STREAM_EXTRACTION 产物时，如实返回 SKIPPED 且零写入。"""
    store = FakeArtifactStore()
    outcome = DataCleaningHandler(_InMemoryArtifactCatalog(), store)(
        _build_running_context(uuid4(), uuid4())
    )

    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "STREAM_EXTRACTION" in outcome.log_summary
    assert outcome.output_refs == ()
    assert outcome.artifacts == ()
    assert store.keys() == []


def test_handler_produces_byte_identical_report_across_reruns_and_preserves_inputs() -> None:
    """不同时间重跑同一流产物，生成的 report.json 字节一致，且上游流产物零改动。"""
    project_id, run_id = uuid4(), uuid4()
    ctx_t1 = _build_running_context(project_id, run_id, now=NOW)
    ctx_t2 = _build_running_context(project_id, run_id, now=NOW + timedelta(hours=3))
    stream_key = f"processing-run/{project_id}/{run_id}/streams/robomimic_hdf5_v1.5/0000.ndjson"
    ndjson_bytes = _make_ndjson_bytes(
        observations=[
            {
                "observation_id": f"obs/{i}",
                "frame_index": i,
                "values": [0.2 * i, 1.0],
                "timestamp_ns": 100 + i,
            }
            for i in range(5)
        ],
        actions=[
            {
                "action_id": f"act/{i}",
                "frame_index": i,
                "values": [0.1 * i, -0.1 * i],
                "timestamp_ns": 100 + i,
            }
            for i in range(5)
        ],
    )
    store = FakeArtifactStore()
    store.put_bytes(stream_key, ndjson_bytes)
    catalog = _InMemoryArtifactCatalog(
        [
            _stream_artifact(
                ctx_t1,
                stream_key,
                size_bytes=len(ndjson_bytes),
                checksum_sha256=hashlib.sha256(ndjson_bytes).hexdigest(),
                source_ref=f"raw/{project_id}/demo.hdf5",
            )
        ]
    )
    handler = DataCleaningHandler(catalog, store)

    out_1 = handler(ctx_t1)
    report_key = cleaning_report_key(project_id, run_id)
    bytes_1 = store.read_bytes(report_key, max_bytes=1024 * 1024)
    out_2 = handler(ctx_t2)
    bytes_2 = store.read_bytes(report_key, max_bytes=1024 * 1024)

    assert out_1.status == ProcessingStepStatus.SUCCEEDED.value
    assert bytes_1 == bytes_2
    assert out_1.artifacts[0].checksum_sha256 == out_2.artifacts[0].checksum_sha256
    assert store.read_bytes(stream_key, max_bytes=1024 * 1024) == ndjson_bytes
    doc = json.loads(bytes_1)
    assert doc["schema_version"] == CLEANING_REPORT_SCHEMA_VERSION
    assert doc["raw_asset_mutated"] is False
    assert doc["derived_data_mutated"] is False
    assert [h["rule_id"] for h in doc["hits"]] == [RULE_CONSTANT_CHANNEL]


def test_handler_maps_storage_and_format_errors_explicitly() -> None:
    """缺失、超限、非法 JSON、对象存储故障均映射为明确的 StageFailure，不吞异常。"""
    ctx = _build_running_context(uuid4(), uuid4())
    stream_key = f"processing-run/{ctx.run.project_id}/{ctx.run.run_id}/streams/l/0000.ndjson"
    catalog = _InMemoryArtifactCatalog([_stream_artifact(ctx, stream_key)])

    with pytest.raises(StageFailure) as missing_exc:
        DataCleaningHandler(catalog, FakeArtifactStore())(ctx)
    assert missing_exc.value.error_code == "cleaning_input_missing"
    assert missing_exc.value.retryable is True

    bad_store = FakeArtifactStore()
    bad_store.put_bytes(stream_key, b"{not-json}\n")
    with pytest.raises(StageFailure) as invalid_exc:
        DataCleaningHandler(catalog, bad_store)(ctx)
    assert invalid_exc.value.error_code == "cleaning_input_invalid"
    assert invalid_exc.value.retryable is False

    with pytest.raises(StageFailure) as limit_exc:
        DataCleaningHandler(catalog, bad_store, max_stream_artifact_bytes=4)(ctx)
    assert limit_exc.value.error_code == "cleaning_input_too_large"
    assert limit_exc.value.retryable is False

    class _BrokenStore(FakeArtifactStore):
        def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
            raise ObjectInspectionError("S3 timeout")

    with pytest.raises(StageFailure) as storage_exc:
        DataCleaningHandler(catalog, _BrokenStore())(ctx)
    assert storage_exc.value.error_code == "object_storage_unavailable"
    assert storage_exc.value.retryable is True


def test_worker_end_to_end_runs_cleaning_on_hdf5_and_skips_non_hdf5(tmp_path: Path) -> None:
    """SQLite 真实 Worker 端到端：HDF5 产出清洗报告且原始资产零篡改；非 HDF5 如实 SKIPPED。"""
    h5py = pytest.importorskip("h5py", reason="需要 h5py 构造 RoboMimic 同布局 HDF5")
    hdf5_file = tmp_path / "demo.hdf5"
    with h5py.File(hdf5_file, "w") as handle:
        group = handle.create_group("data/demo_0")
        group.create_dataset("states", data=[[float(i), 1.0] * 16 for i in range(6)])
        group.create_dataset("actions", data=[[0.1 * i] * 7 for i in range(6)])
    raw_bytes = hdf5_file.read_bytes()
    raw_sha = hashlib.sha256(raw_bytes).hexdigest()

    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'worker_cleaning.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    try:
        ids = seed_identity(factory)
        scope = ProjectScope(ids.project_a, actor_id=ids.editor_a)
        hdf5_ref, plain_ref = f"raw/{ids.project_a}/demo.hdf5", f"raw/{ids.project_a}/plain.bin"
        reader = FakeObjectReader({hdf5_ref: raw_bytes, plain_ref: b"0123456789ab"})
        worker = ProcessingRunWorker(
            factory,
            Settings(),
            inspector=reader,
            reader=reader,
            artifacts=FakeArtifactStore(),  # type: ignore[call-arg]
        )
        with factory() as session:
            use_cases = ProcessingRunUseCases(
                SqlProcessingRunRepository(session),
                SqlProcessingRunStepRepository(session),
                session,
                audit=SqlAuditRepository(session),
                outbox=SqlOutboxRepository(session),
                raw_assets=SqlRawAssetRepository(session),
            )
            hdf5_run = use_cases.create(scope, NewProcessingRun("pipeline-v5.1", (hdf5_ref,)))
            plain_run = use_cases.create(scope, NewProcessingRun("pipeline-v5.1", (plain_ref,)))

        assert (
            worker.process_once(now=datetime.now(UTC))["status"]
            == RunProcessingStatus.PUBLISHED.value
        )
        assert (
            worker.process_once(now=datetime.now(UTC))["status"]
            == RunProcessingStatus.PUBLISHED.value
        )

        with factory() as session:
            step_repo = SqlProcessingRunStepRepository(session)
            hdf5_steps = {s.stage: s for s in step_repo.list(scope, run_id=hdf5_run.run_id)}
            plain_steps = {s.stage: s for s in step_repo.list(scope, run_id=plain_run.run_id)}
            cleaning_arts = SqlProcessingArtifactRepository(session).list(
                scope, run_id=hdf5_run.run_id, stage=ProcessingStage.DATA_CLEANING.value
            )

        assert hdf5_steps[ProcessingStage.DATA_CLEANING.value].status == "SUCCEEDED"
        assert plain_steps[ProcessingStage.DATA_CLEANING.value].status == "SKIPPED"
        assert len(cleaning_arts) == 1
        assert cleaning_arts[0].kind == ProcessingArtifactKind.CANONICAL.value
        assert cleaning_arts[0].schema_version == CLEANING_REPORT_SCHEMA_VERSION
        assert (
            hashlib.sha256(reader.read_range(hdf5_ref, start=0, length=len(raw_bytes))).hexdigest()
            == raw_sha
        )
    finally:
        engine.dispose()
