"""issue_jwt.py 单元测试（遵循 MEM-INC-030：顶层不引入数据库或后端重依赖）。"""

from __future__ import annotations

import datetime
import importlib.util
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "ops" / "issue_jwt.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("issue_jwt", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_jwt_claims_validates_uuid_and_role() -> None:
    mod = _load_module()
    fixed_now = datetime.datetime(2026, 11, 28, 0, 0, 0, tzinfo=datetime.UTC)
    uid = "01960000-0000-7000-8000-000000000001"

    claims = mod.build_jwt_claims(
        user_id=uid,
        role="admin",
        issuer="ddp",
        audience="ddp-api",
        ttl_seconds=3600,
        now=fixed_now,
    )
    assert claims["sub"] == uid
    assert claims["platform_role"] == "admin"
    assert claims["iss"] == "ddp"
    assert claims["aud"] == "ddp-api"
    assert claims["exp"] == int(fixed_now.timestamp()) + 3600


def test_build_jwt_claims_rejects_invalid_role_or_uuid() -> None:
    mod = _load_module()
    with pytest.raises(ValueError, match="非法平台角色"):
        mod.build_jwt_claims(
            user_id="01960000-0000-7000-8000-000000000001",
            role="platform_admin",
            issuer="ddp",
            audience="ddp-api",
            ttl_seconds=3600,
        )

    with pytest.raises(ValueError):
        mod.build_jwt_claims(
            user_id="not-a-uuid",
            role="member",
            issuer="ddp",
            audience="ddp-api",
            ttl_seconds=3600,
        )


def test_normalize_pg_dsn_strips_sqlalchemy_driver_prefix() -> None:
    mod = _load_module()
    assert (
        mod.normalize_pg_dsn("postgresql+psycopg://u:p@localhost:5432/ddp")
        == "postgresql://u:p@localhost:5432/ddp"
    )


def test_sign_hs256_jwt_roundtrip_with_backend_decoder() -> None:
    pytest.importorskip("jwt")
    from app.infrastructure.auth.jwt import decode_hs256_token

    mod = _load_module()
    uid = "01960000-0000-7000-8000-000000000002"
    test_key = "unit-test-only-hs256-signing-key-32chars"
    claims = mod.build_jwt_claims(
        user_id=uid,
        role="admin",
        issuer="ddp",
        audience="ddp-api",
        ttl_seconds=600,
    )
    token = mod.sign_hs256_jwt(claims, test_key)
    decoded = decode_hs256_token(
        token,
        secret=test_key,
        issuer="ddp",
        audience="ddp-api",
    )
    assert decoded.subject == uid
    assert decoded.platform_role == "admin"
