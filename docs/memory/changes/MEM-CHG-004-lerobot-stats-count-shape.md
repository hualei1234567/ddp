### MEM-CHG-004 | LeRobot stats 的 `count` 必须是 shape `(1,)`——0 维标量与错误序列化都会被 aggregate_stats 拒绝

- **类型与状态**：`CHG` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-28`
- **适用范围**：项目 `DDP` | 环节 `LeRobot 导出/训练消费` | 批次/资产 `data-platform/ddp_data/lerobot/frame_writer.py` | 密级 `INTERNAL`
- **核心结论**：官方 `aggregate_stats` 对 stats 值有**硬形状校验**：任何值必须是 numpy 数组且 `ndim >= 1`；`count` 必须 **shape `(1,)`**（0 维标量报 "Number of dimensions must be at least 1"，`(1,1)` 报 "Shape of 'count' must be (1)"）。DDP 旧的「标量 count」在向量级特征上不满足，且 `episodes_stats.jsonl` 的序列化会二次包裹。
- **关键依据与细节**：
  - 实测（2026-11-28，GATE-B）：三连错——①向量级 stats 用标量 count → ndim=0 拒绝；②jsonl 统一 `[值]` 包裹 → 向量级变成 `(1,dim)` 拒绝；③修法见下。
  - **正确序列化规则（统一函数）**：`list 原样，标量包一层`。标量级 stats（`observation.state[0]`、`timestamp`）值是标量 → `[v]`；向量级 stats（`observation.state` / `observation.environment_state` / `action`）值已是逐维数组 → **原样**。
  - 官方 `compute_episode_stats` 的口径：1 维数组 `keepdims=True`（count 是 `(1,)`），2 维数组 `keepdims=False`（min/max/mean/std 是逐维标量，count 仍是 `(1,)`）。
- **影响与后续动作**：
  - 写 `episodes_stats.jsonl` 前，按「值是否已是 list」区分包裹，不要统一包一层。
  - 新增特征时，先确认它的 stats 是标量级还是向量级，再决定序列化形态。

#### 工程扩展字段

- **check_ref**：`data-platform/ddp_data/lerobot/frame_writer.py`（`build_episodes_stats_jsonl` 的 `_wrap` 规则、`_vector_stats`）
- **recurrence**：`1`
- **tags**：lerobot,stats,aggregate-stats,count-shape,jsonl,training-gate
