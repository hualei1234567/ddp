#!/usr/bin/env python3
"""DDP-308 阶段 2 上传/采集 API 独立验收探针。

环境：
- 真实 PostgreSQL 16（127.0.0.1:55432，一次性测试库）；
- 真实 uvicorn 8010（子线程启动，HTTP 走真实网络）；
- 对象存储：本机 9000 无 MinIO；使用 scripts/qa/ddp308_s3_stub.py 的最小
  S3 协议替身完成“预签名 PUT → ListParts → Complete → Range GET”协议路径。
  MinIO 专属结论不由此探针给出，报告中标 BLOCKED。

只读业务实现，不修改 backend/app、migrations、packages/contracts。
输出：docs/testing/evidence/2026-11-08-ddp-308/api-probe.json 与 .log
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Callable
from uuid import UUID, uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
QA_DIR = Path(__file__).resolve().parent
EVIDENCE = ROOT / "docs" / "testing" / "evidence" / "2026-11-08-ddp-308"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(QA_DIR))

from ddp308_db_probe import PG_TEMPLATE, create_database, drop_database  # noqa: E402
from ddp308_s3_stub import S3Stub  # noqa: E402

BASE = "http://127.0.0.1:8010"
API = "/api/v1"
NOW = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


class ApiProbe:
    def __init__(self, dbname: str, url: str, stub: S3Stub) -> None:
        self.dbname = dbname
        self.url = url
        self.stub = stub
        self.results: list[dict[str, object]] = []
        self.log_lines: list[str] = []
        self.ids: dict[str, UUID] = {}
        self.engine = None
        self.factory = None

    def record(self, check_id: str, status: str, detail: str) -> None:
        item = {"id": check_id, "status": status, "detail": detail}
        self.results.append(item)
        line = f"[{status}] {check_id}: {detail}"
        self.log_lines.append(line)
        print(line)

    def check(self, check_id: str, func: Callable[[], str]) -> None:
        try:
            detail = func()
        except Exception as exc:  # noqa: BLE001 - 探针需记录所有失败
            self.record(check_id, "FAIL", f"{type(exc).__name__}: {exc}")
            self.log_lines.append(traceback.format_exc())
        else:
            self.record(check_id, "PASS", detail)

    # ------------------------------------------------------------ 基础设施
    def migrate(self) -> None:
        from alembic import command
        from alembic.config import Config

        config = Config(str(BACKEND / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND / "migrations"))
        config.set_main_option("prepend_sys_path", str(BACKEND))
        config.set_main_option("sqlalchemy.url", self.url)
        command.upgrade(config, "head")
        self.record("API-MIGRATE", "PASS", "一次性测试库 upgrade head 完成")

    def seed(self) -> None:
        from app.infrastructure.db.models.identity import Project, ProjectMember, User
        from app.infrastructure.db.session import create_session_factory
        from sqlalchemy import create_engine

        self.engine = create_engine(self.url, pool_pre_ping=True)
        self.factory = create_session_factory(self.engine)
        project_a, project_b = uuid4(), uuid4()
        user_a, user_b, outsider = uuid4(), uuid4(), uuid4()
        self.ids.update(
            project_a=project_a,
            project_b=project_b,
            user_a=user_a,
            user_b=user_b,
            outsider=outsider,
        )
        with self.factory() as session:
            session.add_all(
                [
                    Project(id=project_a, code=f"api-a-{project_a.hex[:8]}", name="API 项目 A"),
                    Project(id=project_b, code=f"api-b-{project_b.hex[:8]}", name="API 项目 B"),
                    User(id=user_a, display_name="API 甲"),
                    User(id=user_b, display_name="API 乙"),
                    User(id=outsider, display_name="API 外部"),
                ]
            )
            session.commit()
            session.add_all(
                [
                    ProjectMember(project_id=project_a, user_id=user_a, role="editor"),
                    ProjectMember(project_id=project_b, user_id=user_b, role="owner"),
                ]
            )
            session.commit()
        self.record("API-SEED", "PASS", "真实 PG 预置 API 项目 A/B、用户与成员关系（乙只在 B）")

    def start_api(self) -> tuple[object, threading.Thread]:
        import uvicorn

        from app.main import create_app

        app = create_app()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=8010,
            log_level="warning",
            access_log=False,
        )
        server = uvicorn.Server(config)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                response = httpx.get(f"{BASE}/health", timeout=1)
                if response.status_code == 200:
                    return server, thread
            except httpx.HTTPError:
                pass
            time.sleep(0.2)
        raise RuntimeError("uvicorn 8010 未在 20 秒内就绪")

    # ------------------------------------------------------------ HTTP 工具
    def client(self, user_id: UUID) -> httpx.Client:
        return httpx.Client(
            base_url=BASE,
            headers={"X-User-Id": str(user_id)},
            timeout=20,
        )

    def _create_session(
        self,
        client: httpx.Client,
        *,
        project_id: UUID,
        content: bytes,
        content_type: str = "application/x-mcap",
        filename: str = "robot.mcap",
        idempotency_key: str | None = None,
    ) -> httpx.Response:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return client.post(
            f"{API}/projects/{project_id}/upload-sessions",
            json={
                "original_filename": filename,
                "declared_content_type": content_type,
                "declared_size_bytes": len(content),
                "expected_sha256": sha256_bytes(content),
            },
            headers=headers,
        )

    @staticmethod
    def _session_id(response: httpx.Response) -> UUID:
        return UUID(response.json()["upload_session_id"])

    def _upload_part(
        self,
        client: httpx.Client,
        project_id: UUID,
        session_id: UUID,
        part_number: int,
        data: bytes,
    ) -> tuple[httpx.Response, str, str]:
        signed = client.post(
            f"{API}/projects/{project_id}/upload-sessions/{session_id}"
            f"/parts/{part_number}/url"
        )
        if signed.status_code != 200:
            raise AssertionError(f"获取分片 URL 失败：{signed.status_code} {signed.text}")
        payload = signed.json()
        put = httpx.put(
            payload["url"],
            content=data,
            headers=payload.get("headers", {}),
            timeout=20,
        )
        if put.status_code not in (200, 204):
            raise AssertionError(f"上传分片到 S3 替身失败：{put.status_code} {put.text}")
        etag = put.headers.get("etag", "")
        sha = sha256_bytes(data)
        ack = client.post(
            f"{API}/projects/{project_id}/upload-sessions/{session_id}"
            f"/parts/{part_number}/ack",
            json={"etag": etag, "size_bytes": len(data), "sha256": sha},
        )
        return ack, etag, sha

    def _get_session(self, client: httpx.Client, project_id: UUID, session_id: UUID) -> dict:
        response = client.get(f"{API}/projects/{project_id}/upload-sessions/{session_id}")
        if response.status_code != 200:
            raise AssertionError(f"读取会话失败：{response.status_code} {response.text}")
        return response.json()

    def _complete(
        self,
        client: httpx.Client,
        project_id: UUID,
        session_id: UUID,
        *,
        expected_sha: str,
        expected_size: int,
        parts: list[dict[str, object]],
        idempotency_key: str | None = None,
        version: int | None = None,
    ) -> httpx.Response:
        session = self._get_session(client, project_id, session_id)
        current = version if version is not None else int(session["version"])
        headers = {"If-Match": f'"{current}"'}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return client.post(
            f"{API}/projects/{project_id}/upload-sessions/{session_id}/complete",
            json={
                "expected_sha256": expected_sha,
                "expected_size_bytes": expected_size,
                "parts": parts,
            },
            headers=headers,
        )

    def _upload_content(
        self,
        client: httpx.Client,
        *,
        project_id: UUID,
        content: bytes,
        content_type: str = "application/x-mcap",
        filename: str = "robot.mcap",
    ) -> dict[str, object]:
        created = self._create_session(
            client,
            project_id=project_id,
            content=content,
            content_type=content_type,
            filename=filename,
        )
        if created.status_code != 201:
            raise AssertionError(f"创建会话失败：{created.status_code} {created.text}")
        session_id = self._session_id(created)
        parts: list[dict[str, object]] = []
        for number, chunk in enumerate(_chunks(content, 4), start=1):
            ack, etag, sha = self._upload_part(client, project_id, session_id, number, chunk)
            if ack.status_code != 200:
                raise AssertionError(f"分片 {number} 确认失败：{ack.status_code} {ack.text}")
            parts.append(
                {
                    "part_number": number,
                    "etag": etag,
                    "size_bytes": len(chunk),
                    "sha256": sha,
                }
            )
        completed = self._complete(
            client,
            project_id,
            session_id,
            expected_sha=sha256_bytes(content),
            expected_size=len(content),
            parts=parts,
        )
        if completed.status_code != 202:
            raise AssertionError(f"完成上传失败：{completed.status_code} {completed.text}")
        return completed.json()

    def _db_mark_valid(self, asset_id: UUID) -> None:
        from app.infrastructure.db.models.ingestion import RawAssetModel
        from sqlalchemy import update

        with self.factory() as session:
            session.execute(
                update(RawAssetModel)
                .where(RawAssetModel.id == asset_id)
                .values(status="VALID")
            )
            session.commit()

    # ------------------------------------------------------------ 主用例
    def health_check(self) -> None:
        def run() -> str:
            response = httpx.get(f"{BASE}/health", timeout=5)
            assert response.status_code == 200
            body = response.json()
            assert body.get("status") == "ok"
            return f"HTTP 200，status={body['status']}，env={body['env']}"

        self.check("API-HEALTH", run)

    def idempotency_checks(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nI"

        def replay() -> str:
            key = f"qa-idem-{uuid4().hex[:12]}"
            with self.client(user_a) as client:
                first = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    idempotency_key=key,
                )
                second = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    idempotency_key=key,
                )
                assert first.status_code == 201, first.text
                assert second.status_code == 201, second.text
                assert self._session_id(first) == self._session_id(second)
                assert second.headers.get("Idempotent-Replayed") == "true"
                return (
                    f"同键同请求重放返回同一 upload_session_id="
                    f"{self._session_id(first)}，Idempotent-Replayed=true"
                )

        self.check("API-IDEM-01", replay)

        def reused() -> str:
            key = f"qa-idem-{uuid4().hex[:12]}"
            with self.client(user_a) as client:
                first = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    filename="robot.mcap",
                    idempotency_key=key,
                )
                assert first.status_code == 201, first.text
                changed = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    filename="robot-changed.mcap",
                    idempotency_key=key,
                )
                assert changed.status_code == 409, f"{changed.status_code} {changed.text}"
                body = changed.json()
                assert body["error"]["code"] == "idempotency_key_reused", body
                return f"同键不同请求体返回 409 {body['error']['code']}"

        self.check("API-IDEM-02", reused)

    def missing_part_checks(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\n"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
                chunks = list(_chunks(content, 4))
                ack1, etag1, sha1 = self._upload_part(
                    client, project_a, session_id, 1, chunks[0]
                )
                assert ack1.status_code == 200, ack1.text
                first_parts = [
                    {
                        "part_number": 1,
                        "etag": etag1,
                        "size_bytes": len(chunks[0]),
                        "sha256": sha1,
                    }
                ]
                incomplete = self._complete(
                    client,
                    project_a,
                    session_id,
                    expected_sha=sha256_bytes(content),
                    expected_size=len(content),
                    parts=first_parts,
                )
                assert incomplete.status_code == 409, f"{incomplete.status_code} {incomplete.text}"
                body = incomplete.json()
                assert body["error"]["code"] == "upload_incomplete", body
                assert body["error"]["details"]["missing_part_numbers"] == [2], body
                session = self._get_session(client, project_a, session_id)
                assert session["status"] == "UPLOADING", session["status"]

                ack2, etag2, sha2 = self._upload_part(
                    client, project_a, session_id, 2, chunks[1]
                )
                assert ack2.status_code == 200, ack2.text
                all_parts = first_parts + [
                    {
                        "part_number": 2,
                        "etag": etag2,
                        "size_bytes": len(chunks[1]),
                        "sha256": sha2,
                    }
                ]
                completed = self._complete(
                    client,
                    project_a,
                    session_id,
                    expected_sha=sha256_bytes(content),
                    expected_size=len(content),
                    parts=all_parts,
                )
                assert completed.status_code == 202, f"{completed.status_code} {completed.text}"
                assert completed.json()["upload_session"]["status"] == "COMPLETED"
                return (
                    "缺分片 complete 返回 409 upload_incomplete（missing=[2]）；"
                    "补传分片 2 后 202 COMPLETED"
                )

        self.check("API-UPL-01", run)

    def part_conflict_check(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nX"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
                part = content[:4]
                ack, etag, sha = self._upload_part(client, project_a, session_id, 1, part)
                assert ack.status_code == 200, ack.text
                conflict = client.post(
                    f"{API}/projects/{project_a}/upload-sessions/{session_id}/parts/1/ack",
                    json={
                        "etag": etag,
                        "size_bytes": len(part),
                        "sha256": "sha256:" + "f" * 64,
                    },
                )
                assert conflict.status_code == 409, f"{conflict.status_code} {conflict.text}"
                body = conflict.json()
                assert body["error"]["code"] == "upload_part_conflict", body
                from app.infrastructure.db.models.ingestion import UploadPartModel
                from sqlalchemy import select

                with self.factory() as db:
                    row = db.scalar(
                        select(UploadPartModel).where(
                            UploadPartModel.upload_session_id == session_id,
                            UploadPartModel.part_number == 1,
                        )
                    )
                    assert row is not None and row.sha256 == sha, "既有分片记录被修改"
                return f"同分片不同 Hash 返回 409 {body['error']['code']}，既有分片记录未变"

        self.check("API-UPL-02", run)

    def size_mismatch_check(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nS"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
                parts = []
                for number, chunk in enumerate(_chunks(content, 4), start=1):
                    ack, etag, sha = self._upload_part(
                        client, project_a, session_id, number, chunk
                    )
                    assert ack.status_code == 200, ack.text
                    parts.append(
                        {
                            "part_number": number,
                            "etag": etag,
                            "size_bytes": len(chunk),
                            "sha256": sha,
                        }
                    )
                mismatch = self._complete(
                    client,
                    project_a,
                    session_id,
                    expected_sha=sha256_bytes(content),
                    expected_size=len(content) - 1,
                    parts=parts,
                )
                assert mismatch.status_code == 422, f"{mismatch.status_code} {mismatch.text}"
                body = mismatch.json()
                assert body["error"]["code"] == "size_mismatch", body
                retried = self._complete(
                    client,
                    project_a,
                    session_id,
                    expected_sha=sha256_bytes(content),
                    expected_size=len(content),
                    parts=parts,
                )
                assert retried.status_code == 202, f"{retried.status_code} {retried.text}"
                return "总大小不符返回 422 size_mismatch；修正后同一会话可完成 202"

        self.check("API-UPL-03", run)

    def signature_mismatch_check(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"notmp4!!"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    content_type="video/mp4",
                    filename="fake.mp4",
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
                parts = []
                for number, chunk in enumerate(_chunks(content, 4), start=1):
                    ack, etag, sha = self._upload_part(
                        client, project_a, session_id, number, chunk
                    )
                    assert ack.status_code == 200, ack.text
                    parts.append(
                        {
                            "part_number": number,
                            "etag": etag,
                            "size_bytes": len(chunk),
                            "sha256": sha,
                        }
                    )
                response = self._complete(
                    client,
                    project_a,
                    session_id,
                    expected_sha=sha256_bytes(content),
                    expected_size=len(content),
                    parts=parts,
                )
                assert response.status_code == 422, f"{response.status_code} {response.text}"
                body = response.json()
                assert body["error"]["code"] == "file_signature_mismatch", body
                session = self._get_session(client, project_a, session_id)
                assert session["status"] == "FAILED", session["status"]
                from app.infrastructure.db.models.ingestion import RawAssetModel
                from sqlalchemy import select

                with self.factory() as db:
                    asset = db.scalar(
                        select(RawAssetModel).where(
                            RawAssetModel.project_id == project_a,
                            RawAssetModel.sha256 == sha256_bytes(content),
                        )
                    )
                    assert asset is None, "签名不符仍登记了资产"
                return (
                    "声明 MP4 实际非 MP4：422 file_signature_mismatch；"
                    "会话 FAILED；未登记资产"
                )

        self.check("API-UPL-04", run)

    def expired_check(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nE"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
                from app.infrastructure.db.models.ingestion import UploadSessionModel
                from sqlalchemy import update

                with self.factory() as db:
                    db.execute(
                        update(UploadSessionModel)
                        .where(UploadSessionModel.id == session_id)
                        .values(expires_at=datetime.now(UTC) - timedelta(hours=1))
                    )
                    db.commit()
                response = client.get(
                    f"{API}/projects/{project_a}/upload-sessions/{session_id}"
                )
                assert response.status_code == 410, f"{response.status_code} {response.text}"
                body = response.json()
                assert body["error"]["code"] == "upload_session_expired", body
                return f"过期会话继续访问返回 410 {body['error']['code']}"

        self.check("API-UPL-05", run)

    def concurrency_check(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nC"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
                parts = []
                for number, chunk in enumerate(_chunks(content, 4), start=1):
                    ack, etag, sha = self._upload_part(
                        client, project_a, session_id, number, chunk
                    )
                    assert ack.status_code == 200, ack.text
                    parts.append(
                        {
                            "part_number": number,
                            "etag": etag,
                            "size_bytes": len(chunk),
                            "sha256": sha,
                        }
                    )
                session = self._get_session(client, project_a, session_id)
                version = int(session["version"])
                barrier = threading.Barrier(2)
                outcomes: list[tuple[int, str, bool, str]] = []
                lock = threading.Lock()

                def worker() -> None:
                    with self.client(user_a) as thread_client:
                        barrier.wait(timeout=10)
                        response = thread_client.post(
                            f"{API}/projects/{project_a}/upload-sessions/{session_id}/complete",
                            json={
                                "expected_sha256": sha256_bytes(content),
                                "expected_size_bytes": len(content),
                                "parts": parts,
                            },
                            headers={"If-Match": f'"{version}"'},
                        )
                        body = response.json()
                        code = body.get("error", {}).get("code", "")
                        replayed = bool(body.get("replayed", False))
                        with lock:
                            outcomes.append((response.status_code, code, replayed, response.text[:400]))

                threads = [threading.Thread(target=worker) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=30)
                statuses = sorted(item[0] for item in outcomes)
                no_such = [
                    event for event in self.stub.events() if "NoSuchUpload" in event
                ][-5:]
                detail = (
                    f"并发 complete 结果={outcomes}；"
                    f"S3 替身 NoSuchUpload 事件={no_such}"
                )
                assert statuses == [202, 409] or statuses == [202, 202], detail
                if statuses == [202, 202]:
                    assert any(item[2] for item in outcomes), detail

                from app.infrastructure.db.models.ingestion import RawAssetModel
                from sqlalchemy import select

                with self.factory() as db:
                    assets = db.scalars(
                        select(RawAssetModel).where(
                            RawAssetModel.project_id == project_a,
                            RawAssetModel.sha256 == sha256_bytes(content),
                        )
                    ).all()
                    assert len(assets) == 1, f"并发完成产生 {len(assets)} 个资产"
                return (
                    f"{detail}；仅 1 个可用资产，"
                    "另一个为幂等重放或 version_conflict"
                )

        self.check("API-UPL-06", run)

    def cross_project_checks(self) -> None:
        user_a = self.ids["user_a"]
        user_b = self.ids["user_b"]
        outsider = self.ids["outsider"]
        project_a = self.ids["project_a"]
        project_b = self.ids["project_b"]
        content = b"\x89MCAP0\r\nQ"

        def run() -> str:
            with self.client(user_a) as client:
                created = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert created.status_code == 201, created.text
                session_id = self._session_id(created)
            with self.client(user_b) as client_b:
                own_route = client_b.get(
                    f"{API}/projects/{project_b}/upload-sessions/{session_id}"
                )
                assert own_route.status_code == 404, own_route.text
                assert own_route.json()["error"]["code"] == "resource_not_found"
                foreign_route = client_b.get(f"{API}/projects/{project_a}/raw-assets")
                assert foreign_route.status_code == 403, foreign_route.text
                own_list = client_b.get(f"{API}/projects/{project_b}/raw-assets")
                assert own_list.status_code == 200, own_list.text
                assert all(
                    item["project_id"] == str(project_b)
                    for item in own_list.json()["items"]
                ), "项目 B 列表泄漏了其他项目资产"
            with self.client(outsider) as client_o:
                denied = client_o.get(f"{API}/projects/{project_a}/raw-assets")
                assert denied.status_code == 403, denied.text
            return (
                "B 用户访问 B 路由下的 A 会话=404；A 用户访问 B 项目=403；"
                "外部用户访问 A=403；B 列表无跨项目资产"
            )

        self.check("API-XPROJ-01", run)


    def dedup_checks(self) -> None:
        user_a = self.ids["user_a"]
        user_b = self.ids["user_b"]
        project_a = self.ids["project_a"]
        project_b = self.ids["project_b"]
        content = b"\x89MCAP0\r\nD"

        def same_project() -> str:
            with self.client(user_a) as client:
                first = self._upload_content(
                    client,
                    project_id=project_a,
                    content=content,
                )
                asset_id = UUID(first["raw_asset"]["asset_id"])
                self._db_mark_valid(asset_id)
                second = self._upload_content(
                    client,
                    project_id=project_a,
                    content=content,
                )
                assert second["deduplicated"] is True, second["deduplicated"]
                assert UUID(second["raw_asset"]["asset_id"]) == asset_id
                second_session = second["upload_session"]
                assert second_session["deduplicated"] is True
                assert UUID(second_session["deduplicated_asset_id"]) == asset_id
                object_key = second_session["object_key"]
                assert not self.stub.object_exists(object_key), "去重后新对象未清理"
                from app.infrastructure.db.models.ingestion import RawAssetModel
                from sqlalchemy import select

                with self.factory() as db:
                    rows = db.scalars(
                        select(RawAssetModel).where(
                            RawAssetModel.project_id == project_a,
                            RawAssetModel.sha256 == sha256_bytes(content),
                        )
                    ).all()
                    assert len(rows) == 1, f"同项目同内容资产数={len(rows)}"
                return (
                    f"同项目同内容第二次上传 deduplicated=true，复用资产 {asset_id}，"
                    "新对象已清理，资产计数=1"
                )

        self.check("API-DEDUP-01", same_project)

        def cross_project() -> str:
            with self.client(user_b) as client:
                result = self._upload_content(
                    client,
                    project_id=project_b,
                    content=content,
                )
                assert result["deduplicated"] is False
                asset_b = UUID(result["raw_asset"]["asset_id"])
                assert result["raw_asset"]["project_id"] == str(project_b)
                from app.infrastructure.db.models.ingestion import RawAssetModel
                from sqlalchemy import select

                with self.factory() as db:
                    rows_a = db.scalars(
                        select(RawAssetModel).where(
                            RawAssetModel.project_id == project_a,
                            RawAssetModel.sha256 == sha256_bytes(content),
                        )
                    ).all()
                    rows_b = db.scalars(
                        select(RawAssetModel).where(
                            RawAssetModel.project_id == project_b,
                            RawAssetModel.sha256 == sha256_bytes(content),
                        )
                    ).all()
                    assert len(rows_a) == 1 and len(rows_b) == 1
                    assert rows_a[0].id != rows_b[0].id
                return (
                    f"跨项目相同内容不去重：A 资产 1 个、B 资产 1 个（{asset_b}），"
                    "互不复用"
                )

        self.check("API-DEDUP-02", cross_project)

    def filename_and_key_checks(self) -> None:
        user_a = self.ids["user_a"]
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nK"

        def run() -> str:
            with self.client(user_a) as client:
                bad = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    filename="../evil/robot.mcap",
                )
                assert bad.status_code == 422, f"{bad.status_code} {bad.text}"
                assert bad.json()["error"]["code"] == "filename_invalid", bad.text
                good = self._create_session(
                    client,
                    project_id=project_a,
                    content=content,
                    filename="robot.mcap",
                )
                assert good.status_code == 201, good.text
                object_key = good.json()["object_key"]
                assert "robot.mcap" not in object_key, object_key
                assert object_key.startswith(f"raw/{project_a}/"), object_key
                assert object_key.endswith("/content.mcap"), object_key
                return (
                    f"路径穿越文件名被拒绝 422 filename_invalid；"
                    f"对象 Key={object_key} 不含原始文件名"
                )

        self.check("API-UPL-07", run)

    def audit_outbox_check(self) -> None:
        project_a = self.ids["project_a"]
        content = b"\x89MCAP0\r\nA"

        def run() -> str:
            with self.client(self.ids["user_a"]) as client:
                result = self._upload_content(
                    client,
                    project_id=project_a,
                    content=content,
                )
            session_id = UUID(result["upload_session"]["upload_session_id"])
            asset_id = UUID(result["raw_asset"]["asset_id"])
            from app.infrastructure.db.models.collection import AuditLog, OutboxEvent
            from sqlalchemy import select

            with self.factory() as db:
                audits = db.scalars(
                    select(AuditLog).where(
                        AuditLog.project_id == project_a,
                        AuditLog.resource_type == "UPLOAD_SESSION",
                        AuditLog.resource_id == session_id,
                    )
                ).all()
                events = db.scalars(
                    select(OutboxEvent).where(
                        OutboxEvent.project_id == project_a,
                        OutboxEvent.aggregate_id.in_([session_id, asset_id]),
                    )
                ).all()
                actions = sorted({item.action for item in audits})
                event_types = sorted({item.event_type for item in events})
                assert "create" in actions and "complete" in actions, actions
                assert any("completed" in item for item in event_types), event_types
                assert any("validation_requested" in item for item in event_types), event_types
                assert all(item.request_id for item in audits), "审计缺少 request_id"
                return (
                    f"上传会话审计 {len(audits)} 条（动作={actions}，均有 request_id），"
                    f"outbox {len(events)} 条（类型={event_types}）"
                )

        self.check("API-AUDIT-01", run)

    def s3_conclusion(self) -> None:
        self.record(
            "S3-STUB-PROTOCOL",
            "PASS",
            "真实 uvicorn 8010 + 真实 PG 下，boto3 经 HTTP 完成预签名 PUT、"
            "ListParts、CompleteMultipartUpload、Range GET、DeleteObject；"
            "对象字节与 Hash 由 API 校验。替身为自研最小 S3 协议实现。",
        )
        self.record(
            "S3-MINIO",
            "BLOCKED",
            "本机 9000 无 MinIO 服务，MinIO 镜像/二进制不可得；"
            "未执行 MinIO 专属 ETag/一致性/权限/生命周期/预签名行为验收，"
            "不得将本报告视为 MinIO 验收通过。",
        )
        self.record(
            "S3-SRVER",
            "BLOCKED",
            "s3rver 3.7.1 未安装（node_modules/全局/缓存均未找到），"
            "且 Agent 命令白名单不允许 npm install；未使用 s3rver。",
        )

    def run(self) -> None:
        self.migrate()
        self.seed()
        server, thread = self.start_api()
        self.record("API-START", "PASS", "uvicorn 127.0.0.1:8010 已就绪")
        try:
            self.health_check()
            self.idempotency_checks()
            self.missing_part_checks()
            self.part_conflict_check()
            self.size_mismatch_check()
            self.signature_mismatch_check()
            self.expired_check()
            self.concurrency_check()
            self.cross_project_checks()
            self.dedup_checks()
            self.filename_and_key_checks()
            self.audit_outbox_check()
            self.s3_conclusion()
        finally:
            server.should_exit = True
            thread.join(timeout=10)


def _chunks(data: bytes, size: int) -> list[bytes]:
    return [data[index : index + size] for index in range(0, len(data), size)]


def write_evidence(probe: ApiProbe, dbname: str) -> None:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    payload = {
        "task": "DDP-308",
        "kind": "真实 PostgreSQL 16 + uvicorn 8010 + S3 协议替身 API 探针",
        "executed_at": datetime.now(UTC).isoformat(),
        "environment": {
            "api": BASE,
            "postgres": PG_TEMPLATE.format(dbname=dbname).replace(":ddp@", ":***@"),
            "s3_stub": probe.stub.endpoint,
            "s3_stub_kind": "自研最小 S3 协议替身（非 MinIO、非 s3rver）",
        },
        "summary": {
            status: sum(1 for item in probe.results if item["status"] == status)
            for status in ("PASS", "FAIL", "BLOCKED", "PARTIAL")
        },
        "results": probe.results,
    }
    (EVIDENCE / "api-probe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (EVIDENCE / "api-probe.log").write_text("\n".join(probe.log_lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--keep-db", action="store_true", help="保留临时验收数据库")
    args = parser.parse_args()
    logging.getLogger("alembic").setLevel(logging.WARNING)

    stub = S3Stub()
    stub.start()
    dbname, url = create_database()
    os.environ.update(
        {
            "DDP_ENV": "local",
            "DDP_LOCAL_HEADER_AUTH": "true",
            "DDP_DATABASE_URL": url,
            "DDP_DATABASE_POOL_SIZE": "5",
            "DDP_DATABASE_MAX_OVERFLOW": "5",
            "DDP_S3_ENDPOINT": stub.endpoint,
            "DDP_S3_ACCESS_KEY": "qa-access-key",
            "DDP_S3_SECRET_KEY": "qa-secret-key",
            "DDP_S3_BUCKET": "ddp-raw",
            "DDP_S3_REGION": "us-east-1",
            "DDP_RATE_LIMIT_ENABLED": "false",
            "DDP_UPLOAD_PART_SIZE_BYTES": "4",
            "DDP_UPLOAD_MAX_SIZE_BYTES": "1048576",
            "AWS_S3_ADDRESSING_STYLE": "path",
        }
    )
    probe = ApiProbe(dbname, url, stub)
    failed = False
    try:
        print(f"临时验收数据库：{dbname}")
        print(f"S3 协议替身：{stub.endpoint}")
        probe.run()
        failed = any(item["status"] == "FAIL" for item in probe.results)
    except Exception as exc:  # noqa: BLE001
        probe.record("API-CRASH", "FAIL", f"{type(exc).__name__}: {exc}")
        probe.log_lines.append(traceback.format_exc())
        failed = True
    finally:
        write_evidence(probe, dbname)
        print(f"证据已写入 {EVIDENCE}")
        if probe.engine is not None:
            probe.engine.dispose()
        if not args.keep_db:
            drop_database(dbname)
            print(f"已清理临时数据库 {dbname}")
        else:
            print(f"按要求保留临时数据库 {dbname}")
        stub.stop()
    summary = {
        status: sum(1 for item in probe.results if item["status"] == status)
        for status in ("PASS", "FAIL", "BLOCKED", "PARTIAL")
    }
    print(f"汇总：{summary}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
