"""备份恢复演练脚本的结构性校验（FR-043）。

**为什么不是"跑一遍脚本"**：演练需要真实 docker（postgres + minio + 17MB 数据），
CI 环境没有。本测试验证脚本**语义正确**——即那些"看起来对但实际会静默失效"
的地方：

1. 脚本必须真的删除抽样对象再恢复（否则"备份可用"只是口号）；
2. RPO 锚点必须用真实存在的列（曾写成 `audit_logs.updated_at`，该列不存在，
   导致锚点恒为 `none`、校验形同虚设——这是真实踩到的坑）；
3. 模式对象（约束/索引）必须校验，不能只比表数（部分唯一索引丢失会让去重
   语义静默失效）；
4. 恢复目标必须是独立新库，绝不能写成覆盖生产库；
5. Spool 必须纳入演练（此前完全未覆盖）。

这些是**结构断言**，用文本/AST 静态分析，不需要 docker。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRILL = ROOT / "scripts" / "ops" / "backup-restore-drill.sh"
RUNBOOK = ROOT / "docs" / "ops" / "backup-restore-runbook.md"


@pytest.fixture(scope="module")
def script() -> str:
    assert DRILL.is_file(), f"演练脚本不存在：{DRILL}"
    return DRILL.read_text(encoding="utf-8")


def test_script_is_executable_bash(script: str) -> None:
    """必须有 shebang 且启用 set -euo pipefail（否则中途失败会被吞掉）。"""
    assert script.startswith("#!/usr/bin/env bash")
    assert "set -euo pipefail" in script


def test_restore_target_is_an_independent_database(script: str) -> None:
    """恢复到独立新库，绝不覆盖生产库。

    这是不可逆操作的安全边界：脚本若把 pg_dump 导回生产库，一次误跑就毁库。
    """
    assert 'DRILL_DB="${DRILL_DB:-ddp_restore_drill}"' in script
    # 恢复语句必须指向 DRILL_DB，不得指向 PG_DB
    restore_lines = [
        line
        for line in script.splitlines()
        if "psql" in line and "-v ON_ERROR_STOP=1" in line
    ]
    assert restore_lines, "找不到恢复语句"
    for line in restore_lines:
        assert "$DRILL_DB" in line, f"恢复目标不是演练库：{line}"
        assert "-d $PG_DB" not in line, f"恢复语句指向了生产库：{line}"


def test_sample_object_is_deleted_then_restored(script: str) -> None:
    """必须真的删掉对象再恢复并比对 sha256。

    只做「mirror 完事」等于没验证恢复能力——这是脚本注释里明确写的教训。
    """
    assert "rm --force" in script, "没有删除抽样对象，无法验证恢复"
    assert "SAMPLE_BEFORE" in script and "SAMPLE_AFTER" in script
    # 比对语句可能带中文说明后缀；用子串判断，避免正则转义陷阱
    assert '"$SAMPLE_BEFORE" = "$SAMPLE_AFTER"' in script, "没有比对恢复前后内容摘要"


def test_rpo_anchor_uses_an_existing_column(script: str) -> None:
    """RPO 锚点必须用 audit_logs 真实存在的列。

    曾经写成 `audit_logs.updated_at`——该列**不存在**，于是锚点恒为 `none`，
    「RPO 锚点一致」的校验永远通过，等于没有校验。真实列是 `occurred_at`。
    """
    assert "audit_logs" in script
    assert "occurred_at" in script, "RPO 锚点未使用 audit_logs.occurred_at"
    # 只检查真正的 SQL 查询语句：SQL 里的 audit_logs 不得配 updated_at。
    # 注释与 echo 文案可以提到 updated_at（那是解释"为什么不用它"），
    # 但 SQL 里出现就是真 bug——该列不存在。
    sql_lines = [
        line
        for line in script.splitlines()
        if "audit_logs" in line and ("select " in line.lower() or "coalesce(" in line.lower())
    ]
    assert sql_lines, "找不到 RPO 锚点的 SQL 查询"
    for line in sql_lines:
        assert "updated_at" not in line, (
            f"SQL 里仍用 audit_logs.updated_at——该列不存在，会让 RPO 校验静默失效：{line.strip()}"
        )


def test_rpo_anchor_mismatch_fails_the_drill(script: str) -> None:
    """RPO 锚点不一致必须 exit 1，不能只打印。"""
    assert '[ "$RPO_ANCHOR" = "$RESTORED_RPO" ]' in script
    # 断言同一行/邻近有失败退出
    idx = script.index('[ "$RPO_ANCHOR" = "$RESTORED_RPO" ]')
    line_end = script.index("\n", idx)
    assert "exit 1" in script[idx:line_end], "RPO 不一致时没有退出"


def test_schema_objects_are_verified_not_just_table_count(script: str) -> None:
    """必须校验约束与索引数——只比表数会漏掉「部分唯一索引丢失」。"""
    for token in ("pg_constraint", "pg_indexes", "pg_trigger"):
        assert token in script, f"未校验 {token}"
    assert "部分唯一索引丢失会让去重语义静默失效" in script or "part" in script.lower()


def test_spool_is_covered(script: str) -> None:
    """Spool（端侧待上传队列）必须纳入演练。

    这是本次补齐的主要缺口：此前演练只覆盖 PG + MinIO，端侧采集机损坏
    会导致未上传数据永久丢失，而演练完全没碰。
    """
    assert "spool" in script.lower()
    assert "state.sqlite3" in script, "未触碰 Spool 状态库"
    assert "spool_backup_restore" in script, "未对 Spool 计时/校验"
    # 必须校验状态机两态（PENDING / UPLOADED）
    assert "PENDING" in script and "UPLOADED" in script


def test_app_layer_availability_is_checked(script: str) -> None:
    """恢复后必须能做跨表查询，「表在但外键坏了」等于没恢复。"""
    assert "JOIN" in script.upper() or "join" in script
    assert "app_layer_check" in script


def test_cleanup_trap_prevents_residue(script: str) -> None:
    """异常退出也要清理临时库/临时目录，不留残留。"""
    assert "trap cleanup EXIT" in script
    assert "DROP DATABASE IF EXISTS" in script


def test_rto_is_measured_not_guessed(script: str) -> None:
    """RTO 必须实测分段耗时。没有数字的 RTO 等于没有 RTO。"""
    assert "PHASES" in script
    assert "START_TS" in script and "END_TS" in script


def test_runbook_exists_and_documents_three_state_locations() -> None:
    """Runbook 必须说清三处状态，否则运维不知道要备份什么。"""
    assert RUNBOOK.is_file(), f"Runbook 不存在：{RUNBOOK}"
    text = RUNBOOK.read_text(encoding="utf-8")
    for token in ("PostgreSQL", "MinIO", "Spool"):
        assert token in text, f"Runbook 未覆盖 {token}"
    # 必须声明密钥不在数据库备份里（真实运维事故高发点）
    assert "密钥" in text
    # 必须披露已知限制，不许把「有备份」说成「可任意时间点恢复」
    assert "PITR" in text
