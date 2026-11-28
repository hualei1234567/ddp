# GATE-B 真实训练 Smoke Test（V6.1 / V61-011）

## 背景
蓝图红线 9：「不得将 Loader 验证当作训练验证」。V5.1 只做了官方 Loader
逐帧读取（GATE-A，E1 已有证据），**目标训练脚本真实消费从未验证**。
本批完成 GATE-B。

## 验收结论：**GATE-B 通过**

| 项 | 结果 |
|---|---|
| 数据集 | DDP 导出的 LeRobot v2.1 逐帧数据集（RoboMimic `demo_v15.hdf5` 36,699,586 B，59 帧，obs 32 维，action 7 维） |
| 训练脚本 | 官方 `lerobot==0.3.3` 的 `lerobot.scripts.train.train`（**非 mock**） |
| 策略 | ACT，CPU，2 个真实 step，batch_size 2，seed 42 |
| 训练耗时 | 3.06 秒（CPU） |
| 产物 | `checkpoints/000002/`：model.safetensors + optimizer + rng_state + train_config.json |
| **回载验证** | `ACTPolicy.from_pretrained` 加载成功，`select_action` 前向输出形状 (1, 7)，**与 action 维度一致** |
| 模型 Hash | `model.safetensors` sha256 `46bbbabfac48233eeeb4edd72acc567e...`（见 evidence.json） |

**证据等级**：E1（真实环境实跑，含输入/命令/输出/版本/可复核产物）。

## 过程中发现并修掉的真实缺口（GATE-B 前置条件）

1. **导出器只有 `observation.state` 键**：官方 `dataset_to_policy_features` 只把
   `observation.environment_state` 判为 ENV，`observation.state` 判为 STATE；
   ACT 的 `validate_features` 要求「至少一个 image 或 env state」→ 直接拒绝训练。
   → 导出器增加 `observation.environment_state`（ENV 特征，同值并存）。
2. **parquet 缺嵌套向量列**：官方策略前向要 `batch["observation.state"]`
   （整向量）；DDP 只写扁平的 `observation.state[0..N]`，该键不存在 → KeyError。
   → parquet 同时写嵌套 list 列（`observation.state` /
   `observation.environment_state` / `action`），与既有扁平列并存。
3. **stats 的 count 形状**：官方 `aggregate_stats` 要求 `count` 为 shape `(1,)`，
   0 维标量直接拒绝。向量级 stats 的 `count` 必须包成 `[n]`；
   `episodes_stats.jsonl` 的序列化需「标量包一层、数组原样」。

以上三项的回归测试：`data-platform/tests/test_lerobot_frame_writer.py`（17 项）
+ `test_lerobot_video_feature.py`，既有 Loader 验证产物兼容性由
`backend/tests/unit/test_parquet_adapter.py` 保护。

## 复现方式

```bash
# 1. 训练 venv（独立，不污染 backend/.venv）
python3 -m venv /tmp/v61-train-venv
/tmp/v61-train-venv/bin/pip install "torch>=2.2.1,<2.8.0" "torchvision" "lerobot==0.3.3" "datasets<=3.6.0"

# 2. 生成 v2.1 逐帧数据集（含新列）
backend/.venv/bin/python docs/testing/evidence/2026-11-25-lerobot-frame-verify/gen_frame_dataset.py

# 3. 训练 Smoke
/tmp/v61-train-venv/bin/python scripts/train/gate_b_train_smoke.py
```

## 诚实标注（不声称）

- **不声称**训练收敛性（仅 2 步，权重不具备可用性）
- **不声称** GPU 性能（本机为 Apple M5，CPU 执行；未用 MPS）
- **不声称**策略可用性（2 步权重不构成可用策略）
- GATE-A（Loader 逐帧）与 GATE-B（真实训练）是**两个独立状态**，分别记录
- checkpoint 本体不入库（model.safetensors 体积大，只记录路径与 sha256；
  产物在本地 /tmp/v61-train-smoke/ 可复现）
