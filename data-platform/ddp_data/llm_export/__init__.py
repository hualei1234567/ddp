"""SFT / 偏好对齐训练导出的领域模型与训练资格门禁（V52-015/016）。

**红线**（``docs/roadmap/v5.2/design-plan.md`` §4.4）：
1. 未通过审核的标注**绝不**导出为训练数据——由 :func:`partition_by_eligibility` 强制；
2. 每条记录携带 Provenance（来源数据集版本 / 样本 / 提交 / 审核结论），
   训练文件本体保持干净，Provenance 落在 sidecar；
3. 偏好 A/B 位置无关——winner 必须以 canonical（chosen/rejected）语义落盘；
4. SFT 与偏好数据**永不混批**。
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

GENERATOR_VERSION = "ddp.llm-export/1.0.0"
SFT_FORMAT = "SFT_MESSAGES_JSONL"
DPO_FORMAT = "PREFERENCE_DPO_JSONL"
FORMAT_VERSION = "1.0.0"

_ALLOWED_ROLES: frozenset[str] = frozenset({"system", "user", "assistant", "tool"})
_MAX_MESSAGES = 256
_MAX_TEXT_CHARS = 100_000


class LlmExportError(ValueError):
    """LLM 训练导出的领域错误。"""


class ReviewStatus(StrEnum):
    """审核结论（与后端 annotation 域枚举值对齐，此处仅保留导出关心的子集）。"""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class QcStatus(StrEnum):
    """质检结论。"""

    PENDING = "PENDING"
    PASSED = "PASSED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class Provenance:
    """一条训练记录的血缘与审核凭据。

    ``annotator_refs`` 必须是**已脱敏**的稳定引用（如哈希），不得携带原始身份。
    """

    dataset_version_id: str
    sample_id: str
    submission_id: str
    review_status: ReviewStatus
    qc_status: QcStatus
    schema_version: str
    guideline_version: str | None = None
    judge_model: str | None = None
    annotator_refs: tuple[str, ...] = ()
    #: 便捷别名（允许 review=/qc= 构造，与调用方惯例对齐；优先于显式字段）
    review: ReviewStatus | None = None
    qc: QcStatus | None = None

    def __post_init__(self) -> None:
        if self.review is not None:
            object.__setattr__(self, "review_status", self.review)
        if self.qc is not None:
            object.__setattr__(self, "qc_status", self.qc)
        for name in (
            "dataset_version_id",
            "sample_id",
            "submission_id",
            "schema_version",
        ):
            if not str(getattr(self, name)).strip():
                raise LlmExportError(f"Provenance.{name} 不能为空")
        if self.annotator_refs and any(not ref.strip() for ref in self.annotator_refs):
            raise LlmExportError("Provenance.annotator_refs 存在空引用")


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """OpenAI 兼容消息结构。"""

    role: str
    content: str

    def __post_init__(self) -> None:
        if self.role not in _ALLOWED_ROLES:
            raise LlmExportError(f"非法消息角色 {self.role!r}：允许 {_ALLOWED_ROLES}")
        if not self.content.strip():
            raise LlmExportError("消息 content 不能为空")
        if len(self.content) > _MAX_TEXT_CHARS:
            raise LlmExportError(f"消息内容超长（>{_MAX_TEXT_CHARS}）")


def _messages_from(raw: Sequence[Mapping[str, object]]) -> tuple[ChatMessage, ...]:
    if not raw:
        raise LlmExportError("messages 不能为空")
    if len(raw) > _MAX_MESSAGES:
        raise LlmExportError(f"messages 超过上限 {_MAX_MESSAGES}")
    return tuple(ChatMessage(str(m["role"]), str(m["content"])) for m in raw)


@dataclass(frozen=True, slots=True)
class SftConversationRecord:
    """SFT 监督样本：完整多轮对话。"""

    provenance: Provenance
    messages: tuple[ChatMessage, ...]

    @classmethod
    def from_raw(
        cls, provenance: Provenance, messages: Sequence[Mapping[str, object]]
    ) -> "SftConversationRecord":
        return cls(provenance=provenance, messages=_messages_from(messages))


@dataclass(frozen=True, slots=True)
class PreferenceRecord:
    """偏好对齐样本：canonical 语义的 chosen/rejected（与呈现位置无关）。

    ``presented_order`` 只用于复盘人工标注时的呈现序列，导出时以
    ``winner`` 映射回 canonical，保证 A/B 翻转不影响训练数据。
    """

    provenance: Provenance
    prompt: str | tuple[ChatMessage, ...]
    chosen: str | tuple[ChatMessage, ...]
    rejected: str | tuple[ChatMessage, ...]
    rationale: str | None = None
    criteria_scores: Mapping[str, float] | None = None
    confidence: float | None = None
    presented_order: tuple[str, str] = ("A", "B")
    #: True 表示平局判定（TIE）：chosen/rejected 原样保留，导出门禁按
    #: ``PREFERENCE_TIE`` 剔除——TIE 样本不构成偏好对，混入会教坏奖励模型。
    is_tie: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.prompt, (str, tuple)) or not self._nonempty(self.prompt):
            raise LlmExportError("prompt 不能为空")
        for name in ("chosen", "rejected"):
            if not self._nonempty(getattr(self, name)):
                raise LlmExportError(f"{name} 不能为空")
        if self.presented_order != ("A", "B") and self.presented_order != ("B", "A"):
            raise LlmExportError("presented_order 只能是 ('A','B') 或 ('B','A')")
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            raise LlmExportError("confidence 必须在 [0,1]")
        if self.criteria_scores is not None:
            for key, value in self.criteria_scores.items():
                if not key.strip() or not isinstance(value, (int, float)):
                    raise LlmExportError("criteria_scores 必须是非空键 -> 数值")

    @staticmethod
    def _nonempty(value: str | tuple) -> bool:
        if isinstance(value, str):
            return bool(value.strip())
        return len(value) > 0

    @classmethod
    def from_raw(
        cls,
        provenance: Provenance,
        *,
        prompt: str | Sequence[Mapping[str, object]],
        response_a: str | Sequence[Mapping[str, object]],
        response_b: str | Sequence[Mapping[str, object]],
        winner: str,
        rationale: str | None = None,
        criteria_scores: Mapping[str, float] | None = None,
        confidence: float | None = None,
        presented_order: tuple[str, str] = ("A", "B"),
    ) -> "PreferenceRecord":
        """按标注侧视角构造：``winner`` 相对**呈现顺序**，内部归一到 canonical。

        支持显式 ``TIE``：构造成功，但导出门禁会按 ``PREFERENCE_TIE`` 剔除。
        """
        # 呈现位置 -> canonical 槽位映射：presented_order[k] 是呈现槽 k
        # （"A"/"B"）里展示的 canonical 响应名。winner 指呈现槽标签，
        # 必须映射回 canonical——A/B 翻转不影响导出语义（设计 §4.4 红线 3）。
        presented = dict(zip(("A", "B"), presented_order))
        if winner == "TIE":
            chosen_raw, rejected_raw = response_a, response_b
        elif winner in ("A", "B"):
            chosen_slot = presented[winner]
            rejected_slot = "B" if chosen_slot == "A" else "A"
            slots = {"A": response_a, "B": response_b}
            chosen_raw = slots[chosen_slot]
            rejected_raw = slots[rejected_slot]
        else:
            raise LlmExportError(f"winner 只能是 A/B/TIE，收到 {winner!r}")

        prompt_v: str | tuple[ChatMessage, ...] = (
            str(prompt) if isinstance(prompt, str) else _messages_from(prompt)
        )
        chosen_v: str | tuple[ChatMessage, ...] = (
            str(chosen_raw)
            if isinstance(chosen_raw, str)
            else _messages_from(chosen_raw)
        )
        rejected_v: str | tuple[ChatMessage, ...] = (
            str(rejected_raw)
            if isinstance(rejected_raw, str)
            else _messages_from(rejected_raw)
        )
        return cls(
            provenance=provenance,
            prompt=prompt_v,
            chosen=chosen_v,
            rejected=rejected_v,
            rationale=rationale,
            criteria_scores=criteria_scores,
            confidence=confidence,
            presented_order=presented_order,
            is_tie=(winner == "TIE"),
        )


def assert_single_kind(
    sft_records: Sequence[SftConversationRecord] | None,
    preference_records: Sequence[PreferenceRecord] | None,
) -> None:
    """红线 4：SFT 与偏好数据不得混批。调用方一次只能导出一类。"""
    if sft_records and preference_records:
        raise LlmExportError("SFT 与偏好对齐数据不得混入同一次导出")
    if not sft_records and not preference_records:
        raise LlmExportError("必须提供 SFT 或偏好记录之一")


class ExclusionReason(StrEnum):
    """训练资格门禁的剔除原因码（逐条可解释）。"""

    REVIEW_NOT_APPROVED = "REVIEW_NOT_APPROVED"
    QC_FAILED = "QC_FAILED"
    QC_PENDING = "QC_PENDING"
    PREFERENCE_TIE = "PREFERENCE_TIE"


@dataclass(frozen=True, slots=True)
class EligibilityReport:
    """门禁分流结果：eligible 进入导出，excluded 每条带原因码。"""

    eligible: tuple[Provenance, ...] = ()
    excluded: tuple[tuple[Provenance, ExclusionReason], ...] = ()

    @property
    def excluded_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for _, reason in self.excluded:
            counts[reason.value] = counts.get(reason.value, 0) + 1
        return counts


def _check_eligibility(prov: Provenance) -> ExclusionReason | None:
    if prov.review_status is not ReviewStatus.APPROVED:
        return ExclusionReason.REVIEW_NOT_APPROVED
    if prov.qc_status is QcStatus.FAILED:
        return ExclusionReason.QC_FAILED
    if prov.qc_status is QcStatus.PENDING:
        return ExclusionReason.QC_PENDING
    return None


def partition_by_eligibility(
    records: Sequence[SftConversationRecord | PreferenceRecord],
) -> EligibilityReport:
    """按红线 1 分流：未过审核 / 质检不合格的一律剔除，绝不静默降级。"""
    eligible: list[Provenance] = []
    excluded: list[tuple[Provenance, ExclusionReason]] = []
    for record in records:
        # TIE 在资格门禁前判定：平局样本无论审核状态如何都不进偏好导出。
        if getattr(record, "is_tie", False):
            excluded.append((record.provenance, ExclusionReason.PREFERENCE_TIE))
            continue
        reason = _check_eligibility(record.provenance)
        if reason is None:
            eligible.append(record.provenance)
        else:
            excluded.append((record.provenance, reason))
    return EligibilityReport(eligible=tuple(eligible), excluded=tuple(excluded))


def stable_text_digest(*parts: object) -> str:
    """对任意部分做稳定内容摘要（Provenance 脱敏引用的基础件）。"""
    hasher = hashlib.sha256()
    for part in parts:
        hasher.update(repr(part).encode("utf-8"))
        hasher.update(b"\x1f")
    return "sha256:" + hasher.hexdigest()
