"""AI 预标注建议聚合。

## 为什么单独建模，而不是塞进 ``annotation_records.labels``

历史上 AI 结果与人工结果写在**同一张表、同一 JSONB 字段**（``labels``），
只能靠每个标签的 ``source``（``HUMAN`` / ``AI_ASSISTED`` / ``IMPORTED``）
和可选 ``confidence`` 区分。这带来三个无法回避的问题：

1. **模型身份丢失**：``labels`` 里没有任何字段承载 ``model_id`` /
   ``model_version`` / ``prompt_version``。同一批标签到底来自哪个模型版本，
   事后无法追溯，N7 的「AI 建议 vs 人工修订」统计因此根本做不出来。
2. **建议被人工结果覆盖**：人工一旦写了同名字段，AI 原始建议就消失了，
   修订幅度（建议值 → 人工值）无从计算。
3. **两类数据的生命周期不同**：AI 建议可以整批重跑、按模型版本清理；
   人工标注值受提交冻结与审计约束。混在一张表里必然互相牵制。

因此本实体与 ``annotation_records`` **物理分离**：一份建议一行，
``suggested_value`` 保留模型输出，``human_value`` 保留人工最终值，
两者同时存在才能算出「被采纳 / 被修订 / 被丢弃」。

## 校验规则

- ``confidence`` 必须落在 ``[0, 1]``；
- ``model_id`` / ``model_version`` / ``prompt_version`` **必填且非空**——
  这正是审计发现的缺口：缺了它们，任何统计都无法按模型版本切分；
- ``human_confirmed`` 与人工决定字段必须一致：确认了就必须有人工值、
  决定人与决定时间；未确认时这三者必须为空（否则「谁改的、什么时候改的」
  就说不清）。规则只在 :func:`_require_decision_consistency` 一处实现。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.errors import (
    DomainValidationError,
    ResourceImmutableError,
)
from app.domain.annotation.validation import (
    JSON_MAX_BYTES,
    canonical_json,
    normalized_text,
    optional_aware_datetime,
    optional_uuid,
    positive_int,
    required_uuid,
)

# 与 ``LabelValue`` 同强度：任意合法 JSON 值（标量 / 数组 / 对象）。
_JSON_VALUE_TYPES = (str, int, float, bool, list, tuple, dict)

MODEL_ID_MAX_LENGTH = 128
MODEL_VERSION_MAX_LENGTH = 64
PROMPT_VERSION_MAX_LENGTH = 64


def _now() -> datetime:
    return datetime.now(UTC)


def _plain(value: object) -> object:
    """把不可变容器还原成普通 Python 值，便于落库与序列化。"""
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _json_value(value: object, field_name: str) -> object:
    """校验任意 JSON 值并返回普通 Python 副本。

    只复用既有的 ``canonical_json``（可序列化性、拒绝 NaN/Infinity）与
    ``JSON_MAX_BYTES``（落库体量上限），不复制 ``LabelValue`` 的标签业务规则：
    建议值没有 ``label_status``、``source`` 这类标签专属语义。
    """
    if value is None or not isinstance(value, _JSON_VALUE_TYPES):
        raise DomainValidationError(f"{field_name} 必须是合法 JSON 值")
    encoded = canonical_json(value)
    if len(encoded.encode("utf-8")) > JSON_MAX_BYTES:
        raise DomainValidationError(f"{field_name} 序列化后过大")
    return _plain(value)


def _confidence(value: object) -> float:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise DomainValidationError("confidence 必须是 0 到 1 之间的数字")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise DomainValidationError("confidence 必须在 0 到 1 之间")
    return number


def _require_decision_consistency(suggestion: AiSuggestion) -> None:
    """``human_confirmed`` 与人工决定字段必须互相自洽。

    两处独立校验容易漂移（一处改了另一处忘了），因此规则只写在本函数里；
    实体构造与数据库 CHECK 约束表达同一件事，后者是防绕过应用层的兜底。
    """
    decided = (suggestion.human_value, suggestion.decided_by, suggestion.decided_at)
    if suggestion.human_confirmed:
        if any(item is None for item in decided):
            raise DomainValidationError("已确认的 AI 建议必须同时具备人工值、决定人与决定时间")
        return
    if any(item is not None for item in decided):
        raise DomainValidationError("未确认的 AI 建议不得携带人工值、决定人或决定时间")


@dataclass(frozen=True, slots=True)
class AiSuggestion:
    """一条 AI 预标注建议；与标注记录一对一挂靠但独立存储。"""

    suggestion_id: UUID
    project_id: UUID
    annotation_id: UUID
    label_key: str
    suggested_value: object
    model_id: str
    model_version: str
    prompt_version: str
    confidence: float
    generated_at: datetime
    human_confirmed: bool = False
    human_value: object | None = None
    decided_by: UUID | None = None
    decided_at: datetime | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("suggestion_id", "project_id", "annotation_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "label_key",
            normalized_text(self.label_key, "label_key", max_length=100),
        )
        object.__setattr__(
            self,
            "suggested_value",
            _json_value(self.suggested_value, "suggested_value"),
        )
        object.__setattr__(
            self,
            "model_id",
            normalized_text(self.model_id, "model_id", max_length=MODEL_ID_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "model_version",
            normalized_text(
                self.model_version,
                "model_version",
                max_length=MODEL_VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(
            self,
            "prompt_version",
            normalized_text(
                self.prompt_version,
                "prompt_version",
                max_length=PROMPT_VERSION_MAX_LENGTH,
            ),
        )
        object.__setattr__(self, "confidence", _confidence(self.confidence))
        if self.generated_at is None:
            raise DomainValidationError("generated_at 不能为空")
        object.__setattr__(
            self,
            "generated_at",
            optional_aware_datetime(self.generated_at, "generated_at"),
        )
        if not isinstance(self.human_confirmed, bool):
            raise DomainValidationError("human_confirmed 必须是布尔值")
        if self.human_value is not None:
            object.__setattr__(
                self,
                "human_value",
                _json_value(self.human_value, "human_value"),
            )
        object.__setattr__(self, "decided_by", optional_uuid(self.decided_by, "decided_by"))
        object.__setattr__(
            self,
            "decided_at",
            optional_aware_datetime(self.decided_at, "decided_at"),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))
        for name in ("created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        _require_decision_consistency(self)

    @property
    def is_decided(self) -> bool:
        return self.human_confirmed

    def confirm(
        self,
        *,
        actor_id: UUID,
        human_value: object,
        now: datetime | None = None,
    ) -> AiSuggestion:
        """人工确认或修订；``human_value`` 必须是显式人工值（可以是原建议）。"""
        if self.is_decided:
            raise ResourceImmutableError("AI 建议已确认，不可重复确认")
        occurred_at = now or _now()
        return replace(
            self,
            human_confirmed=True,
            human_value=_json_value(human_value, "human_value"),
            decided_by=required_uuid(actor_id, "decided_by"),
            decided_at=occurred_at,
            updated_at=occurred_at,
            updated_by=actor_id,
        )

    def assert_suggestion_immutable(self, candidate: AiSuggestion) -> None:
        """模型输出一旦落库就不可原地改写，否则「AI 建议 vs 人工修订」失真。"""
        if (
            self.annotation_id != candidate.annotation_id
            or self.label_key != candidate.label_key
            or _plain(self.suggested_value) != _plain(candidate.suggested_value)
            or self.model_id != candidate.model_id
            or self.model_version != candidate.model_version
            or self.prompt_version != candidate.prompt_version
            or self.confidence != candidate.confidence
            or self.generated_at != candidate.generated_at
        ):
            raise ResourceImmutableError("AI 建议的模型输出不可原地修改")


__all__ = [
    "AiSuggestion",
    "MODEL_ID_MAX_LENGTH",
    "MODEL_VERSION_MAX_LENGTH",
    "PROMPT_VERSION_MAX_LENGTH",
]
