# V61_Current_Baseline — 当前系统真实建设状态（S0 实测）

> 采集时间：2026-11-28 00:56–01:26 · 全部只读实测 · 证据等级 E2/E3

## 1. Git / 仓库

| 项 | 实测值 |
|---|---|
| main HEAD | `c5f78be`（Merge PR #171，阶段 6 收官） |
| 工作区 | 干净（仅 2 个未跟踪辅助文件：`.claude/SESSION_STATE.md`、`docs/ops/ddp-archify-blueprint.html`） |
| 开放业务 PR | **0**（仅 dependabot 15 个） |
| 最近 main CI | **success**（run 46026948991，45m36s；此前 #170 的 failure 已由本轮 flaky 修复解决） |

## 2. 数据库 / 迁移

| 项 | 实测值 |
|---|---|
| 迁移文件数 | 76 |
| `alembic heads` | **单 head `0086`**（history 76 步与文件数、revision 声明三者一致，无分叉孤儿） |
| 生产库（55432/ddp）当前版本 | **0086**（与仓库一致） |
| 0086 | `webhook_outbox`（阶段 6 对外投递队列） |

## 3. API 契约

| 项 | 实测值 |
|---|---|
| OpenAPI version | **1.40.0** |
| parity | strict 通过，基线内已接受 68 项差异 |
| fragments | paths 按域拆分（ops/annotation/dataset/modelops/webhook…） |

## 4. 代码规模与测试

| 项 | 实测值 |
|---|---|
| backend `app/` | 722 个 .py |
| 前端 src | 556 个 ts/tsx |
| backend 测试用例 | **3,478**（grep `def test_` 实测） |
| web 测试用例 | **1,250** |
| Playwright E2E | 13 条（11 passed + 2 skipped 白名单） |
| scripts/tests | 151 项 |
| 证据目录 | **38** 个（docs/testing/evidence，2026-11-08 → 11-27） |
| 记忆卡片 | **41 张**（check_ref 全有效，星云同步） |
| ADR | 0050 篇 |

## 5. 运行栈（本机生产）

- docker compose：PG 16（55432）、MinIO（9000/9001）、Redis（限流）、api、9 类 worker、web（8088）、local-adapter（9100，ffmpeg）
- monitoring profile：Prometheus（9190）/ Alertmanager（9193）/ alert-receiver（9194）——阶段 6 已真实拉通
- OTel：代码已接线，默认关闭零开销

## 6. 六个基础事实（S0 据此定级）

1. V5.1 数据链（HDF5→MinIO→STREAM_EXTRACTION→Canonical→LeRobot Frame Loader）**代码与测试齐全**，但验收样本仅 1 个 36.7MB RoboMimic `ph`（**不含图像**）。
2. V5.2 标注质量域 10 个子域**全部有代码+测试**（Judge 18、黄金题 27、PRM 42、SFT/DPO 17、Token 34、Badcase 17、ModelOps 37 等）。
3. Badcase→DataRequest 归因链 **VERIFIED**：`feedback.py:326` 等 3 处生产写入点真实填充 `source_feedback_ids`/`source_evaluation_ids`；`retest_evaluation_ids` 有 link-retest API + close 门禁 + 测试闭环。
4. **TrainingRun 证据 PARTIAL**：无 `training_runs` 主表（ADR-0036 有意设计：引用行即 run 记录），但 seed / checkpoint hash / 逐步 metrics 无落库字段。
5. **视频链路未打通**：mp4.py 仅 ffprobe metadata（无 PTS）；frame_writer `video_provided: False`；Canonical 无像素字段；全仓 `pts` 0 命中。
6. **Golden Sample 已在本机**：`~/.cache/huggingface/hub/datasets--lerobot--pusht/` 含 6.6MB 真实 MP4（AV1 96×96@10fps，25,650 帧）+ 659KB 配套 Parquet（state/action）。

## 7. 结论

平台处于「V5.2 全量交付 + 审计剩余 5 项闭环」的健康基线，无未收口 CI、无双 head、无契约漂移。V6.1 的三大 P0（视频链、训练消费、规模化）在代码层面均有可复用底座，缺口集中在**像素级视频处理、PTS 实测、TrainingRun 证据表、真实样本链路验证**四点。
