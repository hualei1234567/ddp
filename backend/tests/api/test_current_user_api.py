"""`GET /api/v1/me` 集成测试：认证主体自述。

为什么需要这个端点：浏览器里只有外部签发的 Bearer JWT，前端读不到令牌里的
用户 ID；「我的任务」「我的工作量」这类个人视图因此无法在前端推断身份。
服务端是唯一权威来源：本端点只回显**已通过认证**的主体，不接受任何客户端
传入的用户 ID，也不复用「项目可见性」语义（那是 `/projects` 的职责）。
"""

from __future__ import annotations

from uuid import uuid4

from app.domain.identity.models import PlatformRole
from app.infrastructure.db.models.identity import User
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

ME = "/api/v1/me"


def test_returns_authenticated_member(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    response = make_client(factory, ids.editor_a).get(ME)

    assert response.status_code == 200, response.text
    assert response.json() == {
        "user_id": str(ids.editor_a),
        "display_name": "编辑者",
        "platform_role": "member",
    }


def test_platform_admin_role_is_reported(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    admin_id = uuid4()
    with factory() as session:
        session.add(User(id=admin_id, display_name="平台管理员"))
        session.commit()

    body = make_client(factory, admin_id, platform_role=PlatformRole.ADMIN).get(ME).json()

    assert body["user_id"] == str(admin_id)
    assert body["platform_role"] == "admin"


def test_unknown_user_is_not_fabricated(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, _ids = api_db
    response = make_client(factory, uuid4()).get(ME)

    # 令牌有效但用户目录里没有这个人：如实 404，不返回半条资料，
    # 也不隐式创建账号（身份签发不属于本平台）。
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_client_cannot_override_user_id(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    client: TestClient = make_client(factory, ids.editor_a)
    response = client.get(
        ME,
        headers={"X-User-Id": str(ids.owner_a)},
        params={"user_id": str(ids.owner_a), "project_id": str(ids.project_a)},
    )

    assert response.status_code == 200, response.text
    assert response.json()["user_id"] == str(ids.editor_a)
