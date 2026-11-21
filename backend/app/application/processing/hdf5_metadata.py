"""HDF5 完整结构到 Processing Stage 有界摘要的投影。"""

from __future__ import annotations

import json

from ddp_data.hdf5 import Hdf5Structure

#: 每类最多保留的结构明细；权威总数和省略数量始终保留。
MAX_HDF5_DETAILS_PER_KIND = 256
#: 给 Stage 外层文档、source_ref 等字段预留空间，低于 256 KiB 产物总预算。
MAX_HDF5_PAYLOAD_BYTES = 192 * 1024


def hdf5_stage_payload(structure: Hdf5Structure) -> dict[str, object]:
    """投影 HDF5 结构，不读取值，并显式报告被省略的明细。"""
    complete = structure.as_metadata()
    datasets = _bounded_items(complete, "datasets", base={})
    groups = _bounded_items(complete, "groups", base={"datasets": datasets})
    links = _bounded_items(
        complete,
        "links",
        base={"datasets": datasets, "groups": groups},
    )
    payload: dict[str, object] = {
        "adapter_id": structure.adapter_id,
        "adapter_version": structure.adapter_version,
        "dataset_count": structure.dataset_count,
        "group_count": structure.group_count,
        "attribute_count": structure.attribute_count,
        "link_count": len(structure.links),
        "truncated": structure.truncated,
        "datasets": datasets,
        "groups": groups,
        "links": links,
        "omitted_dataset_count": structure.dataset_count - len(datasets),
        "omitted_group_count": structure.group_count - len(groups),
        "omitted_link_count": len(structure.links) - len(links),
    }
    payload["details_truncated"] = any(
        payload[key]
        for key in (
            "omitted_dataset_count",
            "omitted_group_count",
            "omitted_link_count",
        )
    )
    return payload


def _bounded_items(
    complete: dict[str, object],
    key: str,
    *,
    base: dict[str, list[object]],
) -> list[object]:
    source = complete[key]
    if not isinstance(source, list):
        raise TypeError(f"HDF5 metadata {key} 必须是列表")
    selected: list[object] = []
    for item in source[:MAX_HDF5_DETAILS_PER_KIND]:
        selected.append(item)
        candidate = {**base, key: selected}
        if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > MAX_HDF5_PAYLOAD_BYTES:
            selected.pop()
            break
    return selected


__all__ = [
    "MAX_HDF5_DETAILS_PER_KIND",
    "MAX_HDF5_PAYLOAD_BYTES",
    "hdf5_stage_payload",
]
