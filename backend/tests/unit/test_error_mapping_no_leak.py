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
    # 9 个域映射 + integrity_error_to_app（后者判定 SQL 约束，不走 code 查表）。
    assert len(mappers) == 10, mappers
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
