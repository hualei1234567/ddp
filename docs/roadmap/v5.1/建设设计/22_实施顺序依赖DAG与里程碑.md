# 22｜V5.1 实施顺序、依赖 DAG 与里程碑

## M0：审计基线与样本盘点
复核最新 HEAD、分支、工作区、Migration、OpenAPI、测试；核对 V5.1 FR 对应代码路径；盘点 Adapter 和真实样本；选择一个 Golden Path 格式；读取相关 ADR；形成 Current Baseline、Reuse Map、Sample Gap、Risk Delta。
**Gate：** 用户确认 Golden Path 和样本来源后进入数据物化开发。

## M1：治理缺口先闭环（可并行）
V51-008 标注冲突恢复；V51-013 RBAC 浏览器 E2E；V51-017 Frozen Dataset 引用保护；V51-014/015/016 恢复演练；V51-010 QC 证据定位。
**Gate：** 真实 UI/API/DB 测试通过，历史数据不被破坏。

## M2：Golden Path 解析和物化
V51-005 Adapter 能力分级；V51-001 SensorStream；V51-002 Observation；V51-003 Action；V51-004 时间同步；V51-007 步骤级幂等；V51-006 Cleaning。
**Gate：** 至少一种真实数据格式从 RawAsset 到 Episode 的逐帧物化通过。

## M3：训练就绪
V51-011 LeRobot Frame-level；V51-012 Lineage E2E；V51-024 Dataset 来源/引用校验；Dataset Gate 和 Split 回归。
**Gate：** 官方 Loader 读取真实 Parquet、视频和 Observation；有 Action 源时读取 Action。

## M4：Staging / Hardware Pilot
V51-018 Staging；V51-019 真实硬件 Pilot；备份恢复和权限 E2E；全链路证据包。
**Gate：** 真实设备或明确标注的公开数据集测试；不混淆测试等级。

## M5：条件性 AI / 训练平台
V51-020 GPU 模型推理；V51-021 外部训练平台连接器。
**Gate：** 资源、权重、API、认证、回调契约全部到位。

## 依赖关系
M0 → M2 → M3 → M4。M0 → M1；M1 可与 M2 并行。M3 不依赖 AI/GPU 或外部训练平台；M5 依赖外部资源，不应阻塞 V5.1 核心数据生产验收。

## 不允许的推进方式
没样本先写一堆格式 Parser；先做 UI 再补服务端数据语义；用模拟 Observation 宣称真实物化；先改 Canonical 主 Schema 再找兼容方式；所有 Adapter 一次性重写；把外部 BLOCKED 事项伪装成开发完成。
