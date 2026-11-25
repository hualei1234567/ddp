"""第三方真实验证（可重复执行）：官方 datasets 加载 SFT/DPO/PRM 导出产物。

语义契约（易错点，见 README）：``winner`` 是**呈现槽标签**（标注员屏幕上的 A/B），
``presented_order[k]`` = 槽 k 展示的 canonical 响应名。库内部把"槽胜出"映射回
canonical 身份——A/B 翻转不改变导出语义。
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3].resolve()
while not (REPO / "data-platform" / "ddp_data").exists() and REPO != REPO.parent:
    REPO = REPO.parent
sys.path.insert(0, str(REPO / "data-platform"))

from ddp_data.llm_export import (  # noqa: E402
    PreferenceRecord,
    Provenance,
    QcStatus,
    ReviewStatus,
    SftConversationRecord,
)
from ddp_data.llm_export.prm import TrajectoryRecord, export_prm  # noqa: E402
from ddp_data.llm_export.writers import export_dpo, export_sft  # noqa: E402

OUT = Path("/tmp/llm-verify-data")


def _prov(sample: str) -> Provenance:
    return Provenance(
        dataset_version_id="dv-9",
        sample_id=sample,
        submission_id=f"sub-{sample}",
        review_status=ReviewStatus.APPROVED,
        qc_status=QcStatus.PASSED,
        schema_version="agent-trace/v1",
    )


def main() -> int:
    import datasets

    shutil.rmtree(OUT, ignore_errors=True)
    OUT.mkdir(parents=True)
    sft = [
        SftConversationRecord.from_raw(
            _prov(f"s{i}"),
            [
                {"role": "system", "content": "你是助手"},
                {"role": "user", "content": f"问题{i}"},
                {"role": "assistant", "content": f"回答{i}"},
            ],
        )
        for i in range(3)
    ]
    prefs = [
        PreferenceRecord.from_raw(
            _prov(f"p{i}"),
            prompt=f"提示{i}",
            response_a=f"好答案{i}",
            response_b=f"差答案{i}",
            winner="A" if i % 2 == 0 else "B",
        )
        for i in range(4)
    ]
    traj = TrajectoryRecord.from_raw(
        _prov("t1"),
        trajectory_id="traj-1",
        task_goal="修复测试",
        steps=[
            {"index": i, "action": f"step{i}", "label": lbl}
            for i, lbl in enumerate(["CORRECT", "CORRECT", "REDUNDANT", "FATAL"])
        ],
    )
    sb, db, pb = export_sft(sft), export_dpo(prefs), export_prm([traj])
    (OUT / "sft_messages.jsonl").write_bytes(sb.files["sft_messages.jsonl"])
    (OUT / "preference_dpo.jsonl").write_bytes(db.files["preference_dpo.jsonl"])
    (OUT / "prm_step_reward.jsonl").write_bytes(pb.files["prm_step_reward.jsonl"])

    checks: list[tuple[str, bool]] = []
    d1 = datasets.load_dataset("json", data_files=str(OUT / "sft_messages.jsonl"), split="train")
    checks.append(("SFT: 3 行、messages[0].role=system", len(d1) == 3 and d1[0]["messages"][0]["role"] == "system"))
    d2 = datasets.load_dataset("json", data_files=str(OUT / "preference_dpo.jsonl"), split="train")
    checks.append(("DPO: 4 行、列恰为 prompt/chosen/rejected", len(d2) == 4 and set(d2.column_names) == {"prompt", "chosen", "rejected"}))
    checks.append(("槽语义: p0 winner=槽A → chosen=好答案0", d2[0]["chosen"] == "好答案0"))
    checks.append(("槽语义: p1 winner=槽B → chosen=差答案1", d2[1]["chosen"] == "差答案1"))
    d3 = datasets.load_dataset("json", data_files=str(OUT / "prm_step_reward.jsonl"), split="train")
    steps = d3[0]["steps"]
    checks.append(("PRM: 1 行 4 步骤、FATAL=-2", len(d3) == 1 and steps[3]["reward"] == -2.0 and steps[3]["label"] == "FATAL"))
    checks.append(("PRM: REDUNDANT=-0.5", steps[2]["reward"] == -0.5))
    stream = datasets.load_dataset("json", data_files=str(OUT / "preference_dpo.jsonl"), split="train", streaming=True)
    checks.append(("streaming 逐行读取", "prompt" in next(iter(stream))))

    ok = all(c for _, c in checks)
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
    print(f"\n环境: datasets=={datasets.__version__}")
    print("THIRD-PARTY VERIFY (SFT+DPO+PRM):", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
