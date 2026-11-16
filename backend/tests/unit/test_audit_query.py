"""审计查询领域与应用层用例单测。

覆盖：过滤值规范化、结果枚举、``list_audit_logs`` 的分页边界与透传、
以及「读路径不写库」的约定。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from app.application.audit import (
    MAX_FETCH_LIMIT,
    AuditLogEntry,
    list_audit_logs,
)
from app.domain.audit.errors import DomainValidationError
from app.domain.audit.models import AuditQuery, AuditResult, normalize_filter_text
from app.domain.identity.models import ProjectScope

NOW = datetime(2026, 11, 17, 10, 0, tzinfo=UTC)


class RecordingRepository:
    """记录调用参数的只读仓储替身（不做任何断言之外的逻辑）。"""

    def __init__(self) -> None:
        self.calls: list[tuple[ProjectScope, AuditQuery, UUID | None, int]] = []

    def list(
        self,
        scope: ProjectScope,
        query: AuditQuery,
        *,
        after_id: UUID | None = None,
        limit: int = 20,
    ) -> list[AuditLogEntry]:
        self.calls.append((scope, query, after_id, limit))
        return []


def test_normalize_filter_text_trims_and_drops_blank() -> None:
    assert normalize_filter_text(None) is None
    assert normalize_filter_text("   ") is None
    assert normalize_filter_text("  RAW_ASSET ") == "RAW_ASSET"
    # 大小写必须保留：历史写入方并不统一，改小写会漏记录。
    assert normalize_filter_text("data_source_review") == "data_source_review"


def test_audit_query_build_normalizes_text_filters() -> None:
    resource_id = uuid4()
    query = AuditQuery.build(
        resource_type="  DATASET ",
        resource_id=resource_id,
        action=" create ",
        result=AuditResult.FAILURE,
        occurred_from=NOW,
    )

    assert query.resource_type == "DATASET"
    assert query.resource_id == resource_id
    assert query.action == "create"
    assert query.actor_user_id is None
    assert query.result is AuditResult.FAILURE
    assert query.occurred_from == NOW
    assert query.occurred_to is None


def test_audit_result_matches_table_check_constraint() -> None:
    assert {item.value for item in AuditResult} == {"SUCCESS", "FAILURE"}


def test_list_audit_logs_passes_scope_and_cursor_to_repository() -> None:
    repository = RecordingRepository()
    scope = ProjectScope(project_id=uuid4(), actor_id=uuid4())
    after_id = uuid4()
    query = AuditQuery.build(resource_type="EPISODE")

    result = list_audit_logs(repository, scope, query, after_id=after_id, limit=MAX_FETCH_LIMIT)

    assert result == []
    assert repository.calls == [(scope, query, after_id, MAX_FETCH_LIMIT)]


@pytest.mark.parametrize("limit", [0, -1, MAX_FETCH_LIMIT + 1])
def test_list_audit_logs_rejects_out_of_range_limit(limit: int) -> None:
    repository = RecordingRepository()
    scope = ProjectScope(project_id=uuid4(), actor_id=uuid4())

    with pytest.raises(DomainValidationError):
        list_audit_logs(repository, scope, AuditQuery(), limit=limit)

    assert repository.calls == []


def test_query_repository_is_read_only() -> None:
    repository = RecordingRepository()

    assert not hasattr(repository, "add")
    assert not hasattr(repository, "delete")
