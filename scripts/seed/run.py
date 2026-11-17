#!/usr/bin/env python3
"""DDP 演示数据种子：按依赖顺序逐个模块走真实流程。

用法::

    backend/.venv/bin/python scripts/seed/run.py            # 全部模块
    backend/.venv/bin/python scripts/seed/run.py collection # 只跑某个模块
    backend/.venv/bin/python scripts/seed/run.py --list

前置：本地 compose 栈在跑（API :8000），`.env` 里有
`DDP_WEB_DEV_AUTH="Bearer <JWT>"`（见 docs/operations/本地开发令牌.md）。

数据会**保留**：脚本不清理任何东西，重复运行只会新增带新时间戳的记录。
"""

from __future__ import annotations

import argparse
import importlib
import sys
import traceback
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from seed.client import Client, Context, SeedFailure  # noqa: E402
from seed.identity import ensure_reviewer, ensure_second_owner  # noqa: E402

# 顺序即依赖：后者会引用前者产出的 ID。
MODULES: list[str] = [
    "fleet",  # 设备 / 传感器 / 机器人 / 校准
    "locations",  # 场地
    "collection",  # 采集方案 / SOP / 场景 / 动作库 / 任务 / 批次
    "datasources",  # 数据源 / 导入 / 审核
    "episodes",  # Episode 生命周期
    "annotation",  # 标注 schema / 抽样 / 任务 / 提交 / 评审 / 仲裁 / 金标 / IAA
    "datasets",  # 数据集 / 版本 / 策略 / 授权 / 质检报告
    "exports",  # 导出任务与产物
    "training",  # 训练需求 / 请求 / 修订 / 交付 / 外部引用
    "modelops",  # 模型 / 版本 / 评估 / 推理服务与任务 / 反馈 / 数据缺口
    "suppliers",  # 供应商 / 到货批次 / 分段 / 验收台账
    "governance",  # 配额 / 用量
    "projects",  # 项目与成员
]


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    path = REPO_ROOT / ".env"
    if not path.exists():
        return env
    for line in path.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _token(env: dict[str, str]) -> str:
    raw = env.get("DDP_WEB_DEV_AUTH", "")
    if not raw.lower().startswith("bearer "):
        raise SeedFailure(
            ".env 里没有可用的 DDP_WEB_DEV_AUTH；" "见 docs/operations/本地开发令牌.md 生成令牌"
        )
    return raw.split(" ", 1)[1].strip()


def _project(client: Client, env: dict[str, str]) -> str:
    if env.get("DDP_SEED_PROJECT_ID"):
        return env["DDP_SEED_PROJECT_ID"]
    status, payload = client.call("GET", "/api/v1/projects")
    if status != 200 or not isinstance(payload, dict):
        raise SeedFailure(f"读取项目列表失败：{status} {payload}")
    items = payload.get("items") or []
    if not items:
        raise SeedFailure("没有任何项目，无法播种")
    return str(items[0]["project_id"])


def _user(token: str) -> str:
    """主身份 = 令牌的 ``sub``。

    早期版本取 `/members` 的第一条，但成员是按 user_id 排序的，拿到的往往
    不是令牌主体——于是机器人 owner、任务指派人等字段会落到别人身上，而
    职责分离（创建人不得审批自己的任务）也会因此错判。
    """
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        sub = claims["sub"]
    except (IndexError, ValueError, KeyError) as exc:
        raise SeedFailure(f"无法从令牌解析 sub：{exc}") from exc
    if not isinstance(sub, str) or not sub:
        raise SeedFailure("令牌 sub 不是有效字符串")
    return sub


def _grantee_project(ctx: Context, env: dict[str, str]) -> str | None:
    """找一个「另一个项目」用于跨项目授权演示。

    为什么要读库：`GET /projects` 对普通成员只返回本人项目，而创建项目需要
    平台管理员。授权本来就要指向**别人**的项目，没有别的发现入口。这里只做
    一次只读查询，不写任何业务数据。
    """
    configured = env.get("DDP_SEED_GRANTEE_PROJECT_ID")
    if configured:
        return configured
    dsn = env.get("DDP_DATABASE_URL")
    if not dsn:
        return None
    try:
        import psycopg
    except ImportError:
        return None
    try:
        with psycopg.connect(
            dsn.replace("postgresql+psycopg://", "postgresql://"), connect_timeout=10
        ) as conn:
            row = conn.execute(
                "select id from projects where id <> %s order by created_at limit 1",
                (ctx.project_id,),
            ).fetchone()
    except Exception:  # noqa: BLE001 - 只是可选的演示前置，拿不到就跳过
        return None
    return str(row[0]) if row else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DDP 演示数据种子")
    parser.add_argument("modules", nargs="*", help="只跑指定模块")
    parser.add_argument("--list", action="store_true", help="列出模块")
    parser.add_argument("--api-base", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    if args.list:
        for name in MODULES:
            print(name)
        return 0

    env = _load_env()
    client = Client(args.api_base, _token(env))
    project_id = _project(client, env)
    token = _token(env)
    ctx = Context(
        base_url=args.api_base,
        token=token,
        project_id=project_id,
        user_id=_user(token),
        stamp=datetime.now(UTC).strftime("%Y%m%d%H%M%S"),
    )
    reviewer = ensure_reviewer(ctx, env, client)
    ensure_second_owner(ctx, env, client)
    grantee = _grantee_project(ctx, env)
    if grantee:
        ctx.remember("projects", "grantee_project_id", grantee)
    print(f"项目 {project_id} / 主身份 {ctx.user_id} / 评审人 {reviewer} / 批次 {ctx.stamp}\n")

    selected = args.modules or MODULES
    unknown = [name for name in selected if name not in MODULES]
    if unknown:
        print(f"未知模块：{unknown}", file=sys.stderr)
        return 2

    failed: list[str] = []
    for name in selected:
        module = importlib.import_module(f"seed.modules.{name}")
        try:
            module.run(ctx, client)
        except SeedFailure as exc:
            print(f"✗ {name}: {exc}")
            failed.append(name)
        except Exception:  # noqa: BLE001 - 单个模块失败不应中断其余模块
            print(f"✗ {name}: 未预期异常")
            traceback.print_exc()
            failed.append(name)
        else:
            print(f"✓ {name}")

    print()
    if failed:
        print(f"未通过：{len(failed)} 个模块 -> {', '.join(failed)}")
        return 1
    print("全部模块完成。数据已保留。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
