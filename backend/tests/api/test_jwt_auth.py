"""接口认证测试：Bearer JWT 映射、负向用例与本地 Header 后门边界。"""

from __future__ import annotations

from typing import Annotated
from uuid import uuid4

from app.api.deps import get_current_principal
from app.core.config import Settings, get_settings
from app.domain.identity.models import PlatformRole, Principal
from app.main import create_app
from fastapi import Depends
from fastapi.testclient import TestClient
from httpx import Response

from tests.jwt_support import (
    JWT_AUDIENCE,
    JWT_ISSUER,
    JWT_SECRET,
    make_token,
    tamper_payload,
)

_STAGING = Settings(
    env="staging",
    jwt_secret=JWT_SECRET,
    jwt_issuer=JWT_ISSUER,
    jwt_audience=JWT_AUDIENCE,
)
_LOCAL_HEADER_AUTH = Settings(
    env="local",
    local_header_auth=True,
    jwt_secret=JWT_SECRET,
    jwt_issuer=JWT_ISSUER,
    jwt_audience=JWT_AUDIENCE,
)
_LOCAL_DEFAULT = Settings(env="local")


def _build_client(settings: Settings) -> TestClient:
    app = create_app()

    @app.get("/test/me")
    def me(
        principal: Annotated[Principal, Depends(get_current_principal)],
    ) -> dict[str, str]:
        return {
            "user_id": str(principal.user_id),
            "platform_role": principal.platform_role.value,
        }

    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app)


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _assert_unauthorized(response: Response) -> None:
    assert response.status_code == 401
    body = response.json()
    assert body["error"]["code"] == "not_authenticated"


def test_valid_token_maps_principal_with_member_role() -> None:
    user_id = uuid4()
    client = _build_client(_STAGING)

    response = client.get("/test/me", headers=_bearer(make_token(subject=user_id)))

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(user_id),
        "platform_role": PlatformRole.MEMBER.value,
    }


def test_valid_token_maps_platform_admin_role() -> None:
    user_id = uuid4()
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(subject=user_id, platform_role="admin")),
    )

    assert response.status_code == 200
    assert response.json() == {
        "user_id": str(user_id),
        "platform_role": PlatformRole.ADMIN.value,
    }


def test_token_without_role_maps_to_member() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(platform_role=None)),
    )

    assert response.status_code == 200
    assert response.json()["platform_role"] == PlatformRole.MEMBER.value


def test_unknown_platform_role_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(platform_role="superuser")),
    )

    _assert_unauthorized(response)


def test_non_uuid_subject_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(subject="not-a-uuid")),
    )

    _assert_unauthorized(response)


def test_missing_token_is_rejected_in_staging() -> None:
    client = _build_client(_STAGING)

    response = client.get("/test/me")

    _assert_unauthorized(response)


def test_expired_token_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get("/test/me", headers=_bearer(make_token(expires_in=-120)))

    _assert_unauthorized(response)


def test_wrong_signature_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(secret="attacker-known-secret")),  # noqa: S106 - 负向用例
    )

    _assert_unauthorized(response)


def test_tampered_payload_is_rejected() -> None:
    token = make_token(platform_role="member")
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(tamper_payload(token, {"platform_role": "admin"})),
    )

    _assert_unauthorized(response)


def test_wrong_issuer_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(issuer="another-issuer")),
    )

    _assert_unauthorized(response)


def test_wrong_audience_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(audience="another-api")),
    )

    _assert_unauthorized(response)


def test_alg_none_token_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(algorithm="none")),
    )

    _assert_unauthorized(response)


def test_not_yet_valid_token_is_rejected() -> None:
    client = _build_client(_STAGING)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(not_before=2_000_000_000)),
    )

    _assert_unauthorized(response)


def test_expired_and_bad_signature_share_the_same_message() -> None:
    client = _build_client(_STAGING)

    expired = client.get("/test/me", headers=_bearer(make_token(expires_in=-120)))
    bad_signature = client.get(
        "/test/me",
        headers=_bearer(make_token(secret="attacker-known-secret")),  # noqa: S106 - 负向用例
    )

    assert expired.json()["error"]["message"] == bad_signature.json()["error"]["message"]


def test_local_header_auth_falls_back_without_authorization() -> None:
    user_id = uuid4()
    client = _build_client(_LOCAL_HEADER_AUTH)

    response = client.get("/test/me", headers={"X-User-Id": str(user_id)})

    assert response.status_code == 200
    assert response.json()["user_id"] == str(user_id)


def test_local_header_auth_is_disabled_by_default() -> None:
    client = _build_client(_LOCAL_DEFAULT)

    response = client.get("/test/me", headers={"X-User-Id": str(uuid4())})

    _assert_unauthorized(response)


def test_bearer_token_takes_precedence_over_header_identity() -> None:
    token_user = uuid4()
    header_user = uuid4()
    client = _build_client(_LOCAL_HEADER_AUTH)

    response = client.get(
        "/test/me",
        headers={
            **_bearer(make_token(subject=token_user)),
            "X-User-Id": str(header_user),
        },
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(token_user)


def test_malformed_authorization_does_not_fall_back_to_header_identity() -> None:
    client = _build_client(_LOCAL_HEADER_AUTH)

    response = client.get(
        "/test/me",
        headers={
            "Authorization": "Token not-a-bearer",
            "X-User-Id": str(uuid4()),
        },
    )

    _assert_unauthorized(response)


def test_invalid_bearer_does_not_fall_back_to_header_identity() -> None:
    client = _build_client(_LOCAL_HEADER_AUTH)

    response = client.get(
        "/test/me",
        headers={
            **_bearer(make_token(secret="attacker-known-secret")),  # noqa: S106 - 负向用例
            "X-User-Id": str(uuid4()),
        },
    )

    _assert_unauthorized(response)


def test_header_identity_is_rejected_in_non_local_environment() -> None:
    client = _build_client(_STAGING)

    response = client.get("/test/me", headers={"X-User-Id": str(uuid4())})

    _assert_unauthorized(response)


def test_non_local_header_auth_flag_cannot_reenable_backdoor() -> None:
    """即使误配 DDP_LOCAL_HEADER_AUTH=true，非 local 也不能走 Header 认证。"""
    misconfigured = Settings(
        env="staging",
        local_header_auth=True,
        jwt_secret=JWT_SECRET,
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
    )
    client = _build_client(misconfigured)

    response = client.get("/test/me", headers={"X-User-Id": str(uuid4())})

    _assert_unauthorized(response)


def test_bearer_jwt_works_in_local_environment_with_header_auth_disabled() -> None:
    user_id = uuid4()
    local_settings = Settings(
        env="local",
        jwt_secret="local-development-secret",  # noqa: S106 - 本地测试密钥
        jwt_issuer=JWT_ISSUER,
        jwt_audience=JWT_AUDIENCE,
    )
    client = _build_client(local_settings)

    response = client.get(
        "/test/me",
        headers=_bearer(make_token(subject=user_id, secret=local_settings.jwt_secret)),
    )

    assert response.status_code == 200
    assert response.json()["user_id"] == str(user_id)
