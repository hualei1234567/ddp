#!/usr/bin/env python3
"""DDP-307 集成环境种子：真实 SQLite 用户/项目/成员 + MinIO 桶。

只写本任务临时数据库与本地临时 MinIO，不读取 staging 配置，
不修改 backend 代码。固定 UUID 供前端集成测试使用。
"""

from __future__ import annotations

import json
import os
from uuid import UUID

import boto3
from botocore.exceptions import ClientError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.infrastructure.db.models.identity import Project, ProjectMember, User

PROJECT_ID = UUID("11111111-1111-7111-8111-111111111111")
USER_ID = UUID("88888888-8888-7888-8888-888888888888")
PROJECT_CODE = "ddp-it-307"
PROJECT_NAME = "DDP-307 集成测试项目"
USER_NAME = "DDP-307 集成测试用户"


def _is_local_endpoint(value: str) -> bool:
    """只允许本机回环地址/容器内主机名。

    脱敏时原先硬编码的 staging IP 被替换成占位符，占位符本身没有判定意义。
    改为**白名单**：只放行 loopback 与 compose 服务名，其余一律拒绝——
    这比「黑名单某个 IP」更严格，也不会因为运维换机器而失效。
    """
    from urllib.parse import urlparse

    host = value
    if "://" in value:
        host = urlparse(value).hostname or ""
    elif "@" in value:
        host = value.rsplit("@", 1)[1].split(":")[0].split("/")[0]
    else:
        host = value.split(":")[0].split("/")[0]
    host = host.strip().lower()
    if host in {"localhost", "127.0.0.1", "::1", "postgres", "minio", "redis", "api"}:
        return True
    return host.startswith("127.")


def _database_url() -> str:
    value = os.environ.get("DDP_DATABASE_URL", "").strip()
    if not value:
        raise SystemExit("缺少 DDP_DATABASE_URL")
    if not _is_local_endpoint(value):
        raise SystemExit("拒绝使用非本机数据库（该脚本只用于本地集成测试）")
    return value


def _seed_identity(database_url: str) -> None:
    engine = create_engine(database_url)
    with Session(engine) as session:
        if session.get(Project, PROJECT_ID) is None:
            session.add(Project(id=PROJECT_ID, code=PROJECT_CODE, name=PROJECT_NAME))
        if session.get(User, USER_ID) is None:
            session.add(User(id=USER_ID, display_name=USER_NAME))
        session.flush()
        member_id = session.scalar(
            select(ProjectMember.id).where(
                ProjectMember.project_id == PROJECT_ID,
                ProjectMember.user_id == USER_ID,
            )
        )
        if member_id is None:
            session.add(
                ProjectMember(
                    project_id=PROJECT_ID,
                    user_id=USER_ID,
                    role="owner",
                )
            )
        session.commit()


def _ensure_bucket() -> None:
    endpoint = os.environ.get("DDP_S3_ENDPOINT", "").strip()
    bucket = os.environ.get("DDP_S3_BUCKET", "").strip()
    access_key = os.environ.get("DDP_S3_ACCESS_KEY", "").strip()
    secret_key = os.environ.get("DDP_S3_SECRET_KEY", "").strip()
    if not endpoint or not bucket:
        raise SystemExit("缺少 DDP_S3_ENDPOINT / DDP_S3_BUCKET")
    if not _is_local_endpoint(endpoint):
        raise SystemExit("拒绝连接非本机对象存储（该脚本只用于本地集成测试）")
    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=os.environ.get("DDP_S3_REGION", "us-east-1"),
    )
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError:
        client.create_bucket(Bucket=bucket)


def main() -> None:
    database_url = _database_url()
    _seed_identity(database_url)
    if os.environ.get("DDP_IT_SKIP_BUCKET") != "1":
        _ensure_bucket()
    print(
        json.dumps(
            {
                "project_id": str(PROJECT_ID),
                "user_id": str(USER_ID),
                "project_code": PROJECT_CODE,
                "database_url": database_url,
                "bucket": os.environ.get("DDP_S3_BUCKET"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
