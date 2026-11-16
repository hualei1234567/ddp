"""项目管理 API 测试辅助。

不是测试用例文件（文件名不以 ``test_`` 开头），只放两个测试文件都要用的
构造器：管理员账号种子与请求体。
"""

from __future__ import annotations

from uuid import UUID, uuid4

from app.infrastructure.db.models.identity import User
from sqlalchemy.orm import Session, sessionmaker


def seed_user(
    factory: sessionmaker[Session],
    *,
    user_id: UUID | None = None,
    display_name: str = "平台管理员",
) -> UUID:
    """登记一个用户目录条目；返回其 ID。"""
    identifier = user_id or uuid4()
    with factory() as session:
        session.add(User(id=identifier, display_name=display_name))
        session.commit()
    return identifier


def project_payload(code: str = "prj-new", **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": code,
        "name": f"项目 {code}",
        "description": "测试用项目",
    }
    payload.update(overrides)
    return payload


def member_payload(user_id: object, role: str = "viewer") -> dict[str, object]:
    return {"user_id": str(user_id), "role": role}


__all__ = ["member_payload", "project_payload", "seed_user"]
