"""项目成员种子：新增 / 修改角色 / 移除，且不碰主身份与项目本身。

覆盖的路径
----------
- ``GET /members`` 读当前成员；
- ``PATCH /members/{user_id}`` 把评审人 ``editor → viewer → editor``（两次真实改角色）；
- ``DELETE /members/{user_id}`` 移除评审人（不是 owner，不会触发「至少保留一名
  owner」的 409）；
- ``POST /members`` 把评审人重新加回为 ``editor``（``201``）。

为什么只动评审人
----------------
平台没有注册/邀请入口（契约明确「用户目录由运维登记」），
``backend/scripts/seed/identity.py`` 是唯一准备第二个身份的通道，它保证这个
用户**本次运行前不在项目里**，因此与 Bearer 令牌的主体必然不是同一个人。
本模块从不移除 ``ctx.user_id``，也从不改成 owner 角色之外的项目属性。

``run.py`` 的 MODULES 把 projects 放在最后：前面所有模块都已用评审人做完
职责分离（如标定四眼校验），此时把评审人角色改回 ``editor`` 再结束，既完成
增删改覆盖，也把成员关系恢复到初始状态——重复运行不会累积权限变化。
"""

from __future__ import annotations

from typing import Any

from seed.client import Client, Context, SeedFailure, expect

GROUP = "projects"


def _members(client: Client, base: str) -> dict[str, dict[str, Any]]:
    """读取全部成员；成员数超过单页上限时按 cursor 继续翻页。"""
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(50):  # 上限只为防御异常的 cursor 循环；5000 名成员已远超演示规模
        query = f"{base}/members?limit=100"
        if cursor is not None:
            query = f"{query}&cursor={cursor}"
        payload = expect("读取项目成员", client.call("GET", query), 200)
        if not isinstance(payload, dict) or not isinstance(payload.get("items"), list):
            raise SeedFailure(f"读取项目成员：响应形状不符 {payload!r}")
        items.extend(payload["items"])
        next_cursor = payload.get("next_cursor")
        if not next_cursor:
            return {str(item["user_id"]): item for item in items}
        cursor = str(next_cursor)
    raise SeedFailure("读取项目成员：cursor 翻页次数超限，疑似分页游标不前进")


def _change_role(client: Client, base: str, user_id: str, role: str, label: str) -> dict[str, Any]:
    payload = expect(
        label,
        client.call("PATCH", f"{base}/members/{user_id}", body={"role": role}),
        200,
    )
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    if payload.get("role") != role:
        raise SeedFailure(f"{label}：期望 role={role}，实际 {payload.get('role')!r}")
    return payload


def _remove(client: Client, base: str, user_id: str, label: str) -> None:
    expect(label, client.call("DELETE", f"{base}/members/{user_id}"), 204)


def _try_remove(client: Client, base: str, user_id: str) -> bool:
    """删除成员；被业务数据引用时返回 False（不是失败）。"""
    status, payload = client.call("DELETE", f"{base}/members/{user_id}")
    if status == 204:
        return True
    if status == 409:
        return False
    raise SeedFailure(f"移除评审人：期望 204 或 409，实际 {status}\n  响应：{payload!r}")


def _add(client: Client, base: str, user_id: str, role: str, label: str) -> dict[str, Any]:
    payload = expect(
        label,
        client.call("POST", f"{base}/members", body={"user_id": user_id, "role": role}),
        201,
    )
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    if payload.get("role") != role:
        raise SeedFailure(f"{label}：期望 role={role}，实际 {payload.get('role')!r}")
    return payload


def _roles(members: dict[str, dict[str, Any]]) -> dict[str, str]:
    return {user_id: str(item["role"]) for user_id, item in members.items()}


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    reviewer = ctx.recall("identity", "reviewer")

    if reviewer == ctx.user_id:
        raise SeedFailure("评审人与主身份相同，拒绝执行移除步骤以免把自己踢出项目")

    before = _members(client, base)
    if reviewer not in before:
        raise SeedFailure("评审人不在项目成员列表里，无法演示成员变更")
    # 评审人可能已被 `ensure_second_owner` 提升为 owner（数据集授权审批需要
    # 第二位负责人）。结束时必须恢复到**进来时的角色**，不能硬编码 editor。
    original_role = str(before[reviewer]["role"])

    # 改角色：先降为只读，再恢复编辑权（两次都是真实写入）。
    _change_role(client, base, reviewer, "viewer", "把评审人降为 viewer")
    _change_role(client, base, reviewer, "editor", "把评审人恢复为 editor")

    # 移除与新增：只移除评审人；主身份与其它成员一概不动。
    #
    # 评审人一旦在标注/数据集等业务数据里被引用，`project_members` 上的
    # `ON DELETE RESTRICT` 外键会拒绝删除（返回 409「数据完整性约束冲突」）。
    # 这是**正确行为**：不能把仍有归属数据的成员直接删掉。此时跳过移除，
    # 只保留前面两次真实的角色变更，并如实说明。
    removed = _try_remove(client, base, reviewer)
    if removed:
        _add(client, base, reviewer, original_role, "重新加入评审人")
    else:
        print("  [跳过] 移除评审人：该成员已被标注/数据集等业务数据引用（ON DELETE RESTRICT）")
        _change_role(client, base, reviewer, original_role, "恢复评审人原角色")

    after = _members(client, base)
    if _roles(after) != _roles(before):
        raise SeedFailure(
            "成员关系未恢复到初始状态：\n" f"  初始：{_roles(before)}\n  现在：{_roles(after)}"
        )
    if not any(role == "owner" for role in _roles(after).values()):
        raise SeedFailure("成员变更后项目没有 owner")
    if ctx.user_id not in after:
        raise SeedFailure("主身份被意外移出项目")

    ctx.remember(GROUP, "reviewer", reviewer)
    ctx.remember(GROUP, "member_count", str(len(after)))
