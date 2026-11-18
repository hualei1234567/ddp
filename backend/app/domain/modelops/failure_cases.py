"""评测失败用例：结构化的事实记录，供 Data Gap 多来源聚合使用（N7-003）。

为什么需要结构化 failure_cases
------------------------------
``metrics`` 只回答"整体成功率是多少"，回答不了"哪些 Episode 在哪一步失败、
证据是什么"。此前适配器只能把这类明细塞进自由 JSON（``report_ref``），
平台既无法校验，也无法按类别/Episode 聚合——Data Gap（N7-004）只能得到
一份不可信的字符串。

本模块只负责**如实记录事实**：类别、严重度、Episode 引用、证据、指标与
观测/期望值。它**不**生成 Data Gap、不推断需求、不修改评测结论。是否把某类
失败聚合成缺口，由 N7-004 的用例决定。

边界与上限
----------
- 单次评测最多 ``MAX_FAILURE_CASES`` 条：失败明细是证据，不是日志倾倒场；
- 序列化后整体不超过 ``MAX_FAILURE_CASES_BYTES``：列是 JSON，必须有界，
  否则单行可无限膨胀；
- 单条 ``evidence`` 文本/JSON 另设上限，避免一条证据吃掉全部额度；
- 类别/严重度走枚举；未知类别必须显式用 ``OTHER``，不得静默落桶。

校验只抛既有的 ``DomainValidationError``（HTTP 收敛为 ``validation_failed``），
不新增错误码：客户端需要的是"字段哪里不对"，而不是又一套码表。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from app.domain.modelops.enums import FailureCaseCategory, FeedbackSeverity
from app.domain.modelops.errors import DomainValidationError
from app.domain.modelops.validation import (
    coerce_enum,
    optional_text,
    text_value,
)

MAX_FAILURE_CASES: Final = 200
MAX_FAILURE_CASES_BYTES: Final = 64 * 1024
MAX_EVIDENCE_BYTES: Final = 4 * 1024
MAX_EVIDENCE_TEXT: Final = 2000
MAX_CASE_ID_LENGTH: Final = 64
MAX_EPISODE_KEY_LENGTH: Final = 200
MAX_METRIC_LENGTH: Final = 100
MAX_SCALAR_TEXT_LENGTH: Final = 200


def _json_bytes(value: object) -> bytes:
    """把证据/整体快照序列化成确定性的 UTF-8 字节，用于上限判定。"""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise DomainValidationError("failure_cases 必须是可 JSON 序列化的值") from exc


def _optional_uuid(value: object, field: str) -> UUID | None:
    """接受 UUID 或 UUID 字符串。

    适配器回调经 JSON 传入的是字符串，存量行从 JSON 列读回的也是字符串；
    二者都必须在领域层归一成 ``UUID``，不能要求调用方先转一遍。
    """
    if value is None or isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError as exc:
            raise DomainValidationError(f"{field} 必须是 UUID") from exc
    raise DomainValidationError(f"{field} 必须是 UUID")


def _optional_scalar(value: object, field: str) -> float | str | None:
    """``observed`` / ``expected``：数值或短字符串，二者都可为空。

    允许字符串是为了兼容分类结果（如 ``"left"`` / ``"right"``），但长度有界；
    ``bool`` 是 ``int`` 的子类，必须显式拒绝，避免 ``True`` 被当成 ``1.0``。
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise DomainValidationError(f"{field} 必须是数字或字符串")
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        return text_value(value, field, max_length=MAX_SCALAR_TEXT_LENGTH)
    raise DomainValidationError(f"{field} 必须是数字或字符串")


def _evidence(value: object) -> dict[str, object] | str | None:
    if value is None:
        return None
    if isinstance(value, str):
        # 字符串证据走与 JSON 证据同样的字节口径，防止用超长文本绕过总量上限。
        cleaned = text_value(value, "failure_cases.evidence", max_length=MAX_EVIDENCE_TEXT)
        if len(_json_bytes(cleaned)) > MAX_EVIDENCE_BYTES:
            raise DomainValidationError(f"failure_cases.evidence 超过 {MAX_EVIDENCE_BYTES} 字节")
        return cleaned
    if not isinstance(value, dict):
        raise DomainValidationError("failure_cases.evidence 必须是对象、字符串或 null")
    if not value:
        raise DomainValidationError("failure_cases.evidence 不能是空对象")
    if not all(isinstance(key, str) for key in value):
        raise DomainValidationError("failure_cases.evidence 的键必须是字符串")
    if len(_json_bytes(value)) > MAX_EVIDENCE_BYTES:
        raise DomainValidationError(f"failure_cases.evidence 超过 {MAX_EVIDENCE_BYTES} 字节")
    return dict(value)


@dataclass(frozen=True, slots=True)
class FailureCase:
    """一条失败用例。

    字段全部来自适配器的实测事实；平台不做补全推断。``suggested_gap`` 是可选
    提示（例如场景/物体名），刻意保持为有界字符串而不是结构化对象：它是
    N7-004 的输入线索，不是平台权威的缺口定义。
    """

    id: str
    category: str
    severity: str
    episode_key: str | None = None
    episode_id: UUID | None = None
    evidence: dict[str, object] | str | None = None
    metric: str | None = None
    observed: float | str | None = None
    expected: float | str | None = None
    suggested_gap: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "id", text_value(self.id, "failure_cases.id", max_length=MAX_CASE_ID_LENGTH)
        )
        object.__setattr__(
            self,
            "category",
            coerce_enum(self.category, FailureCaseCategory, "failure_cases.category").value,
        )
        object.__setattr__(
            self,
            "severity",
            coerce_enum(self.severity, FeedbackSeverity, "failure_cases.severity").value,
        )
        object.__setattr__(
            self,
            "episode_key",
            optional_text(
                self.episode_key,
                "failure_cases.episode_key",
                max_length=MAX_EPISODE_KEY_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "episode_id",
            _optional_uuid(self.episode_id, "failure_cases.episode_id"),
        )
        object.__setattr__(self, "evidence", _evidence(self.evidence))
        object.__setattr__(
            self,
            "metric",
            optional_text(self.metric, "failure_cases.metric", max_length=MAX_METRIC_LENGTH),
        )
        object.__setattr__(
            self, "observed", _optional_scalar(self.observed, "failure_cases.observed")
        )
        object.__setattr__(
            self, "expected", _optional_scalar(self.expected, "failure_cases.expected")
        )
        object.__setattr__(
            self,
            "suggested_gap",
            optional_text(
                self.suggested_gap,
                "failure_cases.suggested_gap",
                max_length=MAX_SCALAR_TEXT_LENGTH,
            ),
        )

    def to_dict(self) -> dict[str, object]:
        """稳定序列化形状；与契约 ``FailureCase`` 字段一一对应。"""
        return {
            "id": self.id,
            "category": self.category,
            "severity": self.severity,
            "episode_key": self.episode_key,
            "episode_id": str(self.episode_id) if self.episode_id else None,
            "evidence": self.evidence,
            "metric": self.metric,
            "observed": self.observed,
            "expected": self.expected,
            "suggested_gap": self.suggested_gap,
        }


def parse_failure_cases(value: object) -> list[FailureCase]:
    """把入参/存量 JSON 归一成 ``FailureCase`` 列表并执行全部上限校验。

    ``None`` 视为空数组：存量行（迁移前创建）没有该字段，不能因此让读取失败。
    """
    if value is None:
        return []
    if not isinstance(value, list | tuple):
        raise DomainValidationError("failure_cases 必须是数组")
    if len(value) > MAX_FAILURE_CASES:
        raise DomainValidationError(f"failure_cases 最多 {MAX_FAILURE_CASES} 条")
    cases: list[FailureCase] = []
    for index, item in enumerate(value):
        try:
            if isinstance(item, FailureCase):
                cases.append(item)
            elif isinstance(item, dict):
                if not all(isinstance(key, str) for key in item):
                    raise DomainValidationError("failure_cases 的键必须是字符串")
                cases.append(FailureCase(**item))
            else:
                raise DomainValidationError("failure_cases 的每一项必须是对象")
        except TypeError as exc:
            raise DomainValidationError(f"failure_cases[{index}] 字段缺失或包含未知字段") from exc
    identifiers = [case.id for case in cases]
    if len(set(identifiers)) != len(identifiers):
        raise DomainValidationError("failure_cases 的 id 必须唯一")
    if len(_json_bytes([case.to_dict() for case in cases])) > MAX_FAILURE_CASES_BYTES:
        raise DomainValidationError(f"failure_cases 序列化后超过 {MAX_FAILURE_CASES_BYTES} 字节")
    return cases


__all__ = [
    "FailureCase",
    "MAX_EVIDENCE_BYTES",
    "MAX_FAILURE_CASES",
    "MAX_FAILURE_CASES_BYTES",
    "parse_failure_cases",
]
