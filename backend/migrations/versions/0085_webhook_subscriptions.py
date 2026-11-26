"""Webhook 订阅表（阶段 5 / 蓝图 N5 生态对接）。

Revision ID: 0085
Revises: 0084
Create Date: 2026-11-27

背景
----
平台此前只有 outbox 的**内部**投递（校验事件 → 校验处理器），
标注完成、熔断触发、回流受理等业务事件**没有对外通道**——下游系统无法感知。
本迁移新建 ``webhook_subscriptions``：下游订阅事件、平台签名投递。

设计要点
--------
- ``secret`` 只存 **hash**（SHA-256，与认证口令同口径），明文只在创建响应里返回一次；
  签名用 HMAC-SHA256，下游凭明文验签；
- ``event_types`` 是 JSONB 数组（订阅方关心的事件子集）；
- ``consecutive_failures`` 支持「连续 5 次失败自动 PAUSED」的熔断语义（应用层驱动）；
- ``last_success_at`` / ``last_error_at`` / ``last_error_message`` 供运营排查；
- ``status`` 库层 CHECK 枚举（MEM-INC-012：枚举值必须与领域一致）；
- 唯一约束 ``(project_id, url)``：同一项目同一地址只允许一份订阅；
- 项目外键 CASCADE：项目删除时订阅随迁（订阅是项目的资产）。

双方言（MEM-INC-013）
--------------------
建表 DDL 两侧同构：无 ALTER TYPE、无 DROP CONSTRAINT、无 pg_constraint 查询。

回滚
----
``downgrade`` 删表，是 upgrade 严格逆操作。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0085"
down_revision = "0084"  # main 实际 head
branch_labels = None
depends_on = None

_TABLE = "webhook_subscriptions"

_STATUS_ALLOWED_SQL = "('ACTIVE', 'PAUSED')"


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(2048), nullable=False),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        # 订阅明文 secret 加密落库（密钥来自部署配置），仅投递时在内存解密，
        # 不落任何日志或响应。强度：HMAC 密钥流（无认证），生产可升级 AEAD。
        sa.Column("secret_encrypted", sa.String(256), nullable=False),
        sa.Column(
            "event_types",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column(
            "consecutive_failures", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_message", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=False),
        sa.Column("updated_by", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint("project_id", "url", name=f"uq_{_TABLE}_project_url"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_created_by_users",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"],
            ["users.id"],
            name=f"fk_{_TABLE}_updated_by_users",
        ),
        sa.CheckConstraint(f"status IN {_STATUS_ALLOWED_SQL}", name=f"ck_{_TABLE}_status_allowed"),
        sa.CheckConstraint("length(url) > 0", name=f"ck_{_TABLE}_url_non_empty"),
        sa.CheckConstraint("length(secret_hash) >= 64", name=f"ck_{_TABLE}_secret_hash_length"),
        sa.CheckConstraint(
            "length(secret_encrypted) > 0", name=f"ck_{_TABLE}_secret_encrypted_non_empty"
        ),
        sa.CheckConstraint("consecutive_failures >= 0", name=f"ck_{_TABLE}_failures_non_negative"),
    )
    op.create_index(f"ix_{_TABLE}_project_status", _TABLE, ["project_id", "status"])


def downgrade() -> None:
    op.drop_index(f"ix_{_TABLE}_project_status", table_name=_TABLE)
    op.drop_table(_TABLE)
