"""V51-010：QC 证据定位的 API 与端到端（三类证据）。

覆盖蓝图 §6 要求的三类证据：

1. **媒体时间区间**：媒体资产的片段区间 → ``time_range`` + ``stream_id``；
2. **数值流越界**：标注数值标签越界 → ``field_path``（+ 记录区间）；
3. **时间同步超差**：多路流偏移超阈值 → 共同可见窗口 + 偏移最大的流。

## 关于测试数据的诚实说明

本仓库**没有真实传感器/媒体样本**（导入链路按 Hugging Face 元数据建
``MediaAsset`` 行，不下载视频字节）。因此这里的时间区间与流 ID 全部来自
可控的**合成 fixture**（``excerpt_start_seconds`` / ``camera`` 由测试显式给出），
不是真实设备数据。标注为 ``SYNTHETIC_FIXTURE`` 的用例即为此类：
它们验证"证据结构、换算与契约"正确，**不**证明真实数据上的定位精度。

数值越界与时间同步两类证据的判定逻辑本身是既有的真实实现（
``evaluate_number_label_within_range`` / ``analyze_stream_sync``）；本批次新增的
是"把结论里的定位暴露出来"，所以这两类可以在真实规则实现上验证，只是输入
区间是合成的。
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.datasources import (
    DataSourceImportModel,
    DataSourceModel,
    MediaAssetModel,
)

from tests.api.conftest import make_client
from tests.api.dataset_support import url

pytestmark = pytest.mark.usefixtures("api_db")

_SHA = "sha256:" + "a" * 64
_REVISION = "a" * 40


def _seed_import(factory, ids) -> tuple[UUID, UUID]:
    source_id, import_id = uuid4(), uuid4()
    with factory() as session:
        session.add(
            DataSourceModel(
                id=source_id,
                project_id=ids.project_a,
                code=f"hf-{source_id.hex[:8]}",
                name="源",
                kind="HUGGINGFACE",
                repo_id="nvidia/PhysicalAI-Human-1M-100K",
                revision=_REVISION,
                license="CC-BY-4.0",
                access_condition="PUBLIC",
                is_gated=False,
                metadata_json={},
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.flush()
        session.add(
            DataSourceImportModel(
                id=import_id,
                project_id=ids.project_a,
                data_source_id=source_id,
                status="SUCCEEDED",
                scope="SAMPLE",
                tool_name="huggingface_hub",
                tool_version="0.27.1",
                sample_limit=15,
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.commit()
    return source_id, import_id


def _seed_asset(
    factory,
    ids,
    source_id: UUID,
    import_id: UUID,
    *,
    episode_key: str,
    camera: str,
    start_seconds: float,
    length_seconds: float,
    size_bytes: int = 1024,
) -> UUID:
    asset_id = uuid4()
    with factory() as session:
        session.add(
            MediaAssetModel(
                id=asset_id,
                project_id=ids.project_a,
                data_source_id=source_id,
                import_id=import_id,
                episode_key=episode_key,
                path=f"videos/{episode_key}-{camera}.mp4",
                camera=camera,
                object_key=f"data-sources/{ids.project_a}/{asset_id}/v.mp4",
                size_bytes=size_bytes,
                sha256=_SHA,
                content_type="video/mp4",
                task_name="demo",
                excerpt_start_seconds=start_seconds,
                excerpt_seconds=length_seconds,
                source_fps=30.0,
                created_by=ids.editor_a,
                updated_by=ids.editor_a,
            )
        )
        session.commit()
    return asset_id


def _create(client, project_id, subject_type: str, subject_id) -> dict[str, object]:
    response = client.post(
        url(project_id, "/quality-runs"),
        json={"subject_type": subject_type, "subject_id": str(subject_id)},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _findings(client, project_id, run_id: str) -> dict[str, dict[str, object]]:
    response = client.get(url(project_id, f"/quality-runs/{run_id}/findings"))
    assert response.status_code == 200, response.text
    return {item["rule_id"]: item for item in response.json()["items"]}


# ---------------------------------------------------------------------------
# 证据一：媒体时间区间（SYNTHETIC_FIXTURE）
# ---------------------------------------------------------------------------


def test_media_time_range_evidence_locates_camera_and_interval(api_db) -> None:
    """SYNTHETIC_FIXTURE：单条媒体资产的机位与片段区间成为证据定位。

    合成数据说明：``excerpt_start_seconds=12.5`` / ``excerpt_seconds=4.0``
    由本用例显式给出，不是真实视频元数据。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    source_id, import_id = _seed_import(factory, ids)
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-evidence",
        camera="wrist",
        start_seconds=12.5,
        length_seconds=4.0,
    )

    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    findings = _findings(editor, ids.project_a, run["run_id"])

    assets = findings["assets_present"]
    location = assets["evidence_location"]
    assert location is not None
    # 机位即媒体流；秒 → 纳秒必须精确（12.5s = 12_500_000_000ns）。
    assert location["stream_id"] == "wrist"
    assert location["time_range"] == {
        "start_time_ns": "12500000000",
        "end_time_ns": "16500000000",
    }
    # 定位是纳秒字符串：不是数字，避免前端 JSON 解析丢精度。
    assert isinstance(location["time_range"]["start_time_ns"], str)


def test_media_time_range_clipped_to_episode_is_not_invented(api_db) -> None:
    """SYNTHETIC_FIXTURE：无法给出可信区间时，service 不编造时间。

    资产声明 ``excerpt_seconds<=0`` 时时间字段本身就是坏的（DB 只约束起点
    非负）：这类结论必须没有 ``time_range``，而不是拿 0 或负值当区间。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    source_id, import_id = _seed_import(factory, ids)
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-bad-timing",
        camera="main",
        # 起点非负但时长为 0：DB 允许，语义上是"坏时序字段"（长度必须为正）。
        start_seconds=0.0,
        length_seconds=0.0,
    )

    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    findings = _findings(editor, ids.project_a, run["run_id"])

    timing = findings["timing_fields_valid"]
    assert timing["status"] == "FAIL"
    location = timing["evidence_location"]
    assert location is not None
    # 坏区间不伪造：只有流与字段定位。
    assert location["time_range"] is None
    assert location["stream_id"] == "main"
    assert location["field_path"] == "excerpt_start_seconds"


# ---------------------------------------------------------------------------
# 证据二：数值流越界
# ---------------------------------------------------------------------------

# 数值越界的证据结构在 `tests/unit/test_annotation_quality_rules.py` 的
# `number_label_within_range` 用例里断言（规则是纯函数，无需经 HTTP），
# 这里只验证"规则产出的定位能被统一引擎提升并落库/读回"。
# 见 `test_annotation_source_number_violation_carries_location`。

# ---------------------------------------------------------------------------
# 证据三：时间同步超差（SYNTHETIC_FIXTURE）
# ---------------------------------------------------------------------------


def test_sync_drift_evidence_has_common_window_and_stream(api_db) -> None:
    """SYNTHETIC_FIXTURE：时间同步超差 → 共同可见窗口 + 偏移最大的流。

    两路流的起点相差 100ms（远超 40ms 容忍阈值）。合成数据：``excerpt_*``
    由用例显式给出，不是真实多机位录制数据。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    source_id, import_id = _seed_import(factory, ids)
    # main 从 0s 开始，wrist 从 0.1s 开始 → 起点偏移 100ms > 40ms 阈值。
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-sync",
        camera="main",
        start_seconds=0.0,
        length_seconds=10.0,
    )
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-sync",
        camera="wrist",
        start_seconds=0.1,
        length_seconds=10.0,
    )

    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    findings = _findings(editor, ids.project_a, run["run_id"])

    sync = findings["stream_sync_aligned"]
    assert sync["status"] == "FAIL"
    location = sync["evidence_location"]
    assert location is not None
    # 共同可见窗口：最晚的 start（0.1s）到最早的 end（10.0s）。
    assert location["time_range"] == {
        "start_time_ns": "100000000",
        "end_time_ns": "10000000000",
    }
    # 偏移最大的流就是要先看的那一路。
    assert location["stream_id"] == "wrist"
    assert location["field_path"] == "streams"
    # 阈值仍在证据里（定位之外的解释性事实）。
    assert sync["evidence"]["tolerance_ns"] == 40_000_000


def test_sync_aligned_evidence_has_no_location(api_db) -> None:
    """同步通过时不给证据定位：通过项不需要人去时间轴上核对。

    这不是"定位失败"，而是"没有需要定位的问题"——界面据此不显示跳转入口。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    source_id, import_id = _seed_import(factory, ids)
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-sync-ok",
        camera="main",
        start_seconds=0.0,
        length_seconds=10.0,
    )
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-sync-ok",
        camera="wrist",
        start_seconds=0.0,
        length_seconds=10.0,
    )

    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    findings = _findings(editor, ids.project_a, run["run_id"])

    sync = findings["stream_sync_aligned"]
    assert sync["status"] == "PASS"
    assert sync["evidence_location"] is None


# ---------------------------------------------------------------------------
# 契约形状
# ---------------------------------------------------------------------------


def test_findings_without_location_serialize_as_null(api_db) -> None:
    """没有定位的结论必须返回 ``null``，不能返回三个空字段的对象。

    三字段全空的对象会被前端读成"有定位但没填"，进而渲染空区间。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    source_id, import_id = _seed_import(factory, ids)
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-null-loc",
        camera="main",
        start_seconds=0.0,
        length_seconds=10.0,
    )

    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    findings = _findings(editor, ids.project_a, run["run_id"])

    # asset_size_positive / asset_sha256_valid 没有可定位的时间/流/字段。
    for rule_id in ("asset_size_positive", "asset_sha256_valid"):
        assert findings[rule_id]["evidence_location"] is None


def test_evidence_location_survives_persistence_roundtrip(api_db) -> None:
    """定位落在独立列上：重跑结论从库里读回后与写入时逐字段一致。

    这条覆盖的是"结构化列而非自由 JSON"：若定位只存在 ``evidence`` 里，
    任何一次读取路径改写都可能把它丢掉。
    """
    factory, ids = api_db
    editor = make_client(factory, ids.editor_a)
    source_id, import_id = _seed_import(factory, ids)
    _seed_asset(
        factory,
        ids,
        source_id,
        import_id,
        episode_key="ep-roundtrip",
        camera="wrist",
        start_seconds=2.0,
        length_seconds=3.0,
    )

    run = _create(editor, ids.project_a, "DATASOURCE_IMPORT", import_id)
    first = _findings(editor, ids.project_a, run["run_id"])["assets_present"]
    # 重新读一次详情接口：走的是"落库 → 仓储 → 响应模型"完整路径。
    second = _findings(editor, ids.project_a, run["run_id"])["assets_present"]

    assert first["evidence_location"] == second["evidence_location"]
    assert first["evidence_location"]["time_range"] == {
        "start_time_ns": "2000000000",
        "end_time_ns": "5000000000",
    }
