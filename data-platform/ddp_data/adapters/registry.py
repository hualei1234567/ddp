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

DDP-V21-N3-002 起，Adapter 除 ``detect`` / ``parse`` 外还可以声明
**可选能力** ``fetch`` / ``verify`` / ``write``（见 :class:`AdapterCapability`）。
能力是**实现驱动**的：只有真正提供了可调用实现才计入能力集；未实现的能力
必须由调用方通过 :meth:`AdapterSpec.require` 显式处理（抛
:class:`AdapterCapabilityError`），不允许把 ``None`` 静默当成"没有数据"或成功。
``parser`` 仍是纯 ``bytes -> Episodes``，fetch/verify/write 不得混入解析路径——
只读解析不引入网络依赖。

设计约束（与 CLAUDE.md 一致）：

- 不引入第二套 Canonical 类型——直接复用 `lerobot.CanonicalEpisode`；
- 未知格式显式失败，不返回 None 让调用方当成"没有数据"；
- 注册表是纯数据 + 纯函数，无 IO、无全局可变状态被外部随意改写。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Protocol, cast

from ddp_data.capability import FormatCapabilityLevel, FormatCapabilityProfile
from ddp_data.lerobot.parser import CanonicalEpisode

# 解析器签名：输入原始字节，输出 Canonical Episode 序列。
# 只接受 bytes 而不是文件路径：Adapter 不应关心数据从哪来（HF / 本地 / S3），
# 也不该在解析阶段做 IO——那会把"能不能读文件"和"能不能解析"混在一起。
Parser = Callable[[bytes], tuple[CanonicalEpisode, ...]]


class AdapterCapability(StrEnum):
    """Adapter 可声明的能力。

    ``PARSE`` 是接口必选能力；``DETECT`` 与 ``FETCH`` / ``VERIFY`` / ``WRITE``
    都是可选能力，是否具备由 ``AdapterSpec`` 上对应实现是否为 ``None`` 决定。
    """

    PARSE = "parse"
    DETECT = "detect"
    FETCH = "fetch"
    VERIFY = "verify"
    WRITE = "write"


class Fetcher(Protocol):
    """按来源内引用读取原始字节（只读，不做解析）。

    ``reference`` 的语义由 Adapter 自己定义（HF 路径、本地键、对象键……），
    实现可以闭包绑定 repo/revision/token。它**不在解析路径上**：``parser``
    仍只接收已就绪的 ``bytes``，只读解析不应因此引入网络调用。
    """

    def __call__(self, reference: str, /) -> bytes: ...


class Verifier(Protocol):
    """校验一段原始内容是否满足本格式的最小契约。

    返回 ``None`` 表示通过；不通过必须**抛异常**，而不是返回 False 让调用方
    有机会忽略。校验只读，不得下载或改写来源。
    """

    def __call__(self, data: bytes, /) -> None: ...


class Writer(Protocol):
    """把 Canonical Episode 序列写成目标格式字节（序列化，不落盘）。

    与 ``Parser`` 对称：写出的字节由调用方决定放哪里（对象存储/本地/导出），
    Adapter 不直接碰存储，避免把 IO 混进纯数据能力包。
    """

    def __call__(self, episodes: tuple[CanonicalEpisode, ...], /) -> bytes: ...


class AdapterNotFoundError(Exception):
    """请求的格式没有已注册的 Adapter。显式失败，不静默返回空。"""


class AdapterRegistrationError(Exception):
    """注册参数非法（重复 ID、空字段声明等）。"""


class AdapterCapabilityError(Exception):
    """请求的 Adapter 未实现该能力。显式失败：不返回 None、不伪装成功。"""


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

_KNOWN_FIELDS: frozenset[str] = frozenset(item.name for item in CANONICAL_EPISODE_FIELDS)


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
    - ``fetch`` / ``verify`` / ``write``：可选能力。未提供表示**不支持**；
      调用方必须通过 :meth:`require` 显式处理，而不是把 ``None`` 当成功。
      解析路径不得依赖它们，``parser`` 仍是纯 ``bytes -> Episodes``。

    向后兼容：新增能力字段全部有默认值且追加在原有字段之后，旧 Adapter
    构造方式（只传 detect/parse）无需修改即可继续注册。
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
    fetch: Fetcher | None = None
    verify: Verifier | None = None
    write: Writer | None = None
    capability_profile: FormatCapabilityProfile | None = None

    def __post_init__(self) -> None:
        # 运行时也校验可调用性：类型标注挡不住动态构造，坏实现必须注册即失败。
        # ``parser`` 是必选能力，缺失或不可调用都直接拒绝；其余能力仅在提供时校验。
        if not callable(self.parser):
            raise AdapterRegistrationError("parser 必须是可调用对象")
        for name, implementation in (
            ("detect", self.detect),
            ("fetch", self.fetch),
            ("verify", self.verify),
            ("write", self.write),
        ):
            if implementation is not None and not callable(implementation):
                raise AdapterRegistrationError(f"{name} 必须是可调用对象")
        if self.capability_profile is not None and not isinstance(
            self.capability_profile, FormatCapabilityProfile
        ):
            raise AdapterRegistrationError(
                "capability_profile 必须是 FormatCapabilityProfile 或 None"
            )
        if not self.adapter_id or "." not in self.adapter_id:
            raise AdapterRegistrationError(
                f"adapter_id 必须形如 <format>.<variant>，得到 {self.adapter_id!r}"
            )
        if not self.version:
            raise AdapterRegistrationError("version 不能为空：映射规则必须有版本")
        unknown = (self.supported_fields | self.missing_fields) - _KNOWN_FIELDS
        if unknown:
            raise AdapterRegistrationError(f"声明了未定义的 Canonical 字段：{sorted(unknown)}")
        overlap = self.supported_fields & self.missing_fields
        if overlap:
            raise AdapterRegistrationError(f"同一字段不能既声明支持又声明缺失：{sorted(overlap)}")
        undeclared = _KNOWN_FIELDS - self.supported_fields - self.missing_fields
        if undeclared:
            # 必须穷尽声明：否则"缺字段"会被误读成"支持但恰好为空"，
            # 下游无法区分"数据没有"与"Adapter 漏了"。
            raise AdapterRegistrationError(f"字段声明不完整，未表态的字段：{sorted(undeclared)}")

    @property
    def declared_gaps(self) -> frozenset[str]:
        return self.missing_fields

    @property
    def capabilities(self) -> frozenset[AdapterCapability]:
        """按实际实现推导的能力集，不做"声明了但没实现"的虚报。"""
        capabilities = {AdapterCapability.PARSE}
        if self.detect is not None:
            capabilities.add(AdapterCapability.DETECT)
        if self.fetch is not None:
            capabilities.add(AdapterCapability.FETCH)
        if self.verify is not None:
            capabilities.add(AdapterCapability.VERIFY)
        if self.write is not None:
            capabilities.add(AdapterCapability.WRITE)
        return frozenset(capabilities)

    def supports(self, capability: AdapterCapability) -> bool:
        return capability in self.capabilities

    def require(self, capability: AdapterCapability) -> AdapterSpec:
        """要求具备某项能力；未实现时显式失败，绝不静默降级。"""
        if not self.supports(capability):
            raise AdapterCapabilityError(
                f"Adapter {self.adapter_id!r} 未实现能力 {capability.value!r}；"
                "不支持必须由调用方显式处理，不得静默降级。"
            )
        return self

    def require_fetch(self) -> Fetcher:
        self.require(AdapterCapability.FETCH)
        return cast(Fetcher, self.fetch)

    def require_verify(self) -> Verifier:
        self.require(AdapterCapability.VERIFY)
        return cast(Verifier, self.verify)

    def require_write(self) -> Writer:
        self.require(AdapterCapability.WRITE)
        return cast(Writer, self.write)

    @property
    def effective_capability_level(self) -> FormatCapabilityLevel:
        """返回已核实的解析深度等级；未声明 capability_profile 时保守回退为 DETECT。"""
        if self.capability_profile is None:
            return FormatCapabilityLevel.DETECT
        return self.capability_profile.max_level

    def satisfies_format_level(self, level: FormatCapabilityLevel) -> bool:
        """判断本 Adapter 是否已达到给定数据解析深度等级。"""
        return self.effective_capability_level.at_least(level)


class AdapterRegistry:
    """Adapter 注册表；实例级而非全局单例，便于测试隔离。"""

    def __init__(self) -> None:
        self._specs: dict[str, AdapterSpec] = {}

    def register(self, spec: AdapterSpec) -> AdapterSpec:
        existing = self._specs.get(spec.adapter_id)
        if existing is not None:
            raise AdapterRegistrationError(
                f"adapter_id 已注册：{spec.adapter_id}" f"（已有 version={existing.version}）"
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

    def list_by_capability(self, capability: AdapterCapability) -> tuple[AdapterSpec, ...]:
        """按能力检索已注册 Adapter；结果按 ``adapter_id`` 排序，可复现。"""
        return tuple(spec for spec in self.list_specs() if spec.supports(capability))

    def require_capability(self, adapter_id: str, capability: AdapterCapability) -> AdapterSpec:
        """取 Adapter 并要求其具备给定能力；不支持时抛 AdapterCapabilityError。"""
        return self.get(adapter_id).require(capability)

    def list_by_format_level(self, min_level: FormatCapabilityLevel) -> tuple[AdapterSpec, ...]:
        """按最低解析深度检索已注册 Adapter；结果按 ``adapter_id`` 排序。"""
        return tuple(spec for spec in self.list_specs() if spec.satisfies_format_level(min_level))

    def detect(self, sample: bytes) -> AdapterSpec | None:
        """按内容样本探测格式；多个命中时取 ``adapter_id`` 字典序最小的。

        为什么不用"最具体的优先"之类规则：探测是**提示**，最终选哪个
        Adapter 必须由调用方显式决定。返回确定性结果只是为了可复现。
        """
        matched = [
            spec for spec in self._specs.values() if spec.detect is not None and spec.detect(sample)
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
    "AdapterCapability",
    "AdapterCapabilityError",
    "AdapterNotFoundError",
    "AdapterRegistrationError",
    "AdapterRegistry",
    "AdapterSpec",
    "CanonicalField",
    "Fetcher",
    "Parser",
    "Verifier",
    "Writer",
]
