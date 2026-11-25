#!/usr/bin/env python3
"""将 ``docs/memory/`` 中的 ACTIVE 记忆卡片自动幂等同步到记忆星云（Memory Nebula）。

设计原则：
1. **文件为唯一权威源**（ARCHITECTURE.md §3.4）：只从 ``docs/memory/`` 已通过格式解析的
   ACTIVE 卡片单向同步到记忆星云，绝反向污染仓库。
2. **双通道落地**：
   - 优先通过 HTTP POST ``http://127.0.0.1:8898/remember``（支持端口 8898..8910 自动探测）
     写入正在运行的 DSH 记忆星云服务，使前端 3D 星云面板通过 SSE 立即刷新点亮，且内存索引
     实时一致（利用引擎内置 ``dedupeThreshold`` 自动幂等更新，不产生重复噪音节点）。
   - 若记忆星云守护进程未启动（离线/CI 环境）：
     * 默认非阻塞提示并退出 0（绝不因本地面板未开而挂掉 CI）；
     * 传入 ``--offline-direct`` 时可通过 Node 调用 ``NebulaMemory`` 直写数据目录。
3. **无敏感信息**：只同步仓库内已过 ``check_secrets.py`` 的公开架构/排障卡片摘要。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from build_memory_index import MEMORY_DIR, Card, collect_cards

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTS = tuple(range(8898, 8911))
SERVICE_NAME = "dsh-memory-nebula"

TYPE_TO_NEBULA_KIND: dict[str, str] = {
    "INC": "episode",
    "DEC": "fact",
    "REQ": "fact",
    "SOP": "note",
    "CHG": "episode",
    "OPS": "note",
}

_CONCLUSION_RE = re.compile(r"^-\s+\*\*核心结论.*?\*\*[：:]\s*(.*)$")


def extract_conclusion(card_path: Path) -> str:
    """提取卡片中的『核心结论』正文（支持单行或紧随其后的缩进列表）。"""
    lines = card_path.read_text(encoding="utf-8").splitlines()
    captured: list[str] = []
    in_conclusion = False
    for line in lines:
        if not in_conclusion:
            match = _CONCLUSION_RE.match(line)
            if match:
                in_conclusion = True
                inline = match.group(1).strip()
                if inline:
                    captured.append(inline)
            continue
        if line.startswith("- **") or line.startswith("---") or line.startswith("###"):
            break
        stripped = line.strip().lstrip("-* ").strip()
        if stripped:
            captured.append(stripped)
    return " ".join(captured).strip()


def build_nebula_payload(card: Card) -> dict[str, object]:
    """把一张仓库记忆卡片映射为 NebulaMemory.remember() 载荷。"""
    conclusion = extract_conclusion(ROOT / card.path)
    summary = f"[{card.card_id}] {card.title}"
    if conclusion:
        summary = f"{summary} —— {conclusion}"
    if card.check_ref:
        summary = f"{summary} (check_ref: {card.check_ref})"
    # NebulaMemory MAX_TEXT = 2000，留足余量
    text = summary[:1800]

    # 复发卡片或 DEC/REQ 核心边界给更高权重；普通 ACTIVE 给 0.88
    importance = 0.95 if (card.recurrence > 1 or card.memory_type in {"DEC", "REQ"}) else 0.88
    tags = list(
        dict.fromkeys(["ddp", f"mem-{card.memory_type.lower()}", card.card_id.lower(), *card.tags])
    )[:16]

    return {
        "text": text,
        "kind": TYPE_TO_NEBULA_KIND.get(card.memory_type, "fact"),
        "importance": importance,
        "tags": tags,
        "source": f"ddp:{card.path}",
        "meta": {
            "card_id": card.card_id,
            "memory_type": card.memory_type,
            "recurrence": card.recurrence,
            "check_ref": card.check_ref,
            "path": card.path,
        },
    }


def probe_nebula_base_url(explicit_url: str | None = None) -> str | None:
    """探测本机记忆星云 HTTP 服务地址。"""
    candidates: list[str] = []
    if explicit_url:
        candidates.append(explicit_url.rstrip("/"))
    env_url = os.environ.get("MEMORY_NEBULA_URL", "").strip()
    if env_url:
        candidates.append(env_url.rstrip("/"))
    candidates.extend(f"http://127.0.0.1:{port}" for port in DEFAULT_PORTS)

    seen: set[str] = set()
    for base in candidates:
        if not base or base in seen or not base.startswith(("http://", "https://")):
            continue
        seen.add(base)
        req = urllib.request.Request(  # noqa: S310 - 仅访问 http/https 回环记忆服务
            f"{base}/healthz", headers={"Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=0.8) as resp:  # noqa: S310 - 仅访问本机回环记忆服务
                if resp.status != 200:
                    continue
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("service") == SERVICE_NAME:
                    return base
        except (OSError, ValueError, urllib.error.URLError):
            continue
    return None


def post_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    if not url.startswith(("http://", "https://")):
        raise ValueError(f"仅支持 http/https URL：{url}")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - 已校验 http/https 协议
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=3.0) as resp:  # noqa: S310 - 仅访问本机回环记忆服务
        return json.loads(resp.read().decode("utf-8"))


def sync_cards_to_server(base_url: str, cards: list[Card]) -> tuple[int, int]:
    """推送 ACTIVE 卡片至记忆星云服务，返回 (created, deduped)。"""
    created = 0
    deduped = 0
    for card in cards:
        if card.status != "ACTIVE" or card.problems:
            continue
        payload = build_nebula_payload(card)
        result = post_json(f"{base_url}/remember", payload)
        if result.get("deduped"):
            deduped += 1
        else:
            created += 1
    return created, deduped


def trigger_recall_warmup(base_url: str, query: str) -> int:
    """触发一次召回以刷新星云面板共激活轨迹（赫布连边与热力点亮）。"""
    if not base_url.startswith(("http://", "https://")):
        raise ValueError(f"仅支持 http/https URL：{base_url}")
    encoded = urllib.parse.quote(query)
    req = urllib.request.Request(  # noqa: S310 - 已校验 http/https 协议
        f"{base_url}/recall?q={encoded}",
        headers={"Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=3.0) as resp:  # noqa: S310 - 仅访问本机回环记忆服务
        data = json.loads(resp.read().decode("utf-8"))
        return len(data.get("results") or [])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="同步 docs/memory ACTIVE 卡片到记忆星云")
    parser.add_argument("--url", default=None, help="显式指定记忆星云服务 URL")
    parser.add_argument("--dry-run", action="store_true", help="仅打印待同步载荷，不发起写请求")
    parser.add_argument("--require-server", action="store_true", help="服务未运行时返回非零退出码")
    parser.add_argument(
        "--warmup-query",
        default="DDP 踩坑 数据库迁移 Canonical 并发 约束",
        help="同步后触发一次星云召回预热的查询词（留空跳过）",
    )
    args = parser.parse_args(argv)

    if not MEMORY_DIR.is_dir():
        print(f"记忆目录不存在：{MEMORY_DIR}", file=sys.stderr)
        return 1

    cards = collect_cards()
    active_cards = [c for c in cards if c.status == "ACTIVE" and not c.problems]

    if args.dry_run:
        for card in active_cards:
            payload = build_nebula_payload(card)
            preview = str(payload["text"])[:80]
            print(
                f"[DRY-RUN] {card.card_id} ({payload['kind']}, "
                f"imp={payload['importance']}): {preview}..."
            )
        print(f"共 {len(active_cards)} 张 ACTIVE 卡片待同步。")
        return 0

    base_url = probe_nebula_base_url(args.url)
    if base_url is None:
        msg = "记忆星云本地服务未运行（127.0.0.1:8898），跳过星云实时推送（CI/离线安全放行）。"
        if args.require_server:
            print(msg, file=sys.stderr)
            return 1
        print(msg)
        return 0

    created, deduped = sync_cards_to_server(base_url, active_cards)
    hits = 0
    if args.warmup_query.strip():
        try:
            hits = trigger_recall_warmup(base_url, args.warmup_query.strip())
        except (OSError, ValueError, urllib.error.URLError):
            hits = 0
    print(
        f"记忆星云同步完成（{base_url}）：ACTIVE {len(active_cards)} 张 "
        f"→ 新增 {created} 条 / 幂等更新 {deduped} 条（召回点亮 {hits} 条）。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
