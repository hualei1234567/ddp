"""Canonical Adapter 注册、发现与字段映射声明。

**为什么需要这一层（N2 的核心）**

`docs/roadmap/next-stage-v2/建设文档/03` §5 要求每个 Adapter 能声明
「支持哪些 Canonical 字段、缺哪些」，并保留来源、版本与原始字段。此前仓库里
只有两个绑定具体数据集的解析函数（`lerobot.parse_preview_metadata`、
`huggingface` 的元信息读取），没有：

- 统一的发现机制——新增格式只能改调用方；
- 字段映射声明——无法回答"这个来源映射了哪些字段、缺了哪些"；
- 版本与偏好——同一格式多个实现时无法择优。

本模块只做**注册与发现**，不碰解析逻辑：解析器仍是 `lerobot` /
未来的 Generic Video 等具体模块，Adapter 是它们的声明式外壳。

设计约束（与 CLAUDE.md 一致）：

- 不引入第二套 Canonical 类型——直接复用 `lerobot.CanonicalEpisode`；
- 未知格式显式失败，不返回 None 让调用方当成"没有数据"；
- 注册表是纯数据 + 纯函数，无 IO、无全局可变状态被外部随意改写。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ddp_data.lerobot.parser import CanonicalEpisode

# 解析器签名：输入原始字节，输出 Canonical Episode 序列。
# 只接受 bytes 而不是文件路径：Adapter 不应关心数据从哪来（HF / 本地 / S3），
# 也不该在解析阶段做 IO——那会把"能不能读文件"和"能不能解析"混在一起。
Parser = Callable[[bytes], tuple[CanonicalEpisode, ...]]


class AdapterNotFoundError(Exception):
    """请求的格式没有已注册的 Adapter。显式失败，不静默返回空。"""


class AdapterRegistrationError(Exception):
    """注册参数非法（重复 ID、空字段声明等）。"""


@dataclass(frozen=True, slots=True)
class CanonicalField:
    """Canonical Schema 的一个字段，以及它是否必填。"""

    name: str
    required: bool = False


# N2 首批 Canonical Episode 字段（对齐蓝图 §4.4 与 `lerobot.CanonicalEpisode`）。
# 这张表是「字段声明」的权威来源：Adapter 声明自己输出哪些字段时只能从这里取，
# 避免每个 Adapter 自造字段名后无法横向比较。
CANONICAL_EPISODE_FIELDS: tuple[CanonicalField, ...] = (
    CanonicalField("episode_key", required=True),
    CanonicalField("task_name", required=True),
    CanonicalField("instruction", required=True),
    CanonicalField("media_assets", required=True),
    CanonicalField("environment"),
    CanonicalField("setting"),
    CanonicalField("episode_seconds"),
    CanonicalField("source_fps"),
    CanonicalField("has_wrist"),
    CanonicalField("raw"),
)

_KNOWN_FIELDS: frozenset[str] = frozenset(
    item.name for item in CANONICAL_EPISODE_FIELDS
)


@dataclass(frozen=True, slots=True)
class AdapterSpec:
    """一个 Adapter 的声明式描述。

    字段约定：

    - ``adapter_id``：稳定标识，格式 ``<format>.<variant>``（如
      ``lerobot.human1m_preview``）。同一格式的不同变体用 variant 区分，
      不覆盖旧实现。
    - ``version``：Adapter 自身版本（不是数据版本）。映射规则变化必须升版本，
      否则历史导入无法复现"当时按什么规则映射的"。
    - ``supported_fields``：实际输出的 Canonical 字段。**必须是
      ``CANONICAL_EPISODE_FIELDS`` 的子集**，多声明会在注册时被拒绝。
    - ``missing_fields``：格式本身没有的字段（如公开数据集无 action）。
      必须显式列出而不是留空——"没声明"和"声明为缺失"语义不同：
      前者是我们没想过，后者是数据确实没有。
    - ``detect``：给定样本字节判断是否属于本格式。可选；不提供表示
      只能按 ``adapter_id`` 显式选择。
    """

    adapter_id: str
    version: str
    format_name: str
    parser: Parser
    supported_fields: frozenset[str]
    missing_fields: frozenset[str] = frozenset()
    description: str = ""
    detect: Callable[[bytes], bool] | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.adapter_id or "." not in self.adapter_id:
            raise AdapterRegistrationError(
                f"adapter_id 必须形如 <format>.<variant>，得到 {self.adapter_id!r}"
            )
        if not self.version:
            raise AdapterRegistrationError("version 不能为空：映射规则必须有版本")
        unknown = (self.supported_fields | self.missing_fields) - _KNOWN_FIELDS
        if unknown:
            raise AdapterRegistrationError(
                f"声明了未定义的 Canonical 字段：{sorted(unknown)}"
            )
        overlap = self.supported_fields & self.missing_fields
        if overlap:
            raise AdapterRegistrationError(
                f"同一字段不能既声明支持又声明缺失：{sorted(overlap)}"
            )
        undeclared = _KNOWN_FIELDS - self.supported_fields - self.missing_fields
        if undeclared:
            # 必须穷尽声明：否则"缺字段"会被误读成"支持但恰好为空"，
            # 下游无法区分"数据没有"与"Adapter 漏了"。
            raise AdapterRegistrationError(
                f"字段声明不完整，未表态的字段：{sorted(undeclared)}"
            )

    @property
    def declared_gaps(self) -> frozenset[str]:
        return self.missing_fields


class AdapterRegistry:
    """Adapter 注册表；实例级而非全局单例，便于测试隔离。"""

    def __init__(self) -> None:
        self._specs: dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> AdapterSpec:
        existing = self._specs.get(spec.adapter_id)
        if existing is not None:
            raise AdapterRegistrationError(
                f"adapter_id 已注册：{spec.adapter_id}"
                f"（已有 version={existing.version}）"
            )
        self._specs[spec.adapter_id] = spec
        return spec

    def get(self, adapter_id: str) -> AdapterSpec:
        spec = self._specs.get(adapter_id)
        if spec is None:
            raise AdapterNotFoundError(
                f"未注册的 Adapter：{adapter_id!r}；" f"已注册：{sorted(self._specs)}"
            )
        return spec

    def list_specs(self) -> tuple[AdapterSpec, ...]:
        return tuple(self._specs[key] for key in sorted(self._specs))

    def detect(self, sample: bytes) -> AdapterSpec | None:
        """按内容样本探测格式；多个命中时取 ``adapter_id`` 字典序最小的。

        为什么不用"最具体的优先"之类规则：探测是**提示**，最终选哪个
        Adapter 必须由调用方显式决定。返回确定性结果只是为了可复现。
        """
        matched = [
            spec
            for spec in self._specs.values()
            if spec.detect is not None and spec.detect(sample)
        ]
        if not matched:
            return None
        return min(matched, key=lambda item: item.adapter_id)

    def __len__(self) -> int:
        return len(self._specs)

    def snapshot(self) -> Mapping[str, AdapterSpec]:
        """只读快照；供报告与测试核对。"""
        return MappingProxyType(dict(self._specs))


__all__ = [
    "CANONICAL_EPISODE_FIELDS",
    "AdapterNotFoundError",
    "AdapterRegistrationError",
    "AdapterRegistry",
    "AdapterSpec",
    "CanonicalField",
    "Parser",
]
