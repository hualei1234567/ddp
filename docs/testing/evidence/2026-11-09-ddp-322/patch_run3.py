#!/usr/bin/env python3
"""第三轮修正：v2 提交前必须先恢复任务/批次到 IN_PROGRESS。

assignment 提交的数据库守卫不允许任务/批次处于 REWORK；因此返工闭环应为：
require-rework → 任务/批次 REWORK → 创建 v2 → resume-work → 提交 v2。
"""

from __future__ import annotations

from pathlib import Path

TARGET = Path(__file__).resolve().parent / "run_ddp322.py"
TEXT = TARGET.read_text(encoding="utf-8")


def replace_once(old: str, new: str) -> None:
    global TEXT
    if old not in TEXT:
        raise SystemExit(f"未找到片段：{old[:120]!r}")
    TEXT = TEXT.replace(old, new, 1)


# 1. v2 提交前恢复任务/批次。
replace_once(
    '''    sub2_submitted = request(
        "D2", "提交 v2", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v2']}/actions/submit",
        json_body={}, headers=ifmatch(sub2["version"]),
    )
''',
    '''    task_resumed = request(
        "D2", "任务 REWORK 恢复", "an1", "POST", f"{task_url}/actions/resume-work",
        json_body={}, headers=ifmatch(task_after.json()["version"]),
    )
    expect_status("D2", "任务 REWORK 恢复", task_resumed, 200)
    batch_resumed = request(
        "D2", "批次 REWORK 恢复", "an1", "POST", f"{batch_url}/actions/resume-work",
        json_body={}, headers=ifmatch(batch_after.json()["version"]),
    )
    expect_status("D2", "批次 REWORK 恢复", batch_resumed, 200)

    sub2_submitted = request(
        "D2", "提交 v2", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-submissions/{ids['sub1_v2']}/actions/submit",
        json_body={}, headers=ifmatch(sub2["version"]),
    )
''',
)

# 2. 删除 D6 前重复的 resume-work（此时已是 IN_PROGRESS）。
replace_once(
    '''    # 返工闭环后任务/批次处于 REWORK；恢复后进行第二轮工作（D6/超限）。
    task_resumed = request(
        "D2", "任务 REWORK 恢复", "an1", "POST", f"{task_url}/actions/resume-work",
        json_body={}, headers=ifmatch(task_after.json()["version"]),
    )
    expect_status("D2", "任务 REWORK 恢复", task_resumed, 200)
    batch_resumed = request(
        "D2", "批次 REWORK 恢复", "an1", "POST", f"{batch_url}/actions/resume-work",
        json_body={}, headers=ifmatch(batch_after.json()["version"]),
    )
    expect_status("D2", "批次 REWORK 恢复", batch_resumed, 200)

''',
    "",
)

TARGET.write_text(TEXT, encoding="utf-8")
print("run_ddp322.py 第三轮修正完成")
