"""Token 成本台账端点测试（V52-010，蓝图 N6）。

覆盖：按 (model_id, purpose) 聚合、金额字符串形态、未配价 null（不是 0）、
项目隔离、权限（仅 owner/平台管理员）、只读、无计量数据时空台账。
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from app.infrastructure.db.models.annotation_ai_token_usage import AiTokenUsage
from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds, make_client

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def _usage(
    *,
    project_id: UUID,
    model_id: str = "ddp-action-vlm",
    purpose: str = "PRE_LABEL",
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cost_estimate: Decimal | None = Decimal("0.0060"),
    actor: UUID | None = None,
) -> AiTokenUsage:
    who = actor or uuid4()
    return AiTokenUsage(
        id=uuid4(),
        project_id=project_id,
        model_id=model_id,
        model_version="1.2.0",
        prompt_version="prompt-7",
        purpose=purpose,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_estimate=cost_estimate,
        occurred_at=NOW,
        version=1,
        created_at=NOW,
        updated_at=NOW,
        created_by=who,
        updated_by=who,
    )


def _url(project_id: UUID) -> str:
    return f"/api/v1/projects/{project_id}/ops/token-ledger"


def _seed(factory: sessionmaker[Session], ids: ApiIds) -> None:
    """审计列有 users 外键：固定两个记账人并复用，避免随机 UUID 撞外键。"""
    from app.infrastructure.db.models.identity import User

    actor_a, actor_b = uuid4(), uuid4()
    with factory() as session:
        session.add_all(
            [
                User(id=actor_a, display_name="记 A"),
                User(id=actor_b, display_name="记 B"),
            ]
        )
        session.flush()
        session.add_all(
            [
                # project_a：两个模型 × 两种用途；其中 EMBED 未配价
                _usage(
                    project_id=ids.project_a,
                    model_id="m-pre",
                    purpose="PRE_LABEL",
                    actor=actor_a,
                ),
                _usage(
                    project_id=ids.project_a,
                    model_id="m-pre",
                    purpose="PRE_LABEL",
                    actor=actor_a,
                ),
                _usage(
                    project_id=ids.project_a,
                    model_id="m-judge",
                    purpose="LLM_JUDGE",
                    input_tokens=2000,
                    output_tokens=100,
                    cost_estimate=Decimal("0.0050"),
                    actor=actor_a,
                ),
                _usage(
                    project_id=ids.project_a,
                    model_id="m-embed",
                    purpose="EMBED",
                    input_tokens=3000,
                    output_tokens=0,
                    cost_estimate=None,
                    actor=actor_a,
                ),
                # 其他项目：不得出现
                _usage(
                    project_id=ids.project_b,
                    model_id="m-pre",
                    purpose="PRE_LABEL",
                    cost_estimate=Decimal("99.0000"),
                    actor=actor_b,
                ),
            ]
        )
        session.commit()


def test_ledger_aggregates_by_model_and_purpose(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    factory, ids = api_db
    _seed(factory, ids)
    owner = make_client(factory, ids.owner_a)

    response = owner.get(_url(ids.project_a))
    assert response.status_code == 200, response.text
    body = response.json()

    buckets = {(entry["model_id"], entry["purpose"]): entry for entry in body["entries"]}
    assert set(buckets) == {
        ("m-embed", "EMBED"),
        ("m-judge", "LLM_JUDGE"),
        ("m-pre", "PRE_LABEL"),
    }
    pre = buckets[("m-pre", "PRE_LABEL")]
    assert pre["call_count"] == 2
    assert pre["input_tokens"] == 2000
    assert pre["output_tokens"] == 1000
    assert pre["total_tokens"] == 3000
    assert pre["cost_estimate"] == "0.0120"
    assert pre["cost_estimated_tokens"] == 3000

    # 未配价模型：成本 null（不是 0），tokens 照常统计
    embed = buckets[("m-embed", "EMBED")]
    assert embed["cost_estimate"] is None
    assert embed["cost_estimated_tokens"] == 0
    assert embed["total_tokens"] == 3000

    # 全项目合计只含有价部分
    assert body["total_cost_estimate"] == "0.0170"


def test_ledger_is_scoped_to_project(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """其他项目的计量行不得出现在本项目的台账里。"""
    factory, ids = api_db
    _seed(factory, ids)
    owner = make_client(factory, ids.owner_a)
    body = owner.get(_url(ids.project_a)).json()
    assert all(entry["model_id"] != "99.0000" for entry in body["entries"])
    assert body["total_cost_estimate"] == "0.0170"  # 若泄漏 project_b 会变 99.0170


def test_empty_ledger_returns_null_total(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """无计量数据：entries 为空数组、合计 null（不是 0）。"""
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    body = owner.get(_url(ids.project_a)).json()
    assert body["entries"] == []
    assert body["total_cost_estimate"] is None
    assert body["generated_at"]


@pytest.mark.parametrize("role_client", ["viewer", "editor", "outsider"])
def test_non_owner_cannot_read_token_ledger(
    api_db: tuple[sessionmaker[Session], ApiIds], role_client: str
) -> None:
    """成本视图只对项目负责人口径开放（owner 或平台管理员）。"""
    factory, ids = api_db
    user = {
        "viewer": ids.viewer_a,
        "editor": ids.editor_a,
        "outsider": ids.outsider,
    }[role_client]
    client = make_client(factory, user)
    response = client.get(_url(ids.project_a))
    assert response.status_code == 403, response.text


def test_platform_admin_can_read_token_ledger(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    from app.domain.identity.models import PlatformRole

    factory, ids = api_db
    _seed(factory, ids)
    admin = make_client(factory, ids.viewer_a, platform_role=PlatformRole.ADMIN)
    response = admin.get(_url(ids.project_a))
    assert response.status_code == 200, response.text


def test_endpoint_is_read_only(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """不提供任何写方法：计量行只走应用层登记通道。"""
    factory, ids = api_db
    owner = make_client(factory, ids.owner_a)
    for method in ("post", "patch", "put", "delete"):
        response = getattr(owner, method)(_url(ids.project_a))
        assert response.status_code == 405, (method, response.status_code)


def test_ledger_does_not_leak_actor_identity(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """聚合视图只含计量与成本字段：登记人身份不得出现。"""
    factory, ids = api_db
    _seed(factory, ids)
    owner = make_client(factory, ids.owner_a)
    raw = owner.get(_url(ids.project_a)).text
    assert "created_by" not in raw
    assert "updated_by" not in raw
