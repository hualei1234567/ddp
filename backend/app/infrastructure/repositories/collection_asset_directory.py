"""采集素材目录适配器：校验任务引用的素材是否同项目存在（COL-002）。

与 ``assignment_directory.py`` 同一职责定位：只回答"该资源在当前项目里存不存在"，
不承载业务规则、不把素材内容读进任务用例。所有查询都带 ``project_id`` 条件，
因此"B 项目的 plan"在 A 项目的 scope 下会返回 ``False``——用 404 表达，
不区分"不存在"与"属于别的项目"，避免泄漏跨项目存在性。

版本一致性检查（``sop_version_exists`` / ``action_spec_version_exists``）判定的是
**被引用那一行自身的版本列**是否等于给定版本，而不是"同一 code 下有没有过这个版本"：
素材升版 = 新增行，任务上冻结的 ``sop_version`` 必须是它所引用的那一行的版本。
若按 code 的版本谱系判定，``sop_id=<v1 行> + sop_version=v2`` 就会被放行，
任务带着"指向 v1 行、冻结 v2 版本"的自相矛盾引用进入执行——这正是要拦的情况。
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.identity.models import ProjectScope
from app.infrastructure.db.models.collection_assets import (
    ActionSpecModel,
    CollectionPlanModel,
    CollectionSopModel,
    ScenarioModel,
)


class SqlCollectionAssetDirectory:
    def __init__(self, session: Session) -> None:
        self._session = session

    def plan_exists(self, scope: ProjectScope, plan_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(CollectionPlanModel.id).where(
                    CollectionPlanModel.id == plan_id,
                    CollectionPlanModel.project_id == scope.project_id,
                )
            )
            is not None
        )

    def sop_exists(self, scope: ProjectScope, sop_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(CollectionSopModel.id).where(
                    CollectionSopModel.id == sop_id,
                    CollectionSopModel.project_id == scope.project_id,
                )
            )
            is not None
        )

    def sop_version_exists(
        self,
        scope: ProjectScope,
        *,
        sop_id: UUID,
        sop_version: str,
    ) -> bool:
        """被引用的那一行 SOP **自身**的 ``sop_version`` 是否等于给定版本。

        只查该 ID 那一行，不按 ``code`` 展开版本谱系：升版是新行，
        ``sop_id=<v1 行> + sop_version=v2`` 必须判为不一致（v2 是另一行），
        否则任务会冻结一个与所引用行不符的版本快照。
        """
        row_version = self._session.scalar(
            select(CollectionSopModel.sop_version).where(
                CollectionSopModel.id == sop_id,
                CollectionSopModel.project_id == scope.project_id,
            )
        )
        if row_version is None:
            return False
        return row_version == sop_version.strip()

    def scenario_exists(self, scope: ProjectScope, scenario_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(ScenarioModel.id).where(
                    ScenarioModel.id == scenario_id,
                    ScenarioModel.project_id == scope.project_id,
                )
            )
            is not None
        )

    def action_spec_exists(self, scope: ProjectScope, spec_id: UUID) -> bool:
        return (
            self._session.scalar(
                select(ActionSpecModel.id).where(
                    ActionSpecModel.id == spec_id,
                    ActionSpecModel.project_id == scope.project_id,
                )
            )
            is not None
        )

    def action_spec_version_exists(
        self,
        scope: ProjectScope,
        *,
        spec_id: UUID,
        spec_version: str,
    ) -> bool:
        """同 :meth:`sop_version_exists`：只比对被引用行自身的版本列。"""
        row_version = self._session.scalar(
            select(ActionSpecModel.spec_version).where(
                ActionSpecModel.id == spec_id,
                ActionSpecModel.project_id == scope.project_id,
            )
        )
        if row_version is None:
            return False
        return row_version == spec_version.strip()
