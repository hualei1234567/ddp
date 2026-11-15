"""Alembic 环境。

数据库 URL 优先使用调用方传入的 ``sqlalchemy.url``（测试/CI 用），
否则从 ``DDP_DATABASE_URL`` 读取，避免在 alembic.ini 里写死。
"""

from __future__ import annotations

from logging.config import fileConfig

import app.infrastructure.db.models  # noqa: F401 - 注册 ORM 模型到 metadata
from alembic import context
from app.core.config import get_settings
from app.infrastructure.db.base import Base
from sqlalchemy import engine_from_config, pool

config = context.config

# 日志配置放在 run_migrations_* 里做，不在模块顶层做。
#
# 原因：`fileConfig()` 会重置**整个** logging 体系——清掉 root 上已有的 handler、
# 把 level 改回 alembic.ini 的 WARNING。测试通过 `command.upgrade()` 导入本模块时，
# 这一步会连带清掉 pytest 的日志捕获 handler，导致其后所有依赖日志的用例
# （限流命中日志、领域异常日志）静默失败——表现为「单独跑绿、全量跑红」。
#
# 挪到 run_migrations_* 之后语义也更准：只有真的要跑迁移才需要 alembic 的日志格式。


def _configure_alembic_logging() -> None:
    """把 alembic.ini 的日志配置应用到当前进程（仅执行迁移时调用）。

    ``disable_existing_loggers=False`` 是必须的：默认 True 会把进程里所有
    已创建的 logger 置为 disabled——包括应用的 ``ddp.*`` 与测试挂在具名
    logger 上的采集 handler。表现为「单独跑绿、全量跑红」：迁移测试跑过之后，
    其后所有依赖日志的用例（限流命中、领域异常、审计）静默拿不到记录。

    本仓库的日志由 ``app.core.logging.configure_logging()`` 统一配置，
    alembic 只需管好自己的输出，不该禁用别人的 logger。
    """
    if config.config_file_name is not None:
        fileConfig(config.config_file_name, disable_existing_loggers=False)


if not config.get_main_option("sqlalchemy.url"):
    config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    _configure_alembic_logging()
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    _configure_alembic_logging()
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
