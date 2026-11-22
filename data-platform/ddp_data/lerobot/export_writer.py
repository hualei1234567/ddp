"""LeRobot 导出写入器（V31-042 切片 2）：纯函数组装 LeRobot 必需产物。

**职责边界**

backend 的 ``exporting.py`` 产出 DDP 自有训练表（NDJSON）与 ``ddp/sidecar.json``；
本模块在其之上**新增** LeRobot 数据集必需的文件，使产物满足 v2.1 结构合规——
能被 ``LeRobotDataset(repo_id, root=...)`` 识别并读出 episode 结构。放这里
而不放 backend，是因为 parquet 字节由 pyarrow 生成，而该依赖只存在于
data-platform（backend 禁止引入 pyarrow，见方案 §5）。

**「结构合规」的诚实边界（与 loadable_scope 契约一致，见切片 1）**

- ``info.json`` 补齐 LeRobot v2.1 必需键：``codebase_version`` / ``fps`` /
  ``features`` / ``data_path`` / ``video_path`` / ``chunks_size`` /
  ``total_episodes`` / ``total_tasks`` / ``total_frames`` 等；
- ``features`` 只声明 episode 级列（``episode_index`` / ``task_index`` /
  ``length`` / ``split``），显式不含 ``observation.*`` 与 ``action``——DDP
  没有逐帧存储，绝不虚构逐帧特征；
- ``meta/tasks.jsonl`` 每行一个任务（当前只有占位任务，task_index=0）；
- ``meta/stats.json`` 给出 episode 级列的统计（LeRobot 要求该文件存在）；
- ``data/chunk-000/episode_*.parquet``：每 Episode 一个 parquet，只含
  episode 级列，无逐帧行——parquet 行数 = 1（元数据行），不是视频帧数；
- ``videos/`` 不产出：媒体在对象存储，未按 LeRobot 布局落盘，
  ``info.json`` 的 ``video_path`` 指向空布局并不宣称视频可用。

**确定性**

同输入两次产出必须字节一致：pyarrow 写 parquet 时显式
``store_schema=False`` 并固定版本参数；JSON/NDJSON 沿用 backend 的
排序键风格（本模块自带实现，避免依赖 backend 代码——data-platform 不导入
backend）。
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

#: LeRobot 写入侧对齐的版本。解析侧（v3_time_manifest.py）按 v3 语义读，
#: 但**写入侧选 v2.1**：方案 §6 明确 v2.0/v2.1/v3 布局差异明显，v2.1 是
#: 「元数据可加载」目标里 Loader 兼容面最广、对无视频产物最友好的版本；
#: v3 要求 per-episode parquet 与视频布局，逐帧/视频链路未落地前会立即
#: 在 Loader 侧失败。版本固定在此，随 ADR 演进，不得漂移。
LEROBOT_CODEBASE_VERSION = "v2.1"

#: LeRobot 每个数据块包含的 Episode 数。产物按 chunk 分目录与官方布局一致；
#: 当前导出规模小，固定 1000（LeRobot 默认值），不暴露为配置。
EPISODES_PER_CHUNK = 1000

#: episode 级 parquet 的列 schema（顺序固定，保证字节确定）。
#: ``length`` 是该 Episode 的声明帧数（来自元数据），不是 parquet 行数。
EPISODE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("episode_index", "int64"),
    ("task_index", "int64"),
    ("length", "int64"),
    ("split", "string"),
)

JSON_CONTENT_TYPE = "application/json"
NDJSON_CONTENT_TYPE = "application/x-ndjson"
PARQUET_CONTENT_TYPE = "application/x-parquet"

TASK_PLACEHOLDER = "ddp.metadata-export/1.0.0"


class WriterInput(NamedTuple):
    """写入器的全部输入；字段顺序即构造顺序，禁止无默认值放后面。"""

    episodes: tuple[Mapping[str, object], ...]
    dataset_version_id: str
    created_at_iso: str


@dataclass(frozen=True, slots=True)
class WrittenArtifact:
    """与 backend ``ExportArtifactSpec`` 字段同形，避免 data-platform 反向依赖。"""

    path: str
    data: bytes
    content_type: str


def _json_bytes(payload: object) -> bytes:
    """确定性 JSON 字节：键排序、紧凑分隔符、ensure_ascii=False、结尾换行。"""
    return (
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _jsonl_bytes(rows: Iterable[Mapping[str, object]]) -> bytes:
    """确定性 NDJSON 字节：逐行 dumps（行内键排序），行序由调用方保证。"""
    lines = [
        json.dumps(dict(row), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for row in rows
    ]
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")


def _episode_sort_key(row: Mapping[str, object]) -> tuple[str, str]:
    """排序键：episode_id 稳定字符串。同一输入永远得到同一顺序。"""
    return (str(row.get("episode_id", "")), str(row.get("episode_version", "")))


def _parquet_bytes(rows: Sequence[Mapping[str, object]]) -> bytes:
    """把 episode 级行写成确定性 parquet 字节。

    列序与类型来自 ``EPISODE_COLUMNS``；写参数固定（无时间戳、无随机种子、
    ``store_schema=False``），保证同输入字节一致。
    """
    import pyarrow as pa
    import pyarrow.parquet as pq

    arrays = []
    for column, column_type in EPISODE_COLUMNS:
        values = [row.get(column) for row in rows]
        if column_type == "int64":
            arrays.append(pa.array(values, type=pa.int64()))
        else:
            arrays.append(pa.array([str(v) for v in values], type=pa.string()))
    table = pa.table(
        dict(zip((c for c, _ in EPISODE_COLUMNS), arrays, strict=True)),
        schema=pa.schema(
            [
                pa.field(column, pa.int64() if column_type == "int64" else pa.string())
                for column, column_type in EPISODE_COLUMNS
            ]
        ),
    )
    sink = io.BytesIO()
    pq.write_table(
        table,
        sink,
        compression="snappy",
        version="2.6",
        store_schema=False,
        write_statistics=True,
        data_page_size=1 << 20,
    )
    return sink.getvalue()


def _build_tasks(episodes: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    """meta/tasks.jsonl：task_index → task 文本。当前只有占位任务。"""
    task_names = sorted(
        {str(row.get("task", TASK_PLACEHOLDER)) for row in episodes}
    ) or [TASK_PLACEHOLDER]
    return [
        {"task_index": index, "task": name} for index, name in enumerate(task_names)
    ]


def _build_stats(
    episodes: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    """meta/stats.json：episode 级列的最小统计。

    LeRobot 要求该文件存在；逐帧统计在无逐帧数据时不存在，只给 episode 级。
    数值全部取整/取固定精度，保证字节确定。
    """
    lengths = [int(row.get("length", 0) or 0) for row in episodes]
    total_frames = sum(lengths)
    n = len(lengths)
    mean = round(total_frames / n, 6) if n else 0.0
    return {
        "episode_index": {
            "min": [0],
            "max": [
                max(
                    (int(row.get("episode_index", 0) or 0) for row in episodes),
                    default=0,
                )
            ],
        },
        "length": {
            "min": [min(lengths) if lengths else 0],
            "max": [max(lengths) if lengths else 0],
            "mean": [mean],
            "count": [float(n)],
            "total": [float(total_frames)],
        },
        "per_split": {
            split: {
                "episodes": count,
                "frames": sum(
                    int(row.get("length", 0) or 0)
                    for row in episodes
                    if str(row.get("split")) == split
                ),
            }
            for split, count in sorted(_split_counts(episodes).items())
        },
    }


def _split_counts(episodes: Sequence[Mapping[str, object]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in episodes:
        split = str(row.get("split", "train"))
        counts[split] = counts.get(split, 0) + 1
    return counts


def write_lerobot_metadata(
    *,
    episodes: Sequence[Mapping[str, object]],
    dataset_version_id: str,
    created_at_iso: str,
) -> tuple[WrittenArtifact, ...]:
    """产出 LeRobot 元数据层必需文件（不含 videos/）。

    ``episodes`` 行至少需要：``episode_id``、``episode_index``、``task``、
    ``length``、``split``。行序无关——内部统一按 ``(episode_id, episode_version)``
    排序，同一集合两次调用产出字节完全一致。

    返回的产物路径即 LeRobot 数据集根下的相对路径；调用方（backend）负责
    与其自有产物合并、写入对象存储并回读校验。
    """
    if not episodes:
        raise ValueError("LeRobot 元数据导出不得为空：episodes 不能为空集合")
    ordered = sorted((dict(row) for row in episodes), key=_episode_sort_key)
    # episode_index：优先沿用上游显式声明的序号（LeRobot 语义里它是数据集级
    # Episode 序号，决定 chunk 归属）；未提供时按排序位置连续化。混合提供与
    # 不提供是调用方错误——序号必须来自同一来源，这里只做确定性兜底。
    for position, row in enumerate(ordered):
        explicit = row.get("episode_index")
        row["episode_index"] = int(explicit) if explicit is not None else position
    missing_chunk = [row for row in ordered if int(row["episode_index"]) < 0]
    if missing_chunk:
        raise ValueError("episode_index 不得为负")

    tasks = _build_tasks(ordered)
    stats = _build_stats(ordered)

    artifacts: list[WrittenArtifact] = [
        # 注意：不产出 meta/info.json —— backend 的 exporting.py 已有承载
        # DDP 自有键的 info.json（权威单一事实源）；LeRobot 必需键由 backend
        # 侧合并进该文件（见 backend exporting.py），避免同名双条目。
        WrittenArtifact(
            path="meta/tasks.jsonl",
            data=_jsonl_bytes(tasks),
            content_type=NDJSON_CONTENT_TYPE,
        ),
        WrittenArtifact(
            path="meta/stats.json",
            data=_json_bytes(stats),
            content_type=JSON_CONTENT_TYPE,
        ),
    ]

    # 每个 Episode 一个 parquet（v2.1 布局），内容只有该 Episode 的元数据行。
    for row in ordered:
        chunk = int(row["episode_index"]) // EPISODES_PER_CHUNK
        path = f"data/chunk-{chunk:03d}/episode_{int(row['episode_index']):06d}.parquet"
        artifacts.append(
            WrittenArtifact(
                path=path,
                data=_parquet_bytes([row]),
                content_type=PARQUET_CONTENT_TYPE,
            )
        )
    return tuple(artifacts)


def required_lerobot_paths(episodes: Sequence[Mapping[str, object]]) -> set[str]:
    """LeRobot 结构合规必需的路径集合（供 Worker 端到端变异验证使用）。"""
    return {"meta/info.json", "meta/tasks.jsonl", "meta/stats.json"}


def artifact_sha256(data: bytes) -> str:
    """sha256 前缀格式与 backend 契约一致（``sha256:<64hex>``）。"""
    return "sha256:" + hashlib.sha256(data).hexdigest()
