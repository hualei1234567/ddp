
---

## 八、实施进度（滚动更新）

- 2026-11-25 阶段1 T1：`judge_routing.py`（位置偏差校正+长度惩罚+三区路由）+18 单测 ✅
- 2026-11-25 阶段1 T2：`honeypot_breaker.py`（黄金题滑窗熔断+暂停/解除）+9 单测 ✅
- 2026-11-25 阶段1 T3：PRM Step-Reward 导出（`llm_export/prm.py`：四类标签 reward 映射、未标注步骤不进训练文件、index 连续性强制、roundtrip 校验）+12 单测 ✅（PR #131）

**阶段 1 判定（2026-11-25 晚间）：T1/T2/T3 全部完成，阶段 1 done。**
`datasets5.2.0` 导出库（SFT / 偏好对 / PRM）验证 PASS；三方真实验证另存 `docs/testing/evidence/`。

**阶段 2（进行中，2026-11-25）**：

- PRM 步级标注域模型——步级标注实体、四类标签与 reward 映射的领域模型扩展 → in-progress
- 技能标签路由——按技能标签路由标注任务到对应 Judge / 人工队列 → in-progress
- Token 成本台账——judge / PRM 调用的 Token 计量与成本归集 → in-progress
