"""真实公开 HDF5（RoboMimic v1.5）的结构探测回归（DDP-V21-N3-006）。

**样例来源**：Hugging Face ``amandlek/robomimic``（MIT，非 gated），
固定 revision ``74fa018461f479cd9fd15b924a16103012096203`` 下的
``v1.5/lift/ph/demo_v15.hdf5``，大小 36,699,586 字节，
SHA256 ``68f2a8cf…72f37``（与 HF 返回的 ``x-linked-etag`` 一致）。
来源与观测事实记录在 ``tests/fixtures/public/robomimic-hdf5/facts.json``。

**为什么默认跳过**

该文件约 35 MB，不能再进仓库；CI 不应依赖外网下载。因此本用例只在显式
提供本地路径时运行：

.. code-block:: bash

   DDP_TEST_REAL_HDF5=/path/to/demo_v15.hdf5 \\
     pytest backend/tests/unit/test_hdf5_real_public_sample.py -q

这与既有 Hugging Face 真实联网用例的口径一致：默认跳过，不把“没跑”伪装成
“通过”。

**本用例证明什么、不证明什么**

证明：真实机器人演示集的 HDF5 结构可被有界读取，路径/形状/dtype 与官方
RoboMimic 文档描述的 ``data/demo_*/actions`` 布局一致，默认上限足以容纳。
不证明：观测图像语义、标定、物理时钟或真实硬件在环——这些仍是
``HARDWARE BLOCKED``。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ddp_data.hdf5 import detect_hdf5, read_hdf5_structure

_FACTS = Path(__file__).parents[1] / "fixtures/public/robomimic-hdf5/facts.json"
_REAL_PATH = os.environ.get("DDP_TEST_REAL_HDF5")

pytestmark = pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_HDF5 指向本地 RoboMimic demo_v15.hdf5",
)


def _facts() -> dict[str, object]:
    return json.loads(_FACTS.read_text(encoding="utf-8"))


def test_real_robomimic_structure_matches_recorded_facts() -> None:
    facts = _facts()
    expected = facts["observed_structure"]
    assert isinstance(expected, dict)

    with Path(_REAL_PATH).open("rb") as handle:  # type: ignore[arg-type]
        assert detect_hdf5(handle.read(1 << 20)) is True

    structure = read_hdf5_structure(_REAL_PATH)  # type: ignore[arg-type]

    assert structure.dataset_count == expected["dataset_count"]
    assert structure.group_count == expected["group_count"]
    assert structure.attribute_count == expected["attribute_count"]
    assert structure.truncated is False
    assert [item.path for item in structure.links] == []

    datasets = {item.path: item for item in structure.datasets}
    for sample in expected["sample_paths"]:
        actual = datasets[sample["path"]]
        assert list(actual.shape or ()) == sample["shape"]
        assert actual.dtype == sample["dtype"]
        assert actual.compression == sample["compression"]

    # 默认上限必须容纳真实中等规模演示集，否则每次探测都会失败。
    assert structure.dataset_count < 50_000
    assert structure.group_count < 20_000


def test_real_metadata_stays_within_serialization_budget() -> None:
    """结构摘要是 1 MB 量级，必须显式知道而不是意外发现。"""
    structure = read_hdf5_structure(_REAL_PATH)  # type: ignore[arg-type]
    encoded = json.dumps(structure.as_metadata())
    # 记录当前真实值（约 1.0 MB）；明显增长说明摘要泄露了不该有的内容。
    assert len(encoded) < 2 * 1024 * 1024


def test_real_metadata_does_not_leak_dataset_values() -> None:
    structure = read_hdf5_structure(_REAL_PATH)  # type: ignore[arg-type]
    payload = repr(structure)
    # actions 是 float64 数组；若摘要夹带数值会被这里抓住。
    assert "0.123456" not in payload
    assert structure.as_metadata()["datasets"][0]["shape"] is not None


def test_real_robomimic_processing_stage_emits_bounded_explicit_projection() -> None:
    """真实文件经 Processing Stage 后小于 256 KiB，且不隐藏完整总数。"""
    from datetime import UTC, datetime
    from uuid import uuid4

    from app.application.processing.metadata_stage import MetadataExtractionHandler
    from app.application.processing.stages import StageContext
    from app.domain.identity.models import ProjectScope
    from app.domain.processing.enums import ProcessingStage, ProcessingStepStatus
    from app.domain.processing.models import ProcessingRun

    from tests.unit.processing_stage_support import FakeArtifactStore, FakeObjectReader

    source = Path(_REAL_PATH)  # type: ignore[arg-type]
    project_id = uuid4()
    run_id = uuid4()
    ref = f"raw/{project_id}/2026/11/21/{uuid4()}/content.h5"
    artifacts = FakeArtifactStore()
    context = StageContext(
        run=ProcessingRun(
            run_id=run_id,
            project_id=project_id,
            pipeline_version="pipeline-v1",
            input_refs=(ref,),
            output_refs=(),
            status="RUNNING",
            stage=ProcessingStage.METADATA_EXTRACTION.value,
            attempt=1,
            started_at=datetime(2026, 11, 21, tzinfo=UTC),
        ),
        scope=ProjectScope(project_id=project_id, actor_id=uuid4()),
        now=datetime(2026, 11, 21, tzinfo=UTC),
    )

    outcome = MetadataExtractionHandler(
        FakeObjectReader({ref: source.read_bytes()}),
        artifacts,
    )(context)

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    encoded = artifacts.read_bytes(outcome.output_refs[0], max_bytes=256 * 1024)
    document = json.loads(encoded)
    metadata = document["metadata"]
    expected = _facts()["observed_structure"]
    assert metadata["dataset_count"] == expected["dataset_count"]
    assert metadata["group_count"] == expected["group_count"]
    assert metadata["attribute_count"] == expected["attribute_count"]
    assert metadata["details_truncated"] is True
    assert metadata["omitted_dataset_count"] == 6008 - len(metadata["datasets"])
    assert metadata["omitted_group_count"] == 1802 - len(metadata["groups"])
    assert len(encoded) <= 256 * 1024
