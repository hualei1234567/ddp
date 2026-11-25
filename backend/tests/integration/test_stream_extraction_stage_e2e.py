"""STREAM_EXTRACTION 阶段端到端集成测试（V51-001/002/003）。

**这是「真的接线」的证明**：此前 payload 读取与提取函数都存在，但阶段没有
接入编排器——``processing_artifacts`` 永远是 0 行。本测试走完整链路：

对象存储 →（有界下载）→ 本地 HDF5 →（真实 payload 读取）→ Canonical 三件套
→（NDJSON 序列化）→ 对象存储产物 + StageArtifact 描述。

**为什么用真实 HDF5**：mock 掉 payload 读取就等于不测数据模型交互（见 MEM-INC-004）。
合成用例用官方 ``h5py`` 现场构造同布局文件；真实用例读 RoboMimic 样本。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from app.application.processing.stages import StageContext
from app.application.processing.stream_extraction_stage import (
    LAYOUTS,
    STREAM_SCHEMA_VERSION,
    StreamExtractionHandler,
    render_ndjson,
)
from app.domain.identity.models import ProjectScope
from app.domain.processing.enums import ProcessingStepStatus
from app.domain.processing.models import ProcessingRun
from ddp_data.hdf5 import read_hdf5_dataset_slice

h5py = pytest.importorskip("h5py", reason="需要官方 h5py SDK")

_REAL_PATH = os.environ.get("DDP_TEST_REAL_HDF5")
_LAYOUT = LAYOUTS[0]


# ---- 受控替身（只替代存储，不替代解析）----


class _InMemoryReader:
    """把内存字节当作对象存储：``download_to`` 落盘，其余语义与真实 reader 一致。"""

    def __init__(self, objects: dict[str, bytes]) -> None:
        self._objects = objects
        self.downloaded: list[tuple[str, int]] = []

    def object_size(self, object_key: str) -> int | None:
        data = self._objects.get(object_key)
        return None if data is None else len(data)

    def read_range(self, object_key: str, *, start: int, length: int) -> bytes:
        return self._objects[object_key][start : start + length]

    def download_to(self, object_key: str, destination: Path, *, max_bytes: int) -> int:
        data = self._objects[object_key]
        if len(data) > max_bytes:
            raise ValueError(f"对象超过上限 {max_bytes}")
        destination.write_bytes(data)
        self.downloaded.append((object_key, len(data)))
        return len(data)


class _InMemoryArtifacts:
    """内存产物存储：记录写入内容，供断言。"""

    def __init__(self) -> None:
        self.written: dict[str, bytes] = {}

    def put_bytes(self, object_key: str, data: bytes) -> None:
        self.written[object_key] = data

    def read_bytes(self, object_key: str, *, max_bytes: int) -> bytes:
        data = self.written[object_key]
        assert len(data) <= max_bytes, "读取超过上限"
        return data


def _build_robomimic_like(path: Path, *, demos: int = 2, frames: int = 5) -> bytes:
    """构造与 RoboMimic 同布局的 HDF5 字节（含 2 条流 + 动作）。"""
    with h5py.File(path, "w") as f:
        for d in range(demos):
            group = f.create_group(f"data/demo_{d}")
            group.create_dataset("actions", data=[[0.1 * (i + 1)] * 7 for i in range(frames)])
            group.create_dataset("states", data=[[float(i), float(d)] * 16 for i in range(frames)])
            group.create_dataset("user_info/0/timestamp", data=[1000.0 + i for i in range(frames)])
    return path.read_bytes()


def _make_run(input_refs: tuple[str, ...]) -> ProcessingRun:
    project_id = UUID(int=1)
    return ProcessingRun(
        run_id=uuid4(),
        project_id=project_id,
        pipeline_version="test/1",
        input_refs=input_refs,
        created_by=UUID(int=9),
        updated_by=UUID(int=9),
    )


def _make_handler(
    reader: _InMemoryReader, artifacts: _InMemoryArtifacts
) -> StreamExtractionHandler:
    return StreamExtractionHandler(
        reader,  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        read_slice=read_hdf5_dataset_slice,
    )


# ---- 端到端 ----


def test_stage_downloads_extracts_and_writes_artifacts(tmp_path: Path) -> None:
    """完整链路：下载 → 提取 → 写产物 → 返回 StageArtifact 描述。"""
    data = _build_robomimic_like(tmp_path / "src.hdf5", demos=2, frames=5)
    key = "raw/00000000-0000-0000-0000-000000000001/sample.hdf5"
    reader = _InMemoryReader({key: data})
    artifacts = _InMemoryArtifacts()
    handler = _make_handler(reader, artifacts)

    run = _make_run((key,))
    outcome = handler(
        StageContext(
            run=run,
            scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
            now=run.created_at,
        )
    )

    # 1) 真的下载了
    assert reader.downloaded and reader.downloaded[0][0] == key
    # 2) 阶段成功（不是 SKIPPED）
    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert "observation" in outcome.log_summary and "action" in outcome.log_summary
    # 3) 每个 demo 一个产物
    assert len(outcome.artifacts) == 2
    assert len(artifacts.written) == 2
    # 4) 产物描述完整
    for artifact in outcome.artifacts:
        assert artifact.schema_version == STREAM_SCHEMA_VERSION
        assert artifact.content_type == "application/x-ndjson"
        assert artifact.size_bytes > 0
        assert artifact.checksum_sha256 and len(artifact.checksum_sha256) == 64
        assert artifact.source_ref == key
    # 5) 产物内容可解析，且逐帧记录数量正确
    first = next(iter(artifacts.written.values())).decode()
    lines = [line for line in first.split("\n") if line]
    assert len(lines) == 1 + 5 + 5  # meta + 5 obs + 5 act
    meta = json.loads(lines[0])["_meta"]
    assert meta["action_source"] == "PRESENT"
    assert meta["schema_version"] == STREAM_SCHEMA_VERSION


def test_stage_skips_when_no_hdf5_input(tmp_path: Path) -> None:
    """无 HDF5 输入 → 如实 SKIPPED，不是失败（可能这个 run 只处理 MP4）。"""
    reader = _InMemoryReader({})
    handler = _make_handler(reader, _InMemoryArtifacts())
    run = _make_run(("raw/p/video.mp4",))

    outcome = handler(
        StageContext(
            run=run,
            scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
            now=run.created_at,
        )
    )
    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "没有 .hdf5 输入" in outcome.log_summary
    assert not reader.downloaded


def test_stage_skips_when_layout_not_found(tmp_path: Path) -> None:
    """HDF5 里没有 ``data/demo_*`` 布局 → SKIPPED 并说明（不猜布局）。"""
    path = tmp_path / "other.hdf5"
    with h5py.File(path, "w") as f:
        f.create_dataset("something_else", data=[1, 2, 3])
    key = "raw/p/other.hdf5"
    reader = _InMemoryReader({key: data_bytes if (data_bytes := path.read_bytes()) else b""})
    handler = _make_handler(reader, _InMemoryArtifacts())
    run = _make_run((key,))

    outcome = handler(
        StageContext(
            run=run,
            scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
            now=run.created_at,
        )
    )
    assert outcome.status == ProcessingStepStatus.SKIPPED.value
    assert "未找到" in outcome.log_summary


def test_stage_fails_loudly_on_download_error() -> None:
    """下载失败 → 阶段失败（可重试），不静默跳过。"""

    class _BrokenReader(_InMemoryReader):
        def download_to(self, object_key: str, destination: Path, *, max_bytes: int) -> int:
            raise OSError("storage unavailable")

    handler = StreamExtractionHandler(
        _BrokenReader({}),  # type: ignore[arg-type]
        _InMemoryArtifacts(),  # type: ignore[arg-type]
        read_slice=read_hdf5_dataset_slice,
    )
    run = _make_run(("raw/p/x.hdf5",))
    with pytest.raises(Exception) as excinfo:
        handler(
            StageContext(
                run=run,
                scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
                now=run.created_at,
            )
        )
    # StageFailure 把错误码放在 error_code 属性上（消息是给人看的摘要）
    assert getattr(excinfo.value, "error_code", None) == "hdf5_download_failed"


def test_artifact_key_is_stable_when_same_run_retries(tmp_path: Path) -> None:
    """同一 run 重试 → 产物 key 相同（不产生产物分叉）。

    注意：key 含 ``run_id``（不同 run 本就该是不同产物），因此稳定性只在
    **同一 run 的重复执行**上成立——这正是重试场景需要的性质。
    """
    data = _build_robomimic_like(tmp_path / "src.hdf5", demos=1, frames=3)
    key = "raw/p/x.hdf5"
    run = _make_run((key,))

    def run_once() -> set[str]:
        artifacts = _InMemoryArtifacts()
        handler = _make_handler(_InMemoryReader({key: data}), artifacts)
        handler(
            StageContext(
                run=run,
                scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
                now=run.created_at,
            )
        )
        return set(artifacts.written)

    assert run_once() == run_once()


def test_different_runs_produce_different_artifact_keys(tmp_path: Path) -> None:
    """不同 run 必须产出不同 key——否则两次处理会互相覆盖。"""
    data = _build_robomimic_like(tmp_path / "src.hdf5", demos=1, frames=3)
    key = "raw/p/x.hdf5"
    seen: list[set[str]] = []
    for _ in range(2):
        artifacts = _InMemoryArtifacts()
        handler = _make_handler(_InMemoryReader({key: data}), artifacts)
        run = _make_run((key,))
        handler(
            StageContext(
                run=run,
                scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
                now=run.created_at,
            )
        )
        seen.append(set(artifacts.written))
    assert seen[0] != seen[1]


# ---- 真实样本 ----


@pytest.mark.skipif(
    not _REAL_PATH or not Path(_REAL_PATH).is_file(),
    reason="需要 DDP_TEST_REAL_HDF5 指向本地 RoboMimic demo_v15.hdf5",
)
def test_real_sample_end_to_end_through_stage() -> None:
    """真实 RoboMimic 样本走完整阶段链路（E1 证据）。"""
    data = Path(_REAL_PATH).read_bytes()  # type: ignore[arg-type]
    key = "raw/p/robomimic.hdf5"
    artifacts = _InMemoryArtifacts()
    handler = StreamExtractionHandler(
        _InMemoryReader({key: data}),  # type: ignore[arg-type]
        artifacts,  # type: ignore[arg-type]
        read_slice=read_hdf5_dataset_slice,
        max_demos=2,
    )
    run = _make_run((key,))
    outcome = handler(
        StageContext(
            run=run,
            scope=ProjectScope(project_id=run.project_id, actor_id=UUID(int=9)),
            now=run.created_at,
        )
    )

    assert outcome.status == ProcessingStepStatus.SUCCEEDED.value
    assert len(outcome.artifacts) == 2

    # 校验真实内容：states 32 维、actions 7 维、动作源存在
    first = next(iter(artifacts.written.values())).decode()
    lines = [json.loads(line) for line in first.split("\n") if line]
    meta = lines[0]["_meta"]
    assert meta["action_source"] == "PRESENT"
    assert any("不含图像" in item for item in meta["limitations"])

    obs = [ln for ln in lines[1:] if ln.get("type") == "observation"]
    act = [ln for ln in lines[1:] if ln.get("type") == "action"]
    assert obs and act
    assert len(obs[0]["values"]) == 32
    assert len(act[0]["values"]) == 7
    # 真实值非占位
    assert any(abs(v) > 1e-9 for v in obs[0]["values"])


def test_ndjson_roundtrip_is_parseable(tmp_path: Path) -> None:
    """渲染出的 NDJSON 每行可独立解析（逐帧消费方不解析整棵树）。"""
    data = _build_robomimic_like(tmp_path / "src.hdf5", demos=1, frames=3)
    from app.application.processing.stream_extraction_stage import extract_robomimic_demo

    local = tmp_path / "src.hdf5"
    local.write_bytes(data)
    record = extract_robomimic_demo(
        read_slice=read_hdf5_dataset_slice,
        file_path=str(local),
        demo_index=0,
        layout=_LAYOUT,
    )
    lines = [line for line in render_ndjson(record).decode().split("\n") if line]
    assert len(lines) == 1 + 3 + 3
    for line in lines:
        json.loads(line)
