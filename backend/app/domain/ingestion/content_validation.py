"""RawAsset 内容一致性校验规则。

本模块只做纯计算与判定，不读写对象存储、数据库或队列：
- 期望值来自已登记的 RawAsset；
- 实际值由 Worker 流式读取对象存储后计算；
- 判定结果给出稳定隔离码，供应用层落库与审计。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.ingestion.enums import RawAssetQuarantineCode
from app.domain.ingestion.errors import DomainValidationError
from app.domain.ingestion.validation import normalize_sha256


@dataclass(frozen=True, slots=True)
class ContentDigest:
    """流式读取得到的实际内容摘要。"""

    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        normalized = normalize_sha256(self.sha256)
        assert normalized is not None
        object.__setattr__(self, "sha256", normalized)
        if (
            isinstance(self.size_bytes, bool)
            or not isinstance(self.size_bytes, int)
            or self.size_bytes < 0
        ):
            raise DomainValidationError("size_bytes 必须是非负整数")


@dataclass(frozen=True, slots=True)
class ValidationDecision:
    """内容校验判定。

    ``valid`` 为 True 时 ``codes`` 必为空；否则 ``codes`` 至少一项，
    ``reason`` 为可直接写入 ``quarantine_reason`` 的稳定说明。
    """

    valid: bool
    codes: tuple[RawAssetQuarantineCode, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.valid and self.codes:
            raise DomainValidationError("校验通过时不得携带隔离码")
        if not self.valid and not self.codes:
            raise DomainValidationError("校验失败时必须给出隔离码")
        if not self.valid and not self.reason:
            raise DomainValidationError("校验失败时必须给出隔离原因")


def evaluate_content(
    *,
    expected_sha256: str,
    expected_size_bytes: int,
    actual: ContentDigest,
) -> ValidationDecision:
    """比较登记值与流式重算值，返回校验判定。

    大小与 Hash 都按独立条件检查：大小不符时 Hash 通常也不符，
    两个稳定码会同时保留，便于排查“截断/替换”两类问题。
    """
    expected = normalize_sha256(expected_sha256)
    assert expected is not None
    if expected_size_bytes <= 0:
        raise DomainValidationError("expected_size_bytes 必须是正整数")

    codes: list[RawAssetQuarantineCode] = []
    reasons: list[str] = []
    if actual.size_bytes != expected_size_bytes:
        codes.append(RawAssetQuarantineCode.SIZE_MISMATCH)
        reasons.append(
            f"对象大小与登记值不一致：expected={expected_size_bytes}, "
            f"actual={actual.size_bytes}"
        )
    if actual.sha256 != expected:
        codes.append(RawAssetQuarantineCode.HASH_MISMATCH)
        reasons.append("对象 SHA-256 与登记值不一致")

    if not codes:
        return ValidationDecision(valid=True)
    return ValidationDecision(
        valid=False,
        codes=tuple(codes),
        reason="；".join(reasons),
    )


def object_missing_decision() -> ValidationDecision:
    """对象 Key 不存在时的固定判定。"""

    return ValidationDecision(
        valid=False,
        codes=(RawAssetQuarantineCode.OBJECT_MISSING,),
        reason="对象存储中不存在该对象",
    )
