"""R-12：错误映射不得把内部标识回显给客户端。

背景：``modelops_error_to_app`` 与 ``supplier_error_to_app`` 在文案表缺项时
回退到 ``str(exc)``。领域异常的字符串里通常含实体名、字段名、状态枚举值
甚至客户端输入——把它原样发给调用方既暴露内部结构，也与其他域
（collection/annotation/dataset 用固定文案表）的策略不一致。

这里用一个"带着敏感内容"的假领域异常来证明：响应 message 只能是固定文案。
"""

from __future__ import annotations

import pytest
from app.api.errors import modelops_error_to_app, supplier_error_to_app
from app.domain.modelops.errors import ModelOpsDomainError
from app.domain.suppliers.errors import SupplierDomainError

SENSITIVE = "内部表 model_versions 的 artifact_hash 与 DB 行 42 不一致"


class _FakeModelOpsError(ModelOpsDomainError):
    code = "model_callback_replayed"

    def __str__(self) -> str:  # 故意带敏感内容
        return SENSITIVE


class _FakeModelOpsUnknown(ModelOpsDomainError):
    code = "some_new_internal_code"

    def __str__(self) -> str:
        return SENSITIVE


class _FakeSupplierError(SupplierDomainError):
    code = "ledger_immutable"

    def __str__(self) -> str:
        return SENSITIVE


class _FakeSupplierUnknown(SupplierDomainError):
    code = "brand_new_supplier_code"

    def __str__(self) -> str:
        return SENSITIVE


@pytest.mark.parametrize(
    "exc",
    [_FakeModelOpsError(), _FakeModelOpsUnknown()],
)
def test_modelops_errors_use_fixed_messages(exc: ModelOpsDomainError) -> None:
    app_error = modelops_error_to_app(exc)
    assert SENSITIVE not in app_error.message
    assert "model_versions" not in app_error.message
    assert app_error.message  # 不能是空串


@pytest.mark.parametrize(
    "exc",
    [_FakeSupplierError(), _FakeSupplierUnknown()],
)
def test_supplier_errors_use_fixed_messages(exc: SupplierDomainError) -> None:
    app_error = supplier_error_to_app(exc)
    assert SENSITIVE not in app_error.message
    assert "model_versions" not in app_error.message
    assert app_error.message


def test_known_codes_keep_their_specific_message() -> None:
    """固定文案不等于千篇一律：已知 code 仍要有针对性说明。"""
    known = modelops_error_to_app(_FakeModelOpsError())
    unknown = modelops_error_to_app(_FakeModelOpsUnknown())
    assert known.message != unknown.message
    assert "回调" in known.message


def test_supplier_known_code_message_is_specific() -> None:
    err = supplier_error_to_app(_FakeSupplierError())
    assert "追加" in err.message


def test_all_domain_mappers_share_the_single_factory() -> None:
    """R-11：各域映射函数都必须走同一份 ``_mapped``，不得再各自手写查表。"""
    import inspect

    from app.api import errors as errors_module

    mappers = [
        name
        for name, value in vars(errors_module).items()
        if name.endswith("_error_to_app") and inspect.isfunction(value)
    ]
    # 14 个域映射（设备/采集/上传/Episode/标注/数据集/数据源/供应商/modelops/
    # 场地/治理/审计查询/处理运行/统一质量引擎）+ integrity_error_to_app（后者
    # 判定 SQL 约束，不走 code 查表）。
    # 新增域时这个数字必须同步加一：它防的是"某个域又手写了一份查表"，
    # 而不是限制域的数量。
    assert len(mappers) == 15, mappers
    for name in mappers:
        source = inspect.getsource(getattr(errors_module, name))
        if name == "integrity_error_to_app":
            # 数据库完整性约束有独立的判定逻辑，不走 code 查表。
            continue
        assert "_mapped(" in source, name
        # 判定本体不得再出现在函数里。
        assert "not in _" not in source, name
        assert "get(code" not in source, name


def test_details_are_propagated_where_declared() -> None:
    """上传/Episode 域透传 details（排障需要），其余域不透传。"""
    from app.api import errors as errors_module
    from app.domain.ingestion.errors import FileTooLargeError

    app_error = errors_module.ingestion_error_to_app(
        FileTooLargeError("文件过大", details={"limit": 100, "actual": 150})
    )
    assert app_error.code == "file_too_large"
    assert app_error.details == {"limit": 100, "actual": 150}


# --- 账本只追加守卫 -> ledger_immutable ------------------------------------


def test_ledger_append_only_violation_maps_to_ledger_immutable() -> None:
    """数据库守卫拒绝账本改删时，客户端必须收到 `ledger_immutable`。

    契约（阶段 6 §8）明列该码。守卫在数据库层（`suppliers_guards.py` 的
    `trg_acceptance_ledger_entries_update/delete`），但此前异常落进
    `integrity_error_to_app` 的兜底分支，客户端收到泛化的 `conflict`——
    无法区分"账本不可改"（重试无用）与其他完整性冲突。
    """
    from app.api.errors import integrity_error_to_app
    from sqlalchemy.exc import IntegrityError

    exc = IntegrityError(
        "UPDATE acceptance_ledger_entries SET reason='x'",
        {},
        Exception("acceptance ledger is append-only"),
    )
    error = integrity_error_to_app(exc)

    assert error.code == "ledger_immutable"
    assert error.status_code == 409


def test_other_integrity_violation_does_not_become_ledger_immutable() -> None:
    """反向保护：普通 CHECK 违反不能被误判成账本冲突。

    判据只认固定短语 `append-only`，不做模糊匹配——否则所有 CHECK 违反
    都会显示"账本只允许追加"，误导客户端。
    """
    from app.api.errors import integrity_error_to_app
    from sqlalchemy.exc import IntegrityError

    exc = IntegrityError(
        "INSERT INTO suppliers ...",
        {},
        Exception("CHECK constraint failed: ck_suppliers_status_allowed"),
    )
    error = integrity_error_to_app(exc)

    assert error.code != "ledger_immutable"
    assert error.code == "conflict"


def test_foreign_key_violation_explains_which_constraint() -> None:
    """外键冲突必须可诊断：通用 409 让客户端无法区分"重试"与"根本删不掉"。

    PostgreSQL 的报错里带约束名，如实回显（只回显约束标识，不含表数据或
    跨项目对象名）；SQLite 不提供约束名，此时只给 ``reason``。
    """
    from app.api.errors import integrity_error_to_app
    from sqlalchemy.exc import IntegrityError

    pg = IntegrityError(
        "DELETE FROM project_members ...",
        {},
        Exception(
            'update or delete on table "users" violates foreign key constraint '
            '"fk_annotation_assignments_project_assignee" on table "annotation_assignments"'
        ),
    )
    error = integrity_error_to_app(pg)
    assert error.code == "conflict"
    assert error.status_code == 409
    assert error.details == {
        "reason": "foreign_key_violation",
        "constraint": "fk_annotation_assignments_project_assignee",
    }
    assert "project_members" not in error.message

    sqlite = IntegrityError(
        "DELETE FROM project_members ...",
        {},
        Exception("FOREIGN KEY constraint failed"),
    )
    fallback = integrity_error_to_app(sqlite)
    assert fallback.details == {"reason": "foreign_key_violation", "constraint": None}
