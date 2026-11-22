# ADR-0041 处理链路创建 Episode 的边界来源

- 状态：**ACCEPTED**
- 日期：2026-11-22
- 决策者：System Architect + 后端；批准人：用户（长期授权）
- 关联：ADR-0014（复用优先）、ADR-0038（Episode 可训练口径）、ADR-0039（产物目录）、V31-018
- 约束：不新增依赖、不新增表/迁移、不改分段规则语义

## 1. 背景

`EPISODE_SEGMENTATION` 此前只做一件事：核验输入已关联 Episode 的冻结规则
版本是否可追溯，**不创建任何 Episode**。意味着"上传一个原始录制文件 →
跑处理流水线 → 得到可标注的 Episode"这条链路在真实数据上断在最后一步，
只能靠人工调用创建 API。

V31-018 要为这条链路补上"创建"，但 Episode 的 `segmentation_rule_version`
是冻结字段，数据集 Manifest 与质量报告都靠它回溯"这条 Episode 是怎么切出来的"。
如果边界来源不可靠，创建出来的 Episode 会带着一个**看起来正确**的溯源标签，
比没有 Episode 更糟。

## 2. 边界来源审计（哪些输入真的"携带明确边界"）

| 来源 | 是否有可用边界 | 结论 |
|---|---|---|
| 元数据阶段 `probe_mp4` | `duration_ms` 即整个文件的时长 | **有**：单文件单 Episode，边界 `[0, duration)` |
| 导入链路 `raw_metadata` | 只有 `episode_key` / `source_path` / `camera` / `source_fps` | 无时间边界 |
| HDF5 结构摘要 | 只有数据集路径与属性名，不解码值 | 无时间边界 |
| MCAP / ROS Bag / Parquet | 单文件可能装多段 Episode，文件起止是**容器**范围 | 无 Episode 边界 |

因此当前唯一可验证的"明确边界"是**单文件 MP4**。

## 3. 候选方案

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 容器文件整段当一个 Episode | 用元数据阶段的对象大小/时长直接建 | 造出覆盖多个真实片段的假 Episode；后续标注、质检、数据集全部建立在错误的样本粒度上 |
| B. 只承认能证明的边界（MP4），其余如实 `SKIPPED` | 边界推导是纯函数；无边界写明原因码 | 容器格式暂时不产出 Episode |
| C. 让处理链路自己实现容器级分段（解析 MCAP 内 Topic/时间戳切分） | 在处理链路里新增多格式分段器 | 需要新的规则版本、参数与验收样例，且与"上游给出边界"的语义冲突：处理链路的职责是复现边界，不是发明边界 |

## 4. 决策

**选方案 B。**

1. **边界推导是纯函数**（`application/processing/episode_boundaries.py`）：
   输入是运行的对象引用与元数据产物文档，输出 `boundaries` 与 `skipped`。
   不读存储、不写库，"为什么这个文件被/没被切成 Episode"可以只靠一次调用复盘。
2. **格式白名单只有一个取值**（`SINGLE_EPISODE_FORMAT = "MP4"`）。新增格式时
   必须同时说明"为什么这个格式不会有多个 Episode"，不接受"先加上再说"。
3. **无边界不是失败，是显式跳过**：阶段 `SKIPPED`，摘要写明原因码
   （`metadata_artifact_missing` / `duration_missing` / `"<FMT> 可能是多
   Episode 容器，缺少明确的 Episode 边界声明"`）。运行本身仍按成功收口——
   "没有可切的样本"与"处理失败"是不同事实。
4. **落库复用 `EpisodeService.create`**：`episode_raw_assets` 快照、血缘、审计
   与 outbox 事件只允许存在一处权威实现。规则用已注册的
   `episode-boundary/1.0.0`，`rule_parameters.boundary_source = "PARSER"`
   记录边界来自解析而非人工。
5. **幂等靠稳定 code**：`EP-<asset_id[:16]>-<start_time_ns>`。重跑先按 code
   命中即返回"已存在"；并发撞 code 时重读一次确认真是同一条，重读为空才
   报冲突。
6. **时间基准是 `DEGRADED`**：文件内相对时长没有墙钟与同步证据。按
   ADR-0038 与 `domain/episodes/validation.py` 的口径，DEGRADED 允许建
   Episode，但自动就绪会因"时间同步未验证"被挡住——先如实登记，再由门禁
   决定能否进数据集。

## 5. 后果

- 真实 MP4 上传 → 处理运行 → Episode（`PENDING`，带 readiness blocker）
  全链路可用；容器格式如实不产出，缺口登记在 `gap-matrix.md`。
- 一个原始资产可能对应多条 Episode（未来多边界），稳定 code 已把
  `start_time_ns` 纳入，不需要迁移。
- `EPISODE_SEGMENTATION` 的摘要同时报告"追溯检查"与"创建统计"，便于
  区分"输入已关联 Episode"与"本次新建"。

## 6. 残余风险

- MP4 之外的容器一旦出现可靠边界声明（例如上游 manifest 显式给出分段），
  需要新规则版本 + 新格式白名单条目 + 验收样例，本 ADR 的"只承认可证明边界"
  原则不变。
- `EpisodeService.create` 内部提交事务；分段阶段若在创建第二条时失败，第一条
  已落库。重跑按 code 幂等，最终一致，但运行内不是全有全无——已在实现注释
  中写明，暂不引入跨阶段事务。

## 7. 验证

```bash
cd backend
.venv/bin/python -m pytest tests/unit/test_processing_episode_boundaries.py \
  tests/unit/test_processing_segmentation_stage.py \
  tests/unit/test_processing_segmentation_pipeline.py \
  tests/workers/test_processing_worker_segmentation.py
```
