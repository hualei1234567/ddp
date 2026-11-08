#!/usr/bin/env python3
"""补充报告中的 server.log 说明。"""

from pathlib import Path

TARGET = (
    Path(__file__).resolve().parents[2]
    / "阶段3标注域验收报告-2026-11-09-v1.2.md"
)
TEXT = TARGET.read_text(encoding="utf-8")
OLD = "| `server.log` | uvicorn 运行日志 |"
NEW = "| `server.log` | uvicorn 运行日志（本轮无错误，0 字节） |"
if OLD not in TEXT:
    raise SystemExit("未找到 server.log 说明行")
TEXT = TEXT.replace(OLD, NEW, 1)
TARGET.write_text(TEXT, encoding="utf-8")
print("报告 server.log 说明已补充")
