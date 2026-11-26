"""训练侧消费探测的 CLI 入口（阶段 5 / 蓝图 N4 终点）。

从**真实导出制品**（现场用 llm_export 生成，或直接读取已有 JSONL）跑
SFT / DPO 两个探测，输出 JSON 报告。

```bash
# 用真实导出的 SFT/DPO 制品跑探测
python scripts/train/run_training_probe.py --sft /path/sft_messages.jsonl \\
    --dpo /path/preference_dpo.jsonl --out report.json

# 只跑 SFT
python scripts/train/run_training_probe.py --sft /path/sft_messages.jsonl
```

定位：这是「数据可用性 + 可学习性」验证，不是生产级微调。
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "data-platform"))

from ddp_data.llm_export.training_probe import (  # noqa: E402
    train_dpo_probe,
    train_sft_probe,
    summarize,
)


def _load_sft(path: Path) -> list[str]:
    """从 SFT JSONL 抽取 assistant 文本作为训练语料。

    兼容两种行形态：OpenAI 兼容的 messages 数组，或直接的文本行。
    """
    texts: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            texts.append(raw)
            continue
        if isinstance(record, dict):
            messages = record.get("messages")
            if isinstance(messages, list):
                assistant = [
                    item.get("content", "")
                    for item in messages
                    if item.get("role") == "assistant"
                ]
                texts.extend(assistant)
                continue
            texts.append(str(record))
    return [text for text in texts if text]


def _load_dpo(path: Path) -> list[tuple[str, str]]:
    """从 DPO JSONL 抽取 (chosen, rejected) 偏好对。"""
    pairs: list[tuple[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            continue
        chosen = record.get("chosen")
        rejected = record.get("rejected")
        if isinstance(chosen, str) and isinstance(rejected, str):
            pairs.append((chosen, rejected))
    return pairs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="训练侧消费探测（真实导出制品）")
    parser.add_argument("--sft", help="SFT JSONL 路径（OpenAI 兼容 messages 或文本行）")
    parser.add_argument("--dpo", help="DPO JSONL 路径（chosen/rejected）")
    parser.add_argument("--sft-epochs", type=int, default=30)
    parser.add_argument("--dpo-epochs", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20261127)
    parser.add_argument("--out", default="", help="报告输出路径")
    args = parser.parse_args(argv)

    if not args.sft and not args.dpo:
        print("至少需要 --sft 或 --dpo 之一", file=sys.stderr)
        return 2

    payload: dict[str, Any] = {"seed": args.seed, "sft": None, "dpo": None}
    if args.sft:
        sft_path = Path(args.sft)
        texts = _load_sft(sft_path)
        if not texts:
            print(f"SFT 文件 {sft_path} 未解析到任何 assistant 文本", file=sys.stderr)
            return 2
        start = time.perf_counter()
        result = train_sft_probe(texts, epochs=args.sft_epochs, seed=args.seed)
        wall = time.perf_counter() - start
        payload["sft"] = {
            "initial_loss": result.initial_loss,
            "final_loss": result.final_loss,
            "loss_improved": result.loss_improved,
            "steps": result.steps,
            "wall_seconds": round(wall, 3),
            "vocab_size": result.vocab_size,
            "param_count": result.param_count,
            "texts_count": len(texts),
            "samples": list(result.samples),
        }
    if args.dpo:
        dpo_path = Path(args.dpo)
        pairs = _load_dpo(dpo_path)
        if not pairs:
            print(f"DPO 文件 {dpo_path} 未解析到任何偏好对", file=sys.stderr)
            return 2
        start = time.perf_counter()
        result = train_dpo_probe(pairs, epochs=args.dpo_epochs, seed=args.seed)
        wall = time.perf_counter() - start
        payload["dpo"] = {
            "initial_accuracy": result.initial_accuracy,
            "final_accuracy": result.final_accuracy,
            "accuracy_improved": result.accuracy_improved,
            "steps": result.steps,
            "wall_seconds": round(wall, 3),
            "pairs_count": len(pairs),
        }

    report = {"generated_at": "训练探测", "summary": summarize(payload), "raw": payload}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.out:
        Path(args.out).write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n报告已写入 {args.out}", file=sys.stderr)

    # 任一探测未收敛即失败（防止"看起来跑了但没学到"被当成成功）
    problems: list[str] = []
    sft_summary = report["summary"].get("sft")
    if sft_summary and not sft_summary["loss_improved"]:
        problems.append("SFT loss 未下降")
    dpo_summary = report["summary"].get("dpo")
    if dpo_summary and not dpo_summary["accuracy_improved"]:
        problems.append("DPO 判对率未提升")
    if problems:
        print("探测未收敛：" + "；".join(problems), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
