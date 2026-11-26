"""LLM 训练导出 API 请求/响应 DTO（V52-037）。

字段口径与 ``data-platform/ddp_data/llm_export`` 的领域记录一一对应：
``records[]`` 是一个**跨格式统一形状**的载体，按 ``format`` 解释：

- ``SFT``：需要 ``messages``（OpenAI 兼容消息数组）；
- ``DPO``：需要 ``prompt`` / ``response_a`` / ``response_b`` / ``winner``
  （``winner`` 相对呈现槽 A/B；``presented_order`` 声明槽位与 canonical 的映射），
  可选 ``rationale`` / ``criteria_scores`` / ``confidence``；
- ``PRM``：需要 ``trajectory_id`` / ``task_goal`` / ``steps``，
  可选 ``final_answer_correct``。

统一形状的原因：JSON 无法传领域对象，且三种格式的字段是**互斥语义**而非
同一实体的不同视图；拆成三套判别联合会把「哪个字段属于哪个格式」变成契约里
的三份重复声明。缺字段由应用层按格式校验并返回 422，不复用领域默认值兜底。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.application.export.llm_dataset import MAX_LLM_EXPORT_RECORDS

ReviewStatusLiteral = Literal["PENDING", "APPROVED", "REJECTED"]
QcStatusLiteral = Literal["PENDING", "PASSED", "FAILED"]
MessageRoleLiteral = Literal["system", "user", "assistant", "tool"]
StepLabelLiteral = Literal["CORRECT", "REDUNDANT", "HALLUCINATED_TOOL", "FATAL"]
LlmExportFormatLiteral = Literal["SFT", "DPO", "PRM"]


class LlmExportProvenanceIn(BaseModel):
    """训练记录的血缘与资格凭据；``annotator_refs`` 必须是脱敏引用。"""

    model_config = ConfigDict(extra="forbid")

    dataset_version_id: str = Field(min_length=1, max_length=200)
    sample_id: str = Field(min_length=1, max_length=200)
    submission_id: str = Field(min_length=1, max_length=200)
    schema_version: str = Field(min_length=1, max_length=64)
    review_status: ReviewStatusLiteral
    qc_status: QcStatusLiteral
    guideline_version: str | None = Field(default=None, max_length=200)
    judge_model: str | None = Field(default=None, max_length=200)
    annotator_refs: list[str] = Field(default_factory=list)


class LlmExportMessageIn(BaseModel):
    """OpenAI 兼容消息。"""

    model_config = ConfigDict(extra="forbid")

    role: MessageRoleLiteral
    content: str = Field(min_length=1)


class LlmExportStepIn(BaseModel):
    """PRM 轨迹步骤；``label`` 未标注的步骤不进训练文件（未标注 ≠ 正确）。"""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    action: str = Field(min_length=1, max_length=2000)
    label: StepLabelLiteral | None = None
    tool: str | None = Field(default=None, max_length=200)
    observation_digest: str | None = Field(default=None, max_length=200)


class LlmExportRecordIn(BaseModel):
    """一条待导出记录（统一形状；按 ``format`` 解释所需字段）。"""

    model_config = ConfigDict(extra="forbid")

    provenance: LlmExportProvenanceIn
    #: SFT
    messages: list[LlmExportMessageIn] | None = None
    #: DPO（winner 是呈现槽标签：A / B / TIE）
    prompt: str | None = Field(default=None, min_length=1)
    response_a: str | None = Field(default=None, min_length=1)
    response_b: str | None = Field(default=None, min_length=1)
    winner: Literal["A", "B", "TIE"] | None = None
    rationale: str | None = Field(default=None, max_length=2000)
    criteria_scores: dict[str, float] | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    presented_order: list[Literal["A", "B"]] | None = Field(
        default=None, min_length=2, max_length=2
    )
    #: PRM
    trajectory_id: str | None = Field(default=None, min_length=1, max_length=200)
    task_goal: str | None = Field(default=None, min_length=1, max_length=2000)
    steps: list[LlmExportStepIn] | None = None
    final_answer_correct: bool | None = None


class LlmExportCreate(BaseModel):
    """导出请求：一次只允许一种格式（SFT / 偏好 / PRM 永不混批）。"""

    model_config = ConfigDict(extra="forbid")

    format: LlmExportFormatLiteral
    records: list[LlmExportRecordIn] = Field(
        min_length=1,
        max_length=MAX_LLM_EXPORT_RECORDS,
    )


class LlmExportFileDigest(BaseModel):
    """一个导出产物的摘要（不返回文件内容）。"""

    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    bytes: int = Field(ge=0)


class LlmExportSummaryOut(BaseModel):
    """导出摘要：格式版本、行数、门禁剔除统计与产物哈希。"""

    format: str
    format_version: str
    generator_version: str
    record_count: int
    excluded_counts: dict[str, int]
    files: dict[str, LlmExportFileDigest]


class LlmExportFormatItem(BaseModel):
    format: str
    format_version: str


class LlmExportFormatsOut(BaseModel):
    formats: list[LlmExportFormatItem]


__all__ = [
    "LlmExportCreate",
    "LlmExportFileDigest",
    "LlmExportFormatItem",
    "LlmExportFormatsOut",
    "LlmExportMessageIn",
    "LlmExportProvenanceIn",
    "LlmExportRecordIn",
    "LlmExportStepIn",
    "LlmExportSummaryOut",
]
