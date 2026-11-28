### MEM-CHG-003 | 官方 LeRobot 策略消费的是**嵌套向量列**，只有扁平 `observation.state[0..N]` 列会 KeyError

- **类型与状态**：`CHG` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-28`
- **适用范围**：项目 `DDP` | 环节 `LeRobot 导出/训练消费` | 批次/资产 `data-platform/ddp_data/lerobot/frame_writer.py` | 密级 `INTERNAL`
- **核心结论**：官方 `save_episode` 经 HF datasets 写出的是**嵌套 list 列**（如 `observation.state` 为整向量 list）；官方策略前向直接取 `batch["observation.state"]`（整向量）。DDP 只写扁平的 `observation.state[0..N]` 列时，该键**不存在**，ACT 前向 KeyError。**必须同时写嵌套向量列，与既有扁平列并存。**
- **关键依据与细节**：
  - 实测（2026-11-28，GATE-B）：仅改 ENV 键声明后，策略构造通过但训练一步内 `batch["observation.state"]` KeyError；补上三个嵌套 list 列后跑通。
  - 键名冲突确认：`observation.state` 与 `observation.state[0]` 在 Parquet 里是**不同列名**，共存无歧义（实测 pyarrow 读取，列数 80）。
  - 修改面：parquet 列（`pa.list_(pa.float32())`）+ features 声明 + stats（向量级）三处必须同步，缺一训练必败。
- **影响与后续动作**：
  - 导出器的「特征键名」与「列形态」是两条独立维度：键名供官方策略匹配，列形态（扁平/嵌套）供前向取数。改一个维度时检查另一个。
  - 回归保护：`data-platform/tests/test_lerobot_frame_writer.py` 断言列清单与 features 一致性。

#### 工程扩展字段

- **check_ref**：`data-platform/ddp_data/lerobot/frame_writer.py`（`_write_parquet` 的嵌套 list 列）
- **recurrence**：`1`
- **tags**：lerobot,nested-columns,parquet,training-gate,act-policy
