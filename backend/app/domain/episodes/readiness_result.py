"""自动就绪判定的结果值对象（拒绝原因 / notice / 判定结论）。

这些类型是**跨层共用的稳定结构**：领域判定产出它们、``episodes`` 实体把它们
持久化、API 把它们原样暴露给客户端。因此单独成模块，避免与判定规则互相牵制。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from app.domain.episodes.errors import DomainValidationError

# 阻塞项与 notice 的文案上限：可读原因要能直接给人看，也不能无限膨胀。
BLOCKER_MESSAGE_MAX = 500
# 单个 Episode 的阻塞项上限；本模块按 code 聚合，正常远小于该值。
MAX_BLOCKERS = 20
# 多机位阈值：达到 2 路流即需要机位间几何关系（标定快照）。
MULTI_STREAM_THRESHOLD = 2


class ReadinessBlockerCode(StrEnum):
    """自动就绪的拒绝原因稳定码（供 API/审计分组，不随文案变化）。"""

    STREAMS_MISSING = "STREAMS_MISSING"
    STREAM_INCOMPLETE = "STREAM_INCOMPLETE"
    STREAM_SNAPSHOT_MISMATCH = "STREAM_SNAPSHOT_MISMATCH"
    TIME_BASE_INVALID = "TIME_BASE_INVALID"
    TIME_SYNC_UNVERIFIED = "TIME_SYNC_UNVERIFIED"
    CALIBRATION_MISSING = "CALIBRATION_MISSING"
    DURATION_UNKNOWN = "DURATION_UNKNOWN"
    FRAME_RATE_UNKNOWN = "FRAME_RATE_UNKNOWN"
    FRAME_RATE_INCONSISTENT = "FRAME_RATE_INCONSISTENT"
    FRAME_COUNT_UNDETERMINED = "FRAME_COUNT_UNDETERMINED"


class ReadinessNoticeCode(StrEnum):
    """不阻塞就绪、但必须留痕的事实。"""

    SINGLE_STREAM_CALIBRATION_ABSENT = "SINGLE_STREAM_CALIBRATION_ABSENT"
    # 多机位缺标定同样不阻塞"就绪"，但必须留痕：能否用于训练由数据集准入
    # （CALIBRATION_MISSING / allow_missing_calibration）判断，而不是在这里
    # 把整份公开数据集永远挡在 READY 之外。
    MULTI_STREAM_CALIBRATION_ABSENT = "MULTI_STREAM_CALIBRATION_ABSENT"


@dataclass(frozen=True, slots=True)
class ReadinessBlocker:
    """一条可读的拒绝原因；``code`` 稳定、``message`` 面向人。"""

    code: ReadinessBlockerCode
    message: str
    detail: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        try:
            code = ReadinessBlockerCode(self.code)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("readiness blocker code 取值非法") from exc
        object.__setattr__(self, "code", code)
        if not isinstance(self.message, str) or not self.message.strip():
            raise DomainValidationError("readiness blocker 必须给出可读原因")
        message = self.message.strip()
        if len(message) > BLOCKER_MESSAGE_MAX:
            raise DomainValidationError(
                f"readiness blocker 原因不得超过 {BLOCKER_MESSAGE_MAX} 字符"
            )
        object.__setattr__(self, "message", message)
        if not isinstance(self.detail, Mapping):
            raise DomainValidationError("readiness blocker detail 必须是对象")
        object.__setattr__(self, "detail", {str(key): value for key, value in self.detail.items()})

    def as_dict(self) -> dict[str, object]:
        return {"code": str(self.code), "message": self.message, "detail": dict(self.detail)}


@dataclass(frozen=True, slots=True)
class ReadinessNotice:
    code: ReadinessNoticeCode
    message: str

    def __post_init__(self) -> None:
        try:
            code = ReadinessNoticeCode(self.code)
        except (TypeError, ValueError) as exc:
            raise DomainValidationError("readiness notice code 取值非法") from exc
        object.__setattr__(self, "code", code)
        if not isinstance(self.message, str) or not self.message.strip():
            raise DomainValidationError("readiness notice 必须给出可读说明")
        object.__setattr__(self, "message", self.message.strip()[:BLOCKER_MESSAGE_MAX])

    def as_dict(self) -> dict[str, object]:
        return {"code": str(self.code), "message": self.message}


@dataclass(frozen=True, slots=True)
class ReadinessDecision:
    """判定结果：要么给出可用的 Manifest，要么给出至少一条阻塞原因。"""

    manifest: Mapping[str, object] | None
    frame_count: int | None
    blockers: tuple[ReadinessBlocker, ...]
    notices: tuple[ReadinessNotice, ...] = ()

    @property
    def is_ready(self) -> bool:
        return not self.blockers

    def require_manifest(self) -> Mapping[str, object]:
        if self.manifest is None:
            raise DomainValidationError("就绪判定未通过，没有可用的 Manifest")
        return self.manifest

    def require_frame_count(self) -> int:
        if self.frame_count is None:
            raise DomainValidationError("就绪判定未通过，无法确定 frame_count")
        return self.frame_count


def normalize_readiness_blockers(value: object) -> tuple[ReadinessBlocker, ...]:
    """把持久化形态（list[dict]）或已构造对象归一为领域对象；非法即抛错。"""
    if value is None:
        return ()
    if isinstance(value, str | bytes) or not isinstance(value, Sequence):
        raise DomainValidationError("readiness_blockers 必须是数组")
    normalized: list[ReadinessBlocker] = []
    for item in value:
        if isinstance(item, ReadinessBlocker):
            normalized.append(item)
            continue
        if not isinstance(item, Mapping):
            raise DomainValidationError("readiness_blockers 只能包含对象")
        normalized.append(
            ReadinessBlocker(
                code=item.get("code"),  # type: ignore[arg-type]
                message=item.get("message"),  # type: ignore[arg-type]
                detail=item.get("detail") or {},
            )
        )
    return tuple(normalized)
