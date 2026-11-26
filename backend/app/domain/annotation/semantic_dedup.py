"""语义近似去重（V52-031，蓝图 G6 / N1 训练集防污染）。

## 为什么做、为什么先做 TRIGRAM

训练集与评测集之间、甚至同一训练集内部，**文本近似重复**会让模型记住样本
而不是学会能力（数据污染）。平台已有数据集域的元数据近重复检测
（``domain/datasets/near_duplicate.py``：时间区间/场景标签/时长），但那针对
Episode 元数据，**不覆盖文本**；G6 需要的是文本级近似去重。

生产 PostgreSQL 16.16 **未安装 pgvector**（``pg_available_extensions``
无 ``vector``），而 **pg_trgm 1.6 可用**。因此本模块把后端做成**可插拔**：

- :attr:`DedupBackend.TRIGRAM`：默认后端，真实可用。PostgreSQL 走
  ``pg_trgm`` + GIN 索引；SQLite（集成测试）走本模块的纯 Python 兜底。
- :attr:`DedupBackend.VECTOR`：**只保留接口**。向量检索在本版本未实现，
  :func:`assert_backend_available` 会显式报错（区分"扩展缺失"与"代码未实现"
  两种原因），**不得静默降级**成"没发现重复"——那会让调用方误以为查过向量。

## 算法与阈值语义

:func:`trigram_similarity` 是**字符 3-gram 的 Jaccard 相似度**（集合语义）：
先按 Unicode 字母数字切词，每个词前补 2 空格、后补 1 空格（对齐 ``pg_trgm``
的补位口径），取全部 3-gram 后算交集/并集。纯 Python、确定性、可单测。

阈值语义**只有一条**：``score >= threshold`` 命中（含边界）。默认
:data:`DEFAULT_THRESHOLD` = 0.85。空文本与空文本视为完全相同（1.0）；
一空一非空视为不相似（0.0）——调用方在 :func:`find_near_duplicates` 里
必须先拒绝空查询文本，避免"空查询命中全部空候选"。

## 能力边界（如实标注）

- Python 兜底按 Unicode alnum 切词，**支持中文**；``pg_trgm`` 对 CJK 的切词
  依 locale 不同，二者分数**不承诺逐位一致**。阈值语义（>= 命中）一致，
  但极端文本（纯 CJK/符号串）可能出现召回差异，需在真实数据上实测调参。
- 本模块只做**文本指纹与相似度判定**，不负责取候选（仓储做有界拉取）、
  不负责向量化（VECTOR 未实现）。
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final
from uuid import UUID

from app.domain.annotation.errors import DomainValidationError
from app.domain.annotation.validation import SHA256_PREFIX, optional_uuid, positive_int

#: 默认命中阈值：0.85 与蓝图 G6 口径一致；可由调用方显式覆盖（API/配置）。
DEFAULT_THRESHOLD: Final[float] = 0.85

#: 单段文本上限（与 ``episodes.language_instruction`` 的 String(2000) 同口径）。
MAX_TEXT_LENGTH: Final[int] = 2000

#: 相似度小数位：对外分数统一 6 位，避免浮点尾巴污染契约与断言。
SCORE_DECIMALS: Final[int] = 6

#: 候选窗口默认值：一次查询最多比较这么多条既有样本。
#: 近似去重是 **有界召回**：窗口外的候选不参与比较（禁止全表扫描），
#: 这是能力边界而不是缺陷——需要更大窗口时由调用方显式调大，仍受上限约束。
DEFAULT_CANDIDATE_LIMIT: Final[int] = 200

#: 候选窗口硬上限：单请求比较量封顶，防止"调大 limit"变成全表扫描。
MAX_CANDIDATE_LIMIT: Final[int] = 500

#: ``sha256:`` + 64 位十六进制的总长度（与库内既有摘要列同口径）。
SHA256_TEXT_LENGTH: Final[int] = len(SHA256_PREFIX) + 64

#: 词内字符判定：Unicode 字母或数字。中文汉字 ``isalnum()`` 为真，因此
#: 纯中文连续串会被当成一个"词"，产出可用的字符 3-gram（与 pg_trgm 有差异，
#: 见模块文档的能力边界）。
_MIN_TRIGRAM_LENGTH: Final[int] = 3

#: pg_trgm 的词边界补位：每个词前补 2 个空格、后补 1 个空格。
_LEFT_PAD: Final[str] = "  "
_RIGHT_PAD: Final[str] = " "


class DedupBackend(StrEnum):
    """近似去重后端（可插拔，禁止静默降级）。"""

    TRIGRAM = "TRIGRAM"
    VECTOR = "VECTOR"


class DedupBackendUnavailableError(DomainValidationError):
    """请求的去重后端不可用（原因在 ``details`` 里结构化给出）。

    继承 :class:`DomainValidationError` 而非新造错误码：阶段 3 标注域错误码
    注册表已随 DDP-323 冻结，为一次只读能力新增冻结表外的码会让客户端契约
    漂移。API 层按 ``validation_failed`` 回 422，``details`` 里带
    ``backend`` / ``reason`` 便于排障（不含任何客户端输入回显）。
    """

    def __init__(
        self,
        message: str,
        *,
        backend: DedupBackend,
        reason: str,
        has_vector_extension: bool | None = None,
    ) -> None:
        details: dict[str, object] = {"backend": backend.value, "reason": reason}
        if has_vector_extension is not None:
            details["has_vector_extension"] = has_vector_extension
        super().__init__(message, details=details)


def coerce_backend(value: object) -> DedupBackend:
    """把外部输入收敛成 :class:`DedupBackend`；未知值显式失败。"""
    if isinstance(value, DedupBackend):
        return value
    try:
        return DedupBackend(str(value))
    except ValueError as exc:
        allowed = ", ".join(item.value for item in DedupBackend)
        raise DomainValidationError(f"backend 必须是 {allowed} 之一") from exc


def assert_backend_available(
    backend: DedupBackend | str,
    has_vector_extension: bool,
) -> None:
    """确认后端可用；不可用时**抛出明确原因**，绝不返回"看起来可用"。

    - ``TRIGRAM``：真实可用（PG 需 pg_trgm 扩展，由迁移 0084 安装；
      扩展可用性由仓储侧探测后传入，这里不做 DB 探测）。
    - ``VECTOR``：本版本只有接口，没有实现。扩展缺失与扩展存在但代码未实现
      是两种不同故障，必须给不同原因——否则运维无法判断该装扩展还是该升级。

    返回 ``None``；调用方无需接收返回值（失败即异常）。
    """
    resolved = coerce_backend(backend)
    if resolved is DedupBackend.TRIGRAM:
        return
    if not has_vector_extension:
        raise DedupBackendUnavailableError(
            "VECTOR 后端不可用：数据库未安装 pgvector 扩展"
            "（pg_available_extensions 无 vector），无法进行向量检索",
            backend=DedupBackend.VECTOR,
            reason="extension_missing",
            has_vector_extension=False,
        )
    raise DedupBackendUnavailableError(
        "VECTOR 后端不可用：pgvector 扩展存在，但本版本尚未实现向量检索"
        "（接口预留，不提供向量能力）",
        backend=DedupBackend.VECTOR,
        reason="not_implemented",
        has_vector_extension=True,
    )


def normalize_text(value: object, field_name: str = "text") -> str:
    """规范化一段待比较文本：去首尾空白、把连续空白压成单个空格。

    只做**结构规范化**，不改大小写、不做同义词/纠错——相似度算法的输入
    必须可解释，业务语义（同义词等）不在本层职责内。
    """
    if not isinstance(value, str):
        raise DomainValidationError(f"{field_name} 必须是字符串")
    text = " ".join(value.split())
    if not text:
        raise DomainValidationError(f"{field_name} 不能为空")
    if len(text) > MAX_TEXT_LENGTH:
        raise DomainValidationError(f"{field_name} 长度不得超过 {MAX_TEXT_LENGTH} 个字符")
    return text


def text_digest(value: object) -> str:
    """文本的稳定指纹：``sha256:`` + 规范化文本的 SHA-256 十六进制。

    指纹用于**完全重复的精确判定**与库内去重列（``episodes.text_digest``）；
    近似判定仍走 :func:`trigram_similarity`，两者职责不同。
    """
    normalized = normalize_text(value)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"{SHA256_PREFIX}{digest}"


def _trigram_set(text: str) -> frozenset[str]:
    """按 ``pg_trgm`` 口径切出字符 3-gram 集合（小写、按 alnum 分词、补位）。"""
    lowered = text.lower()
    grams: set[str] = set()
    word: list[str] = []
    for char in lowered:
        if char.isalnum():
            word.append(char)
            continue
        if word:
            grams.update(_word_trigrams("".join(word)))
            word = []
    if word:
        grams.update(_word_trigrams("".join(word)))
    return frozenset(grams)


def _word_trigrams(word: str) -> set[str]:
    padded = f"{_LEFT_PAD}{word}{_RIGHT_PAD}"
    return {padded[index : index + _MIN_TRIGRAM_LENGTH] for index in range(len(padded) - 2)}


def trigram_similarity(left: str, right: str) -> float:
    """字符 3-gram 的 Jaccard 相似度，范围 ``[0.0, 1.0]``。

    - 两侧完全相同（含两侧都是空文本）→ ``1.0``；
    - 一侧为空、另一侧非空 → ``0.0``（没有可比较的 3-gram）；
    - 其余 → ``|A∩B| / |A∪B|``。

    这是**纯函数**：不读库、不用扩展、不依赖 locale，SQLite 集成测试与
    PostgreSQL 的 ``pg_trgm.similarity()`` 是同类算法（分数可能略有差异）。
    """
    left_grams = _trigram_set(left)
    right_grams = _trigram_set(right)
    if not left_grams and not right_grams:
        return 1.0
    if not left_grams or not right_grams:
        return 0.0
    return len(left_grams & right_grams) / len(left_grams | right_grams)


def validate_threshold(value: object) -> float:
    """校验阈值：必须是 ``[0, 1]`` 内的数值；边界语义为 ``>= threshold`` 命中。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise DomainValidationError("threshold 必须是 0 到 1 之间的数值")
    threshold = float(value)
    if not 0.0 <= threshold <= 1.0:
        raise DomainValidationError("threshold 必须落在 [0, 1] 区间内")
    return threshold


def validate_candidate_limit(value: object) -> int:
    """校验候选窗口：正整数且不超过 :data:`MAX_CANDIDATE_LIMIT`。

    窗口是"有界召回"的硬边界；校验放在领域层，仓储与应用层共用同一份规则，
    避免两侧上限不一致导致"API 放过、SQL 封顶"的静默截断。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError("limit 必须是正整数")
    limit = positive_int(value, "limit")
    if limit > MAX_CANDIDATE_LIMIT:
        raise DomainValidationError(f"limit 不得超过 {MAX_CANDIDATE_LIMIT}")
    return limit


def _resolve_threshold(threshold: float) -> float:
    return validate_threshold(threshold)


@dataclass(frozen=True, slots=True)
class NearDuplicateCandidate:
    """一条候选（通常是已存在的训练/标注样本文本）及其相似度分数。

    ``score`` 是**与本函数查询文本**的 trigram 相似度，由
    :func:`find_near_duplicates` 计算后回填；仓储侧拉取候选时先给 ``0.0``。
    ``episode_id`` 是候选的样本载体引用（只读追溯用，可空以兼容非 Episode 载体）。
    """

    text_digest: str
    text: str
    score: float = 0.0
    episode_id: UUID | None = None

    def __post_init__(self) -> None:
        digest = self.text_digest
        if not isinstance(digest, str) or not digest.strip():
            raise DomainValidationError("text_digest 不能为空")
        digest = digest.strip()
        if len(digest) > SHA256_TEXT_LENGTH:
            raise DomainValidationError("text_digest 长度非法")
        object.__setattr__(self, "text_digest", digest)
        object.__setattr__(self, "text", normalize_text(self.text, "candidate.text"))
        score = self.score
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise DomainValidationError("score 必须是数值")
        score_value = float(score)
        if not 0.0 <= score_value <= 1.0:
            raise DomainValidationError("score 必须落在 [0, 1] 区间内")
        object.__setattr__(self, "score", round(score_value, SCORE_DECIMALS))
        object.__setattr__(self, "episode_id", optional_uuid(self.episode_id, "episode_id"))

    @classmethod
    def from_text(
        cls,
        text: str,
        *,
        score: float = 0.0,
        episode_id: UUID | None = None,
    ) -> NearDuplicateCandidate:
        """由文本直接构造候选（指纹现算），供仓储/测试复用。"""
        return cls(
            text_digest=text_digest(text),
            text=text,
            score=score,
            episode_id=episode_id,
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "text_digest": self.text_digest,
            "text": self.text,
            "score": self.score,
            "episode_id": None if self.episode_id is None else str(self.episode_id),
        }


def find_near_duplicates(
    new_text: str,
    candidates: Iterable[NearDuplicateCandidate],
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[NearDuplicateCandidate, ...]:
    """返回与 ``new_text`` 近似重复的候选，按分数降序（纯函数、确定性）。

    判定规则：对每条候选**重新计算** :func:`trigram_similarity`，保留
    ``score >= threshold`` 的（含边界，:func:`validate_threshold` 保证阈值
    语义只有一条）；候选自带的 ``score`` 不参与判定，避免仓储侧的 DB 估算
    与领域结论不一致。

    排序：分数降序；同分时按 ``text_digest`` 升序、再按 ``episode_id`` 升序，
    保证同一输入永远同一输出（便于断言与复现）。

    空查询文本、超长文本、非法阈值一律抛 :class:`DomainValidationError`：
    静默返回空列表会把"输入不合法"伪装成"没有重复"，那是更危险的错误。
    """
    normalized = normalize_text(new_text)
    resolved_threshold = _resolve_threshold(threshold)
    matched: list[NearDuplicateCandidate] = []
    for candidate in candidates:
        score = trigram_similarity(normalized, candidate.text)
        if score < resolved_threshold:
            continue
        matched.append(replace(candidate, score=round(score, SCORE_DECIMALS)))
    matched.sort(key=lambda item: (-item.score, item.text_digest, str(item.episode_id or "")))
    return tuple(matched)


__all__ = [
    "DEFAULT_CANDIDATE_LIMIT",
    "DEFAULT_THRESHOLD",
    "DedupBackend",
    "DedupBackendUnavailableError",
    "MAX_CANDIDATE_LIMIT",
    "MAX_TEXT_LENGTH",
    "NearDuplicateCandidate",
    "SCORE_DECIMALS",
    "SHA256_TEXT_LENGTH",
    "assert_backend_available",
    "coerce_backend",
    "find_near_duplicates",
    "normalize_text",
    "text_digest",
    "trigram_similarity",
    "validate_candidate_limit",
    "validate_threshold",
]
