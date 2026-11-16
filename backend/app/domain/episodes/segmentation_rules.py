"""切片规则注册表（N3：Episode 切片规则可配置、可版本化）。

为什么需要注册表：``Episode.segmentation_rule_version`` 是冻结字段，数据集
Manifest 与质量报告都靠它回溯"这条 Episode 是怎么切出来的"。只有版本号而
没有规则体，等于只能回答"用了哪个名字"，回答不了"这个名字对应什么规则、
哪些参数合法、切出来是什么"。注册表把
``rule_version → 规则定义 + 参数 schema + 纯函数`` 固化下来，使同一份输入
配同一版本必然得到同一份切片结果。

设计约束：

- **纯函数**：无 I/O、不读当前时间、不依赖全局可变状态；确定性是可复现的前提。
- **实例级注册表**：不提供模块级单例，测试与不同装配点各自持有，互不污染。
- **未注册版本显式抛错**：不返回 ``None``。静默 None 会退化成
  "未知规则默认成功"，这正是 DDP 禁止的失败模式。
- **参数校验复用 ``jsonschema``**（``requirements.txt`` 已固定 4.23.0），
  不手写校验器。schema 直接描述逻辑值形状（``window_ns`` 等仍是整数），
  与契约里 ``rule_parameters`` 允许任意 JSON 对象的口径一致。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.domain.episodes.errors import (
    DomainValidationError,
    SegmentationRuleNotRegisteredError,
)
from app.domain.episodes.validation import int64, time_range

# 规则标识用 kebab-case，版本用三段式 semver；两者拼成 ``rule_version``。
_RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+$")

# 单次切分的窗口数上限：防止 ``window_ns=1`` 配超长时间区间时把进程拖死。
# 这是**资源边界**而非业务阈值：正常规则（窗口 ≥ 1ms、区间 ≤ 数小时）远达不到。
MAX_WINDOWS_PER_INPUT: Final = 100_000

# 规则版本字面量集中在此处，避免各调用点各写一遍字符串。
FIXED_WINDOW_RULE_VERSION: Final = "fixed-window/1.0.0"
EPISODE_BOUNDARY_RULE_VERSION: Final = "episode-boundary/1.0.0"


@dataclass(frozen=True, slots=True)
class SegmentationWindow:
    """一片切分结果（左闭右开，纳秒）。"""

    start_time_ns: int
    end_time_ns: int

    def __post_init__(self) -> None:
        start, end = time_range(self.start_time_ns, self.end_time_ns)
        object.__setattr__(self, "start_time_ns", start)
        object.__setattr__(self, "end_time_ns", end)

    @property
    def duration_ns(self) -> int:
        return self.end_time_ns - self.start_time_ns


@dataclass(frozen=True, slots=True)
class SegmentationInput:
    """规则输入。

    ``source_episode_boundaries`` 是源数据（解析器/导入器）给出的 episode
    边界，供 ``episode-boundary`` 规则使用；其他规则忽略它。这里只校验形状，
    "是否乱序/重叠" 由具体规则判断——集成层不该替规则定义语义。
    """

    start_time_ns: int
    end_time_ns: int
    source_episode_boundaries: tuple[tuple[int, int], ...] = ()

    def __post_init__(self) -> None:
        start, end = time_range(self.start_time_ns, self.end_time_ns)
        object.__setattr__(self, "start_time_ns", start)
        object.__setattr__(self, "end_time_ns", end)
        boundaries: list[tuple[int, int]] = []
        raw = self.source_episode_boundaries
        if not isinstance(raw, tuple | list):
            raise DomainValidationError("source_episode_boundaries 必须是二元组列表")
        for index, item in enumerate(raw):
            if not isinstance(item, tuple | list) or len(item) != 2:
                raise DomainValidationError(
                    f"source_episode_boundaries[{index}] 必须是 (start_time_ns, end_time_ns)"
                )
            item_start, item_end = item
            # 用 ``int64`` 兜住取值范围，而不只是 ``isinstance(int)``：Python 的
            # 整数无上限，``10**30`` 也能通过类型检查。这类值一定是源数据损坏或
            # 单位搞错（例如误传微秒后再乘一次），若不拦下，后续被输入区间裁切后
            # 会得到一条**看起来完全正常**的窗口——脏数据变成静默的正确结果，
            # 这正是"未知状态默认成功"的同构失败。int64 与 Episode 的
            # ``start_time_ns``/``end_time_ns`` 同域，越界值本来就无法落库。
            item_start = int64(item_start, f"source_episode_boundaries[{index}].start_time_ns")
            item_end = int64(item_end, f"source_episode_boundaries[{index}].end_time_ns")
            boundaries.append((item_start, item_end))
        object.__setattr__(self, "source_episode_boundaries", tuple(boundaries))


@dataclass(frozen=True, slots=True)
class SegmentationRule:
    """一条已版本化的切片规则。

    ``apply`` 是不做参数校验的纯函数；对外入口用 :meth:`execute`（先按
    ``parameters_schema`` 校验参数再调用 ``apply``）。拆成两层的原因：
    规则实现可以专注切分数学，参数校验只写一遍、对所有规则一致。
    """

    rule_id: str
    version: str
    description: str
    parameters_schema: Mapping[str, object]
    apply: Callable[[SegmentationInput, Mapping[str, object]], tuple[SegmentationWindow, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.rule_id, str) or _RULE_ID_PATTERN.match(self.rule_id) is None:
            raise DomainValidationError("切片规则 rule_id 必须是 kebab-case 标识")
        if not isinstance(self.version, str) or _VERSION_PATTERN.match(self.version) is None:
            raise DomainValidationError("切片规则 version 必须是三段式版本号，如 1.0.0")
        if not isinstance(self.description, str) or not self.description.strip():
            raise DomainValidationError("切片规则 description 必须是非空字符串")
        if not isinstance(self.parameters_schema, Mapping):
            raise DomainValidationError("切片规则 parameters_schema 必须是 JSON Schema 对象")
        schema = dict(self.parameters_schema)
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            raise DomainValidationError(
                f"切片规则 {self.rule_version} 的 parameters_schema 不是合法 JSON Schema："
                f"{exc.message}"
            ) from exc
        object.__setattr__(self, "parameters_schema", schema)
        if not callable(self.apply):
            raise DomainValidationError("切片规则 apply 必须是可调用对象")

    @property
    def rule_version(self) -> str:
        """注册键：``<rule_id>/<version>``，与 ``Episode.segmentation_rule_version`` 同域。"""
        return f"{self.rule_id}/{self.version}"

    def validate_parameters(self, parameters: object) -> dict[str, object]:
        """按本规则 schema 校验参数；失败抛 ``DomainValidationError`` 并给字段路径。"""
        if not isinstance(parameters, Mapping):
            raise DomainValidationError(
                f"切片规则 {self.rule_version} 的参数必须是对象",
                details={"rule_version": self.rule_version},
            )
        payload = _plain_json(parameters)
        if not isinstance(payload, dict):
            raise DomainValidationError(
                f"切片规则 {self.rule_version} 的参数必须是 JSON 对象",
                details={"rule_version": self.rule_version},
            )
        errors = sorted(
            Draft202012Validator(self.parameters_schema).iter_errors(payload),
            key=lambda error: [str(part) for part in error.absolute_path],
        )
        if not errors:
            return payload
        first = errors[0]
        path = "/".join(str(part) for part in first.absolute_path) or "(根)"
        suffix = f"（共 {len(errors)} 处不合法，仅显示第一处）" if len(errors) > 1 else ""
        raise DomainValidationError(
            f"切片规则 {self.rule_version} 参数不合法：{path} — {first.message}{suffix}",
            details={
                "rule_version": self.rule_version,
                "field_path": [str(part) for part in first.absolute_path],
                "error_count": len(errors),
            },
        )

    def execute(
        self,
        segment_input: SegmentationInput,
        parameters: Mapping[str, object],
    ) -> tuple[SegmentationWindow, ...]:
        """校验参数后执行规则；同输入同输出。"""
        prepared = self.validate_parameters(parameters)
        return self.apply(segment_input, prepared)


class SegmentationRuleRegistry:
    """切片规则注册表（实例级，非全局单例）。

    注册顺序不影响 :meth:`list_rules` 的结果：列表按 ``rule_version`` 排序，
    保证"同一组规则无论注册顺序如何，输出顺序一致"。
    """

    def __init__(self) -> None:
        self._rules: dict[str, SegmentationRule] = {}

    def register(self, rule: SegmentationRule) -> None:
        """注册规则；重复注册同一版本显式失败（静默覆盖会掩盖版本冲突）。"""
        if not isinstance(rule, SegmentationRule):
            raise DomainValidationError("只能注册 SegmentationRule 实例")
        key = rule.rule_version
        if key in self._rules:
            raise DomainValidationError(
                f"切片规则 {key} 已注册，禁止覆盖；变更规则必须提升版本号",
                details={"rule_version": key},
            )
        self._rules[key] = rule

    def get(self, rule_version: str) -> SegmentationRule:
        """按 ``rule_version`` 取规则；未注册直接抛错，不返回 None。"""
        key = rule_version.strip() if isinstance(rule_version, str) else ""
        rule = self._rules.get(key) if key else None
        if rule is None:
            raise SegmentationRuleNotRegisteredError(
                rule_version,
                registered=self.registered_versions(),
            )
        return rule

    def has(self, rule_version: object) -> bool:
        """只读探测（用于展示/统计）；校验路径必须走 :meth:`get`。"""
        return isinstance(rule_version, str) and rule_version.strip() in self._rules

    def list_rules(self) -> tuple[SegmentationRule, ...]:
        return tuple(self._rules[key] for key in sorted(self._rules))

    def registered_versions(self) -> tuple[str, ...]:
        return tuple(sorted(self._rules))

    def __len__(self) -> int:
        return len(self._rules)


def build_segmentation_rule_registry() -> SegmentationRuleRegistry:
    """构造带内置规则的**新**注册表。

    每次调用返回独立实例：调用方可以按需追加自定义规则而不影响他人。
    """
    registry = SegmentationRuleRegistry()
    registry.register(FIXED_WINDOW_RULE)
    registry.register(EPISODE_BOUNDARY_RULE)
    return registry


# ---------------------------------------------------------------------------
# 内置规则 1：fixed-window/1.0.0
# ---------------------------------------------------------------------------

_FIXED_WINDOW_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["window_ns"],
    "properties": {
        # 窗口长度：正整数纳秒。语义是"目标窗口长度"，首尾窗口会被输入区间裁切。
        "window_ns": {"type": "integer", "minimum": 1},
        # 对齐锚点：默认取输入起点。给定锚点后窗口边界落在 anchor + k*window_ns 上，
        # 这样跨多次导入的同一条采集流可以得到一致的窗口边界。
        "anchor_ns": {"type": "integer"},
    },
}


def _apply_fixed_window(
    segment_input: SegmentationInput,
    parameters: Mapping[str, object],
) -> tuple[SegmentationWindow, ...]:
    window_ns = _parameter_int(parameters, "window_ns", FIXED_WINDOW_RULE_VERSION)
    anchor_ns = _parameter_int(
        parameters,
        "anchor_ns",
        FIXED_WINDOW_RULE_VERSION,
        default=segment_input.start_time_ns,
    )
    windows: list[SegmentationWindow] = []
    index = (segment_input.start_time_ns - anchor_ns) // window_ns
    while True:
        window_start = anchor_ns + index * window_ns
        window_end = window_start + window_ns
        start = max(window_start, segment_input.start_time_ns)
        end = min(window_end, segment_input.end_time_ns)
        if start < end:
            windows.append(SegmentationWindow(start_time_ns=start, end_time_ns=end))
            if len(windows) > MAX_WINDOWS_PER_INPUT:
                raise DomainValidationError(
                    f"切片窗口数超过上限 {MAX_WINDOWS_PER_INPUT}：请增大 window_ns",
                    details={"rule_version": FIXED_WINDOW_RULE_VERSION},
                )
        if window_end >= segment_input.end_time_ns:
            break
        index += 1
    return tuple(windows)


def _parameter_int(
    parameters: Mapping[str, object],
    name: str,
    rule_version: str,
    *,
    default: int | None = None,
) -> int:
    """从规则参数里取整数并做类型收窄。

    为什么不用 ``assert`` 收窄：``python -O`` 会剥离 assert，一旦校验被绕过，
    后续就会拿字符串/布尔去参与整除与乘法——静默算错比直接报错危险得多。
    这里的失败分支是"防御性兜底"，正常路径已由 schema 校验拦下。
    """
    value = parameters.get(name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(
            f"切片规则 {rule_version} 的参数 {name} 必须是整数",
            details={"rule_version": rule_version, "parameter": name},
        )
    return value


FIXED_WINDOW_RULE: Final = SegmentationRule(
    rule_id="fixed-window",
    version="1.0.0",
    description=(
        "按固定时长窗口切分。窗口边界对齐 anchor_ns（默认输入起点），"
        "首尾窗口按输入时间区间裁切；窗口左闭右开、互不重叠。"
    ),
    parameters_schema=_FIXED_WINDOW_SCHEMA,
    apply=_apply_fixed_window,
)


# ---------------------------------------------------------------------------
# 内置规则 2：episode-boundary/1.0.0
# ---------------------------------------------------------------------------

_EPISODE_BOUNDARY_SCHEMA: Final[Mapping[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    # 为什么需要一个必填参数：阶段 2 契约冻结（docs/architecture/阶段2-契约冻结-v1.0.md
    # §3.5）明确要求 ``method=RULE`` 必须提供 ``rule_parameters``。若本规则 schema
    # 允许 ``{}``，它就永远无法经 Episode 创建路径使用——注册表里挂着一条用不了的
    # 规则比不注册更糟。``boundary_source`` 不是凑数的占位符：它记录边界从哪来，
    # 正是 N3 要的"可追溯"（同一边界不同来源，复现时才知道该信谁）。
    "required": ["boundary_source"],
    "properties": {
        "boundary_source": {"type": "string", "enum": ["PARSER", "MANIFEST", "EXTERNAL"]},
        # 丢弃时长短于该阈值的源数据片段（去除解析产生的碎片 episode）。
        # 0 表示不丢弃；默认 0，保持"原样按边界切"的语义。
        "min_duration_ns": {"type": "integer", "minimum": 0},
    },
}


def _apply_episode_boundary(
    segment_input: SegmentationInput,
    parameters: Mapping[str, object],
) -> tuple[SegmentationWindow, ...]:
    boundaries = segment_input.source_episode_boundaries
    if not boundaries:
        raise DomainValidationError(
            "episode-boundary 规则需要源数据 episode 边界，但输入未提供",
            details={"rule_version": EPISODE_BOUNDARY_RULE_VERSION},
        )
    min_duration_ns = _parameter_int(
        parameters,
        "min_duration_ns",
        EPISODE_BOUNDARY_RULE_VERSION,
        default=0,
    )
    windows: list[SegmentationWindow] = []
    previous_end: int | None = None
    for index, (start_time_ns, end_time_ns) in enumerate(boundaries):
        _assert_boundary_order(index, start_time_ns, end_time_ns, previous_end)
        previous_end = end_time_ns
        start = max(start_time_ns, segment_input.start_time_ns)
        end = min(end_time_ns, segment_input.end_time_ns)
        if start >= end:
            continue
        # 阈值按**裁切后**的时长判定：跨区间的长片段被裁短后可能不再是有效样本。
        if min_duration_ns and end - start < min_duration_ns:
            continue
        windows.append(SegmentationWindow(start_time_ns=start, end_time_ns=end))
    if not windows:
        # 全部边界落在区间外（或被阈值过滤）不是"成功的空结果"，
        # 而是输入与参数不匹配，必须报错。
        raise DomainValidationError(
            "源数据 episode 边界与切片时间区间没有交集",
            details={"rule_version": EPISODE_BOUNDARY_RULE_VERSION},
        )
    return tuple(windows)


def _assert_boundary_order(
    index: int,
    start_time_ns: int,
    end_time_ns: int,
    previous_end: int | None,
) -> None:
    """校验单段边界的自洽性与顺序；乱序/重叠会让切片结果不可复现。"""
    if start_time_ns >= end_time_ns:
        raise DomainValidationError(
            f"源数据 episode 边界第 {index} 段起点不早于终点",
            details={"rule_version": EPISODE_BOUNDARY_RULE_VERSION, "boundary_index": index},
        )
    if previous_end is not None and start_time_ns < previous_end:
        raise DomainValidationError(
            f"源数据 episode 边界第 {index} 段与前一段重叠或乱序",
            details={"rule_version": EPISODE_BOUNDARY_RULE_VERSION, "boundary_index": index},
        )


EPISODE_BOUNDARY_RULE: Final = SegmentationRule(
    rule_id="episode-boundary",
    version="1.0.0",
    description=(
        "按源数据自带的 episode 边界切分，不做二次等分。边界来自"
        "SegmentationInput.source_episode_boundaries；必填 boundary_source 记录"
        "边界来源（PARSER/MANIFEST/EXTERNAL）以便复现，可选 min_duration_ns 过滤"
        "裁切后过短的碎片片段。"
    ),
    parameters_schema=_EPISODE_BOUNDARY_SCHEMA,
    apply=_apply_episode_boundary,
)


def _plain_json(value: object) -> object:
    """把 MappingProxyType / tuple 递归转成普通 dict / list，供 jsonschema 校验。

    领域层可能拿到冻结视图；``jsonschema`` 只认 dict/list。校验是只读操作，
    转换不改变语义。
    """
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_plain_json(item) for item in value]
    return value


__all__ = [
    "EPISODE_BOUNDARY_RULE",
    "EPISODE_BOUNDARY_RULE_VERSION",
    "FIXED_WINDOW_RULE",
    "FIXED_WINDOW_RULE_VERSION",
    "MAX_WINDOWS_PER_INPUT",
    "SegmentationInput",
    "SegmentationRule",
    "SegmentationRuleRegistry",
    "SegmentationWindow",
    "build_segmentation_rule_registry",
]
