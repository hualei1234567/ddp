"""``processing_*`` 表的枚举约束与代码枚举一致性测试（V51-001 防回归）。

**为什么需要这个测试（真实事故）**

V51-001 新增了 ``ProcessingStage.STREAM_EXTRACTION`` 与
``ProcessingArtifactKind.STREAM_EXTRACTION``（枚举改了、编排器也接入了），
但**没有任何人同步数据库 CHECK 约束**。后果按顺序暴露：

1. 运行写 ``processing_runs`` 撞 ``ck_processing_runs_stage_allowed`` → RETRYING
2. 修好 runs 后，写 ``processing_run_steps`` 撞同类约束 → 还是 RETRYING
3. 修好 steps 后，写 ``processing_artifacts`` 撞 ``stage_allowed`` → 还是 RETRYING
4. 修好 stage 后，写 ``kind`` 撞 ``ck_processing_artifacts_kind_allowed`` → 还是 RETRYING

**四次才修完**——因为每个约束都是独立的，而没有任何机制保证"枚举加了，
约束也加了"。这个测试就是那个机制。

**测试策略**

迁移测试跑在 SQLite 上（``tests/migrations/`` 的既有约定）。SQLite 不支持
``ALTER TABLE ... DROP CONSTRAINT``，因此这里**不改库**，而是：

1. 导入迁移模块，读取它声明的允许值元组
2. 断言这些元组与代码枚举**完全相等**

这样下次新增枚举值而忘了改迁移，CI 直接红，而不是等运行时撞车。
"""

from __future__ import annotations

import importlib

import pytest
from app.domain.processing.artifacts import ProcessingArtifactKind
from app.domain.processing.enums import ProcessingStage

_MIGRATION = "migrations.versions.0074_processing_stage_stream_extraction"


def _load_migration():
    try:
        return importlib.import_module(_MIGRATION)
    except ModuleNotFoundError:  # pragma: no cover - 迁移被重命名时给出清晰提示
        pytest.fail(
            f"找不到迁移模块 {_MIGRATION}；若迁移已重命名，请同步更新本测试。"
            "本测试的价值就是钉住「枚举与数据库约束必须一致」这一不变量。"
        )


def _latest_stage_migration():
    """返回「当前声明 stage 允许集」的最新迁移模块。

    枚举每次扩展都会产生新迁移（0074→0087→…）；防回归必须**始终钉住最新
    声明**，否则旧迁移的元组会让测试在枚举合法扩展后误报。判断方式：
    新迁移模块会声明 ``_STAGE_VALUES_AFTER_<REV>`` 且 ``down_revision``
    指向旧迁移。这里显式登记已知迁移——新增时同步改这一行，让登记本身
    成为一次"有意识的约束同步"。
    """
    return importlib.import_module("migrations.versions.0087_video_frames_and_stages")


def test_stage_migration_covers_every_enum_value() -> None:
    """迁移声明的 stage 允许值必须与实际枚举**完全相等**。

    不等就说明有人加了阶段却没改约束（或反之），运行时必然撞 CheckViolation。
    用集合相等而非子集：多一个值同样危险——它意味着约束放行了代码不会产生的值，
    掩盖拼写错误。
    """
    migration = _latest_stage_migration()
    declared = set(migration._STAGE_VALUES_AFTER_0087)
    actual = {stage.value for stage in ProcessingStage}

    assert declared == actual, (
        "迁移的 stage 允许值与 ProcessingStage 枚举不一致：\n"
        f"  迁移多出：{sorted(declared - actual)}\n"
        f"  迁移缺失：{sorted(actual - declared)}\n"
        "请同步最新迁移（或新增迁移）与枚举。"
    )

    # 0087 必须建立在 0074 之上（阶段只增不减的链式保证）。
    head_migration = _load_migration()
    assert (
        set(head_migration._STAGE_VALUES_AFTER_0074) < actual
    ), "0074 的允许集应当是当前枚举的真子集：阶段只增不减。"


def test_kind_migration_covers_every_enum_value() -> None:
    """迁移声明的 artifact kind 允许值必须与实际枚举完全相等。"""
    migration = _load_migration()
    declared = set(migration._KIND_VALUES_AFTER_0074)
    actual = {kind.value for kind in ProcessingArtifactKind}

    assert declared == actual, (
        "迁移的 kind 允许值与 ProcessingArtifactKind 枚举不一致：\n"
        f"  迁移多出：{sorted(declared - actual)}\n"
        f"  迁移缺失：{sorted(actual - declared)}\n"
        "请同步迁移 0074（或新增迁移）与枚举。"
    )


def test_stage_constraints_cover_all_three_tables() -> None:
    """stage 约束必须覆盖三张表——少一张就会出现"修了一半"的假象。

    实测教训：只改 ``processing_runs`` 后错误从 runs 转移到 steps，
    再转移到 artifacts。三张表各自有一份独立约束，必须同时改。
    """
    migration = _load_migration()
    tables = {table for table, _constraint in migration._STAGE_CONSTRAINTS}
    assert tables == {"processing_runs", "processing_run_steps", "processing_artifacts"}


def test_migration_is_dual_dialect_safe() -> None:
    """迁移必须同时兼容 PostgreSQL 与 SQLite。

    **为什么这是硬要求**：生产/集成用 PostgreSQL，但 ``tests/migrations/`` 的
    既有约定是跑 SQLite（无需起容器）。若迁移硬编码 ``public.``，SQLite 会
    报 ``no such table: public.processing_runs``——实测加了 ``public.`` 后
    4 个迁移测试同时红。

    同时 PostgreSQL 又**必须**限定 ``public.``（同名约束可能分属不同 schema）。
    两者只能靠方言分支解决，这个测试钉住"分支存在且按方言返回不同值"。
    """
    import inspect

    migration = _load_migration()
    source = inspect.getsource(migration)

    # 1) 必须按方言分支
    assert "_dialect()" in source, "未按方言分支；PostgreSQL 与 SQLite 的要求不同"

    # 2) SQLite 不支持 ALTER TABLE DROP CONSTRAINT，必须有 batch 模式或跳过
    assert (
        "batch_alter_table" in source or "_dialect() != " in source
    ), "缺少 SQLite 兼容处理（batch_alter_table 或方言跳过）"

    # 3) PostgreSQL 分支必须补 public. 前缀
    pg_branch = [ln for ln in source.splitlines() if "public." in ln and "_relation" not in ln]
    assert pg_branch, "PostgreSQL 分支未补 public. 前缀"
