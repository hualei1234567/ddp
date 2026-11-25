
---

## 八、实施进度（滚动更新）

- 2026-11-25 阶段1 T1：`judge_routing.py`（位置偏差校正+长度惩罚+三区路由）+18 单测 ✅
- 2026-11-25 阶段1 T2：`honeypot_breaker.py`（黄金题滑窗熔断+暂停/解除）+9 单测 ✅
- 2026-11-25 阶段1 T3：PRM Step-Reward 导出（`llm_export/prm.py`：四类标签 reward 映射、未标注步骤不进训练文件、index 连续性强制、roundtrip 校验）+12 单测 ✅（PR #131）

**阶段 1 判定（2026-11-25 晚间）：T1/T2/T3 全部完成，阶段 1 done。**
`datasets5.2.0` 导出库（SFT / 偏好对 / PRM）验证 PASS；三方真实验证证据见 `docs/testing/evidence/2026-11-25-llm-export-verify/`（PR #133）。

**阶段 2（进行中，2026-11-25）**：

- PRM 步级标注域模型——PR #134（域模型+Fork 分叉→Step-DPO+契约 1.40.0，39 测试）→ CI 审核中
- 技能标签路由——`skill_routing.py`（必配过滤+可选加权评分+零配置兼容）+迁移 0080 → PR #136（契约版本待主控统一 bump 1.40.0）
- Token 成本台账——PR #135（迁移 0081+ROI 口径+可选注入记账，全量 1236 passed）→ CI 审核中
