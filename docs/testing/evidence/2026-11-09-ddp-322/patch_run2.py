#!/usr/bin/env python3
"""修正 run_ddp322.py：
- 0016 只在 0015 注入历史缺陷函数后执行一次；
- D2 首次反馈审前重新读取任务/批次版本（IAA 会推进任务版本）；
- 标注员 2 的分配移到返工恢复之后，避免任务提交时存在未结分配；
- 超限阶段复用已恢复的任务/批次，并重新读取版本。
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


# A. 迁移循环不含 0016；随后注入旧函数体，再单独 upgrade 0016。
replace_once(
    'for revision in ("0012", "0013", "0014", "0015", "0016"):',
    'for revision in ("0012", "0013", "0014", "0015"):',
)

# B. 移除 D2 首次提交前创建的标注员 2 分配。
replace_once(
    '''    # 标注员 2 分配（用于 D6 的第二份提交）
    assignment2 = request(
        "A3", "分配标注员 2", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-batches/{ids['batch_main']}/annotation-assignments",
        json_body={"assignee_id": pid("an2"), "planned_item_count": 1, "planned_minutes": 30},
    )
    expect_status("A3", "分配标注员 2", assignment2, 201)
    ids["assignment_an2"] = UUID(assignment2.json()["assignment_id"])
    assignment2_json = assignment2.json()
    claim2 = request(
        "A3", "标注员 2 领取", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an2']}/actions/claim",
        json_body={}, headers=ifmatch(assignment2_json["version"]),
    )
    expect_status("A3", "标注员 2 领取", claim2, 200)
    start2 = request(
        "A3", "标注员 2 开始", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an2']}/actions/start",
        json_body={}, headers=ifmatch(claim2.json()["version"]),
    )
    expect_status("A3", "标注员 2 开始", start2, 200)
''',
    "",
)

# C. 首次反馈审前读取当前版本（IAA 已使任务版本递增）。
replace_once(
    '''    # ------------------------------------------------------------------
    # D2 复核驳回 → require-rework → v2 → 复检
    # ------------------------------------------------------------------
    batch_submitted = request(
        "D2", "批次提交反馈审", "an1", "POST", f"{batch_url}/actions/submit",
        json_body={}, headers=ifmatch(batch_started.json()["version"]),
    )
    expect_status("D2", "批次提交反馈审", batch_submitted, 200)
    task_submitted2 = request(
        "D2", "任务提交反馈审", "an1", "POST", f"{task_url}/actions/submit",
        json_body={}, headers=ifmatch(task_started.json()["version"]),
    )
''',
    '''    # ------------------------------------------------------------------
    # D2 复核驳回 → require-rework → v2 → 复检
    # ------------------------------------------------------------------
    task_current_for_submit = request("D2", "读取任务当前版本", "an1", "GET", task_url)
    batch_current_for_submit = request("D2", "读取批次当前版本", "an1", "GET", batch_url)
    expect_status("D2", "读取任务当前版本", task_current_for_submit, 200)
    expect_status("D2", "读取批次当前版本", batch_current_for_submit, 200)
    batch_submitted = request(
        "D2", "批次提交反馈审", "an1", "POST", f"{batch_url}/actions/submit",
        json_body={}, headers=ifmatch(batch_current_for_submit.json()["version"]),
    )
    expect_status("D2", "批次提交反馈审", batch_submitted, 200)
    task_submitted2 = request(
        "D2", "任务提交反馈审", "an1", "POST", f"{task_url}/actions/submit",
        json_body={}, headers=ifmatch(task_current_for_submit.json()["version"]),
    )
''',
)

# D. v2 复检通过后：恢复任务/批次，创建并启动标注员 2 分配。
replace_once(
    '''    expect_eq("D2", "v2 最终 APPROVED", "APPROVED", v2_final.json()["status"])

    # ------------------------------------------------------------------
    # D6 抽检 plan 三种策略
    # ------------------------------------------------------------------
''',
    '''    expect_eq("D2", "v2 最终 APPROVED", "APPROVED", v2_final.json()["status"])

    # 返工闭环后任务/批次处于 REWORK；恢复后进行第二轮工作（D6/超限）。
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

    assignment2 = request(
        "A3", "分配标注员 2", "an1", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-batches/{ids['batch_main']}/annotation-assignments",
        json_body={"assignee_id": pid("an2"), "planned_item_count": 1, "planned_minutes": 30},
    )
    expect_status("A3", "分配标注员 2", assignment2, 201)
    ids["assignment_an2"] = UUID(assignment2.json()["assignment_id"])
    assignment2_json = assignment2.json()
    claim2 = request(
        "A3", "标注员 2 领取", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an2']}/actions/claim",
        json_body={}, headers=ifmatch(assignment2_json["version"]),
    )
    expect_status("A3", "标注员 2 领取", claim2, 200)
    start2 = request(
        "A3", "标注员 2 开始", "an2", "POST",
        f"/api/v1/projects/{ids['project_a']}/annotation-assignments/{ids['assignment_an2']}/actions/start",
        json_body={}, headers=ifmatch(claim2.json()["version"]),
    )
    expect_status("A3", "标注员 2 开始", start2, 200)

    # ------------------------------------------------------------------
    # D6 抽检 plan 三种策略
    # ------------------------------------------------------------------
''',
)

# E. 超限阶段不再重复 resume-work（已在 D6 前完成）。
replace_once(
    '''    # ------------------------------------------------------------------
    # D2 超限 409（v3 返工）
    # ------------------------------------------------------------------
    # 任务/批次当前 REWORK；先 resume-work 到 IN_PROGRESS，再 rework 分配并创建 v3。
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
    '''    # ------------------------------------------------------------------
    # D2 超限 409（v3 返工）
    # ------------------------------------------------------------------
''',
)

# F. 再次反馈审前读取当前版本。
replace_once(
    '''    batch_submitted2 = request(
        "D2", "批次再次提交反馈审", "an1", "POST", f"{batch_url}/actions/submit",
        json_body={}, headers=ifmatch(batch_resumed.json()["version"]),
    )
    expect_status("D2", "批次再次提交反馈审", batch_submitted2, 200)
    task_submitted3 = request(
        "D2", "任务再次提交反馈审", "an1", "POST", f"{task_url}/actions/submit",
        json_body={}, headers=ifmatch(task_resumed.json()["version"]),
    )
''',
    '''    task_current_for_submit2 = request("D2", "读取任务当前版本（第二轮）", "an1", "GET", task_url)
    batch_current_for_submit2 = request("D2", "读取批次当前版本（第二轮）", "an1", "GET", batch_url)
    expect_status("D2", "读取任务当前版本（第二轮）", task_current_for_submit2, 200)
    expect_status("D2", "读取批次当前版本（第二轮）", batch_current_for_submit2, 200)
    batch_submitted2 = request(
        "D2", "批次再次提交反馈审", "an1", "POST", f"{batch_url}/actions/submit",
        json_body={}, headers=ifmatch(batch_current_for_submit2.json()["version"]),
    )
    expect_status("D2", "批次再次提交反馈审", batch_submitted2, 200)
    task_submitted3 = request(
        "D2", "任务再次提交反馈审", "an1", "POST", f"{task_url}/actions/submit",
        json_body={}, headers=ifmatch(task_current_for_submit2.json()["version"]),
    )
''',
)

TARGET.write_text(TEXT, encoding="utf-8")
print("run_ddp322.py 第二轮修正完成")
