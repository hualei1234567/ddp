"""维护 Worker（阶段 8）：定时备份、事件归档与幂等表清扫。

运行方式::

    DDP_MAINTENANCE_ENABLED=true DDP_BACKUP_DIR=/var/backups/ddp \\
        python -m app.workers.maintenance

为什么要有它：备份脚本此前只能手工执行（`scripts/ops/backup-restore-drill.sh`），
"没人跑"就等于"没有备份"。R-13 的 T-3 也把投递类事件的保留/归档挂在这里。

职责边界：

- **备份**：调用 ``pg_dump``（子进程），产物落到 ``backup_dir``，按保留天数清理旧文件。
  不做 MinIO 全量镜像——对象存储的复制策略属于部署层，本 worker 只管数据库。
- **归档**：把超过保留期的 ``PUBLISHED`` / ``RECORDED`` 事件删掉。
  ``PENDING`` 与 ``FAILED``（死信）**不删**——前者还在等消费者，后者需要人工处理。
- **清扫**：删除已过 ``expires_at`` 的幂等记录。

安全约束：

- 默认关闭（``maintenance_enabled=False``），未启用不会自动改动机器上的数据。
- ``pg_dump`` 用参数列表调用（不经 shell），环境变量只传 ``PGPASSWORD``。
- 备份目录不可写时**失败即报警**，不静默跳过——静默跳过正是"以为有备份"的来源。
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlparse

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.infrastructure.db.models.collection import (
    IdempotencyRecord,
)
from app.infrastructure.db.models.collection import (
    OutboxEvent as OutboxModel,
)
from app.infrastructure.db.session import create_session_factory, get_engine

logger = logging.getLogger("ddp.worker.maintenance")

# 只删除终态事件：PENDING 仍在等消费者，FAILED 需要人工处理。
_ARCHIVABLE_STATUSES = ("PUBLISHED", "RECORDED")


@dataclass(frozen=True, slots=True)
class BackupResult:
    path: Path
    size_bytes: int
    removed: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class SweepResult:
    archived_events: int
    purged_idempotency: int


class MaintenanceWorker:
    def __init__(
        self,
        factory: sessionmaker[Session],
        settings: Settings,
        *,
        database_url: str | None = None,
    ) -> None:
        self._factory = factory
        self._settings = settings
        self._database_url = database_url or settings.database_url

    # ---- 备份 ----
    def backup_once(self, *, now: datetime | None = None) -> BackupResult:
        moment = now or datetime.now(UTC)
        directory = Path(self._settings.backup_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)

        target = directory / f"ddp-{moment.strftime('%Y%m%dT%H%M%SZ')}.sql"
        command = [
            "pg_dump",
            "--no-owner",
            "--no-acl",
            "--file",
            str(target),
            *self._connection_args(),
        ]
        env = dict(os.environ)
        password = self._password()
        if password is not None:
            env["PGPASSWORD"] = password

        try:
            completed = subprocess.run(  # noqa: S603 —— 参数列表调用，不经 shell
                command,
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=self._settings.maintenance_poll_interval_seconds * 30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            # fail-closed：备份失败必须显式失败，不能留下一个空文件冒充成功。
            target.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump 执行失败：{exc}") from exc

        if completed.returncode != 0:
            target.unlink(missing_ok=True)
            raise RuntimeError(
                f"pg_dump 返回 {completed.returncode}：{completed.stderr.strip()[:500]}"
            )
        size = target.stat().st_size
        if size < 1024:
            target.unlink(missing_ok=True)
            raise RuntimeError(f"pg_dump 产物异常小（{size} 字节），已删除")

        removed = self._prune_backups(directory, now=moment)
        logger.info(
            "备份完成",
            extra={"path": str(target), "size_bytes": size, "pruned": len(removed)},
        )
        return BackupResult(path=target, size_bytes=size, removed=removed)

    def _prune_backups(self, directory: Path, *, now: datetime) -> tuple[Path, ...]:
        cutoff = now - timedelta(days=self._settings.backup_retention_days)
        removed: list[Path] = []
        for candidate in sorted(directory.glob("ddp-*.sql")):
            try:
                stamp = datetime.strptime(
                    candidate.stem.removeprefix("ddp-"), "%Y%m%dT%H%M%SZ"
                ).replace(tzinfo=UTC)
            except ValueError:
                # 不认识的命名不动它：可能是人工放进去的备份。
                continue
            if stamp < cutoff:
                candidate.unlink(missing_ok=True)
                removed.append(candidate)
        return tuple(removed)

    def _connection_args(self) -> list[str]:
        parsed = urlparse(self._database_url)
        args: list[str] = []
        if parsed.hostname:
            args += ["--host", parsed.hostname]
        if parsed.port:
            args += ["--port", str(parsed.port)]
        if parsed.username:
            args += ["--username", unquote(parsed.username)]
        database = parsed.path.lstrip("/")
        if database:
            args += ["--dbname", database]
        return args

    def _password(self) -> str | None:
        parsed = urlparse(self._database_url)
        return unquote(parsed.password) if parsed.password else None

    # ---- 归档与清扫 ----
    def sweep_once(self, *, now: datetime | None = None) -> SweepResult:
        moment = now or datetime.now(UTC)
        event_cutoff = moment - timedelta(days=self._settings.outbox_retention_days)
        with self._factory() as session:
            archived = session.execute(
                delete(OutboxModel).where(
                    OutboxModel.status.in_(_ARCHIVABLE_STATUSES),
                    OutboxModel.created_at < event_cutoff,
                )
            ).rowcount
            purged = session.execute(
                delete(IdempotencyRecord).where(IdempotencyRecord.expires_at < moment)
            ).rowcount
            session.commit()
        logger.info(
            "清扫完成",
            extra={"archived_events": archived, "purged_idempotency": purged},
        )
        return SweepResult(archived_events=archived or 0, purged_idempotency=purged or 0)

    def pending_backlog(self) -> dict[str, int]:
        """健康指标：积压量与死信数。供监控抓取，不参与决策。"""
        with self._factory() as session:
            rows = session.execute(
                select(OutboxModel.status, OutboxModel.dispatch)
                .where(OutboxModel.status.in_(("PENDING", "FAILED")))
                .limit(10000)
            ).all()
        pending = sum(1 for status, dispatch in rows if status == "PENDING" and dispatch)
        dead = sum(1 for status, _ in rows if status == "FAILED")
        return {"pending": pending, "dead_letter": dead}

    # ---- 主循环 ----
    def run(self, *, stop_event: threading.Event) -> None:
        next_backup = 0.0
        while not stop_event.is_set():
            now_monotonic = time.monotonic()
            if now_monotonic >= next_backup:
                try:
                    self.backup_once()
                except RuntimeError:
                    # 备份失败要让进程可见（日志 error），但循环继续——
                    # 下一次重试比直接退出更有价值。
                    logger.exception("备份失败")
                next_backup = now_monotonic + self._settings.maintenance_backup_interval_seconds
            try:
                self.sweep_once()
            except Exception:  # noqa: BLE001 —— 清扫失败不应终止备份循环
                logger.exception("清扫失败")
            stop_event.wait(self._settings.maintenance_poll_interval_seconds)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    import signal

    def _stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 维护 Worker（备份/归档/清扫）")
    parser.add_argument("--once", action="store_true", help="备份+清扫各一次后退出")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging()
    if not settings.maintenance_enabled:
        logger.error("维护 worker 未启用：请设置 DDP_MAINTENANCE_ENABLED=true 与 DDP_BACKUP_DIR")
        return 2
    if shutil.which("pg_dump") is None:
        logger.error("未找到 pg_dump；请安装 postgresql-client")
        return 2

    factory = create_session_factory(get_engine())
    worker = MaintenanceWorker(factory, settings)
    if args.once:
        result = worker.backup_once()
        sweep = worker.sweep_once()
        logger.info(
            "单次维护完成",
            extra={
                "backup": str(result.path),
                "size_bytes": result.size_bytes,
                "archived_events": sweep.archived_events,
                "purged_idempotency": sweep.purged_idempotency,
            },
        )
        return 0

    stop_event = threading.Event()
    _install_signal_handlers(stop_event)
    worker.run(stop_event=stop_event)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
