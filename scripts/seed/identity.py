"""准备种子所需的第二个身份（评审人）。

为什么需要直接写 `users` 表
--------------------------
平台没有注册/用户管理入口（`PLANNED`），`POST /projects/{pid}/members` 要求
用户已存在，否则返回 404「用户不存在」。业务数据仍然全部走 HTTP API；这里
只负责**准备一个可用的身份**，因为除此之外没有别的入口。

这里创建的用户只用于本地演示数据，名字明确标注。
"""

from __future__ import annotations

import datetime
import uuid

import jwt
import psycopg

from seed.client import Client, Context, SeedFailure


def _dsn(raw: str) -> str:
    return raw.replace("postgresql+psycopg://", "postgresql://")


def _sign(env: dict[str, str], user_id: str) -> str:
    now = datetime.datetime.now(datetime.UTC)
    return jwt.encode(
        {
            "sub": user_id,
            "iss": env["DDP_JWT_ISSUER"],
            "aud": env["DDP_JWT_AUDIENCE"],
            "iat": int(now.timestamp()),
            "exp": int((now + datetime.timedelta(days=1)).timestamp()),
        },
        env["DDP_JWT_SECRET"],
        algorithm="HS256",
    )


def ensure_reviewer(ctx: Context, env: dict[str, str], client: Client) -> str:
    """确保项目里有一个「不是创建人」的 editor，返回它的 user_id。"""
    dsn = env.get("DDP_DATABASE_URL")
    if not dsn:
        raise SeedFailure(".env 缺少 DDP_DATABASE_URL")

    with psycopg.connect(_dsn(dsn), connect_timeout=10) as conn:
        # 先复用项目里已有的非 owner 成员：早期版本每次运行都新拉一个人进来，
        # 重复运行几十次后项目里堆了几十个 editor。评审人才需要「另一个人」，
        # 同一个就够。
        row = conn.execute(
            """
            select u.id, u.display_name
            from project_members pm
            join users u on u.id = pm.user_id
            where pm.project_id = %s and pm.role <> 'owner' and pm.user_id <> %s
            order by u.display_name
            limit 1
            """,
            (ctx.project_id, ctx.user_id),
        ).fetchone()
        already_member = row is not None
        if row is None:
            row = conn.execute(
                """
                select u.id, u.display_name from users u
                where not exists (
                    select 1 from project_members pm
                    where pm.project_id = %s and pm.user_id = u.id
                )
                order by u.created_at
                limit 1
                """,
                (ctx.project_id,),
            ).fetchone()
        if row is None:
            user_id = str(uuid.uuid4())
            conn.execute(
                "insert into users (id, display_name) values (%s, %s)",
                (user_id, "演示评审员"),
            )
            conn.commit()
        else:
            user_id = str(row[0])

    if already_member:
        # 已经是成员：不再发写请求，省一次配额，也避免并发下的 409 噪音。
        ctx.tokens["reviewer"] = _sign(env, user_id)
        ctx.remember("identity", "reviewer", user_id)
        return user_id

    status, payload = client.call(
        "POST",
        f"/api/v1/projects/{ctx.project_id}/members",
        body={"user_id": user_id, "role": "editor"},
    )
    if status not in (200, 201):
        # 多个种子进程并行时可能选中同一个候选人：后到的那个会收到
        # 「已是成员」。这不是失败，确认成员确实在项目里即可。
        members_status, members = client.call("GET", f"/api/v1/projects/{ctx.project_id}/members")
        existing = (
            {str(item.get("user_id")) for item in (members or {}).get("items", [])}
            if members_status == 200
            else set()
        )
        if user_id not in existing:
            raise SeedFailure(f"把评审人加入项目失败：{status} {payload}")

    ctx.tokens["reviewer"] = _sign(env, user_id)
    ctx.remember("identity", "reviewer", user_id)
    return user_id


def ensure_second_owner(ctx: Context, env: dict[str, str], client: Client) -> str:
    """确保项目里有第二个 owner，返回它的 user_id。

    为什么要第二个 owner：跨项目数据集授权的审批要求「项目负责人」权限，
    而同一份授权又要求「创建人不得审批」，所以审批人必须是**另一位** owner。
    `POST /members` 的 role 降到 `editor` 不够，这里显式提升。
    """
    reviewer = ctx.ids.get("identity", {}).get("reviewer")
    if not reviewer:
        reviewer = ensure_reviewer(ctx, env, client)

    status, payload = client.call(
        "PATCH",
        f"/api/v1/projects/{ctx.project_id}/members/{reviewer}",
        body={"role": "owner"},
    )
    if status not in (200, 201):
        # 已经是 owner 时某些实现会返回 409；确认一下再决定是否失败。
        if status != 409:
            raise SeedFailure(f"提升第二 owner 失败：{status} {payload}")

    ctx.tokens["owner2"] = _sign(env, reviewer)
    ctx.remember("identity", "owner2", reviewer)
    return reviewer
