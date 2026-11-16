"""仓库内置 Adapter 的注册。

每个 Adapter 都是**已有解析器的声明式外壳**，不重复实现解析：

- ``lerobot.human1m_preview`` → ``lerobot.parse_preview_metadata``（已用于
  真实 HF 导入，10 episode / 30 资产验收通过）；
- ``generic.jsonl`` → 通用 JSONL 行 → Canonical，作为"第二种来源"证明
  两个不同格式能映射到同一 Canonical Schema（N2 完成定义之一）。

**如实标注缺失字段**：Human-1M 的 preview 元数据没有动作、位姿、时间同步
与标定信息。这些必须出现在 ``missing_fields`` 里；把它们说成"支持"会让
下游以为拿到了数据，而实际是空的——蓝图 §5.4 明令禁止这种伪装。
"""

from __future__ import annotations

import json
from collections.abc import Mapping

from ddp_data.adapters.registry import (
    CANONICAL_EPISODE_FIELDS,
    AdapterRegistry,
    AdapterSpec,
)
from ddp_data.lerobot.parser import (
    CanonicalEpisode,
    CanonicalMediaAsset,
    parse_preview_metadata,
)

_ALL_FIELDS = frozenset(item.name for item in CANONICAL_EPISODE_FIELDS)

# Human-1M preview 实际输出的字段。
_HUMAN1M_SUPPORTED = frozenset(
    {
        "episode_key",
        "task_name",
        "instruction",
        "media_assets",
        "environment",
        "setting",
        "episode_seconds",
        "source_fps",
        "has_wrist",
        "raw",
    }
)


class GenericJsonlParseError(Exception):
    """通用 JSONL 不符合约定结构。"""


def parse_generic_jsonl(data: bytes) -> tuple[CanonicalEpisode, ...]:
    """通用 JSONL → Canonical Episode。

    约定结构（每行一个 Episode 对象）::

        {"episode_key": "...", "task_name": "...", "instruction": "...",
         "media_assets": [{"path": "...", "camera": "main"}],
         "episode_seconds": 12.0, "environment": "...", "setting": "..."}

    这是**宽松的最小契约**：只要求 Episode 级标识与至少一个媒体资产。
    缺失的可选字段留空而不补默认值——「没有」必须能被下游看见。
    """
    episodes: list[CanonicalEpisode] = []
    seen: set[str] = set()
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenericJsonlParseError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
        if not isinstance(row, Mapping):
            raise GenericJsonlParseError(f"第 {lineno} 行必须是 JSON 对象")
        key = row.get("episode_key")
        if not isinstance(key, str) or not key.strip():
            raise GenericJsonlParseError(f"第 {lineno} 行缺少 episode_key")
        if key in seen:
            raise GenericJsonlParseError(f"episode_key 重复：{key}")
        seen.add(key)
        assets_raw = row.get("media_assets")
        if not isinstance(assets_raw, list) or not assets_raw:
            raise GenericJsonlParseError(f"{key} 缺少 media_assets")
        assets = tuple(_asset_of(key, item) for item in assets_raw)
        episodes.append(
            CanonicalEpisode(
                episode_key=key.strip(),
                task_name=_text(row.get("task_name"), f"{key}.task_name"),
                instruction=_text(row.get("instruction"), f"{key}.instruction"),
                environment=_optional_text(row.get("environment")),
                setting=_optional_text(row.get("setting")),
                episode_seconds=_optional_number(
                    row.get("episode_seconds"), "episode_seconds"
                ),
                source_fps=_optional_number(row.get("source_fps"), "source_fps"),
                has_wrist=bool(row.get("has_wrist", False)),
                media_assets=assets,
                raw=dict(row),
            )
        )
    if not episodes:
        raise GenericJsonlParseError("JSONL 不含任何 Episode")
    return tuple(episodes)


def _asset_of(episode_key: str, item: object) -> CanonicalMediaAsset:
    if not isinstance(item, Mapping):
        raise GenericJsonlParseError(f"{episode_key} 的 media_assets 元素必须是对象")
    path = item.get("path")
    if not isinstance(path, str) or not path.strip():
        raise GenericJsonlParseError(f"{episode_key} 的媒体资产缺少 path")
    camera = item.get("camera")
    return CanonicalMediaAsset(
        path=path.strip(),
        camera=(
            camera.strip() if isinstance(camera, str) and camera.strip() else "main"
        ),
        view_raw=str(item.get("view_raw") or ""),
        modality=str(item.get("modality") or "unknown"),
        excerpt_start_seconds=_optional_number(
            item.get("excerpt_start_seconds"), "start"
        ),
        excerpt_seconds=_optional_number(item.get("excerpt_seconds"), "seconds"),
        source_fps=_optional_number(item.get("source_fps"), "fps"),
    )


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GenericJsonlParseError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GenericJsonlParseError("可选文本字段必须是字符串或 null")
    return value.strip() or None


def _optional_number(value: object, field_name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise GenericJsonlParseError(f"{field_name} 必须是数字或 null")
    return float(value)


def parse_jsonl_rows(data: bytes) -> list[dict[str, object]]:
    """把 JSONL 字节解码为对象列表；空行跳过，非对象行显式失败。

    ``lerobot.parse_preview_metadata`` 的输入是**已解析的行列表**而非字节——
    它只负责映射，不负责 IO/解码。Adapter 的 Parser 契约是 bytes（数据从哪来
    与怎么解析分开），因此这一层解码必须有个明确归属。放在这里而不是各调用方
    各写一份：此前 worker 里就有一份 ``_parse_metadata``，换个入口就得再写。
    """
    rows: list[dict[str, object]] = []
    for lineno, line in enumerate(data.decode("utf-8").splitlines(), start=1):
        text = line.strip()
        if not text:
            continue
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GenericJsonlParseError(f"第 {lineno} 行不是合法 JSON：{exc}") from exc
        if not isinstance(value, dict):
            raise GenericJsonlParseError(f"第 {lineno} 行必须是 JSON 对象")
        rows.append(value)
    if not rows:
        raise GenericJsonlParseError("JSONL 不含任何记录")
    return rows


def parse_human1m_preview(data: bytes) -> tuple[CanonicalEpisode, ...]:
    """Human-1M preview 元数据（bytes）→ Canonical Episode。

    解码与映射分两步：``parse_jsonl_rows`` 负责前者，``parse_preview_metadata``
    负责后者。两个函数各自独立可测，组合起来满足 Adapter 的 bytes 契约。
    """
    return parse_preview_metadata(parse_jsonl_rows(data))


def _looks_like_metadata_jsonl(sample: bytes) -> bool:
    """Human-1M 特征：首行含 ``example_id`` 与 ``file_name``。"""
    try:
        first = sample.decode("utf-8", errors="ignore").splitlines()[0]
        row = json.loads(first)
    except (IndexError, json.JSONDecodeError):
        return False
    return isinstance(row, dict) and "example_id" in row and "file_name" in row


def _looks_like_generic_episode_jsonl(sample: bytes) -> bool:
    try:
        first = sample.decode("utf-8", errors="ignore").splitlines()[0]
        row = json.loads(first)
    except (IndexError, json.JSONDecodeError):
        return False
    return isinstance(row, dict) and "episode_key" in row and "media_assets" in row


# Human-1M preview 在 **Canonical Episode 结构字段**上没有缺失：它填满
# episode_key / task_name / instruction / media_assets 等 10 个字段。
#
# 但它在 **Canonical 语义内容**上有真实缺失——动作序列、机器人位姿、时间同步、
# 标定、深度都不在元数据里。这些不是 Episode 的字段，而是 Canonical Schema
# 更下层（Frame / Observation / Action）的内容，N2 尚未定义那些实体，
# 因此用 ``metadata`` 如实登记，而不是塞进 missing_fields 混淆两个层级。
_HUMAN1M_MISSING: frozenset[str] = frozenset()
_HUMAN1M_SEMANTIC_GAPS = (
    "action_sequence",
    "robot_pose",
    "time_sync",
    "calibration",
    "depth",
)

# 通用 JSONL 能表达全部 Canonical Episode 字段（可选字段缺省为 None）。
_GENERIC_MISSING: frozenset[str] = frozenset()


def register_builtin_adapters(registry: AdapterRegistry) -> AdapterRegistry:
    """把仓库内置 Adapter 注册进给定注册表；返回同一实例便于链式调用。"""
    registry.register(
        AdapterSpec(
            adapter_id="lerobot.human1m_preview",
            version="1.0.0",
            format_name="LeRobot preview metadata.jsonl",
            parser=parse_human1m_preview,
            supported_fields=_HUMAN1M_SUPPORTED,
            missing_fields=_HUMAN1M_MISSING,
            description=(
                "NVIDIA Human-1M preview 平铺元数据：一行一个机位视频，"
                "按 example_id 聚合成 Episode。已用于真实导入验收。"
            ),
            detect=_looks_like_metadata_jsonl,
            metadata={
                "source": "huggingface",
                "aggregation": "by_example_id",
                # 语义内容缺口（不是 Episode 结构字段）：如实登记，便于下游
                # 判断"这批数据没有动作/位姿"，而不是以为拿到了。
                "semantic_gaps": _HUMAN1M_SEMANTIC_GAPS,
            },
        )
    )
    registry.register(
        AdapterSpec(
            adapter_id="generic.episode_jsonl",
            version="1.0.0",
            format_name="DDP generic episode JSONL",
            parser=parse_generic_jsonl,
            supported_fields=_ALL_FIELDS,
            missing_fields=_GENERIC_MISSING,
            description="通用一行一 Episode 的 JSONL；用于非特定数据集的接入。",
            detect=_looks_like_generic_episode_jsonl,
            metadata={"source": "generic"},
        )
    )
    return registry


def build_default_registry() -> AdapterRegistry:
    """构造带内置 Adapter 的注册表（每次新建实例，测试互不影响）。"""
    return register_builtin_adapters(AdapterRegistry())


__all__ = [
    "GenericJsonlParseError",
    "build_default_registry",
    "parse_generic_jsonl",
    "parse_human1m_preview",
    "parse_jsonl_rows",
    "register_builtin_adapters",
]
