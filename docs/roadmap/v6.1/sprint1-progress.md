# V6.1 第一夜建设记录（Sprint 1）

> 2026-11-28 通宵 · General Owner 总控 · 三链并行 + 串行合并

## 交付清单

| 任务 | 交付 | 验证 |
|---|---|---|
| T1-01/02 视频 fixture + 解码 | `data-platform/ddp_data/media/video_decode.py`（probe_frames 逐帧 pts 实测）+ `docs/testing/evidence/2026-11-28-video-fixture/` | AV1 实测可解；25,650 帧、pts 0→2564.9s 自洽；20 项测试（19+1 skip） |
| T1-03 迁移 0087 | `video_frames` 表 + STREAM_EXTRACTION 阶段扩展（VIDEO_DECODE/FRAME_INDEX/TIMESTAMP_NORMALIZE）+ 防回归测试更新 | 临时库 upgrade→downgrade→upgrade 往返；防回归 4 项 |
| T1-04 视频阶段处理器 | `app/application/processing/video_decode_stage.py`（VIDEO_DECODE → video_frames 落库） | 4 项测试（原始时间保留/归一化留空/失败帧留痕/超限拒绝） |
| T1-06 LeRobot video | 导出器支持 `observation.environment_state` + video feature（`video_provided` 真实反映） | 17 项 frame_writer + 17 项 parquet_adapter 全绿 |
| T3 GATE-B 真实训练 | 导出器补嵌套向量列 + 向量级 stats + `scripts/train/gate_b_train_smoke.py` + 证据 | **2 步真实训练 + checkpoint 回载前向验证通过**（见 `2026-11-28-gate-b-training/`） |
| F1/F2 前端 | Badcase 路由确认 + Pipeline 监控页（列表/详情/四态） | 16 项新测试；前端全量 1295 passed |

## 关键实测数字

- **GATE-B**：59 帧 v2.1 数据集 → ACT 策略 CPU 2 步 → checkpoint（model.safetensors sha256 `46bbbabf...`）→ 回载前向输出 (1,7) 与 action 维度一致。墙钟 3.06s。
- **视频解码**：AV1 96×96@10fps 25,650 帧全部可解（ffmpeg 9.0.2）。
- **迁移 0087**：76→77 步，单 head，临时库往返验证通过。

## 过程中修掉的真实缺陷（含记忆卡）

1. **ENV 特征缺失**（MEM-CHG-002）：官方 `dataset_to_policy_features` 只认 `observation.environment_state` 为 ENV，DDP 只写 `observation.state` → ACT 拒绝训练。
2. **嵌套向量列缺失**（MEM-CHG-003）：官方策略前向要 `batch["observation.state"]` 整向量，DDP 只有扁平 `[0..N]` → KeyError。
3. **stats count 形状**（MEM-CHG-004）：官方 `aggregate_stats` 要求 count shape `(1,)`，0 维标量拒绝；jsonl 序列化需「标量包一层、数组原样」。
4. **CSS/类型细节**：前端测试缺 Router 包装（Link 上下文）、阶段名断言需用中文展示名、onAct 签名需带 payload。

## 合并结果

`feat/v61-video` 聚合了 video + training + frontend 三条链（含各代理提交），
合并后 ruff/secret/契约/前端全绿，check.sh 全量后台执行中。

## 尚未做（诚实标注）

- T1-05 时间同步阶段（TIMESTAMP_NORMALIZE 处理器）——枚举与表已就位，处理器未接线
- T1-06 GATE-A 视频解码的官方 Loader 验证（导出器已支持，未跑端到端）
- T3-03 `training_run_evidence` 表（0088）——GATE-B 已有证据 JSON，落库待后续
- 推送 PR 与 CI ——check.sh 通过后执行
