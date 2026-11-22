"""Canonical 导出格式 HTTP API（V31-041）。

只验证**格式选择**的契约面：``CANONICAL`` 被接受、与 ``LE_ROBOT`` 版本交叉
组合被拒绝、未知格式仍返回 ``export_format_unsupported``。产物内容由
``test_export_canonical_format.py`` 覆盖。

冻结链路复用 ``build_frozen_version``：它走真实的
create → build → validate → freeze 四步，不绕过任何状态机。
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from tests.api.conftest import ApiIds
from tests.api.dataset_support import url
from tests.api.export_support import build_frozen_version, export_payload


def _create_export(editor, project_id, version_id, **overrides):
    return editor.post(
        url(project_id, "/exports"),
        json=export_payload(version_id, **overrides),
        headers={"Idempotency-Key": f"export-{uuid4().hex[:12]}"},
    )


def test_canonical_format_is_accepted(api_db: tuple[sessionmaker[Session], ApiIds]) -> None:
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids, code=f"canon-ok-{uuid4().hex[:8]}")

    response = _create_export(
        ctx["editor"],
        ids.project_a,
        ctx["version_id"],
        format="CANONICAL",
        format_version="ddp.canonical/1.0.0",
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["format"] == "CANONICAL"
    assert body["format_version"] == "ddp.canonical/1.0.0"


def test_canonical_rejects_lerobot_format_version(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """格式与版本必须成对；交叉组合会让 Loader 用错解析器。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids, code=f"canon-cross-{uuid4().hex[:8]}")

    response = _create_export(
        ctx["editor"],
        ids.project_a,
        ctx["version_id"],
        format="CANONICAL",
        format_version="ddp.lerobot/1.0.0",
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "export_format_version_unsupported"


def test_unknown_format_is_still_rejected(
    api_db: tuple[sessionmaker[Session], ApiIds],
) -> None:
    """新增 CANONICAL 不得放宽未知格式的拒绝。"""
    factory, ids = api_db
    ctx = build_frozen_version(factory, ids, code=f"canon-bad-{uuid4().hex[:8]}")

    response = _create_export(
        ctx["editor"],
        ids.project_a,
        ctx["version_id"],
        format="PARQUET",
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "export_format_unsupported"
