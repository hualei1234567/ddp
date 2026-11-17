"""场地种子：基地 / 房间 / 实验室三类场地，并把设备域数据挂到房间上。

覆盖的路径
----------
- 创建 ``SITE`` / ``ROOM`` / ``LAB`` 各一条（``LocationKind`` 三个取值全覆盖）；
- ``GET`` 详情、``GET ?kind=ROOM`` 列表过滤；
- ``PATCH`` 改名/挪地址（``If-Match`` 乐观锁），再用显式 ``null`` 清空备注，
  验证契约里「``address`` / ``notes`` 才允许置空」的语义。

与设备域的关联
--------------
``run.py`` 的 MODULES 顺序是 fleet → locations，所以设备域的数机器人在本模块
运行前已经存在，但那时还没有场地可引用（``location_id`` 只能留空）。这里在
建好房间后把主机器人与相机模组挂到该房间上，让「场地 → 设备」关系在演示数据
里真实存在。单独运行 ``run.py locations`` 时上下文里没有 fleet 产物，跳过关联，
不报错也不伪造 ID。

契约没有 ``DELETE``：场地被复合外键引用，删除入口按
``packages/contracts/fragments/paths.stage9.yaml`` 的说明留给后续迭代。
"""

from __future__ import annotations

from typing import Any

from seed.client import Client, Context, SeedFailure, expect, idempotency_key, if_match

GROUP = "locations"


def _create(client: Client, path: str, body: dict[str, Any], label: str) -> dict[str, Any]:
    headers = {"Idempotency-Key": idempotency_key()}
    payload = expect(label, client.call("POST", path, body=body, headers=headers), 201)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _patch(
    client: Client,
    path: str,
    version: int,
    body: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    payload = expect(label, client.call("PATCH", path, body=body, headers=if_match(version)), 200)
    if not isinstance(payload, dict):
        raise SeedFailure(f"{label}：响应不是对象：{payload!r}")
    return payload


def _link(ctx: Context, client: Client, base: str, room_id: str) -> None:
    """把设备域的主机器人与相机模组挂到房间；仅在本次运行有 fleet 产物时执行。"""
    fleet = ctx.ids.get("fleet", {})
    links = (
        ("robot_primary", f"{base}/robots"),
        ("device_camera", f"{base}/devices"),
    )
    for key, collection in links:
        resource_id = fleet.get(key)
        if not resource_id:
            continue
        path = f"{collection}/{resource_id}"
        current = expect(f"读取 {key} 以关联场地", client.call("GET", path), 200)
        if not isinstance(current, dict):
            raise SeedFailure(f"读取 {key} 以关联场地：响应不是对象")
        _patch(
            client,
            path,
            int(current["version"]),
            {"location_id": room_id},
            f"把 {key} 关联到房间",
        )


def _room_is_listed(client: Client, base: str, room_id: str) -> bool:
    """按 kind 过滤并翻页查找刚创建的房间（重复运行会累积超过一页）。"""
    cursor: str | None = None
    for _ in range(50):  # 上限只为防御游标异常；5000 条场地已远超演示规模
        query = f"{base}/locations?kind=ROOM&limit=100"
        if cursor is not None:
            query = f"{query}&cursor={cursor}"
        page = expect("按 kind 过滤房间", client.call("GET", query), 200)
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise SeedFailure(f"按 kind 过滤房间：响应形状不符 {page!r}")
        if any(item.get("location_id") == room_id for item in page["items"]):
            return True
        next_cursor = page.get("next_cursor")
        if not next_cursor:
            return False
        cursor = str(next_cursor)
    raise SeedFailure("按 kind 过滤房间：cursor 翻页次数超限，疑似分页游标不前进")


def run(ctx: Context, client: Client) -> None:
    base = f"/api/v1/projects/{ctx.project_id}"
    stamp = ctx.stamp

    site = _create(
        client,
        f"{base}/locations",
        {
            "code": f"loc-{stamp}-site",
            "name": "上海临港具身智能数据基地",
            "kind": "SITE",
            "address": "上海市浦东新区临港新片区环湖西二路 888 号",
            "notes": "包含 2 层家庭场景实验间与 1 间标定实验室，采集区与办公区分离。",
        },
        "创建基地场地",
    )
    room = _create(
        client,
        f"{base}/locations",
        {
            "code": f"loc-{stamp}-room-a3",
            "name": "A 栋 3 层家庭场景实验间",
            "kind": "ROOM",
            "address": "A 栋 301",
            "notes": "厨房台面高度 90cm，客厅含沙发、茶几与地毯，光照 300-500lux。",
        },
        "创建场景实验间",
    )
    lab = _create(
        client,
        f"{base}/locations",
        {
            "code": f"loc-{stamp}-lab-ft",
            "name": "力控与标定实验室",
            "kind": "LAB",
            "address": "B 栋 105",
            "notes": "配备六维力传感器标定台、双目标定台与可调色温光源。",
        },
        "创建标定实验室",
    )

    site = expect(
        "读取基地详情",
        client.call("GET", f"{base}/locations/{site['location_id']}"),
        200,
    )
    if not isinstance(site, dict) or site.get("kind") != "SITE":
        raise SeedFailure(f"读取基地详情：场地类型不符 {site!r}")

    room = _patch(
        client,
        f"{base}/locations/{room['location_id']}",
        int(room["version"]),
        {
            "name": "A 栋 3 层家庭场景实验间（二期扩建）",
            "address": "A 栋 301-303",
            "notes": "扩建后新增餐桌与阳台两个布局，采光可调，地面防滑处理。",
        },
        "更新场景实验间",
    )

    # 显式 null 只对 address / notes 生效：清空一条临时备注。
    lab = _patch(
        client,
        f"{base}/locations/{lab['location_id']}",
        int(lab["version"]),
        {"notes": None},
        "清空实验室备注",
    )
    if lab.get("notes") is not None:
        raise SeedFailure(f"清空实验室备注：notes 仍为 {lab.get('notes')!r}")

    if not _room_is_listed(client, base, str(room["location_id"])):
        raise SeedFailure("按 kind 过滤房间：刚创建的房间不在结果里")

    ctx.remember(GROUP, "site", str(site["location_id"]))
    ctx.remember(GROUP, "room", str(room["location_id"]))
    ctx.remember(GROUP, "lab", str(lab["location_id"]))

    _link(ctx, client, base, str(room["location_id"]))
