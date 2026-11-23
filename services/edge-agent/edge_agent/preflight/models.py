"""Preflight 数据模型：逐项结果、聚合报告、继续决策证据。

``PreflightReport.overall`` 是由 ``results`` 计算出的属性，而不是可写字段：
调用方无法把含 BLOCKED 的报告“改成”可继续。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class CheckStatus(StrEnum):
    PASS = "PASS"  # noqa: S105
    WARN = "WARN"
    BLOCKED = "BLOCKED"


# 聚合严重度：BLOCKED > WARN > PASS。
_SEVERITY = {CheckStatus.PASS: 0, CheckStatus.WARN: 1, CheckStatus.BLOCKED: 2}


@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    message: str
    evidence: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PreflightReport:
    capture_session_id: str
    generated_at: datetime
    results: tuple[CheckResult, ...]

    @property
    def overall(self) -> CheckStatus:
        worst = CheckStatus.PASS
        for result in self.results:
            if _SEVERITY[result.status] > _SEVERITY[worst]:
                worst = result.status
        return worst

    def ids_with(self, status: CheckStatus) -> tuple[str, ...]:
        return tuple(r.check_id for r in self.results if r.status == status)

    def to_document(self) -> dict[str, Any]:
        return {
            "capture_session_id": self.capture_session_id,
            "generated_at": self.generated_at.isoformat(),
            "overall": self.overall.value,
            "results": [
                {
                    "check_id": r.check_id,
                    "status": r.status.value,
                    "message": r.message,
                    "evidence": dict(r.evidence),
                }
                for r in self.results
            ],
        }

    def report_hash(self) -> str:
        raw = json.dumps(
            self.to_document(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ProceedEvidence:
    """“带 WARN 继续”的证据记录：谁、何时、确认了哪些项、对应哪份报告。"""

    evidence_id: str
    capture_session_id: str
    decided_at: datetime
    acknowledged_by: str | None
    acknowledged_check_ids: tuple[str, ...]
    warned_results: tuple[CheckResult, ...]
    report_hash: str

    def to_document(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "capture_session_id": self.capture_session_id,
            "decided_at": self.decided_at.isoformat(),
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_check_ids": list(self.acknowledged_check_ids),
            "warned": [
                {"check_id": r.check_id, "message": r.message, "evidence": dict(r.evidence)}
                for r in self.warned_results
            ],
            "report_hash": self.report_hash,
        }
