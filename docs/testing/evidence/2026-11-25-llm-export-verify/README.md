# V52-015/016/017 LLM 训练导出 —— 第三方真实验证证据

## 结论

**THIRD-PARTY VERIFY: PASS**（SFT + DPO + PRM 三种格式，官方 `datasets==5.2.0`）

## 环境

- 隔离 venv `/tmp/llm-export-verify`（python 3.11.17，不在 backend/.venv）
- `pip install datasets` → `datasets==5.2.0`

## 命令

```bash
# 单元测试（普通 CI）
PYTHONPATH="data-platform" backend/.venv/bin/python -m pytest data-platform/tests/test_llm_export.py data-platform/tests/test_llm_export_prm.py -q

# 第三方加载验证（隔离 venv）
/tmp/llm-export-verify/bin/python \
  docs/testing/evidence/2026-11-25-llm-export-verify/verify_third_party_load.py
```

## 断言明细（全部 PASS）

| 断言 | 说明 |
|---|---|
| SFT 3 行、messages[0].role=system | OpenAI 兼容结构 |
| DPO 4 行、列恰为 prompt/chosen/rejected | TRL 兼容 |
| 槽语义 winner=槽A → chosen=好答案0 | winner 是呈现槽标签，映射回 canonical |
| 槽语义 winner=槽B → chosen=差答案1 | A/B 翻转不改变导出语义（位置无关） |
| PRM 1 行 4 步骤、FATAL=-2 | 四类标签 reward 映射 |
| PRM REDUNDANT=-0.5 | 冗余绕路扣分 |
| streaming 逐行读取 | 大文件官方路径 |

## 易错点备忘（复现本验证前必读）

`PreferenceRecord.from_raw` 的 `winner` 参数是**呈现槽标签**（标注员屏幕上的 A/B 按钮），
`presented_order[k]` 是槽 k 展示的 canonical 响应名。`presented_order=("A","B")` 时
`winner="B"` → chosen = canonical B。**不要**把 winner 当 canonical 身份直接断言。

## 诚实边界

- 未安装/未验证 `trl` trainer（结构对齐 TRL preference 约定，但未跑真实 trainer）
- 未接线后端 `ExportFormat` 枚举（纯 data-platform 库，接线为后续任务）
- 未跑真实训练：本证据只证明"官方库正确解析产物结构"，不代表训练效果
