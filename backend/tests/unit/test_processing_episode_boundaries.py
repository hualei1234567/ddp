"""边界推导（V31-018）：只承认输入本身携带的明确边界。

覆盖四种真实情形：

1. 单文件 MP4 有 duration → 边界 ``[0, duration)``；
2. MP4 缺 duration → ``duration_missing``，不按 0 边界伪造；
3. MCAP / ROS Bag / HDF5 等容器 → 明确记入 skipped，不整段当成一个 Episode；
4. canonical manifest 等非 RawAsset key → 不参与推导（它已关联 Episode）。
"""

from __future__ import annotations

from uuid import UUID

from app.application.processing.episode_boundaries import (
    BoundaryPlan,
    plan_episode_boundaries,
)

PROJECT_ID = UUID("00000000-0000-7000-8000-000000000401")
OTHER_PROJECT_ID = UUID("00000000-0000-7000-8000-000000000402")
ASSET_ID = UUID("00000000-0000-7000-8000-000000000403")
EPISODE_ID = UUID("00000000-0000-7000-8000-000000000404")

RAW_KEY = f"raw/{PROJECT_ID}/2026/11/08/{ASSET_ID}/content.mp4"
MCAP_KEY = f"raw/{PROJECT_ID}/2026/11/08/{ASSET_ID}/content.mcap"
FOREIGN_KEY = f"raw/{OTHER_PROJECT_ID}/2026/11/08/{ASSET_ID}/content.mp4"
MANIFEST_KEY = f"canonical/{PROJECT_ID}/{EPISODE_ID}/1/manifest.json"


def _mp4_document(*, duration_ms: object = 5000, source_ref: str = RAW_KEY) -> dict[str, object]:
    return {
        "schema_version": "ddp.processing.metadata/1.0.0",
        "source_ref": source_ref,
        "format": "MP4",
        "metadata": {"duration_ms": duration_ms, "fps": 30.0},
    }


def _plan(
    *input_refs: str,
    documents: dict[str, dict[str, object]] | None = None,
) -> BoundaryPlan:
    return plan_episode_boundaries(
        project_id=PROJECT_ID,
        input_refs=input_refs,
        metadata_documents=documents or {},
    )


def test_single_file_mp4_yields_whole_file_boundary() -> None:
    plan = _plan(RAW_KEY, documents={RAW_KEY: _mp4_document()})

    assert len(plan.boundaries) == 1
    boundary = plan.boundaries[0]
    assert boundary.raw_object_key == RAW_KEY
    assert boundary.start_time_ns == 0
    assert boundary.end_time_ns == 5_000_000_000
    assert boundary.duration_ns == 5_000_000_000
    assert plan.skipped == ()


def test_mp4_without_duration_is_skipped_not_fabricated() -> None:
    for invalid in (None, 0, -1, True, "5000"):
        plan = _plan(
            RAW_KEY,
            documents={RAW_KEY: _mp4_document(duration_ms=invalid)},
        )
        assert plan.boundaries == (), invalid
        assert [item.reason for item in plan.skipped] == ["duration_missing"], invalid


def test_multi_episode_container_is_skipped_with_reason() -> None:
    documents = {
        MCAP_KEY: {
            "schema_version": "ddp.processing.metadata/1.0.0",
            "source_ref": MCAP_KEY,
            "format": "MCAP",
            "metadata": {"message_count": 10},
        }
    }
    plan = _plan(MCAP_KEY, documents=documents)

    assert plan.boundaries == ()
    assert len(plan.skipped) == 1
    assert plan.skipped[0].raw_object_key == MCAP_KEY
    assert "多 Episode 容器" in plan.skipped[0].reason


def test_missing_metadata_document_is_reported_not_guessed() -> None:
    plan = _plan(RAW_KEY)

    assert plan.boundaries == ()
    assert [item.reason for item in plan.skipped] == ["metadata_artifact_missing"]


def test_non_raw_asset_refs_and_foreign_keys_are_not_processed() -> None:
    plan = _plan(
        MANIFEST_KEY,
        FOREIGN_KEY,
        f"raw/{PROJECT_ID}/capture.mcap",
        documents={
            FOREIGN_KEY: _mp4_document(source_ref=FOREIGN_KEY),
            MANIFEST_KEY: _mp4_document(source_ref=MANIFEST_KEY),
        },
    )

    # canonical manifest 已关联 Episode；跨项目 key 不属于本次输入；
    # 不符合 RawAsset key 结构的串不猜格式。
    assert plan.boundaries == ()
    assert plan.skipped == ()


def test_duplicate_refs_are_processed_once_in_input_order() -> None:
    second_asset = UUID("00000000-0000-7000-8000-000000000405")
    second_key = f"raw/{PROJECT_ID}/2026/11/08/{second_asset}/content.mp4"
    plan = _plan(
        RAW_KEY,
        second_key,
        RAW_KEY,
        documents={
            RAW_KEY: _mp4_document(),
            second_key: _mp4_document(duration_ms=1000, source_ref=second_key),
        },
    )

    assert [item.raw_object_key for item in plan.boundaries] == [RAW_KEY, second_key]
    assert [item.end_time_ns for item in plan.boundaries] == [5_000_000_000, 1_000_000_000]
