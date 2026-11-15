"""迁移必须是不可变快照：不得用活的 ORM 模型建表/加列。

**为什么要有这条测试**

迁移 0025 原先写的是 ``MediaAssetModel.__table__.create(...)``。这看起来
"省事、不会写错列"，实则把历史迁移绑死在**当前**模型定义上：

- 后续给 ``MediaAssetModel`` 加一列（迁移 0033 加 ``raw_asset_id``），
  0025 在全新库上跑时就把这一列一起建了出来；
- 0033 再 ``op.add_column`` 同一列 → ``duplicate column name``；
- 结果：**全新库上 ``alembic upgrade head`` 必然失败**，而已升级过的旧库
  完全看不出来。CI 与任何新环境都会中，本地老库不会——这类缺陷最难发现。

0025 已改为显式 ``op.create_table``。但同样的写法还存在于下列 6 个迁移，
它们**目前没出事**是因为对应模型自建表以来还没加过列；一旦有人给其中任何
一张表加列，就会复现同一故障。

``_KNOWN_LIVE_MODEL_MIGRATIONS`` 是**已知存量基线**，不是白名单豁免：

- 新增迁移一律不得使用 ``XxxModel.__table__``（新文件会被本测试红灯）；
- 给基线里的任何一张表加列之前，必须先把该迁移改写成显式 ``op.create_table``，
  否则迁移测试会在全新库上失败；
- 基线只减不增。

**为什么现在不全改写**：这 6 个迁移共涉及 23 张表，逐列人工重写 500+ 行 DDL
风险远大于收益，而真正的兜底（全新库 upgrade 到 head）已由
``tests/migrations/`` 的其余用例覆盖。按增量迭代原则，先守住"不再新增"，
在需要给这些表加列时再逐张改写。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

MIGRATIONS_DIR: Final = Path(__file__).resolve().parents[2] / "migrations" / "versions"

# 已知存量：迁移文件名 → 为何暂不改写。
_KNOWN_LIVE_MODEL_MIGRATIONS: Final[dict[str, str]] = {
    "0017_stage4_datasets.py": "9 张数据集表，显式 DDL 约 300 行；给这些表加列前必须改写",
    "0018_stage4_export_jobs.py": "ExportJob 单表；加列前必须改写",
    "0019_stage4_training.py": "6 张训练/授权表；加列前必须改写",
    "0022_supplier_inbound.py": "4 张供应商表；加列前必须改写",
    "0024_datasource_huggingface.py": "2 张数据源表；加列前必须改写",
    "0026_datasource_reviews.py": "DataReview 单表；加列前必须改写",
}


def _migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.py"))


def _uses_live_orm_table(path: Path) -> bool:
    """迁移文件里是否出现 ``XxxModel.__table__``。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or node.attr != "__table__":
            continue
        value = node.value
        if isinstance(value, ast.Name) and value.id.endswith("Model"):
            return True
        if isinstance(value, ast.Attribute) and value.attr.endswith("Model"):
            return True
    return False


def test_no_new_migration_builds_tables_from_live_orm_models() -> None:
    offenders = {path.name for path in _migration_files() if _uses_live_orm_table(path)}
    new_offenders = sorted(offenders - set(_KNOWN_LIVE_MODEL_MIGRATIONS))
    assert not new_offenders, (
        "以下迁移使用了活的 ORM 模型（`XxxModel.__table__`）建表或加列，"
        "这会让历史迁移随模型变化而漂移，最终在全新库上升级失败：\n  "
        + "\n  ".join(new_offenders)
        + "\n\n改为显式 `op.create_table(...)` / `op.add_column(...)`，"
        "把迁移冻结成历史快照（详见迁移 0025 的模块文档）。"
    )


def test_known_baseline_has_no_stale_entries() -> None:
    """基线只减不增：已改写的迁移必须从基线里移除，避免长期挂着假豁免。"""
    still_live = {path.name for path in _migration_files() if _uses_live_orm_table(path)}
    stale = sorted(set(_KNOWN_LIVE_MODEL_MIGRATIONS) - still_live)
    assert not stale, (
        "以下迁移已不再使用活的 ORM 模型，请从 _KNOWN_LIVE_MODEL_MIGRATIONS 移除：\n  "
        + "\n  ".join(stale)
    )


def test_migration_file_parses() -> None:
    """任何迁移文件至少要是合法 Python，避免语法错让整个 upgrade 崩在末尾。"""
    for path in _migration_files():
        ast.parse(path.read_text(encoding="utf-8"))


def test_fresh_upgrade_reaches_head_without_duplicate_columns(tmp_path: Path) -> None:
    """全新库上 upgrade 到 head 必须成功。

    这是本文件要保护的真实属性：任何"迁移引用活模型"引起的历史漂移，
    最终都会在这里表现为 ``duplicate column name`` 之类的中断。
    """
    from alembic import command
    from alembic.config import Config

    backend_dir = MIGRATIONS_DIR.parents[1]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("script_location", str(backend_dir / "migrations"))
    config.set_main_option("prepend_sys_path", str(backend_dir))
    config.set_main_option("sqlalchemy.url", f"sqlite+pysqlite:///{tmp_path / 'fresh.db'}")
    command.upgrade(config, "head")
