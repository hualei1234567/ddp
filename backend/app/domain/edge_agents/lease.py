"""Agent 任务 Lease 规则（FR-003，ADR-0047 §3.4）。

- 同一 CaptureSession 同时只有一个 active Lease（数据库部分唯一索引兜底）；
- **过期不能开始新采集**、不能续租；续租只在 active 时成立；
- 释放必须记录原因；已释放的 Lease 不可再变更。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from uuid import UUID

from app.domain.edge_agents.enums import LeaseReleaseReason, LeaseStatus
from app.domain.edge_agents.errors import DomainValidationError, StateTransitionError
from app.domain.edge_agents.models import clean_reason

RELEASE_NOTE_MAX_LENGTH = 500
MAX_LEASE_DEVICES = 64


@dataclass(frozen=True, slots=True)
class Lease:
    lease_id: UUID
    project_id: UUID
    agent_id: UUID
    session_id: UUID
    device_ids: tuple[UUID, ...]
    acquired_at: datetime
    expires_at: datetime
    last_renewed_at: datetime
    released_at: datetime | None = None
    release_reason: LeaseReleaseReason | None = None
    release_note: str | None = None

    @classmethod
    def acquire(
        cls,
        *,
        lease_id: UUID,
        project_id: UUID,
        agent_id: UUID,
        session_id: UUID,
        device_ids: tuple[UUID, ...],
        now: datetime,
        ttl_seconds: int,
    ) -> Lease:
        if ttl_seconds < 1:
            raise DomainValidationError("Lease TTL 必须为正数")
        if len(device_ids) > MAX_LEASE_DEVICES or len(set(device_ids)) != len(device_ids):
            raise DomainValidationError("device_ids 不得重复，且数量不能超过上限")
        return cls(
            lease_id=lease_id,
            project_id=project_id,
            agent_id=agent_id,
            session_id=session_id,
            device_ids=device_ids,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
            last_renewed_at=now,
        )

    def status(self, now: datetime) -> LeaseStatus:
        if self.released_at is not None:
            return LeaseStatus.RELEASED
        if now >= self.expires_at:
            return LeaseStatus.EXPIRED
        return LeaseStatus.ACTIVE

    def is_active(self, now: datetime) -> bool:
        return self.status(now) is LeaseStatus.ACTIVE

    def ensure_can_start_capture(self, now: datetime) -> None:
        """过期或已释放的 Lease 不能开始新采集。"""
        if not self.is_active(now):
            raise StateTransitionError("Lease 已过期或已释放，不能开始新采集")

    def renew(self, now: datetime, *, ttl_seconds: int) -> Lease:
        """只有 active 的 Lease 才能续租；过期后必须重新申请（可能已被他人持有）。"""
        self.ensure_can_start_capture(now)
        return replace(
            self,
            last_renewed_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )

    def release(
        self,
        now: datetime,
        *,
        reason: LeaseReleaseReason,
        note: str | None = None,
    ) -> Lease:
        """释放必须记原因；允许释放"已过期但未回收"的 Lease（保留 Agent 自报原因）。"""
        if self.released_at is not None:
            raise StateTransitionError("Lease 已释放")
        return replace(
            self,
            released_at=now,
            release_reason=reason,
            release_note=clean_reason_note(note),
        )


def clean_reason_note(note: str | None) -> str | None:
    cleaned = clean_reason(note, required=False)
    if cleaned is not None and len(cleaned) > RELEASE_NOTE_MAX_LENGTH:
        raise DomainValidationError(f"释放备注不能超过 {RELEASE_NOTE_MAX_LENGTH} 个字符")
    return cleaned
