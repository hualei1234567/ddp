"""``DATA_CLEANING`` TRANSFORM / QUARANTINE 动作引擎测试（V61-015 / V61-016）。

覆盖两条红线：

1. **原始资产零篡改**：变换只产出衍生切片，输入切片与上游对象字节不变；
2. **隔离显式留痕**：命中隔离规则的 target 不会被静默丢弃，而是写入结构化清单。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import app.infrastructure.db.models  # noqa: F401 - 注册全部 ORM 模型
import pytest
from app.application.processing.cleaning_stage import (
    DataCleaningHandler,
    cleaning_transformed_key,
)
from app.application.processing.stages import StageContext
from app.application.processing.stream_extraction_stage import STREAM_SCHEMA_VERSION
from app.domain.identity.models import ProjectScope
from app.domain.processing.artifacts import ProcessingArtifact, ProcessingArtifactKind
from app.domain.processing.cleaning_actions import (
    SUPPORTED_TRANSFORM_RULE_IDS,
    TRANSFORM_SCHEMA_VERSION,
    apply_cleaning_actions,
)
from app.domain.processing.cleaning_evaluators import RULE_ACTION_STEP_JUMP
from app.domain.processing.cleaning_rules import (
    RULE_NON_FINITE_OR_EMPTY_VECTOR,
    CleaningFrameSample,
    CleaningStreamSlice,
    CleaningThresholds,
    evaluate_stream_records,
)
from app.domain.processing.enums import ProcessingRunStatus, ProcessingStepStatus
from app.domain.processing.errors import DomainValidationError
from app.infrastructure.db.base import Base
from app.infrastructure.db.session import create_db_engine, create_session_factory
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import seed_identity
from tests.unit.processing_stage_support import FakeArtifactStore

NOW = datetime(2026, 11, 29, tzinfo=UTC)
THRESHOLDS = CleaningThresholds(max_action_step_delta=1.0)


def _slice_with_jump(target: str = "streams/demo0.ndjson") -> CleaningStreamSlice:
    observations = tuple(
        CleaningFrameSample(
            record_id=f"obs-{i}", frame_index=i, values=(0.1 * i, 0.0), timestamp_ns=i * 10**8
        )
        for i in range(4)
    )
    actions = (
        CleaningFrameSample(record_id="act-0", frame_index=0, values=(0.0, 0.0), timestamp_ns=0),
        # 第二帧单步跳变 +5.0，超过阈值 1.0 → TRANSFORM 应限幅到 +1.0
        CleaningFrameSample(
            record_id="act-1", frame_index=1, values=(5.0, 0.0), timestamp_ns=10**8
        ),
        CleaningFrameSample(
            record_id="act-2", frame_index=2, values=(5.5, 0.0), timestamp_ns=2 * 10**8
        ),
    )
    return CleaningStreamSlice(
        target_ref=target,
        source_ref="raw/source.hdf5",
        action_source="PRESENT",
        observations=observations,
        actions=actions,
    )


# ---------------------------------------------------------------- 纯领域层


def test_default_flag_only_rules_do_not_quarantine_or_transform() -> None:
    """默认规则集全部 FLAG 且未显式启用动作 → 不隔离、不变换（向后兼容）。"""
    item = _slice_with_jump()
    report = evaluate_stream_records([item], thresholds=THRESHOLDS)

    result = apply_cleaning_actions([item], report, thresholds=THRESHOLDS)

    assert result.quarantined == ()
    assert result.transformed_slices == ()
    assert result.raw_asset_mutated is False
    assert result.derived_data_mutated is False


def test_explicit_quarantine_rule_marks_target_and_keeps_input_untouched() -> None:
    """显式隔离规则 → 生成隔离记录，且输入切片值原样不变。"""
    item = _slice_with_jump()
    report = evaluate_stream_records([item], thresholds=THRESHOLDS)

    result = apply_cleaning_actions(
        [item],
        report,
        thresholds=THRESHOLDS,
        quarantine_rule_ids={RULE_ACTION_STEP_JUMP},
    )

    assert len(result.quarantined) == 1
    decision = result.quarantined[0]
    assert decision.target_ref == item.target_ref
    assert decision.rule_ids == (RULE_ACTION_STEP_JUMP,)
    assert decision.hit_count == 1
    assert result.quarantined_target_refs == frozenset({item.target_ref})
    # 原始切片字节级未变：动作值仍是原始跳变值
    assert [a.values for a in item.actions] == [(0.0, 0.0), (5.0, 0.0), (5.5, 0.0)]
    assert result.raw_asset_mutated is False


def test_transform_clamps_action_step_and_is_deterministic() -> None:
    """TRANSFORM → 衍生切片限幅到阈值，且两次执行结果完全一致。"""
    item = _slice_with_jump()
    report = evaluate_stream_records([item], thresholds=THRESHOLDS)

    first = apply_cleaning_actions(
        [item], report, thresholds=THRESHOLDS, transform_rule_ids={RULE_ACTION_STEP_JUMP}
    )
    second = apply_cleaning_actions(
        [item], report, thresholds=THRESHOLDS, transform_rule_ids={RULE_ACTION_STEP_JUMP}
    )

    assert len(first.transformed_slices) == 1
    transformed = first.transformed_slices[0]
    # 限幅对**输出序列**施加最大步长（逐步累加）：
    # 原始 0.0 → 5.0 步长 5.0 超阈值 → 输出 1.0；随后原始 5.5 相对**输出** 1.0
    # 步长 4.5 仍超阈值 → 输出 2.0。这样相邻输出帧之间永不超过阈值。
    assert [a.values for a in transformed.actions] == [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0)]
    assert transformed.clamped_frames == 2
    assert transformed.clamped_dimensions == 2
    assert transformed.transform_provenance["max_action_step_delta"] == 1.0
    assert transformed.transform_provenance["rule_id"] == RULE_ACTION_STEP_JUMP
    # 幂等确定性：相同输入两次结果一致
    assert first.transformed_slices[0].transform_provenance == (
        second.transformed_slices[0].transform_provenance
    )
    assert [a.values for a in second.transformed_slices[0].actions] == [
        a.values for a in transformed.actions
    ]
    # 原输入仍未变
    assert [a.values for a in item.actions] == [(0.0, 0.0), (5.0, 0.0), (5.5, 0.0)]
    assert first.raw_asset_mutated is False
    assert first.derived_data_mutated is True


def test_transformed_target_is_skipped_when_also_quarantined() -> None:
    """同一 target 同时被隔离与变换 → 只隔离，不产出衍生数据。"""
    item = _slice_with_jump()
    report = evaluate_stream_records([item], thresholds=THRESHOLDS)

    result = apply_cleaning_actions(
        [item],
        report,
        thresholds=THRESHOLDS,
        quarantine_rule_ids={RULE_ACTION_STEP_JUMP},
        transform_rule_ids={RULE_ACTION_STEP_JUMP},
    )

    assert len(result.quarantined) == 1
    assert result.transformed_slices == ()
    assert result.derived_data_mutated is False


def test_unsupported_transform_rule_fails_explicitly() -> None:
    """把不支持的规则显式列为 TRANSFORM → 明确报错，不静默跳过。"""
    item = _slice_with_jump()
    report = evaluate_stream_records([item], thresholds=THRESHOLDS)

    with pytest.raises(DomainValidationError, match="不支持 TRANSFORM"):
        apply_cleaning_actions(
            [item],
            report,
            thresholds=THRESHOLDS,
            transform_rule_ids={RULE_NON_FINITE_OR_EMPTY_VECTOR},
        )
    assert RULE_NON_FINITE_OR_EMPTY_VECTOR not in SUPPORTED_TRANSFORM_RULE_IDS


def test_error_severity_rule_can_quarantine_all_targets() -> None:
    """CLN-NUM-001（ERROR 级）显式启用隔离 → 命中目标全部进入隔离清单。"""
    bad = CleaningStreamSlice(
        target_ref="streams/bad.ndjson",
        source_ref=None,
        action_source="PRESENT",
        observations=(
            CleaningFrameSample(record_id="o0", frame_index=0, values=(1.0,)),
            CleaningFrameSample(record_id="o1", frame_index=1, values=(float("nan"),)),
        ),
        actions=(),
    )
    report = evaluate_stream_records([bad], thresholds=THRESHOLDS)

    result = apply_cleaning_actions(
        [bad], report, thresholds=THRESHOLDS, quarantine_rule_ids={RULE_NON_FINITE_OR_EMPTY_VECTOR}
    )

    assert result.quarantined_target_refs == frozenset({"streams/bad.ndjson"})
    assert RULE_NON_FINITE_OR_EMPTY_VECTOR in result.quarantined[0].rule_ids


# ---------------------------------------------------------------- 阶段处理器层


class _FakeArtifactCatalog:
    def __init__(self, items: list[ProcessingArtifact]) -> None:
        self._items = items

    def list(self, scope, *, run_id, kind):  # noqa: ANN001
        return [item for item in self._items if item.kind == kind and item.run_id == run_id]


def _stream_ndjson(has_jump: bool) -> bytes:
    lines = [
        json.dumps(
            {
                "_meta": {
                    "schema_version": STREAM_SCHEMA_VERSION,
                    "action_source": "PRESENT",
                }
            }
        )
    ]
    for i in range(4):
        lines.append(
            json.dumps(
                {
                    "type": "observation",
                    "observation_id": f"obs-{i}",
                    "frame_index": i,
                    "timestamp_ns": i * 100_000_000,
                    "values": [0.1 * i, 0.0],
                }
            )
        )
    for i in range(3):
        jump = 5.0 if (has_jump and i == 1) else 0.0
        lines.append(
            json.dumps(
                {
                    "type": "action",
                    "action_id": f"act-{i}",
                    "frame_index": i,
                    "timestamp_ns": i * 100_000_000,
                    "values": [jump, 0.0],
                }
            )
        )
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture()
def session_and_project(tmp_path: Path) -> tuple[Session, UUID]:
    engine = create_db_engine(
        f"sqlite+pysqlite:///{tmp_path / 'cleaning-actions.db'}",
        pool_size=1,
        max_overflow=0,
        pool_timeout_seconds=5,
        connect_timeout_seconds=5,
    )
    Base.metadata.create_all(engine)
    factory: sessionmaker[Session] = create_session_factory(engine)
    ids = seed_identity(factory)
    with factory() as session:
        try:
            yield session, ids.project_a
        finally:
            session.close()
    engine.dispose()


def _make_run(session: Session, project_id: UUID) -> object:
    # 直接构造领域实体：绕开 raw_asset 解析，只验证清洗阶段本身
    from app.domain.processing.models import ProcessingRun

    run = ProcessingRun(
        run_id=uuid4(),
        project_id=project_id,
        pipeline_version="pipeline-v1",
        input_refs=("raw/source.hdf5",),
        status=ProcessingRunStatus.RUNNING.value,
        stage="DATA_CLEANING",
        attempt=1,
        started_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
    return run


def _context(run: object, project_id: UUID) -> StageContext:
    return StageContext(
        run=run,  # type: ignore[arg-type]
        scope=ProjectScope(project_id=project_id, actor_id=uuid4()),
        now=NOW,
    )


def test_handler_writes_transformed_artifact_and_quarantine_in_report(
    session_and_project: tuple[Session, UUID],
) -> None:
    """阶段处理器：显式启用隔离与变换 → 报告含清单、对象存储有衍生产物。"""
    session, project_id = session_and_project
    run = _make_run(session, project_id)
    source_key = "processing-run/src/streams/demo0.ndjson"
    data = _stream_ndjson(has_jump=True)
    catalog = _FakeArtifactCatalog(
        [
            ProcessingArtifact(
                artifact_id=uuid4(),
                project_id=project_id,
                run_id=run.run_id,
                stage="DATA_CLEANING",
                object_key=source_key,
                kind=ProcessingArtifactKind.STREAM_EXTRACTION.value,
                schema_version=STREAM_SCHEMA_VERSION,
                content_type="application/x-ndjson",
                size_bytes=len(data),
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                source_ref="raw/source.hdf5",
                attempt=1,
            )
        ]
    )
    store = FakeArtifactStore()
    store.put_bytes(source_key, data)
    handler = DataCleaningHandler(
        catalog,
        store,
        thresholds=THRESHOLDS,
        quarantine_rule_ids={RULE_ACTION_STEP_JUMP},
        transform_rule_ids={RULE_ACTION_STEP_JUMP},
    )

    outcome = handler(_context(run, project_id))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "隔离 1 个" in outcome.log_summary
    # 同时命中隔离 → 不产出衍生变换产物
    assert "衍生变换 0 个" in outcome.log_summary
    report_key = f"processing-run/{project_id}/{run.run_id}/cleaning/report.json"
    report = json.loads(store._objects[report_key])
    assert len(report["quarantined_targets"]) == 1
    assert report["quarantined_targets"][0]["target_ref"] == source_key
    assert report["transformed_artifacts"] == []
    # 原始资产与上游流字节级未变
    assert store._objects[source_key] == data
    assert report["raw_asset_mutated"] is False


def test_handler_transform_only_writes_derived_stream(
    session_and_project: tuple[Session, UUID],
) -> None:
    """阶段处理器：仅启用 TRANSFORM → 衍生 NDJSON 落对象存储，且带溯源。"""
    session, project_id = session_and_project
    run = _make_run(session, project_id)
    source_key = "processing-run/src/streams/demo0.ndjson"
    data = _stream_ndjson(has_jump=True)
    catalog = _FakeArtifactCatalog(
        [
            ProcessingArtifact(
                artifact_id=uuid4(),
                project_id=project_id,
                run_id=run.run_id,
                stage="DATA_CLEANING",
                object_key=source_key,
                kind=ProcessingArtifactKind.STREAM_EXTRACTION.value,
                schema_version=STREAM_SCHEMA_VERSION,
                content_type="application/x-ndjson",
                size_bytes=len(data),
                checksum_sha256=hashlib.sha256(data).hexdigest(),
                source_ref="raw/source.hdf5",
                attempt=1,
            )
        ]
    )
    store = FakeArtifactStore()
    store.put_bytes(source_key, data)
    handler = DataCleaningHandler(
        catalog,
        store,
        thresholds=THRESHOLDS,
        transform_rule_ids={RULE_ACTION_STEP_JUMP},
    )

    outcome = handler(_context(run, project_id))

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "衍生变换 1 个" in outcome.log_summary
    transformed_key = cleaning_transformed_key(project_id, run.run_id, 0)
    assert transformed_key in store._objects
    payload_lines = store._objects[transformed_key].decode("utf-8").splitlines()
    meta = json.loads(payload_lines[0])["_meta"]
    assert meta["schema_version"] == TRANSFORM_SCHEMA_VERSION
    assert meta["provenance"]["rule_id"] == RULE_ACTION_STEP_JUMP
    action_lines = [json.loads(line) for line in payload_lines[1:] if '"action"' in line]
    # fixture 只在第 1 帧制造 5.0 的跳变：输出限幅到 1.0，随后回到 0.0
    # 的步长为 -1.0，恰好等于阈值（不超限）故不再裁剪。
    assert [line["values"] for line in action_lines] == [
        [0.0, 0.0],
        [1.0, 0.0],
        [0.0, 0.0],
    ]
    report = json.loads(
        store._objects[f"processing-run/{project_id}/{run.run_id}/cleaning/report.json"]
    )
    assert len(report["transformed_artifacts"]) == 1
    assert report["transformed_artifacts"][0]["object_key"] == transformed_key
    assert report["derived_data_mutated"] is True
    assert report["raw_asset_mutated"] is False
    # 原始上游流未被改写
    assert store._objects[source_key] == data


# ------------------------------------------------- 衍生流写入器（模块抽取后）


def test_serialize_transformed_slice_is_byte_deterministic() -> None:
    """同一条衍生切片两次序列化必须字节级相同（报告与产物 SHA 才有意义）。"""
    from app.application.processing.cleaning_transformed_writer import (
        serialize_transformed_slice,
    )
    from app.domain.processing.cleaning_actions import apply_cleaning_actions

    item = _slice_with_jump()
    report = evaluate_stream_records([item], thresholds=THRESHOLDS)
    result = apply_cleaning_actions(
        [item], report, thresholds=THRESHOLDS, transform_rule_ids={RULE_ACTION_STEP_JUMP}
    )
    transformed = result.transformed_slices[0]

    first = serialize_transformed_slice(transformed)
    second = serialize_transformed_slice(transformed)

    assert first == second
    lines = first.decode("utf-8").splitlines()
    assert json.loads(lines[0])["_meta"]["provenance"]["rule_id"] == RULE_ACTION_STEP_JUMP
    # 首行 _meta，随后是 observation 行（先）+ action 行（后）
    types = [json.loads(line).get("type") for line in lines[1:]]
    assert types == ["observation"] * 4 + ["action"] * 3


def test_write_transformed_slices_uses_shared_key_helper() -> None:
    """抽取后的写入器与处理器共用同一 Key 生成规则（不出现两套路径）。"""
    from app.application.processing.cleaning_transformed_writer import (
        cleaning_transformed_key as writer_key,
    )

    assert writer_key is cleaning_transformed_key
    assert cleaning_transformed_key(uuid4(), uuid4(), 2).endswith("transformed-002.ndjson")
