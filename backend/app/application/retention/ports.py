"""保留策略（V51-017）应用层端口。

只定义"要什么事实"，不定义"怎么查"。仓储实现放在 infrastructure；本模块
因此可以被用例层与测试替身共同依赖，不牵出 SQLAlchemy。

## 与既有血缘查询的分工（为什么这里不重建 Lineage）

``app/application/episodes/graph.py`` 的 ``LineageGraphService`` 已经能回答
"某个 RawAsset 的上下游是谁"，但它**算不出**本任务要的三个事实：

1. **「曾冻结」而非「当前 FROZEN」**：图遍历返回节点与边，不返回
   ``dataset_versions.frozen_at``。``SUPERSEDED`` / ``ARCHIVED`` 的冻结版本在
   图里只是普通 ``DATASET_VERSION`` 节点，无法区分"冻结过"与"从未冻结"。
2. **每个版本的 Episode 计数**：图是**分页 + 深度上限 3 + 节点上限 500** 的
   受控遍历。把"某一层有几个 Episode"从一页边里数出来，既受分页影响
   （``next_cursor`` 非空时结果不完整），也会在超过上限时给出 ``truncated``
   的残缺数字。删除阻断**必须**基于完整集合，不能用可能被截断的图。
3. **持锁一致性**：阻断判定与随后的状态写入必须在同一事务、并在持锁下
   读取，否则会出现"查完发现没引用 → 别人冻结 → 我们继续删"的窗口。
   ``LineageGraphService`` 是只读服务，不参与锁协议。

因此本端口的实现走**已存在的索引**直接 join（见仓储文档），不复用图遍历；
但它**不是**第二套血缘系统——不写 ``data_lineage_edges``、不维护节点表、
不提供通用遍历。它只是冻结保护所需的一次定向聚合查询。

反过来，``GET /raw-assets/{id}/lineage`` 继续负责"完整血缘链"，两者互补。

## 并发协议：为什么锁 ``raw_assets`` 行（而不是 ``dataset_versions``）

不变量是「**一个冻结版本不得引用已被销毁内容的资产**」。要让它对并发安全，
两个方向必须争夺**同一把锁**：

- **销毁方**（tombstone / reject）：先 ``SELECT ... FOR UPDATE`` 锁住目标
  ``raw_assets`` 行，**再**在同一事务里重新计算冻结引用；有引用则拒绝。
- **冻结方**：先锁住"本版本将引用到的全部 ``raw_assets`` 行"（这些资产的
  集合可以从该版本的 ``dataset_items → episode_raw_assets`` 算出），**再**
  校验其中没有已销毁内容的资产；有则拒绝冻结。

两者锁的是同一批行，因此在 PostgreSQL 上必然冲突：

- 冻结先持锁 → 销毁方阻塞；冻结提交后销毁方重新读到新冻结版本 → **拒绝删除**；
- 销毁先持锁 → 冻结方阻塞；销毁提交后冻结方读到资产已 TOMBSTONED → **拒绝冻结**。

**为什么不能改锁 ``dataset_versions`` 行**：销毁方无法预先锁住"未来可能引用
这个资产的行"——那些行在并发事务里才产生/才被更新，删除方不可能对不存在的
行加锁。锁定被引用的资产行是唯一可以对两个方向都成立的汇合点。

**死锁预防**：销毁方只锁 1 行；冻结方锁 N 行时按 UUID 升序加锁，因此不存在
"我持 A 等 B、你持 B 等 A"的环。
"""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.domain.identity.models import ProjectScope
from app.domain.retention.models import ReferenceImpact


class ReferenceImpactRepository(Protocol):
    """冻结引用影响查询与锁协议。

    所有实现必须**强制项目作用域**：跨项目的引用既不计入影响，也不泄露
    存在性。查询本身不写任何数据（锁不算写，且只在事务内有效）。
    """

    # ---------- 只读：影响预览 ----------

    def raw_asset_impact(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> ReferenceImpact:
        """``RawAsset`` 被哪些曾冻结的 ``DatasetVersion`` 引用。

        路径：``RawAsset ← EpisodeRawAsset ← Episode ← DatasetItem ←
        DatasetVersion(frozen_at IS NOT NULL)``。

        实现**不做分页**：阻断判定需要完整集合。真实数据里一个资产被引用的
        冻结版本数是可枚举量级（同一资产的多次分段/多版本），不构成规模风险。
        """
        ...

    def processing_artifact_impact(
        self,
        scope: ProjectScope,
        run_id: UUID,
        object_key: str,
    ) -> ReferenceImpact:
        """``ProcessingArtifact`` 的**派生**引用影响（预览用，不阻断）。

        ## 为什么是"派生"而不是权威引用

        当前 schema 里**不存在** ``DatasetVersion → ProcessingArtifact`` 的
        引用边：``dataset_items`` 只有 ``episode_id`` 与 ``raw_asset_ids``
        快照；``processing_artifacts`` 挂在 ``processing_runs`` 上，而
        ``episodes`` / ``dataset_items`` 都没有 ``run_id`` 外键（已 grep 确认）。
        凭空造一条"版本引用了产物"的断言就是伪造血缘。

        因此本方法只报告**可证明的间接关系**：产物行上的 ``source_ref`` 指向
        它由哪个原始对象派生而来；把该对象解析成 RawAsset 后，再问"哪些冻结
        版本引用了这个 RawAsset"。结论如实标注为派生，且**不阻断任何操作**
        ——产物当前没有销毁入口（随运行级联删除，运行无删除用例）。
        """
        ...

    # ---------- 存在性（只读预览用） ----------

    def raw_asset_exists(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> bool:
        """资产是否存在于当前项目（只读，不加锁）。

        预览端点必须先用它确认对象存在：只查引用会返回"0 个引用、可以删"，
        把一个不存在的 ID 渲染成"安全可删"的假结论。
        """
        ...

    # ---------- 事务内：锁 + 状态读取 ----------

    def lock_raw_asset(
        self,
        scope: ProjectScope,
        asset_id: UUID,
    ) -> str | None:
        """锁住单个资产行并返回其**当前**状态；资产不存在返回 ``None``。

        PostgreSQL 用 ``SELECT ... FOR UPDATE``；SQLite 忽略该子句，由其写
        事务本身串行化，语义等价（与 ``SqlRawAssetRepository.get_for_update``
        的既有约定一致）。
        """
        ...

    def lock_raw_assets(
        self,
        scope: ProjectScope,
        asset_ids: tuple[UUID, ...],
    ) -> dict[UUID, str]:
        """按 UUID 升序批量锁定资产行，返回 ``asset_id → status``。

        排序是为了固定加锁顺序、避免与另一条同样顺序的路径互锁成环。
        """
        ...

    def raw_asset_ids_for_version(
        self,
        scope: ProjectScope,
        dataset_version_id: UUID,
    ) -> tuple[UUID, ...]:
        """该数据集版本引用到的全部 RawAsset ID（去重、升序）。

        路径：``DatasetVersion → DatasetItem → Episode → EpisodeRawAsset``。
        冻结方用它在加锁前算出"要锁哪些行"。该版本若尚无条目，返回空元组。
        """
        ...
