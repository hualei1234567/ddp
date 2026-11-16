"""evaluations.baseline_dataset_version_id —— 跨数据集基线对比的可追溯信息

Revision ID: 0041
Revises: 0040
Create Date: 2026-11-16

背景（N6 剩余能力）
------------------
``app/application/modelops/evaluations.py`` 原先强制基线评测必须与当前评测用
**同一个数据集版本**。这挡住了"模型升级后在不同数据集上对比"这一正常场景，
但直接放开更危险：两次评测的任务语义可能不同（一个测 pick、一个测 place），
系统却会一本正经地给出"提升 12%"。

放开后的对比因此必须可追溯：结论要能回答"基线是哪个数据集版本、当前是哪个"。

为什么不靠 ``baseline_evaluation_id`` 现查基线行就够了
------------------------------------------------------
能查：``baseline_evaluation_id`` JOIN 回 ``evaluations`` 就能拿到基线行的
``dataset_version_id``，而后者在被评测成功后由不可变守卫保护
（``_IMMUTABLE_WHEN`` 已含 ``dataset_version_id``）。所以本列**不是**唯一真相，
是刻意冗余的事实快照。三个理由：

1. **领域字段完整**：领域实体 ``Evaluation`` 目前只有 ``baseline_evaluation_id``，
   要判断"这次对比跨不跨数据集"必须在应用层再查一次基线行——这是用例编排层的
   认知负担，且很容易在"只看字段就够了"的地方漏掉；
2. **契约可直接表达**：客户端要区分"同数据集复测"与"跨数据集对比"，
   每次都要自己 JOIN 一次基线才答得出来；一个字段就能读完；
3. **语义锚定**：本列描述的是"创建这条评测时对比的是哪个数据集版本"，
   与 ``baseline_evaluation_id`` 指向的行各自独立演进（基线行可能被归档等），
   记录当时的取值比每次推断更明确。

风险与代价（如实说明）：冗余就有不一致的可能。本次用**配对 CHECK**把
"只有一半"排除掉，并在应用层构造时同步写入两者；但没有任何约束能保证
"本列 == 基线行的 dataset_version_id"——因为跨数据集对比放开后它们**本就允许
不同**，这正是本列存在的意义。

列语义
------
- ``baseline_dataset_version_id``：创建本评测时，基线评测所用数据集版本的快照；
- **成对约束**：``(baseline_evaluation_id IS NULL) = (baseline_dataset_version_id IS NULL)``。
  只有基线 ID 没有数据集版本，等于留下半截引用（与迁移 0040 对 ``sop_version``
  的口径一致）；反过来有数据集版本却没有基线更无从解释；
- 同数据集版本对比时该值等于 ``dataset_version_id``，不引入第二套语义；
- 可空：无基线的评测合法，历史行（本迁移前创建）也为 NULL。

两条建库路径的不同（为什么必须幂等）
------------------------------------
``0021_stage5_modelops.py`` 用**活的 ORM 表** ``EvaluationRow.__table__.create()``
建表。给 ORM 模型新增本列后：

1. **全新库**：升级到 0021 时该列（与配对 CHECK）已随 ORM 元数据建出，
   0041 必须是空操作——否则 ``duplicate column`` 让全新库升级直接失败；
2. **存量库**：0021 早已执行完，表里没有该列，必须 ``add_column``。

因此 upgrade/downgrade 都以 ``inspector`` 的**实际**结构为准，两个方向都幂等。
这是迁移 0038 已经踩过并写进文档的同一条路径分叉。

为什么用 ``batch_alter_table`` 加 CHECK（SQLite 的硬约束）
----------------------------------------------------------
SQLite 不支持 ``ALTER TABLE ... ADD CONSTRAINT``。``recreate="auto"`` 在 SQLite
上走"建新表 + 拷贝 + 换名"，在 PostgreSQL 上走原生 ``ADD CONSTRAINT``，同一份
代码覆盖两方言，避免"本地绿、真实 PG 炸"。

加列本身用 ``op.add_column`` 而不是重建整表：重建会连带搬动 ``evaluations``
上的四个守卫触发器与两条外键，风险与收益不成比例（0038 已就此说明过）。
只有加 CHECK 这一步在 SQLite 上无法回避重建。

⚠️ 实测陷阱：重建会**静默删掉** ``evaluations`` 的全部守卫触发器
------------------------------------------------------------------------
``batch_alter_table`` 在 SQLite 上是 ``DROP TABLE`` + ``CREATE TABLE`` +
``RENAME``。SQLite 没有 ``ALTER TABLE ADD CONSTRAINT``，只能在内存里重建整表，
而 ``DROP TABLE`` 会连带删除表上所有触发器，`alembic` 不会替你建回来。
实测（本迁移落地时复现）：升级完成后 ``trg_evaluations_initial_status`` /
``trg_evaluations_transition`` / ``trg_evaluations_no_delete`` /
``trg_evaluations_immutable`` **全部消失**——状态机、不可变字段、禁删三道
数据库兜底同时失效，而 API 测试全绿（它们不检查触发器是否存在）。

因此升级前先 ``drop_modelops_guards``、重建后 ``install_modelops_guards``
装回。守卫模块是既有实现（``Base.metadata`` 的 ``after_create`` 与迁移 0021
共用同一份 DDL），不在这里另写一份——与迁移 0040 处理 ``collection_tasks``
重建的做法一致。``downgrade`` 同样需要，否则回滚同样留下无守卫的表。

回滚
----
``downgrade`` 先删 CHECK 再删列，是 upgrade 的严格逆操作。删列时不需要额外拆
守卫做特殊处理：本次**没有**把新列加进 ``_IMMUTABLE_WHEN``，触发器正文不引用
它（对照 0038：那里把新列加进了不可变清单，因此删列前必须先 drop 守卫，
否则 SQLite 在 ``DROP COLUMN`` 时校验触发器正文会直接报错）。但删 CHECK 同样
要走 batch 重建，所以守卫照样要拆装一轮。

回滚**丢弃**已写入的基线数据集版本快照。它们是可由 ``baseline_evaluation_id``
重新查得的派生记录，不是唯一真相来源。
"""

from __future__ import annotations

# ruff: noqa: S608 - 回填/清理语句里的表名与列名只来自本模块常量，
# 不接收任何外部输入；与迁移 0040 的既有做法一致。
import logging

import sqlalchemy as sa
from alembic import op
from app.infrastructure.db.modelops_guards import (
    drop_modelops_guards,
    install_modelops_guards,
)

revision = "0041"
down_revision = "0040"
branch_labels = None
depends_on = None

logger = logging.getLogger("ddp.migration.0041")

_TABLE = "evaluations"
_COLUMN = "baseline_dataset_version_id"
_CHECK = "ck_evaluations_baseline_pair"
# 与领域实体 ``Evaluation.__post_init__`` 同一语义；库层是防绕过应用层的兜底。
_PAIR_EXPRESSION = "(baseline_evaluation_id IS NULL) = (baseline_dataset_version_id IS NULL)"


def _columns() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(_TABLE)}


def _check_names() -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {item["name"] for item in inspector.get_check_constraints(_TABLE)}


def upgrade() -> None:
    if _COLUMN not in _columns():
        op.add_column(_TABLE, sa.Column(_COLUMN, sa.Uuid(), nullable=True))
    if _CHECK in _check_names():
        # 全新库路径：0021 按 ORM 元数据建表时已带出该 CHECK，无需重建。
        # 全新库也没有存量行，回填无意义，直接返回。
        return
    connection = op.get_bind()
    # 回填必须发生在**拆守卫之后**：存量里有已 ``SUCCEEDED`` 的评测，而
    # ``trg_evaluations_immutable`` 禁止改动它们的不可变列（本次把
    # ``baseline_evaluation_id`` 也纳入了清单）。实测在守卫在线时回填会直接
    # 报 ``evaluations_immutable`` 让升级失败。
    drop_modelops_guards(connection)
    _backfill_baseline_dataset_version()
    with op.batch_alter_table(_TABLE, recreate="auto") as batch:
        # ``op.f()`` 表示"名字已按命名约定展开，不要再拼前缀"。
        # 不加它，batch 模式会套一遍 ``ck_%(table_name)s_%(constraint_name)s``，
        # 得到 ``ck_evaluations_ck_evaluations_baseline_pair``——与全新库路径
        # （0021 按 ORM 建表，ORM 写的是 ``baseline_pair`` + 约定）产生的
        # ``ck_evaluations_baseline_pair`` 不一致。同一约束在两个环境叫不同名字，
        # 会让后续迁移与守卫测试必须在两套名字间猜测。实测确认过这个差异。
        batch.create_check_constraint(op.f(_CHECK), _PAIR_EXPRESSION)
    install_modelops_guards(connection)


def _backfill_baseline_dataset_version() -> None:
    """把存量「有基线」的评测回填基线数据集版本，并清理悬空基线引用。

    **不加这一步，升级会在存量库上直接失败**：``baseline_evaluation_id`` 从
    0021 起就允许写入，真实库里必然有带基线的评测；这些行的新列为 NULL，
    加配对 CHECK 时 SQLite/PG 都会报约束违反（实测：迁移往返时
    ``CHECK constraint failed: ck_evaluations_baseline_pair``）。

    回填规则与清理策略（**做、但要留痕**）：

    - 基线行存在且同项目 → 取它的 ``dataset_version_id``。这是唯一可信来源：
      本迁移之前创建评测时，应用层**强制**基线与当前同一数据集版本，因此
      回填值必然等于该行自身的 ``dataset_version_id``，语义不变；
    - 基线行不存在（幽灵引用或跨项目）→ 一并把 ``baseline_evaluation_id`` 置
      NULL。保留 ID 而新列为 NULL 会违反配对 CHECK，而"引用一个查不到的评测"
      本来就不是可用事实；置 NULL 后该评测变成"无基线"，比留一个半截引用可
      解释。被清理的行 ID 通过 ``logging`` 输出到 alembic 运行日志
      （``ddp.migration.0041``），可审计、可追认，不是静默吞数据；
    - 本迁移**不**校验基线可追溯到的历史评测是否真的满足新的可比性规则。
      可比性只在**新建**评测时校验；历史数据按当时规则创建，不做追溯改判。

    ``downgrade`` 不恢复被清理的引用：那些值本就指向不存在的对象，
    恢复它们等于恢复缺陷（与迁移 0040 的口径一致）。
    """
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            f"SELECT id, project_id, dataset_version_id, baseline_evaluation_id "
            f"FROM {_TABLE} WHERE baseline_evaluation_id IS NOT NULL"
        )
    ).fetchall()
    if not rows:
        return
    dangling: list[str] = []
    for row in rows:
        baseline = connection.execute(
            sa.text(f"SELECT project_id, dataset_version_id FROM {_TABLE} WHERE id = :baseline_id"),
            {"baseline_id": row.baseline_evaluation_id},
        ).fetchone()
        if baseline is None or baseline.project_id != row.project_id:
            connection.execute(
                sa.text(f"UPDATE {_TABLE} SET baseline_evaluation_id = NULL WHERE id = :id"),
                {"id": row.id},
            )
            dangling.append(str(row.id))
            continue
        connection.execute(
            sa.text(f"UPDATE {_TABLE} SET {_COLUMN} = :value WHERE id = :id"),
            {"value": baseline.dataset_version_id, "id": row.id},
        )
    if dangling:
        logger.warning(
            "0041 清空 %d 条评测的悬空基线引用（基线行不存在或跨项目）：%s",
            len(dangling),
            ", ".join(dangling),
        )


def downgrade() -> None:
    """删 CHECK 与列，是 upgrade 的严格逆操作。

    删 CHECK 要重建表（SQLite 无 ``DROP CONSTRAINT``），重建会连带删掉
    ``evaluations`` 的四个触发器 → 因此要拆装一轮守卫。

    删列前同样要拆守卫：本列已登记进 ``modelops_guards._IMMUTABLE_WHEN``，
    触发器正文引用了它；SQLite 在 ``ALTER TABLE ... DROP COLUMN`` 时会校验
    触发器正文，引用已删列直接报
    ``error in trigger trg_evaluations_immutable ...: no such column``
    让整条回滚失败（迁移 0038 踩过同一个坑）。重装守卫时按删列后的实际结构
    生成，不会再把该列写回去。

    两条路径（有 CHECK / 无 CHECK）都走同一段拆装，逻辑不必分叉。
    """
    connection = op.get_bind()
    drop_modelops_guards(connection)
    if _CHECK in _check_names():
        with op.batch_alter_table(_TABLE, recreate="auto") as batch:
            # 必须用 ``op.f()`` 包一层：batch 模式对表做反射时会再套一遍命名约定，
            # 直接传已经带前缀的 ``ck_evaluations_baseline_pair`` 会被拼成
            # ``ck_evaluations_ck_evaluations_baseline_pair`` 而找不到约束
            # （实测报 ``ValueError: No such constraint``）。``conv`` 标记告诉
            # alembic"名字已按约定展开，不要再拼"。
            batch.drop_constraint(op.f(_CHECK), type_="check")
    if _COLUMN in _columns():
        op.drop_column(_TABLE, _COLUMN)
    install_modelops_guards(connection)
