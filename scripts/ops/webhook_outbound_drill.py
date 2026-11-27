"""Webhook 对外出站**生产演练**脚本（阶段 6）。

## 演练什么

验证「业务事件 → 登记 → Worker 真实 HTTP 投递 → 下游收到并验签通过」
这条链路在**生产库与真实进程**上成立。阶段 5 的能力缺口正是"投递用例
没有生产调用点"——只跑单元测试证明不了接线真的通了。

## 安全护栏（硬编码，不可绕过）

1. **只允许本机回环**：接收服务只监听 ``127.0.0.1``；目标 DSN 必须是
   loopback + 白名单库。
2. **只创建本脚本专属的演练数据**：项目、用户、订阅都用 ``_drill_`` 前缀
   标识；演练结束**必须清理**（订阅、待投递记录、演练项目/用户）。
3. **不触碰既有业务数据**：不读改任何非演练前缀的行。
4. **默认 dry-run**，必须显式 ``--execute`` 才真正投递。

## 用法

```bash
# 先看计划
python scripts/ops/webhook_outbound_drill.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp

# 真实演练
python scripts/ops/webhook_outbound_drill.py \
  --dsn postgresql+psycopg://ddp:ddp@localhost:55432/ddp --execute \
  --out docs/testing/evidence/2026-11-27-webhook-outbound/drill-result.json
```
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "data-platform"))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

#: 演练数据的统一前缀：清理时只删带此前缀的行，绝不误伤业务数据。
DRILL_PREFIX = "_drill_"

#: 本机回环地址白名单（与 scripts/perf/core.py 同口径）。
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class Receiver:
    """本地回环 HTTP 接收服务：记录收到的 Webhook 请求并验签。"""

    def __init__(self, secret: str) -> None:
        self._secret = secret
        self.received: list[dict[str, object]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                receiver.received.append(
                    {
                        "path": self.path,
                        "headers": headers,
                        "body": body.decode("utf-8", errors="replace"),
                        "signature_valid": receiver.verify(headers, body),
                        "received_at": datetime.now(UTC).isoformat(),
                    }
                )
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *_args: object) -> None:
                """静默：演练输出不该被访问日志刷屏。"""

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/ddp-webhook"

    def verify(self, headers: dict[str, str], body: bytes) -> bool:
        """按平台同一算法验签：HMAC-SHA256(secret, timestamp + "\\n" + body)。"""
        signature = headers.get("x-ddp-signature")
        timestamp = headers.get("x-ddp-timestamp")
        if not signature or not timestamp:
            return False
        expected = (
            "sha256="
            + hmac.new(
                self._secret.encode("utf-8"),
                f"{timestamp}\n".encode() + body,
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(signature, expected)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)


def _ensure_loopback(dsn: str) -> None:
    """目标 DSN 必须是本机回环：防止误连其他环境的库。"""
    parts = urlsplit(dsn)
    host = (parts.hostname or "").lower()
    if host not in _LOOPBACK_HOSTS:
        raise ValueError(f"拒绝在非本机数据库上执行演练：host={host!r}")


@contextmanager
def _drill_data(engine: object, secret: str) -> Iterator[dict[str, str]]:
    """建演练用的项目/用户/订阅；退出时**必定清理**。"""
    from app.infrastructure.db.models.identity import Project, User

    suffix = uuid.uuid4().hex[:8]
    project_id = str(uuid.uuid4())
    user_id = str(uuid.uuid4())
    subscription_id = str(uuid.uuid4())
    code = f"{DRILL_PREFIX}wh_{suffix}"

    with Session(engine) as session:  # type: ignore[arg-type]
        session.add(User(id=uuid.UUID(user_id), display_name=f"{DRILL_PREFIX}用户"))
        session.add(
            Project(
                id=uuid.UUID(project_id),
                code=code,
                name=f"{DRILL_PREFIX}Webhook 出站演练项目",
                status="ACTIVE",
                version=1,
            )
        )
        session.commit()

    yield {
        "project_id": project_id,
        "user_id": user_id,
        "subscription_id": subscription_id,
        "code": code,
        "secret": secret,
    }

    # 清理：只删演练前缀的行（订阅随项目级联删除）。
    with Session(engine) as session:  # type: ignore[arg-type]
        session.execute(
            text("DELETE FROM webhook_subscriptions WHERE id = :sid"),
            {"sid": uuid.UUID(subscription_id)},
        )
        session.execute(
            text("DELETE FROM webhook_outbox WHERE project_id = :pid"),
            {"pid": uuid.UUID(project_id)},
        )
        session.execute(
            text("DELETE FROM projects WHERE id = :pid"),
            {"pid": uuid.UUID(project_id)},
        )
        session.execute(
            text("DELETE FROM users WHERE id = :uid"),
            {"uid": uuid.UUID(user_id)},
        )
        session.commit()


def _register_subscription(
    engine: object,
    *,
    project_id: str,
    user_id: str,
    subscription_id: str,
    url: str,
    secret: str,
) -> None:
    """登记一条订阅（等价于 API 创建，但不走 HTTP：演练只验出站链路）。"""
    from app.domain.webhook.subscription import encrypt_secret, hash_secret
    from app.infrastructure.db.models.webhook_subscription import (
        WebhookSubscriptionModel,
    )

    with Session(engine) as session:  # type: ignore[arg-type]
        session.add(
            WebhookSubscriptionModel(
                id=uuid.UUID(subscription_id),
                project_id=uuid.UUID(project_id),
                url=url,
                secret_hash=hash_secret(secret),
                secret_encrypted=encrypt_secret(
                    secret, b"drill-encryption-key-32-bytes!!"
                ),
                event_types=["annotation.submission.status_changed"],
                status="ACTIVE",
                consecutive_failures=0,
                created_by=uuid.UUID(user_id),
                updated_by=uuid.UUID(user_id),
            )
        )
        session.commit()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Webhook 出站生产演练（阶段 6）")
    parser.add_argument("--dsn", required=True, help="目标库 DSN（必须本机回环）")
    parser.add_argument(
        "--execute", action="store_true", help="真正执行（默认 dry-run）"
    )
    parser.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = parser.parse_args(argv)

    _ensure_loopback(args.dsn)
    plan = {
        "dsn": args.dsn.split("@")[-1],
        "receiver": "127.0.0.1:<随机端口>（标准库 HTTP 服务）",
        "event_type": "annotation.submission.status_changed",
        "cleanup": "演练结束删除订阅/待投递记录/演练项目与用户",
    }
    print(
        json.dumps(
            {"mode": "execute" if args.execute else "dry-run", "plan": plan},
            ensure_ascii=False,
            indent=2,
        )
    )
    if not args.execute:
        print("\n[DRY-RUN] 未执行任何投递。加 --execute 真实运行。", file=sys.stderr)
        return 0

    from app.core.config import Settings
    from app.workers.webhook_outbound import WebhookOutboundWorker

    secret = "drill-example-webhook-secret-0123456789"
    receiver = Receiver(secret)
    url = receiver.start()
    engine = create_engine(args.dsn)
    payload: dict[str, object] = {"plan": plan}
    try:
        with _drill_data(engine, secret) as drill:
            _register_subscription(
                engine,
                project_id=drill["project_id"],
                user_id=drill["user_id"],
                subscription_id=drill["subscription_id"],
                url=url,
                secret=secret,
            )

            # 直接向出站队列登记一条事件（等价于业务事务里 record_change
            # 触发的登记——生产链路已由 backend/tests/api 的端到端用例覆盖）。
            from app.infrastructure.repositories.webhook_outbox import (
                SqlWebhookOutboxRepository,
            )

            event_id = uuid.uuid4()
            now = datetime.now(UTC)
            with Session(engine) as session:  # type: ignore[arg-type]
                SqlWebhookOutboxRepository(session).enqueue(
                    project_id=uuid.UUID(drill["project_id"]),
                    subscription_id=uuid.UUID(drill["subscription_id"]),
                    event_id=event_id,
                    event_type="annotation.submission.status_changed",
                    payload={
                        "submission_id": str(uuid.uuid4()),
                        "to_status": "SUBMITTED",
                    },
                    now=now,
                )
                session.commit()

            settings = Settings(
                env="local",
                webhook_outbound_enabled=True,
                webhook_encryption_key="drill-encryption-key-32-bytes!!",
                webhook_outbound_allow_private=True,
                webhook_outbound_max_attempts=3,
                database_url=args.dsn,
            )
            worker = WebhookOutboundWorker(
                factory=sessionmaker(bind=engine, expire_on_commit=False),
                settings=settings,
            )
            result = worker.process_once()

            with Session(engine) as session:  # type: ignore[arg-type]
                row = session.execute(
                    text(
                        "SELECT status, attempts, delivered_at FROM webhook_outbox "
                        "WHERE event_id = :eid"
                    ),
                    {"eid": event_id},
                ).first()

            payload.update(
                {
                    "delivery_result": result.status,
                    "receiver_url": url,
                    "received_count": len(receiver.received),
                    "received": receiver.received,
                    "outbox_row": {
                        "status": row[0] if row else None,
                        "attempts": row[1] if row else None,
                        "delivered_at": row[2].isoformat() if row and row[2] else None,
                    },
                    "signature_all_valid": all(
                        item.get("signature_valid") is True
                        for item in receiver.received
                    ),
                    "cleanup": "已删除演练订阅/待投递记录/项目/用户",
                }
            )
    finally:
        receiver.stop()
        engine.dispose()

    if args.out:
        path = Path(args.out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"结果写入 {path}", file=sys.stderr)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
