"""场地域实体单测（N9 多基地）。

只测领域层单实体不变量：必填、长度、``kind`` 取值、``version`` 为正、
PATCH 白名单与"显式 null 清空"语义。跨对象前置条件（同项目存在）由应用层
用例与数据库复合外键负责，不在这里测。
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from app.domain.locations import (
    ADDRESS_MAX_LENGTH,
    CODE_MAX_LENGTH,
    NAME_MAX_LENGTH,
    NOTES_MAX_LENGTH,
    DomainValidationError,
    Location,
    LocationKind,
)


def _location(**overrides: object) -> Location:
    payload: dict[str, object] = {
        "location_id": uuid4(),
        "project_id": uuid4(),
        "code": "BJ-1",
        "name": "北京基地",
    }
    payload.update(overrides)
    return Location(**payload)  # type: ignore[arg-type]


def test_defaults_kind_site_and_version_one() -> None:
    location = _location()
    assert location.kind is LocationKind.SITE
    assert location.version == 1
    assert location.address is None
    assert location.notes is None


def test_text_fields_are_trimmed() -> None:
    location = _location(code="  BJ-1  ", name="  北京基地  ", address="  亦庄  ")
    assert location.code == "BJ-1"
    assert location.name == "北京基地"
    assert location.address == "亦庄"


@pytest.mark.parametrize(
    ("field", "value", "limit"),
    [
        ("code", "", CODE_MAX_LENGTH),
        ("code", "x" * (CODE_MAX_LENGTH + 1), CODE_MAX_LENGTH),
        ("name", "  ", NAME_MAX_LENGTH),
        ("name", "x" * (NAME_MAX_LENGTH + 1), NAME_MAX_LENGTH),
        ("address", "x" * (ADDRESS_MAX_LENGTH + 1), ADDRESS_MAX_LENGTH),
        ("notes", "x" * (NOTES_MAX_LENGTH + 1), NOTES_MAX_LENGTH),
    ],
)
def test_invalid_text_is_rejected(field: str, value: str, limit: int) -> None:
    with pytest.raises(DomainValidationError):
        _location(**{field: value})


@pytest.mark.parametrize("kind", ["SITE", "ROOM", "LAB"])
def test_known_kinds_are_accepted(kind: str) -> None:
    assert _location(kind=kind).kind is LocationKind(kind)


@pytest.mark.parametrize("kind", ["WAREHOUSE", "site", "", None])
def test_unknown_kind_is_rejected(kind: object) -> None:
    """未知 ``kind`` 一律拒绝，不存在"默认落到 SITE"的分支。"""
    with pytest.raises(DomainValidationError):
        _location(kind=kind)


@pytest.mark.parametrize("version", [0, -1, True, 1.5, "1"])
def test_version_must_be_positive_int(version: object) -> None:
    with pytest.raises(DomainValidationError):
        _location(version=version)


def test_identifiers_must_be_uuid() -> None:
    with pytest.raises(DomainValidationError):
        _location(location_id=str(uuid4()))
    with pytest.raises(DomainValidationError):
        _location(project_id="not-a-uuid")


def test_patch_applies_whitelisted_fields() -> None:
    location = _location()
    now = datetime.now(UTC)
    actor = uuid4()

    updated = location.apply_patch(
        {"name": " 上海基地 ", "kind": "LAB", "notes": "三楼东侧"},
        now=now,
        actor_id=actor,
    )

    assert updated.name == "上海基地"
    assert updated.kind is LocationKind.LAB
    assert updated.notes == "三楼东侧"
    assert updated.updated_at == now
    assert updated.updated_by == actor
    # 原实体不被修改（frozen dataclass 的不可变语义）。
    assert location.name == "北京基地"


def test_patch_rejects_code_and_unknown_fields() -> None:
    location = _location()
    now, actor = datetime.now(UTC), uuid4()
    # ``code`` 是项目内业务键，改它等于换一个场地，不允许原地改名。
    with pytest.raises(DomainValidationError):
        location.apply_patch({"code": "NEW"}, now=now, actor_id=actor)
    with pytest.raises(DomainValidationError):
        location.apply_patch({"project_id": uuid4()}, now=now, actor_id=actor)


def test_patch_requires_at_least_one_field() -> None:
    with pytest.raises(DomainValidationError):
        _location().apply_patch({}, now=datetime.now(UTC), actor_id=uuid4())


def test_patch_distinguishes_explicit_null_from_absent() -> None:
    """显式 ``None`` 清空可空字段；未出现的字段保持不变。

    否则调用方无法清空一个填错的地址——这正是两个语义必须严格区分的原因。
    """
    location = _location(address="旧地址", notes="旧备注")
    now, actor = datetime.now(UTC), uuid4()

    cleared = location.apply_patch({"address": None}, now=now, actor_id=actor)
    assert cleared.address is None
    assert cleared.notes == "旧备注"

    untouched = location.apply_patch({"name": "新名字"}, now=now, actor_id=actor)
    assert untouched.address == "旧地址"
    assert untouched.notes == "旧备注"
