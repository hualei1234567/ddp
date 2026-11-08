#!/usr/bin/env python3
"""第四轮修正：复核通过必须提供全部通过的 checklist。"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent / "run_ddp322.py"
TEXT = TARGET.read_text(encoding="utf-8")

OLD = '''    approved = request(
        "D2", "复检通过 v2", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review2']}/actions/approve",
        json_body={}, headers=ifmatch(review2_in["version"]),
    )
'''
NEW = '''    approved = request(
        "D2", "复检通过 v2", "owner_a", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-reviews/{ids['review2']}/actions/approve",
        json_body={"checklist": [{"key": "labels", "passed": True}]},
        headers=ifmatch(review2_in["version"]),
    )
'''

if OLD not in TEXT:
    raise SystemExit("未找到复检通过片段")
TEXT = TEXT.replace(OLD, NEW, 1)
TARGET.write_text(TEXT, encoding="utf-8")
print("run_ddp322.py 第四轮修正完成")
