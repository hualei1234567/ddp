"""V61-005：``processing_*`` 三张表 ``stage`` 允许值补入视频链三阶段 +
新建 ``video_frames`` 帧索引表（迁移 0087）。

Revision ID: 0087
Revises: 0086
Create Date: 2026-11-28

背景
----
V6.1 M2（视频多模态 Golden Path）：验收样本不含图像，视频帧读取从未验证
（``HARDWARE_CAPTURE_NOT_TESTED`` 同族的 ``VIDEO_NOT_TESTED`` 空白）。
本迁移做两件事：

1. ``stage`` CHECK 补入三个视频阶段：``VIDEO_DECODE`` / ``FRAME_INDEX`` /
   ``TIMESTAMP_NORMALIZE``。插入位置遵循数据流：容器解析（FORMAT_PARSING）
   之后、流提取（STREAM_EXTRACTION）之前——先解码拿到帧，才能提取流。
2. 新建 ``video_frames``：逐帧索引（:class:`VideoFrameModel`）。
   **原始视频永不可变**（RawAsset 红线）：帧图像是衍生 Artifact，本表只存
   索引与校验信息，像素本体走对象存储（``frame_object_ref``）。

0087 的教训（0074 同族）
------------------------
0085 教训重演风险：**新增枚举必须同步所有 CHECK**。0074 真实踩坑
（"stage 加了、约束没改，运行永远卡 RETRYING"），并留下防回归测试
``test_processing_stage_constraints.py``——它会断言 ``ProcessingStage``
枚举每个值都在三表约束内。本迁移同步补齐，防回归测试自动覆盖新值。

双方言（MEM-INC-013）
--------------------
- 建表 DDL 两侧同构；
- stage CHECK 变更沿用 0074 模式：**仅 PostgreSQL** 执行（SQLite 无
  ALTER CONSTRAINT；迁移链测试在 SQLite 上只验证链路能跑通）。

回滚
----
- stage：回退前断言无行使用新阶段（0085 同款防御，拒绝静默截断）；
- ``video_frames``：drop table（严格逆操作）。
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0087"
down_revision = "0086"  # main 实际 head
branch_labels = None
depends_on = None

#: (表名, 约束名)——与 0074 完全一致的三张表。
_STAGE_CONSTRAINTS: tuple[tuple[str, str], ...] = (
    ("processing_runs", "ck_processing_runs_stage_allowed"),
    ("processing_run_steps", "ck_processing_run_steps_stage_allowed"),
    ("processing_artifacts", "ck_processing_artifacts_stage_allowed"),
)

#: 0087 之后的允许集合。顺序与 ``ProcessingStage`` 枚举一致：
#: 三个视频阶段插在 FORMAT_PARSING 之后、STREAM_EXTRACTION 之前。
_STAGE_VALUES_AFTER_0087: tuple[str, ...] = (
    "FILE_VALIDATION",
    "METADATA_EXTRACTION",
    "FORMAT_PARSING",
    "VIDEO_DECODE",
    "FRAME_INDEX",
    "TIMESTAMP_NORMALIZE",
    "TIME_ALIGNMENT",
    "CALIBRATION_VALIDATION",
    "STREAM_EXTRACTION",
    "EPISODE_SEGMENTATION",
    "DATA_CLEANING",
    "QUALITY_PRE_CHECK",
)

_STAGE_VALUES_BEFORE_0087: tuple[str, ...] = tuple(
    value
    for value in _STAGE_VALUES_AFTER_0087
    if value not in {"VIDEO_DECODE", "FRAME_INDEX", "TIMESTAMP_NORMALIZE"}
)

#: 新阶段集合（回滚防御用）。
_NEW_STAGES: tuple[str, ...] = ("VIDEO_DECODE", "FRAME_INDEX", "TIMESTAMP_NORMALIZE")

_TABLE = "video_frames"


def _stage_list(values: tuple[str, ...]) -> str:
    """渲染成 SQL IN 列表（值是代码常量，非用户输入）。"""
    return ", ".join(f"'{value}'" for value in values)


def _dialect() -> str:
    return op.get_bind().dialect.name


def _relation(table: str) -> str:
    """PostgreSQL 显式限定 public.（0074 的 schema 陷阱）；SQLite 不加前缀。"""
    if _dialect() == "postgresql":
        return f"public.{table}"
    return table


def _supports_constraint_ddl() -> bool:
    """CHECK 变更仅 PostgreSQL（理由见模块文档）。"""
    return _dialect() == "postgresql"


def _drop_all(connection: sa.Connection, table: str, constraint: str) -> None:
    """删掉表上所有同名 CHECK（PG 允许同表同名约束，0074 实测坑）。"""
    if _dialect() != "postgresql":
        return
    relation = _relation(table)
    for _ in range(64):
        remaining = connection.execute(
            sa.text(
                "SELECT COUNT(*) FROM pg_constraint "  # noqa: S608 - 关系名来自模块常量
                f"WHERE conname = :name AND conrelid = '{relation}'::regclass"
            ),
            {"name": constraint},
        ).scalar()
        if not remaining:
            return
        op.execute(
            sa.text(f"ALTER TABLE {relation} DROP CONSTRAINT {constraint}")  # noqa: S608 - 常量
        )
    raise RuntimeError(f"无法清空 {relation}.{constraint}：疑似异常多的重复约束")


def _replace(
    connection: sa.Connection, table: str, constraint: str, values: tuple[str, ...]
) -> None:
    if not _supports_constraint_ddl():
        return
    _drop_all(connection, table, constraint)
    op.execute(
        sa.text(  # noqa: S608 - 表名/约束名/值均为模块常量
            f"ALTER TABLE {_relation(table)} ADD CONSTRAINT {constraint} "
            f"CHECK (stage IN ({_stage_list(values)}))"
        )
    )


def upgrade() -> None:
    connection = op.get_bind()

    # ---- 1. stage CHECK 扩展（先查存量无未知值，0085 同款防御）----
    for table, _constraint in _STAGE_CONSTRAINTS:
        unknown = connection.execute(
            sa.text(
                # 表名与允许值均为模块常量，非用户输入（同 0074 先例）。
                f"SELECT DISTINCT stage FROM {_relation(table)} "  # noqa: S608
                f"WHERE stage IS NOT NULL AND stage NOT IN ({_stage_list(_STAGE_VALUES_AFTER_0087)})"  # noqa: S608
            )
        ).fetchall()
        if unknown:
            names = ", ".join(str(row[0]) for row in unknown)
            raise RuntimeError(f"{table} 存在未知 stage 值：{names}；请先人工确认")

    for table, constraint in _STAGE_CONSTRAINTS:
        _replace(connection, table, constraint, _STAGE_VALUES_AFTER_0087)

    # ---- 2. 新建 video_frames ----
    op.create_table(
        _TABLE,
        # 帧 id：确定性派生（asset_id + stream_id + frame_index 的 uuid5）
        # 或服务端 uuid4；不设业务唯一键之外的约束，由 (asset_id, stream_id,
        # frame_index) 唯一约束承担幂等语义。
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("project_id", sa.Uuid(), nullable=False),
        # 原始视频资产引用（RawAsset / MediaAsset 之上的一层——帧是衍生品）
        sa.Column("asset_id", sa.Uuid(), nullable=False),
        sa.Column("stream_id", sa.String(128), nullable=False),
        sa.Column("frame_index", sa.Integer(), nullable=False),
        # **原始时间**：解码器原样带回，绝不改写（蓝图红线：保留原始时间戳）。
        # pts 以秒为单位的 NUMERIC——AV1/MP4 的 time_base 各异，整型 ticks
        # 的换算发生在解码层，这里统一收秒级高精度值。
        sa.Column("pts_seconds", sa.Numeric(18, 9), nullable=True),
        sa.Column("timestamp_raw", sa.String(64), nullable=True),
        sa.Column("time_base", sa.String(64), nullable=True),
        sa.Column("timestamp_normalized_seconds", sa.Numeric(18, 9), nullable=True),
        # 归一化方法留痕（NONE=未做；由 TIMESTAMP_NORMALIZE 阶段填写）
        sa.Column("normalize_method", sa.String(64), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("pixel_format", sa.String(64), nullable=True),
        sa.Column("codec", sa.String(64), nullable=True),
        sa.Column("is_keyframe", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        # OK / DECODE_ERROR / SKIPPED——解码失败的帧也要留痕（不静默跳过）
        sa.Column("decode_status", sa.String(16), nullable=False),
        # 像素本体在对象存储：引用（bucket key）+ 整帧 sha256
        sa.Column("frame_object_ref", sa.String(1024), nullable=True),
        sa.Column("frame_hash", sa.String(64), nullable=True),
        sa.Column("parser_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{_TABLE}"),
        sa.UniqueConstraint(
            "asset_id", "stream_id", "frame_index", name=f"uq_{_TABLE}_asset_stream_frame"
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=f"fk_{_TABLE}_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "decode_status IN ('OK', 'DECODE_ERROR', 'SKIPPED')",
            name=f"ck_{_TABLE}_decode_status",
        ),
        sa.CheckConstraint("frame_index >= 0", name=f"ck_{_TABLE}_frame_index_non_negative"),
    )
    # 抢占路径：按资产+流取帧范围（Episode Explorer 按时间窗浏览）
    op.create_index(
        f"ix_{_TABLE}_asset_stream_frame", _TABLE, ["asset_id", "stream_id", "frame_index"]
    )
    # 项目维度清理/审计
    op.create_index(f"ix_{_TABLE}_project_created_at", _TABLE, ["project_id", "created_at"])


def downgrade() -> None:
    """回滚：先断言新阶段无行使用（0085 防御），再删表、收约束。"""
    connection = op.get_bind()
    in_use: list[str] = []
    for table, _constraint in _STAGE_CONSTRAINTS:
        for stage in _NEW_STAGES:
            count = connection.execute(
                sa.text(
                    # 表名/阶段名均为模块常量，非用户输入（同 0074 先例）。
                    f"SELECT COUNT(*) FROM {_relation(table)} WHERE stage = '{stage}'"  # noqa: S608
                )
            ).scalar()
            if count:
                in_use.append(f"{table}.{stage}（{count} 行）")
    if in_use:
        raise RuntimeError(
            "仍有运行记录使用视频阶段，无法回滚：" + "、".join(in_use) + "；请先清理"
        )

    op.drop_index(f"ix_{_TABLE}_project_created_at", table_name=_TABLE)
    op.drop_index(f"ix_{_TABLE}_asset_stream_frame", table_name=_TABLE)
    op.drop_table(_TABLE)

    for table, constraint in _STAGE_CONSTRAINTS:
        _replace(connection, table, constraint, _STAGE_VALUES_BEFORE_0087)
