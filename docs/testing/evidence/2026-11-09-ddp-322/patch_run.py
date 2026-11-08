#!/usr/bin/env python3
"""修正 run_ddp322.py 中任务/批次提交时的 If-Match 版本取值。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent / "run_ddp322.py"
TEXT = TARGET.read_text(encoding="utf-8")

REPLACEMENTS = (
    (
        'headers=ifmatch(batch_ready_json["version"] + 1),',
        'headers=ifmatch(batch_started.json()["version"]),',
    ),
    (
        'headers=ifmatch(task_ready_json["version"] + 1),',
        'headers=ifmatch(task_started.json()["version"]),',
    ),
    (
        'headers=ifmatch(batch_resumed.json()["version"] + 1),',
        'headers=ifmatch(batch_resumed.json()["version"]),',
    ),
    (
        'headers=ifmatch(task_resumed.json()["version"] + 1),',
        'headers=ifmatch(task_resumed.json()["version"]),',
    ),
)

for old, new in REPLACEMENTS:
    if old not in TEXT:
        raise SystemExit(f"未找到待替换片段：{old}")
    TEXT = TEXT.replace(old, new, 1)

TARGET.write_text(TEXT, encoding="utf-8")
print("run_ddp322.py If-Match 修正完成")
