"""质量结论的**证据定位**（V51-010 / FR-025）。

## 为什么需要独立结构，而不是继续塞进 ``evidence``

``quality_findings.evidence`` 是自由 JSON：它回答"规则看到了什么"（阈值、
缺失键、逐流偏移……），但**不承诺**任何字段能被机器定位。前端要"点击结论 →
播放器跳到证据时间 → 时间轴高亮区间 → 高亮对应流"，就必须有稳定的定位契约：

- ``time_range``：证据覆盖的绝对时间区间（起止纳秒）；
- ``stream_id``：证据属于哪一路媒体流（机位/轨道），对象级结论为空；
- ``field_path``：证据指向被检查对象的哪个字段（如 ``labels.velocity``）。

三者都是**可空**的：不是每条结论都有时间/流/字段维度（例如"缺少必填标签"
只有字段维度；"多路同步超差"只有流维度且影响整个 Episode）。因此规则必须
如实给出它能定位到什么程度，而不是为了让界面好看编造区间。

## 纳秒为什么用十进制字符串

纳秒时间戳约 ``1.8e18``，超出 IEEE-754 双精度可精确表示范围（``2^53``）。
若在 JSON 里写成数字，浏览器 ``JSON.parse`` 会静默丢精度——定位到错误的时间
点比不定位更糟。因此对外载荷一律用**十进制字符串**；读取时同时接受整数与
数字字符串（历史证据由规则以整数构造），内部统一为 ``int``。

## 保留键

规则实现（导入/标注/数据集三套既有规则）只产出 ``Mapping``，不依赖质量领域
类型。适配器通过 :data:`EVIDENCE_LOCATION_KEY` 从 ``evidence`` 中取出定位并
提升为结构化字段，其余键原样保留。取出的键不会留在 ``evidence`` 里，避免同一
事实两处存储、两处漂移。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from app.domain.quality.errors import DomainValidationError

#: 规则在 ``evidence`` 里放定位信息的保留键。
EVIDENCE_LOCATION_KEY = "location"

STREAM_ID_MAX_LENGTH = 128
FIELD_PATH_MAX_LENGTH = 256
_NS_REQUIRED_KEYS = frozenset({"start_time_ns", "end_time_ns"})
_LOCATION_KEYS = frozenset({"time_range", "stream_id", "field_path"})


def _coerce_ns(value: object, field_name: str) -> int:
    """纳秒转整数：接受整数或十进制字符串，其余一律拒绝。"""
    if isinstance(value, bool):
        raise DomainValidationError(f"{field_name} 必须是整数纳秒")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        # 只接受纯十进制（可带负号）：十六进制/浮点/空串都说明生产方写错了字段。
        body = text[1:] if text.startswith("-") else text
        if not body or not body.isdigit():
            raise DomainValidationError(f"{field_name} 必须是十进制整数纳秒字符串")
        return int(text)
    raise DomainValidationError(f"{field_name} 必须是整数纳秒")


@dataclass(frozen=True, slots=True)
class EvidenceTimeRange:
    """证据覆盖的绝对时间区间；``start < end`` 由构造期强制。"""

    start_time_ns: int
    end_time_ns: int

    def __post_init__(self) -> None:
        start = _coerce_ns(self.start_time_ns, "evidence.time_range.start_time_ns")
        end = _coerce_ns(self.end_time_ns, "evidence.time_range.end_time_ns")
        if start >= end:
            raise DomainValidationError("evidence.time_range 的起点必须早于终点")
        object.__setattr__(self, "start_time_ns", start)
        object.__setattr__(self, "end_time_ns", end)

    def to_payload(self) -> dict[str, str]:
        return {
            "start_time_ns": str(self.start_time_ns),
            "end_time_ns": str(self.end_time_ns),
        }


def _optional_text(value: object, field_name: str, *, max_length: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = value.strip()
    if not text:
        raise DomainValidationError(f"{field_name} 不得为空字符串")
    if len(text) > max_length:
        raise DomainValidationError(f"{field_name} 超过 {max_length} 字符")
    return text


@dataclass(frozen=True, slots=True)
class EvidenceLocation:
    """一条质量结论的证据定位；至少给出一个维度，全空视为"没有定位"。

    "没有定位"在接口上表现为 ``evidence_location: null``，而不是三个字段都
    为空对象——后者会被前端读成"有定位但字段没填"，进而渲染一个空区间。
    """

    time_range: EvidenceTimeRange | Mapping[str, object] | None = None
    stream_id: str | None = None
    field_path: str | None = None

    def __post_init__(self) -> None:
        raw_range = self.time_range
        if raw_range is not None and not isinstance(raw_range, EvidenceTimeRange):
            if not isinstance(raw_range, Mapping):
                raise DomainValidationError("evidence.time_range 必须是对象")
            unknown = set(raw_range) - _NS_REQUIRED_KEYS
            if unknown:
                raise DomainValidationError("evidence.time_range 包含未知字段")
            if not _NS_REQUIRED_KEYS.issubset(raw_range):
                raise DomainValidationError("evidence.time_range 必须包含起止纳秒")
            raw_range = EvidenceTimeRange(
                start_time_ns=raw_range["start_time_ns"],  # type: ignore[arg-type]
                end_time_ns=raw_range["end_time_ns"],  # type: ignore[arg-type]
            )
        object.__setattr__(self, "time_range", raw_range)
        object.__setattr__(
            self,
            "stream_id",
            _optional_text(self.stream_id, "evidence.stream_id", max_length=STREAM_ID_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "field_path",
            _optional_text(
                self.field_path, "evidence.field_path", max_length=FIELD_PATH_MAX_LENGTH
            ),
        )
        if raw_range is None and self.stream_id is None and self.field_path is None:
            raise DomainValidationError("evidence.location 至少要给出时间区间、流或字段之一")

    @property
    def has_time_range(self) -> bool:
        return isinstance(self.time_range, EvidenceTimeRange)

    def to_payload(self) -> dict[str, object]:
        time_range = self.time_range if isinstance(self.time_range, EvidenceTimeRange) else None
        return {
            "time_range": time_range.to_payload() if time_range is not None else None,
            "stream_id": self.stream_id,
            "field_path": self.field_path,
        }


def parse_evidence_location(value: object) -> EvidenceLocation | None:
    """把接口/规则产出的定位载荷转成领域对象；``None`` 原样返回。"""
    if value is None:
        return None
    if isinstance(value, EvidenceLocation):
        return value
    if not isinstance(value, Mapping):
        raise DomainValidationError("evidence_location 必须是对象或 null")
    unknown = set(value) - _LOCATION_KEYS
    if unknown:
        raise DomainValidationError("evidence_location 包含未知字段")
    return EvidenceLocation(
        time_range=value.get("time_range"),
        stream_id=value.get("stream_id"),  # type: ignore[arg-type]
        field_path=value.get("field_path"),  # type: ignore[arg-type]
    )


def split_evidence_location(
    evidence: Mapping[str, object] | None,
) -> tuple[EvidenceLocation | None, dict[str, object] | None]:
    """从规则产出里取出保留键 ``location``，返回 ``(定位, 其余证据)``。

    两个返回值都可能是 ``None``：没有 evidence、或 evidence 只有定位时，
    剩下的证据为空——空对象不再作为 evidence 落库（"没有排障信息"与
    "排障信息是空对象"在读取侧无法区分，前者才是事实）。
    """
    if evidence is None:
        return None, None
    if not isinstance(evidence, Mapping):
        raise DomainValidationError("evidence 必须是 JSON 对象")
    if EVIDENCE_LOCATION_KEY not in evidence:
        return None, dict(evidence)
    location = parse_evidence_location(evidence[EVIDENCE_LOCATION_KEY])
    rest = {key: item for key, item in evidence.items() if key != EVIDENCE_LOCATION_KEY}
    return location, (rest or None)


__all__ = [
    "EVIDENCE_LOCATION_KEY",
    "FIELD_PATH_MAX_LENGTH",
    "STREAM_ID_MAX_LENGTH",
    "EvidenceLocation",
    "EvidenceTimeRange",
    "parse_evidence_location",
    "split_evidence_location",
]
