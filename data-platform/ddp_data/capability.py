"""Adapter 六级数据解析能力分级与平台格式能力矩阵（ADR-0048 / V51-005）。

**与 ``AdapterCapability`` 五值枚举的关系（正交维度）**

- :class:`ddp_data.adapters.registry.AdapterCapability`（``PARSE / DETECT /
  FETCH / VERIFY / WRITE``）回答的是「**接口支持哪些操作**」（运行时分派依据）。
- :class:`FormatCapabilityLevel`（``DETECT → INSPECT → PAYLOAD_PARSE →
  STREAM_EXTRACT → EPISODE_BUILD → ROUND_TRIP``）回答的是「**数据解析到了多深、
  对外能诚实宣称什么**」（验收、UI 与文档口径的唯一权威来源）。

**三条结构性防虚报约束**

1. **连续性（无跳级）**：宣称第 N 级意味着 1..N 级全部满足；若显式传入
   ``verified_levels``，必须恰好覆盖 ``DETECT..max_level`` 连续区间，缺级或越界
   一律在构造时抛 :class:`CapabilityValidationError`。
2. **无证据不宣称深度**：当 ``max_level > DETECT`` 时，``evidence`` 严禁为空，
   且每条证据必须指向仓库内可核验的测试、脚本或样本事实文件。
3. **诚实边界硬规则**：``limitations`` 严禁为空，必须显式列出当前不支持的布局、
   模态或语义缺口，杜绝「注册成功即完整支持」的误导。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType


class CapabilityValidationError(ValueError):
    """格式能力档案声明不合法（跳级、缺证据、空限制或空版本号）。"""


class FormatCapabilityLevel(StrEnum):
    """数据解析深度六级枚举（ADR-0048 §3.1）。

    级别按解析深度严格递增，宣称高层级必须先满足全部前置层级。
    """

    DETECT = "detect"
    INSPECT = "inspect"
    PAYLOAD_PARSE = "payload_parse"
    STREAM_EXTRACT = "stream_extract"
    EPISODE_BUILD = "episode_build"
    ROUND_TRIP = "round_trip"

    @property
    def rank(self) -> int:
        """返回 1..6 的有序整数等级，便于跨级别比较。"""
        return _LEVEL_RANKS[self]

    def implied_levels(self) -> tuple[FormatCapabilityLevel, ...]:
        """返回本级别隐含满足的全部连续前置级别（含自身，按 1..N 排序）。"""
        return _ORDERED_LEVELS[: self.rank]

    def at_least(self, minimum: FormatCapabilityLevel) -> bool:
        """判断当前等级是否达到或超过 ``minimum``。"""
        if not isinstance(minimum, FormatCapabilityLevel):
            raise TypeError(f"minimum 必须是 FormatCapabilityLevel，得到 {type(minimum).__name__}")
        return self.rank >= minimum.rank


_ORDERED_LEVELS: tuple[FormatCapabilityLevel, ...] = (
    FormatCapabilityLevel.DETECT,
    FormatCapabilityLevel.INSPECT,
    FormatCapabilityLevel.PAYLOAD_PARSE,
    FormatCapabilityLevel.STREAM_EXTRACT,
    FormatCapabilityLevel.EPISODE_BUILD,
    FormatCapabilityLevel.ROUND_TRIP,
)

_LEVEL_RANKS: Mapping[FormatCapabilityLevel, int] = MappingProxyType(
    {level: index for index, level in enumerate(_ORDERED_LEVELS, start=1)}
)


def _validate_non_empty_strings(items: tuple[str, ...], field_name: str) -> None:
    if not isinstance(items, tuple):
        raise CapabilityValidationError(f"{field_name} 必须是 tuple[str, ...]")
    for index, item in enumerate(items):
        if not isinstance(item, str) or not item.strip():
            raise CapabilityValidationError(f"{field_name}[{index}] 必须是非空字符串")


@dataclass(frozen=True, slots=True)
class FormatCapabilityProfile:
    """单个格式或 Adapter 变体的解析深度档案（ADR-0048 §3.2）。

    参数约定：
    - ``format_name``：格式或变体名称（非空）。
    - ``max_level``：已实证达到的最高级别。
    - ``evidence``：可核验的证据路径（如 ``path/to/test.py::test_func``）；
      当 ``max_level > DETECT`` 时严禁为空。
    - ``limitations``：诚实边界说明，严禁为空，必须显式列出已知限制。
    - ``parser_version``：对应解析器/写入器版本（非空）。
    - ``verified_levels``：可选的显式已验证级别元组；若提供，必须从 ``DETECT``
      连续覆盖到 ``max_level`` 且无跳级、无越界、无重复，否则抛
      :class:`CapabilityValidationError`。
    """

    format_name: str
    max_level: FormatCapabilityLevel
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]
    parser_version: str
    verified_levels: tuple[FormatCapabilityLevel, ...] | None = None
    satisfied_levels: frozenset[FormatCapabilityLevel] = field(init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.format_name, str) or not self.format_name.strip():
            raise CapabilityValidationError("format_name 必须是非空字符串")
        if not isinstance(self.max_level, FormatCapabilityLevel):
            raise CapabilityValidationError(
                f"max_level 必须是 FormatCapabilityLevel，得到 {self.max_level!r}"
            )
        if not isinstance(self.parser_version, str) or not self.parser_version.strip():
            raise CapabilityValidationError("parser_version 必须是非空字符串")

        _validate_non_empty_strings(self.evidence, "evidence")
        if self.max_level.rank > FormatCapabilityLevel.DETECT.rank and not self.evidence:
            raise CapabilityValidationError(
                f"格式 {self.format_name!r} 宣称 {self.max_level.value!r} "
                "（高于 detect），evidence 严禁为空"
            )

        _validate_non_empty_strings(self.limitations, "limitations")
        if not self.limitations:
            raise CapabilityValidationError(
                f"格式 {self.format_name!r} 的 limitations 严禁为空：必须显式声明诚实边界"
            )

        expected = self.max_level.implied_levels()
        if self.verified_levels is not None:
            self._validate_verified_levels(expected)
        else:
            object.__setattr__(self, "verified_levels", expected)
        object.__setattr__(self, "satisfied_levels", frozenset(expected))

    def _validate_verified_levels(self, expected: tuple[FormatCapabilityLevel, ...]) -> None:
        assert self.verified_levels is not None
        if not isinstance(self.verified_levels, tuple) or not self.verified_levels:
            raise CapabilityValidationError(
                "verified_levels 若提供则必须是非空 tuple[FormatCapabilityLevel, ...]"
            )
        for item in self.verified_levels:
            if not isinstance(item, FormatCapabilityLevel):
                raise CapabilityValidationError(f"verified_levels 含非法元素：{item!r}")
        if len(set(self.verified_levels)) != len(self.verified_levels):
            raise CapabilityValidationError("verified_levels 不允许包含重复级别")

        provided_set = frozenset(self.verified_levels)
        expected_set = frozenset(expected)
        missing = [lvl.value for lvl in expected if lvl not in provided_set]
        extra = [
            lvl.value for lvl in _ORDERED_LEVELS if lvl in provided_set and lvl not in expected_set
        ]
        if missing or extra:
            details: list[str] = []
            if missing:
                details.append(f"缺少前置级别 {missing}")
            if extra:
                details.append(f"超出 max_level({self.max_level.value}) 的级别 {extra}")
            raise CapabilityValidationError(
                f"格式 {self.format_name!r} 能力级别声明不连续：{'; '.join(details)}"
            )
        object.__setattr__(self, "verified_levels", expected)

    def supports_level(self, level: FormatCapabilityLevel) -> bool:
        """判断该档案是否已满足给定解析深度级别。"""
        return level in self.satisfied_levels

    def as_dict(self) -> dict[str, object]:
        """导出可 JSON 序列化的只读字典，供 API / 报告消费。"""
        return {
            "format_name": self.format_name,
            "max_level": self.max_level.value,
            "max_level_rank": self.max_level.rank,
            "satisfied_levels": [lvl.value for lvl in self.max_level.implied_levels()],
            "evidence": list(self.evidence),
            "limitations": list(self.limitations),
            "parser_version": self.parser_version,
        }


# ---------------------------------------------------------------------------
# 内置 Adapter 专属能力档案（供 ddp_data.adapters.builtin 挂载）
# ---------------------------------------------------------------------------

HUMAN1M_PREVIEW_CAPABILITY_PROFILE = FormatCapabilityProfile(
    format_name="lerobot_preview_jsonl",
    max_level=FormatCapabilityLevel.INSPECT,
    evidence=(
        "backend/tests/unit/test_lerobot_parser.py::test_groups_multiple_cameras_into_one_episode",
        "backend/tests/unit/test_adapter_registry.py::test_detect_routes_human1m_metadata",
        "backend/tests/unit/test_adapter_registry.py::test_human1m_declares_semantic_gaps_honestly",
    ),
    limitations=(
        (
            "仅解析 NVIDIA Human-1M preview 平铺 metadata.jsonl 元数据行，"
            "无逐帧 observation/action payload"
        ),
        "不含动作序列、机器人位姿、时间同步、相机标定与深度流（见 metadata.semantic_gaps）",
    ),
    parser_version="1.0.0",
)

GENERIC_JSONL_CAPABILITY_PROFILE = FormatCapabilityProfile(
    format_name="generic_jsonl",
    max_level=FormatCapabilityLevel.INSPECT,
    evidence=(
        "backend/tests/unit/test_adapter_registry.py::test_detect_routes_generic_jsonl",
        "backend/tests/unit/test_adapter_registry.py::test_two_formats_map_to_same_canonical_shape",
    ),
    limitations=(
        "仅解析一行一 Episode 的元数据 JSONL 文本，不读取或解码底层媒体与传感器逐帧数据流",
        "缺失的可选元数据字段保持 None，不产出 SensorStream/Observation/Action 三件套",
    ),
    parser_version="1.0.0",
)


# ---------------------------------------------------------------------------
# 全平台权威格式能力矩阵（ADR-0048 §4 同步 V5.1 实证基线）
# ---------------------------------------------------------------------------

BUILTIN_FORMAT_CAPABILITY_MATRIX: Mapping[str, FormatCapabilityProfile] = MappingProxyType(
    {
        "hdf5": FormatCapabilityProfile(
            format_name="hdf5",
            max_level=FormatCapabilityLevel.ROUND_TRIP,
            evidence=(
                "backend/tests/unit/test_hdf5_probe.py::test_nested_structure_is_bounded_and_explicit",
                "data-platform/tests/test_hdf5_payload.py::test_read_2d_slice_returns_real_values",
                "backend/tests/unit/test_stream_extraction_stage.py::test_extract_produces_streams_observations_and_actions",
                "data-platform/tests/test_lerobot_frame_writer.py::test_writes_frame_level_parquet_and_info",
                "docs/testing/evidence/2026-11-25-lerobot-frame-verify/verify_frame_load.py",
                "backend/tests/fixtures/public/robomimic-hdf5/facts.json",
            ),
            limitations=(
                (
                    "仅 robomimic_hdf5_v1.5 已知布局打通 STREAM_EXTRACT→EPISODE_BUILD→"
                    "ROUND_TRIP 逐帧闭环；通用任意布局 HDF5 仅达 INSPECT"
                    "（read_hdf5_structure 只读结构）"
                ),
                "RoboMimic ph 样本不含图像流（video_provided=false），视频帧级提取与解码尚未验证",
                (
                    "软链接与外部链接仅登记声明文本不跟随；公开数据集验证"
                    "（PUBLIC_DATASET_INTEGRATION_TESTED）不等同真实硬件采集联调"
                ),
            ),
            parser_version="1.1.0",
        ),
        "rosbag": FormatCapabilityProfile(
            format_name="rosbag",
            max_level=FormatCapabilityLevel.INSPECT,
            evidence=(
                "backend/tests/unit/test_rosbag_probe.py::test_rosbag2_structure_is_read_without_message_bodies",
                "backend/tests/unit/test_rosbag_probe.py::test_rosbag1_structure_is_read",
            ),
            limitations=(
                (
                    "只读 rosbag1/rosbag2 索引与连接元数据（topic/msgtype/计数/起止时间），"
                    "显式不遍历 messages() 且不反序列化消息体"
                ),
                "孤立 bag 缺少任务与指令语义，不直接产出 Canonical Episode 或逐帧数据流",
            ),
            parser_version="1.0.0",
        ),
        "mp4": FormatCapabilityProfile(
            format_name="mp4",
            max_level=FormatCapabilityLevel.INSPECT,
            evidence=(
                "backend/tests/unit/test_mp4_probe.py::test_parses_bounded_metadata",
                "backend/tests/unit/test_mp4_probe.py::test_rejects_non_mp4_before_invoking_ffprobe",
            ),
            limitations=(
                (
                    "仅通过受控 ffprobe 子进程探测容器与音视频流头信息"
                    "（时长/帧率/分辨率/编码），不解码视频帧且不抽帧"
                ),
                (
                    "运行时依赖宿主机安装可执行的 ffprobe；单文件仅作媒体资产引用，"
                    "不产出像素级 Observation"
                ),
            ),
            parser_version="1.0.0",
        ),
        "parquet": FormatCapabilityProfile(
            format_name="parquet",
            max_level=FormatCapabilityLevel.INSPECT,
            evidence=(
                "backend/tests/unit/test_parquet_adapter.py::test_lerobot_sample_schema_and_counts",
                "backend/tests/unit/test_parquet_adapter.py::test_only_footer_region_is_read_not_whole_file",
            ),
            limitations=(
                (
                    "读取侧仅通过 PyArrow 解析文件尾 footer 元数据"
                    "（schema/行数/行组数/列缺失检查），不读取数据页行值"
                ),
                (
                    "加密 footer（PARE）显式拒绝；孤立表不含数据集级任务语义，"
                    "不直接注册为 Episode 导入器"
                ),
            ),
            parser_version="1.0.0",
        ),
        "mcap": FormatCapabilityProfile(
            format_name="mcap",
            max_level=FormatCapabilityLevel.INSPECT,
            evidence=(
                "backend/tests/unit/test_mcap_adapter.py::test_detect_requires_both_magic_marks",
                "backend/tests/unit/test_mcap_adapter.py::test_multichannel_metadata_is_bounded_and_explicit",
            ),
            limitations=(
                (
                    "仅通过官方 mcap SeekingReader 读取 header/footer/summary 索引"
                    "（channel/schema/消息计数/时间范围），不读取 chunk 数据页且不解码消息体"
                ),
                (
                    "无 summary 索引的文件仅返回 has_summary=False 与未知计数（None），"
                    "不遍历全文件统计"
                ),
            ),
            parser_version="1.0.0",
        ),
        "lerobot": FormatCapabilityProfile(
            format_name="lerobot",
            max_level=FormatCapabilityLevel.ROUND_TRIP,
            evidence=(
                "backend/tests/unit/test_lerobot_parser.py::test_groups_multiple_cameras_into_one_episode",
                "backend/tests/unit/test_lerobot_export_writer.py::test_per_episode_parquet_layout_and_content",
                "data-platform/tests/test_lerobot_frame_writer.py::test_writes_frame_level_parquet_and_info",
                "data-platform/tests/test_lerobot_frame_writer.py::test_parquet_columns_are_expanded_by_dimension",
                "docs/testing/evidence/2026-11-25-lerobot-frame-verify/verify_frame_load.py",
            ),
            limitations=(
                (
                    "写入/导出侧双路共存：存在已物化逐帧流时由 frame_writer 产出 v2.1 "
                    "LOADER_FRAME 级 Parquet 并通过 lerobot==0.3.3 逐帧往返验证；"
                    "仅有元数据时由 export_writer 产出 METADATA_ONLY（不可宣称训练就绪）"
                ),
                (
                    "导入侧（lerobot.human1m_preview）仍止步 INSPECT，仅解析 preview "
                    "metadata.jsonl，不支持通用 LeRobot Parquet 逐帧逆向导入"
                ),
                (
                    "当前逐帧导出仅覆盖低维 observation.state 与 action 数值列，"
                    "不含视频文件封装（video_provided=false），且尚未在多 Episode "
                    "与真实训练脚本中闭环验证"
                ),
            ),
            parser_version="2.1.0",
        ),
        "generic_jsonl": GENERIC_JSONL_CAPABILITY_PROFILE,
    }
)


__all__ = [
    "BUILTIN_FORMAT_CAPABILITY_MATRIX",
    "GENERIC_JSONL_CAPABILITY_PROFILE",
    "HUMAN1M_PREVIEW_CAPABILITY_PROFILE",
    "CapabilityValidationError",
    "FormatCapabilityLevel",
    "FormatCapabilityProfile",
]
