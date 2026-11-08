"""黄金集聚合与准确率计算。

隐藏注入：对标注员返回的视图永远不含 ``expected_labels``；期望答案仅在显式
授权视图中出现。准确率只按已评估黄金项计算，未评估时返回 N/A 而非 0。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from uuid import UUID

from app.domain.annotation.enums import (
    AnnotationSchemaStatus,
    GoldItemDifficulty,
    GoldRotationPolicy,
    GoldSetExposurePolicy,
    GoldSetStatus,
)
from app.domain.annotation.errors import (
    DomainValidationError,
    GoldSetSchemaIncompatibleError,
    ResourceImmutableError,
    SchemaNotPublishedError,
)
from app.domain.annotation.state_machines import (
    action_requires_reason,
    gold_set_target,
    validate_action_name,
)
from app.domain.annotation.validation import (
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    REASON_MAX_LENGTH,
    canonical_json,
    coerce_enum,
    frozen_mapping,
    normalized_text,
    optional_aware_datetime,
    optional_text,
    positive_int,
    required_uuid,
    validate_sha256,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_plain(item) for item in value]
    return value


def _validated_json(value: object) -> object:
    if value is None or not isinstance(value, str | int | float | bool | list | tuple | dict):
        raise DomainValidationError("黄金集标签值必须是合法 JSON 值")
    canonical_json(value)
    return _plain(value)


@dataclass(frozen=True, slots=True)
class ExpectedLabel:
    label_key: str
    value: object
    tolerance: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "label_key",
            normalized_text(self.label_key, "expected_label.label_key", max_length=100),
        )
        object.__setattr__(self, "value", _validated_json(self.value))
        if self.tolerance is not None:
            spec = frozen_mapping(self.tolerance, "expected_label.tolerance")
            unknown = set(spec) - {"abs", "start_ns", "end_ns"}
            if unknown:
                raise DomainValidationError("tolerance 包含未知字段")
            for key, item in spec.items():
                if not isinstance(item, int | float) or isinstance(item, bool) or item < 0:
                    raise DomainValidationError(f"tolerance.{key} 必须是非负数字")
            object.__setattr__(self, "tolerance", spec)

    def matches(self, actual: object) -> bool:
        expected = self.value
        if (
            isinstance(expected, int | float)
            and not isinstance(expected, bool)
            and isinstance(actual, int | float)
            and not isinstance(actual, bool)
            and self.tolerance is not None
            and self.tolerance.get("abs") is not None
        ):
            return abs(float(actual) - float(expected)) <= float(self.tolerance["abs"])
        if (
            isinstance(expected, Mapping)
            and isinstance(actual, Mapping)
            and expected.get("start_time_ns") is not None
            and expected.get("end_time_ns") is not None
            and actual.get("start_time_ns") is not None
            and actual.get("end_time_ns") is not None
            and self.tolerance is not None
        ):
            start_tol = int(self.tolerance.get("start_ns", 0))
            end_tol = int(self.tolerance.get("end_ns", 0))
            return (
                abs(int(actual["start_time_ns"]) - int(expected["start_time_ns"])) <= start_tol
                and abs(int(actual["end_time_ns"]) - int(expected["end_time_ns"])) <= end_tol
            )
        return actual == expected

    def to_payload(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "label_key": self.label_key,
            "value": _plain(self.value),
        }
        if self.tolerance is not None:
            payload["tolerance"] = _plain(self.tolerance)
        return payload


@dataclass(frozen=True, slots=True)
class GoldItem:
    gold_item_id: UUID
    episode_id: UUID
    expected_labels: tuple[ExpectedLabel, ...]
    difficulty: GoldItemDifficulty | str = GoldItemDifficulty.MEDIUM
    weight: float = 1.0
    active: bool = True
    episode_version: int | None = None
    time_range: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        required_uuid(self.gold_item_id, "gold_item_id")
        required_uuid(self.episode_id, "episode_id")
        if not isinstance(self.expected_labels, list | tuple) or not self.expected_labels:
            raise DomainValidationError("黄金项必须至少包含一个期望标签")
        labels: list[ExpectedLabel] = []
        seen: set[str] = set()
        for item in self.expected_labels:
            label = item if isinstance(item, ExpectedLabel) else ExpectedLabel(**item)  # type: ignore[arg-type]
            if label.label_key in seen:
                raise DomainValidationError("黄金项期望标签键不得重复")
            seen.add(label.label_key)
            labels.append(label)
        object.__setattr__(self, "expected_labels", tuple(labels))
        object.__setattr__(
            self,
            "difficulty",
            coerce_enum(self.difficulty, GoldItemDifficulty, "difficulty"),
        )
        if not isinstance(self.weight, int | float) or isinstance(self.weight, bool):
            raise DomainValidationError("weight 必须是数字")
        if float(self.weight) < 0:
            raise DomainValidationError("weight 不得为负")
        object.__setattr__(self, "weight", float(self.weight))
        if not isinstance(self.active, bool):
            raise DomainValidationError("active 必须是布尔值")
        if self.episode_version is not None:
            object.__setattr__(
                self,
                "episode_version",
                positive_int(self.episode_version, "episode_version"),
            )
        if self.time_range is not None:
            object.__setattr__(
                self,
                "time_range",
                frozen_mapping(self.time_range, "time_range"),
            )

    def redacted_payload(self) -> dict[str, object]:
        """标注员可见视图：不含期望答案。"""
        payload: dict[str, object] = {
            "gold_item_id": str(self.gold_item_id),
            "episode_id": str(self.episode_id),
            "difficulty": self.difficulty.value,
            "weight": self.weight,
            "active": self.active,
            "answers_redacted": True,
        }
        if self.episode_version is not None:
            payload["episode_version"] = self.episode_version
        if self.time_range is not None:
            payload["time_range"] = _plain(self.time_range)
        return payload

    def answer_payload(self) -> dict[str, object]:
        payload = self.redacted_payload()
        payload.pop("answers_redacted", None)
        payload["expected_labels"] = [item.to_payload() for item in self.expected_labels]
        return payload


@dataclass(frozen=True, slots=True)
class GoldItemResult:
    gold_item_id: UUID
    episode_id: UUID
    evaluated: bool
    passed: bool
    checked_labels: int

    def to_payload(self) -> dict[str, object]:
        return {
            "gold_item_id": str(self.gold_item_id),
            "episode_id": str(self.episode_id),
            "evaluated": self.evaluated,
            "passed": self.passed,
            "checked_labels": self.checked_labels,
        }


@dataclass(frozen=True, slots=True)
class GoldEvaluation:
    evaluated_count: int
    passed_count: int
    accuracy: float | None
    status: str
    results: tuple[GoldItemResult, ...] = ()

    @property
    def sample_size(self) -> int:
        return self.evaluated_count

    def to_payload(self) -> dict[str, object]:
        return {
            "evaluated_count": self.evaluated_count,
            "passed_count": self.passed_count,
            "accuracy": self.accuracy,
            "status": self.status,
            "sample_size": self.sample_size,
            "results": [item.to_payload() for item in self.results],
        }


@dataclass(frozen=True, slots=True)
class GoldSet:
    gold_set_id: UUID
    project_id: UUID
    code: str
    schema_id: UUID
    schema_version: int
    schema_sha256: str
    items: tuple[GoldItem, ...]
    status: GoldSetStatus | str = GoldSetStatus.DRAFT
    name: str | None = None
    blind: bool = True
    exposure_policy: GoldSetExposurePolicy | str = GoldSetExposurePolicy.HIDDEN
    max_exposure_per_annotator: int = 1
    rotation_policy: GoldRotationPolicy | str = GoldRotationPolicy.NONE
    usage_policy: Mapping[str, object] = field(default_factory=dict)
    published_at: datetime | None = None
    retired_at: datetime | None = None
    revoked_reason: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    created_by: UUID | None = None
    updated_by: UUID | None = None

    def __post_init__(self) -> None:
        for name in ("gold_set_id", "project_id", "schema_id"):
            required_uuid(getattr(self, name), name)
        object.__setattr__(
            self,
            "code",
            normalized_text(self.code, "code", max_length=CODE_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "name",
            optional_text(self.name, "name", max_length=NAME_MAX_LENGTH),
        )
        object.__setattr__(
            self,
            "schema_version",
            positive_int(self.schema_version, "schema_version"),
        )
        object.__setattr__(
            self,
            "schema_sha256",
            validate_sha256(self.schema_sha256, "schema_sha256"),
        )
        if not isinstance(self.items, list | tuple) or not self.items:
            raise DomainValidationError("黄金集至少需要一个黄金项")
        items: list[GoldItem] = []
        seen: set[UUID] = set()
        for item in self.items:
            gold_item = item if isinstance(item, GoldItem) else GoldItem(**item)  # type: ignore[arg-type]
            if gold_item.gold_item_id in seen:
                raise DomainValidationError("gold_item_id 不得重复")
            seen.add(gold_item.gold_item_id)
            items.append(gold_item)
        object.__setattr__(self, "items", tuple(items))
        object.__setattr__(
            self,
            "status",
            coerce_enum(self.status, GoldSetStatus, "status"),
        )
        if not isinstance(self.blind, bool) or not self.blind:
            raise DomainValidationError("黄金集必须 blind=true")
        object.__setattr__(
            self,
            "exposure_policy",
            coerce_enum(self.exposure_policy, GoldSetExposurePolicy, "exposure_policy"),
        )
        object.__setattr__(
            self,
            "max_exposure_per_annotator",
            positive_int(self.max_exposure_per_annotator, "max_exposure_per_annotator"),
        )
        object.__setattr__(
            self,
            "rotation_policy",
            coerce_enum(self.rotation_policy, GoldRotationPolicy, "rotation_policy"),
        )
        object.__setattr__(
            self,
            "usage_policy",
            frozen_mapping(self.usage_policy, "usage_policy"),
        )
        for name in ("published_at", "retired_at", "created_at", "updated_at"):
            object.__setattr__(self, name, optional_aware_datetime(getattr(self, name), name))
        object.__setattr__(
            self,
            "revoked_reason",
            optional_text(self.revoked_reason, "revoked_reason", max_length=REASON_MAX_LENGTH),
        )
        object.__setattr__(self, "version", positive_int(self.version, "version"))

    @property
    def is_active(self) -> bool:
        return self.status is GoldSetStatus.ACTIVE

    @property
    def is_content_immutable(self) -> bool:
        return self.status in {
            GoldSetStatus.ACTIVE,
            GoldSetStatus.RETIRED,
            GoldSetStatus.REVOKED,
        }

    def assert_schema_compatible(self, schema) -> None:
        if schema.status is not AnnotationSchemaStatus.PUBLISHED:
            raise SchemaNotPublishedError("黄金集必须引用已发布 Schema")
        if (
            schema.schema_id != self.schema_id
            or schema.schema_version != self.schema_version
            or schema.schema_sha256 != self.schema_sha256
        ):
            raise GoldSetSchemaIncompatibleError("黄金集 Schema 快照与当前版本不一致")

    def apply_action(
        self,
        action: object,
        *,
        reason: str | None = None,
        now: datetime | None = None,
    ) -> GoldSet:
        action_name = validate_action_name(action, family="gold_set")
        target = gold_set_target(self.status, action_name)
        occurred_at = now or _now()
        cleaned_reason = optional_text(reason, "reason", max_length=REASON_MAX_LENGTH)
        if action_requires_reason("gold_set", action_name) and cleaned_reason is None:
            raise DomainValidationError("该黄金集动作必须填写原因")
        changes: dict[str, object] = {"status": target, "updated_at": occurred_at}
        if target is GoldSetStatus.PENDING_REVIEW:
            changes["published_at"] = None
        if target is GoldSetStatus.ACTIVE:
            changes["published_at"] = self.published_at or occurred_at
        if target is GoldSetStatus.RETIRED:
            changes["retired_at"] = occurred_at
        if target is GoldSetStatus.REVOKED:
            changes["revoked_reason"] = cleaned_reason
        return replace(self, **changes)

    def assert_content_immutable(self, candidate: GoldSet) -> None:
        if self.is_content_immutable and (
            self.schema_id != candidate.schema_id
            or self.schema_version != candidate.schema_version
            or self.schema_sha256 != candidate.schema_sha256
            or self.blind != candidate.blind
            or [item.redacted_payload() | item.answer_payload() for item in self.items]
            != [item.redacted_payload() | item.answer_payload() for item in candidate.items]
        ):
            raise ResourceImmutableError("已启用黄金集内容不可原地修改")

    def redacted_view(self) -> dict[str, object]:
        """标注员安全视图：绝不包含 expected_labels。"""
        return {
            "gold_set_id": str(self.gold_set_id),
            "project_id": str(self.project_id),
            "code": self.code,
            "name": self.name,
            "status": self.status.value,
            "schema_id": str(self.schema_id),
            "schema_version": self.schema_version,
            "schema_sha256": self.schema_sha256,
            "blind": self.blind,
            "exposure_policy": self.exposure_policy.value,
            "max_exposure_per_annotator": self.max_exposure_per_annotator,
            "rotation_policy": self.rotation_policy.value,
            "items": [item.redacted_payload() for item in self.items],
            "version": self.version,
        }

    def answer_view(self) -> dict[str, object]:
        payload = self.redacted_view()
        payload["items"] = [item.answer_payload() for item in self.items]
        return payload


def evaluate_gold_set(
    gold_set: GoldSet,
    actual_labels_by_episode: Mapping[UUID, Mapping[str, object]],
) -> GoldEvaluation:
    """按 Episode 比较已提交标签；只统计能匹配到黄金项的评估。"""
    results: list[GoldItemResult] = []
    for item in gold_set.items:
        if not item.active:
            continue
        actual = actual_labels_by_episode.get(item.episode_id)
        if actual is None:
            results.append(
                GoldItemResult(
                    gold_item_id=item.gold_item_id,
                    episode_id=item.episode_id,
                    evaluated=False,
                    passed=False,
                    checked_labels=0,
                )
            )
            continue
        checked = 0
        passed = True
        for expected in item.expected_labels:
            if expected.label_key not in actual:
                passed = False
                continue
            checked += 1
            if not expected.matches(actual[expected.label_key]):
                passed = False
        evaluated = checked > 0
        results.append(
            GoldItemResult(
                gold_item_id=item.gold_item_id,
                episode_id=item.episode_id,
                evaluated=evaluated,
                passed=evaluated and passed,
                checked_labels=checked,
            )
        )
    evaluated_results = [item for item in results if item.evaluated]
    passed_count = sum(1 for item in evaluated_results if item.passed)
    evaluated_count = len(evaluated_results)
    if evaluated_count == 0:
        return GoldEvaluation(
            evaluated_count=0,
            passed_count=0,
            accuracy=None,
            status="N/A",
            results=tuple(results),
        )
    return GoldEvaluation(
        evaluated_count=evaluated_count,
        passed_count=passed_count,
        accuracy=passed_count / evaluated_count,
        status="OK",
        results=tuple(results),
    )


__all__ = [
    "ExpectedLabel",
    "GoldEvaluation",
    "GoldItem",
    "GoldItemResult",
    "GoldSet",
    "evaluate_gold_set",
]
