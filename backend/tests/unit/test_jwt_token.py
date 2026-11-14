"""HS256 JWT 校验单元测试：签名、时间、iss/aud 与声明映射。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from app.infrastructure.auth.jwt import (
    JwtClaims,
    TokenValidationError,
    decode_hs256_token,
)

from tests.jwt_support import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET,
    make_token,
    tamper_payload,
)


def _decode(token: str, *, leeway_seconds: int = 0, now: float | None = None) -> JwtClaims:
    return decode_hs256_token(
        token,
        secret=JWT_SECRET,
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
        leeway_seconds=leeway_seconds,
        now=now,
    )


def test_valid_token_maps_claims() -> None:
    user_id = uuid4()

    claims = _decode(make_token(subject=user_id, platform_role="admin"))

    assert claims == JwtClaims(subject=str(user_id), platform_role="admin")


def test_missing_platform_role_defaults_to_member() -> None:
    claims = _decode(make_token(platform_role=None))

    assert claims.platform_role == "member"


def test_audience_list_is_accepted() -> None:
    token = make_token(audience=["other-api", JWT_AUDIENCE])

    assert _decode(token).subject


def test_expired_token_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(expires_in=-1))


def test_expiry_respects_leeway() -> None:
    token = make_token(expires_in=-5)

    assert _decode(token, leeway_seconds=30).subject


def test_missing_exp_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(omit=("exp",)))


def test_bool_exp_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(extra_claims={"exp": True}))


@pytest.mark.parametrize("expires_at", [float("nan"), float("inf")])
def test_non_finite_exp_is_rejected(expires_at: float) -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(extra_claims={"exp": expires_at}))


def test_nbf_in_the_future_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(not_before=2_000_000_000))


def test_wrong_signature_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(secret=uuid4().hex))  # 错误签名用例：随机密钥


def test_tampered_payload_is_rejected() -> None:
    token = make_token()
    tampered = tamper_payload(token, {"platform_role": "admin"})

    with pytest.raises(TokenValidationError):
        _decode(tampered)


def test_wrong_issuer_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(issuer="another-issuer"))


def test_missing_issuer_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(omit=("iss",)))


def test_wrong_audience_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(audience="another-api"))


def test_missing_audience_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(omit=("aud",)))


def test_alg_none_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(algorithm="none"))


def test_non_hs256_algorithm_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(algorithm="HS512"))


def test_missing_subject_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        _decode(make_token(omit=("sub",)))


def test_non_uuid_subject_is_accepted_by_adapter_but_mapped_later() -> None:
    """适配器只保证 sub 是非空字符串；UUID 映射由 API 层负责。"""
    claims = _decode(make_token(subject="not-a-uuid"))

    assert claims.subject == "not-a-uuid"


@pytest.mark.parametrize(
    "token",
    [
        "",
        "not-a-jwt",
        "a.b",
        "a.b.c.d",
        "a..c",
        "a.b.",
        "课程.令牌.签名",
        "a.b.YWJj!",
    ],
)
def test_malformed_tokens_are_rejected(token: str) -> None:
    with pytest.raises(TokenValidationError):
        _decode(token)


def test_empty_secret_is_rejected() -> None:
    with pytest.raises(TokenValidationError):
        decode_hs256_token(
            make_token(secret=""),
            secret="",
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
        )


# --- PyJWT 迁移后的负向探针 -------------------------------------------------
# 这些用例锁定"换库不换语义"：手写实现原有的拒绝行为必须全部保留。


def test_crit_header_is_rejected() -> None:
    """``crit`` 扩展意味着校验方必须理解该扩展；未实现时必须拒绝。"""
    import base64 as _b64
    import hashlib as _hash
    import hmac as _hmac
    import json as _json
    import time as _time

    header = {"alg": "HS256", "typ": "JWT", "crit": ["exp"]}
    payload = {
        "sub": str(uuid4()),
        "iss": JWT_ISSUER,
        "aud": JWT_AUDIENCE,
        "exp": int(_time.time()) + 300,
    }

    def _seg(value: dict[str, object]) -> str:
        raw = _json.dumps(value, separators=(",", ":")).encode("utf-8")
        return _b64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    header_seg, payload_seg = _seg(header), _seg(payload)
    signing_input = f"{header_seg}.{payload_seg}".encode("ascii")
    signature = _hmac.new(JWT_SECRET.encode("utf-8"), signing_input, _hash.sha256).digest()
    token = f"{header_seg}.{payload_seg}."
    token += _b64.urlsafe_b64encode(signature).rstrip(b"=").decode("ascii")

    with pytest.raises(TokenValidationError, match="crit"):
        _decode(token)


def test_oversized_token_is_rejected() -> None:
    with pytest.raises(TokenValidationError, match="超长"):
        _decode("a" * 9000)


def test_non_ascii_token_is_rejected() -> None:
    with pytest.raises(TokenValidationError, match="ASCII"):
        _decode("课程.令牌.签名")


def test_negative_leeway_is_rejected() -> None:
    with pytest.raises(TokenValidationError, match="容差"):
        _decode(make_token(), leeway_seconds=-1)
