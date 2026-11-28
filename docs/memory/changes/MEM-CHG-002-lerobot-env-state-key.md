### MEM-CHG-002 | LeRobot 导出必须同时写 `observation.environment_state`——官方策略只认 ENV 键，只写 `observation.state` 会拒绝训练

- **类型与状态**：`CHG` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-28`
- **适用范围**：项目 `DDP` | 环节 `LeRobot 导出/训练消费` | 批次/资产 `data-platform/ddp_data/lerobot/frame_writer.py` | 密级 `INTERNAL`
- **核心结论**：`lerobot==0.3.3` 的 `dataset_to_policy_features` 把 `observation.environment_state` 判为 `FeatureType.ENV`，把 `observation.state` 判为 **STATE**；ACT 等策略的 `validate_features` 要求「至少一个 image 或 env state」——只写 `observation.state` 会直接抛 `You must provide at least one image or the environment state among the inputs.` 拒绝训练（V61-011 GATE-B 实测）。
- **关键依据与细节**：
  - 实测（2026-11-28）：DDP 导出的 v2.1 数据集只有 `observation.state[0..N]` 扁平列 → `make_policy` 构造策略时 `validate_features` 失败。
  - 注意 `make_policy`（`lerobot/policies/factory.py`）会**从数据集元数据推导并覆盖** `cfg.input_features`——在 ACTConfig 里手动传 input_features **无效**，必须在数据集 features 里提供。
  - 修法：导出器**同时**声明 `observation.environment_state`（dtype float32、shape 同 observation_dim），与 `observation.state` **同值并存**。既有键向后兼容（Loader 验证与 parquet adapter 测试依赖它），新键供官方策略消费。
  - 同族：官方 v3.0 数据集用 `observation.image`（video 类型）做 VISUAL，lerobot 0.3.3 只支持 v2.1 —— 想用官方 pusht 直接训练不可行，必须走 DDP 导出 v2.1。
- **影响与后续动作**：
  - 任何向 LeRobot 导出逐帧数据的代码，feature 清单必须包含 `observation.environment_state`。
  - 新增导出 feature 时，先查官方 `dataset_to_policy_features` 的类型判定表，再设计键名。

#### 工程扩展字段

- **check_ref**：`data-platform/ddp_data/lerobot/frame_writer.py`（`build_frame_features` 的 environment_state 声明）
- **recurrence**：`1`
- **tags**：lerobot,feature-naming,env-state,training-gate,v21,export
