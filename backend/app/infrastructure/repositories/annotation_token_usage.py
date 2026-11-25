"""Token 成本台账 SQLAlchemy 仓储。

独立成文件的理由：``annotation_ai_suggestions.py`` 与本表没有共享状态，
且后者明确记录了"逼近拆分阈值时分文件"的先例；台账的读写（登记计量行、
按项目聚合）与建议/提交/复核用例无关。

写路径（``record_many``）把领域 :class:`TokenUsageRecord` 映射成 ORM 行，
与调用方同事务提交（fail-closed：记账失败则整个登记回滚）。
同时提供 :class:`SqlTokenUsageRecorder`——应用层
``TokenUsageRecorder`` 协议的 SQLAlchemy 实现，供依赖工厂按配置注入。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.application.annotation.ai_suggestions import TokenUsageRecorder
from app.domain.annotation.token_usage import TokenUsageRecord
from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.annotation_ai_token_usage import AiTokenUsage as _Model


class SqlAiTokenUsageRepository:
    """``ai_token_usage`` 的写入仓储；聚合读取走 application/ops 查询。"""

    def __init__(self, session: Session) -> None:
        self._session = session

    def record_many(self, scope: ProjectScope, records: tuple[TokenUsageRecord, ...]) -> None:
        """登记一批计量行（同事务，不提交——由调用方的 UoW 统一提交）。"""
        now = datetime.now(UTC)
        for record in records:
            scope.ensure(record.project_id)
            self._session.add(
                _Model(
                    id=record.usage_id,
                    project_id=record.project_id,
                    model_id=record.model_id,
                    model_version=record.model_version,
                    prompt_version=record.prompt_version,
                    purpose=record.purpose.value,
                    input_tokens=record.input_tokens,
                    output_tokens=record.output_tokens,
                    cost_estimate=record.cost_estimate,
                    occurred_at=record.occurred_at,
                    annotation_id=record.annotation_id,
                    version=1,
                    created_at=record.created_at or now,
                    updated_at=record.created_at or now,
                    created_by=scope.actor_id,
                    updated_by=scope.actor_id,
                )
            )


class SqlTokenUsageRecorder(TokenUsageRecorder):
    """把计量行写入请求级 Session 的 recorder；与业务写入同事务提交。"""

    def __init__(self, session: Session) -> None:
        self._repo = SqlAiTokenUsageRepository(session)

    def record(self, scope: ProjectScope, records: tuple[TokenUsageRecord, ...]) -> None:
        self._repo.record_many(scope, records)


__all__ = ["SqlAiTokenUsageRepository", "SqlTokenUsageRecorder"]
