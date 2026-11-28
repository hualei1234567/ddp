#!/usr/bin/env python3
"""签发 DDP 访问令牌（HS256 Bearer JWT），并可选在 users 表幂等登记用户。

用法示例::

    # 公网容器内一键登记管理员并签发令牌：
    docker compose -f docker-compose.yml -f infra/deploy/docker-compose.prod.yml \\
      exec api python -m scripts_placeholder  # 或宿主机运行：
    backend/.venv/bin/python scripts/ops/issue_jwt.py \\
      --display-name "开源管理员" --role admin

    # 纯离线签发（不连数据库，指定已有 UUID）：
    backend/.venv/bin/python scripts/ops/issue_jwt.py \\
      --user-id 01960000-0000-7000-8000-000000000001 --role admin --no-db

注意：遵循 MEM-INC-030，本脚本顶层仅导入 Python 标准库；``jwt`` 与 ``psycopg``
均在函数内按需惰性导入，确保最小依赖 CI 安全任务收集测试零报错。
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import uuid
from pathlib import Path

VALID_PLATFORM_ROLES = ("member", "admin")
DEFAULT_ENV_PATH = Path(__file__).resolve().parents[2] / ".env"


def load_env_map(env_path: Path | None = None) -> dict[str, str]:
    """合并文件配置与进程环境变量（进程环境变量优先）。"""
    merged: dict[str, str] = {}
    target = env_path if env_path is not None else DEFAULT_ENV_PATH
    if target.exists():
        for raw_line in target.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            merged[key.strip()] = val.strip().strip('"').strip("'")
    for key, val in os.environ.items():
        if key.startswith("DDP_"):
            merged[key] = val
    return merged


def normalize_pg_dsn(raw_url: str) -> str:
    """将 SQLAlchemy URL 转为 psycopg 原生连接串。"""
    dsn = raw_url.strip()
    if not dsn:
        raise ValueError("缺少 DDP_DATABASE_URL")
    return dsn.replace("postgresql+psycopg://", "postgresql://", 1)


def build_jwt_claims(
    *,
    user_id: str,
    role: str,
    issuer: str,
    audience: str,
    ttl_seconds: int,
    now: datetime.datetime | None = None,
) -> dict[str, object]:
    """构造与 backend/app/infrastructure/auth/jwt.py 严格对齐的声明集合。"""
    parsed_uuid = str(uuid.UUID(user_id.strip()))
    normalized_role = role.strip().lower()
    if normalized_role not in VALID_PLATFORM_ROLES:
        allowed = ", ".join(VALID_PLATFORM_ROLES)
        raise ValueError(f"非法平台角色 {role!r}，仅允许：{allowed}")
    if ttl_seconds <= 0:
        raise ValueError("令牌有效期必须大于 0 秒")
    if not issuer.strip() or not audience.strip():
        raise ValueError("issuer 与 audience 不能为空")

    ts_now = int((now or datetime.datetime.now(datetime.UTC)).timestamp())
    return {
        "sub": parsed_uuid,
        "platform_role": normalized_role,
        "iss": issuer.strip(),
        "aud": audience.strip(),
        "iat": ts_now,
        "exp": ts_now + ttl_seconds,
    }


def sign_hs256_jwt(claims: dict[str, object], secret: str) -> str:
    """使用官方 PyJWT 签发 HS256 令牌（惰性导入以兼容最小 CI 环境）。"""
    if not secret or not secret.strip():
        raise ValueError("DDP_JWT_SECRET 不能为空")
    import jwt

    return jwt.encode(claims, secret.strip(), algorithm="HS256")


def ensure_user_in_db(
    dsn: str,
    *,
    user_id: str | None,
    display_name: str,
) -> str:
    """在 users 表幂等查找或创建用户，返回最终 UUID 字符串。"""
    clean_name = display_name.strip()
    if not clean_name:
        raise ValueError("display_name 不能为空")

    import psycopg

    with psycopg.connect(normalize_pg_dsn(dsn), connect_timeout=8) as conn:
        if user_id:
            final_id = str(uuid.UUID(user_id.strip()))
            conn.execute(
                """
                INSERT INTO users (id, display_name)
                VALUES (%s, %s)
                ON CONFLICT (id) DO UPDATE
                SET display_name = EXCLUDED.display_name,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (final_id, clean_name),
            )
            conn.commit()
            return final_id

        row = conn.execute(
            "SELECT id FROM users WHERE display_name = %s ORDER BY created_at LIMIT 1",
            (clean_name,),
        ).fetchone()
        if row is not None:
            return str(row[0])

        final_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO users (id, display_name) VALUES (%s, %s)",
            (final_id, clean_name),
        )
        conn.commit()
        return final_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="登记 DDP 用户并签发 HS256 Bearer JWT")
    parser.add_argument("--user-id", help="指定用户 UUID（不传则按显示名查找或新建）")
    parser.add_argument("--display-name", default="开源体验者", help="用户显示名称")
    parser.add_argument(
        "--role",
        choices=VALID_PLATFORM_ROLES,
        default="member",
        help="平台角色：member（默认）或 admin",
    )
    parser.add_argument(
        "--hours", type=int, default=24, help="令牌有效小时数（默认 24）"
    )
    parser.add_argument(
        "--env-file", type=Path, default=None, help="指定 .env 文件路径"
    )
    parser.add_argument(
        "--no-db",
        action="store_true",
        help="跳过数据库写入，仅离线签发 JWT（需配合 --user-id 或自动生成 UUID）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    env = load_env_map(args.env_file)

    secret = env.get("DDP_JWT_SECRET", "")
    issuer = env.get("DDP_JWT_ISSUER", "ddp")
    audience = env.get("DDP_JWT_AUDIENCE", "ddp-api")

    if args.no_db:
        resolved_uid = (
            str(uuid.UUID(args.user_id)) if args.user_id else str(uuid.uuid4())
        )
    else:
        raw_dsn = env.get("DDP_DATABASE_URL", "")
        resolved_uid = ensure_user_in_db(
            raw_dsn,
            user_id=args.user_id,
            display_name=args.display_name,
        )

    claims = build_jwt_claims(
        user_id=resolved_uid,
        role=args.role,
        issuer=issuer,
        audience=audience,
        ttl_seconds=args.hours * 3600,
    )
    token = sign_hs256_jwt(claims, secret)
    sys.stderr.write(
        f"[issue_jwt] user_id={resolved_uid} role={args.role} expires_in={args.hours}h\n"
    )
    print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
