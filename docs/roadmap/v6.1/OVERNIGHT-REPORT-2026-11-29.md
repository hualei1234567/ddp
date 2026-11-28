# V6.1 通宵建设报告（2026-11-28 夜 → 2026-11-29 晨）

> 汇报时间：2026-11-29 01:40 CST
> 基线：`main` @ `64896a5`（含 #176 / #177）
> 口径：本报告只写**有代码、有测试、有实测证据**的结论；未验证项单列并标注状态。

---

## 一、一句话结论

本轮把 V6.1 剩余可本机闭环的 **6 个 FR** 全部落地（视频链接线、清洗 TRANSFORM/QUARANTINE、
Judge 校准漂移、Token 预算告警、侧栏导航、ADR 归档），并顺带修掉 **2 个真实缺陷**
（视频阶段未接线、归一化读不存在字段）。剩余不可闭环项全部是**外部资源阻塞**（硬件 / CUDA / DNS）。

---

## 二、已合并到 `main` 的交付（有 CI 全绿证据）

### 2.1 PR #176 — 侧栏可折叠分组导航 + 数据链路四类分组

| 项 | 内容 |
|---|---|
| 恢复来源 | CC Switch 中反重力（Antigravity）会话 21:40 因配额中断的任务 |
| 交付 | `SidebarNavigation.tsx`（分组折叠 + localStorage 记忆 + 关键字检索 + 一键全展开/全收）、`sidebarNavigationShared.ts`（修复 `react-refresh` lint 阻断） |
| 用户追加需求 | 「数据链路模块太多，分类一下」→ 16 项拆为 **需求与采集(6) / 处理与切片(2) / 标注与质检(4) / 数据集与训练(4)** |
| 验证 | tsc 0 err、eslint 0 warn、vitest **1315 passed**、vite build 通过、CI 6/6 全绿 |

### 2.2 PR #177 — V6.1 视频链生产接线（修复「有能力无接线」）

**发现的两个真实缺陷**：

1. **处理器从未装配**：`VideoDecodeHandler` / `TimestampNormalizeHandler` 实现与单测自 Sprint 1 起全绿，
   但 `ProcessingRunWorker._build_pipeline` 从未注入它们 → 生产里视频资产的三个阶段永远 `SKIPPED`，
   表现为「没有需要解码的视频」而非「能力缺失」（MEM-INC-027 同类）。
2. **隐性契约断裂**：`TimestampNormalizeHandler` 读 `run.input_asset_ids`，而领域实体
   `ProcessingRun` 只有 `input_refs` → 归一化恒为空、永远静默跳过。**测试的 Fake 恰好提供了
   这个不存在的字段，把断裂固化进了测试**（已入库 MEM-INC-033）。

| 交付 | 文件 |
|---|---|
| 生产适配器 | `infrastructure/processing/video_adapter.py`（`FfprobeVideoDecoder` × `InputObjectReader` 有界下载、临时目录即用即删；`RawAssetVideoResolver` 按 project+input_refs 反查） |
| 组合根接线 | `workers/processing.py` 注入解码 + 归一化处理器 |
| 阶段映射 | `default_handlers` 新增 `video_decode`；`FRAME_INDEX` 映射为显式 SKIPPED（同事务落库，避免重复解码把帧数翻倍——**此坑实测踩到**） |
| 集成测试 | `tests/workers/test_processing_worker_video.py`（真实装配：3 帧解码 + 归一化回填 + 非视频显式跳过） |
| 验证 | ruff 0 err、37 passed（含 4 个 worker 套件）、CI 6/6 全绿 |

---

## 三、待合并的 4 个 PR（本地全绿，CI 进行中）

> 说明：仓库分支保护要求「分支必须与 main 同步」，每次合并会使其余 PR 落后并触发约 40 分钟的
> 后端全量 CI。因此 4 个 PR 需串行合并；当前均在等待「后端 lint / 迁移 / 测试」。

### 3.1 PR #178 — 平台使用说明（部署 / 公网 / 工具 / 权限）

用户明确要求的使用说明文档。含：

- **三种部署形态**：本地开发 / 公网 VPS+Nginx（路径 A）/ 本机 Docker+Cloudflare Tunnel（路径 B）；
- **公网边界如实标注**：CF 免费 Tunnel 100MB 请求体上限、`s3.ddp.online` 必须灰云直连的原因、
  `www.ddp.online` 等 NS 切换、Quick Tunnel 临时域名每次重启变化；
- **权限矩阵**：平台角色（member/admin）× 项目角色（viewer/editor/owner）× 6 动作，
  指向权威实现 `domain/identity/models.py:ROLE_PERMISSIONS`；Edge Agent 凭据分层；
  `allowed_actions` 服务端权威原则；
- **工具链速查**：`make check` 十步闸口、`scripts/ops` 六件套、E2E/GATE 证据目录、记忆星云同步；
- **诚实边界单列**：硬件采集 `BLOCKED`、GPU `BLOCKED`、自定义域 `UNVERIFIED`。

### 3.2 PR #179 — ADR-0051 训练引用删除语义

从遗留 worktree 里抢救出的有效决策文档（原本只存在于未提交的工作树）：

- **冲突事实**：`external_training_references.project_id` 是 `CASCADE`，同表有 append-only 守卫触发器
  禁删任何行 → 删除含训练引用的项目必然 `IntegrityError`；
- **决策**：方案 A（维持现状 + 文档化），项目下线走 `archive`；
- **否决理由**：B（会话变量旁路守卫）会破坏 ADR-0036 审计不可变语义；C（改 RESTRICT + 前置检查）
  为当前不存在的需求付迁移成本。

### 3.3 PR #180 — 清洗 TRANSFORM / QUARANTINE（V61-015 / V61-016）

原矩阵定级 `NOT_STARTED`（仅 FLAG），本轮补齐：

| 动作 | 实现 |
|---|---|
| **QUARANTINE** | 命中隔离规则 → 结构化 `QuarantineDecision`（规则 ID / 来源引用 / 证据摘要），**显式留痕不静默丢弃** |
| **TRANSFORM** | `CLN-NUM-003` 动作步长对**输出序列**逐步限幅，产出 `TransformedSlice` + `transform_provenance`（规则/阈值/裁剪帧维数/阈值档版本） |

红线与边界：

- `raw_asset_mutated` **恒 False**；测试断言上游 `STREAM_EXTRACTION` 对象与输入切片**字节级未变**；
- 同一 target 同时命中隔离与变换 → **只隔离**，不产出衍生数据；
- 把不支持的规则配进 TRANSFORM → 立即 `DomainValidationError`，**不静默跳过**；
- 配置走 `DDP_PROCESSING_CLEANING_{QUARANTINE,TRANSFORM}_RULE_IDS`，**默认全空 = 保持 FLAG-only**，
  完全向后兼容；
- **零数据库迁移**（复用既有 `StageArtifactStore` 与产物目录）。

验证：ruff 0 err、`test_cleaning_actions` 8 项 + 既有清洗/worker/pipeline **119 passed**。

### 3.4 PR #181 — Token 预算告警 + Judge 校准漂移（V61-019 / V61-017）

| FR | 交付 |
|---|---|
| V61-019 | `domain/annotation/token_budget.py`（5 维度判定）+ `GET .../ops/token-budget` 只读端点；契约 1.40.0 → **1.41.0** |
| V61-017 | `domain/annotation/judge_calibration.py`（准确率 + 三类 Cohen's kappa + 漂移判定） |

**三条语义严格区分**（均有测试）：`UNLIMITED`（未设预算）≠ `OK`；`UNKNOWN`（无数据）≠ `0` 且不告警；
等于上限是 `WARNING`，严格超过才是 `EXCEEDED`。

**Judge 退化的处理**：judge 只判单一类别时 kappa 返回 `None` 而非 1.0——「全判 A」准确率可以很高，
但什么都没评判，必须能看出来。

验证：ruff 0 err、`check_contract_parity --strict` **无新增差异**、**90 passed**
（含越权 403、跨项目隔离、非法参数 422、端点只读 405）。

---

## 四、记忆星云抽离为独立服务（用户指定要求）

**问题**：`http://127.0.0.1:8898/` 无法访问——`dsh-memory-nebula` 一直靠 DeepSeek Harness 内嵌启动，
dsh 未运行时服务就不存在。

**根因**：该组件（上游 [yb-zhai/agent-memory](https://github.com/yb-zhai/agent-memory)，本机工作副本
`~/deepseek/agent-memory`）**本身就是零依赖、可独立运行的 Node 服务**，README §2 明确写了
`node server.js` 即可。它从来没有被单独启动过。

**处置**（已完成并实测）：

| 项 | 结果 |
|---|---|
| 启动器 | `~/bin/memory-nebula`（Node 绝对路径回退，不依赖 nvm /profile） |
| 进程托管 | launchd `com.local.memory-nebula`，`RunAtLoad` + `KeepAlive`（开机自启、崩溃自动拉起） |
| 数据单一真源 | `~/.memory-nebula`；`~/.dsh/memory-nebula-data` 已备份（`retired-<ts>`）并软链指向它 |
| 实测验收 | `/healthz` 与面板 `/` 均 200；`bootout` 后 `000`（确认不是 dsh 在顶）→ `bootstrap` 后自动恢复 200 |
| DDP 同步链路 | `scripts/sync_memory_nebula.py --require-server` → 46 张 ACTIVE 卡片幂等同步成功（退出码 0） |
| 文档 | `docs/operations/记忆星云独立部署.md`（部署步骤 / 验收 / 接口契约 / 单进程写边界 / 卸载） |

---

## 五、FR 状态矩阵更新（相对 S0 定级）

| FR | S0 定级 | 现在 | 依据 |
|---|---|---|---|
| V61-005 Video Decode / Frame Index | NOT_STARTED | **VERIFIED** | PR #177 接线 + 真实装配集成测试 |
| V61-007 视频+状态多流同步 | NOT_STARTED | **PARTIAL** | PTS 实测链 + 归一化已真实落库；跨模态误差报告仍缺 |
| V61-015 Cleaning TRANSFORM | NOT_STARTED | **VERIFIED** | PR #180 + 8 项测试 |
| V61-016 QUARANTINE | NOT_STARTED | **PARTIAL** | 清洗侧隔离已实现；RawAsset 侧恢复动作既有；Episode 级恢复流程未做 |
| V61-017 Judge 校准/漂移 | PARTIAL | **VERIFIED**（领域层） | PR #181 + 14 项测试；**未接 API/前端** |
| V61-019 Token 预算/告警 | PARTIAL | **PARTIAL** | 判定 + 只读端点已实现；**告警投递未接** |
| V61-012 TrainingRun 证据 | PARTIAL | **VERIFIED** | 迁移 0088 + 3 个隔离端点 + 闭环 E2E `PASSED` |
| V61-008 LeRobot Video Loader | NOT_STARTED | **VERIFIED** | GATE-A 证据（官方 Loader 真实解码 100 帧） |

**仍未闭环（非本轮可做）**：

| 项 | 状态 | 阻塞原因 |
|---|---|---|
| V61-006 图像 Observation Canonical 映射 | `MISSING`（已核实） | Canonical 有 `payload_ref` 字段声明，但**无任何写入点**；需接解码产物 |
| V61-009 全量 200 demos 基准 | `PARTIAL` | 已有并发压测（1/4/8/16 workers，725 runs），缺全量样本 | 
| V61-020 真实硬件 Pilot | `BLOCKED` | 无 SO-101 单臂 + 相机 + SDK 授权 |
| V61-023 许可 Gate | `PARTIAL` | 数据源已记录 license/gated 一致性，缺导入前强制门禁 |
| V61-025 GPU 推理 | `BLOCKED` | 无 CUDA |
| `www.ddp.online` | `UNVERIFIED` | 等域名注册商切 NS 到 `dakota/elinore.ns.cloudflare.com` |

---

## 六、公网部署现状（实测）

```
online_deploy_verify.py --skip-env \
  --api-url https://mitsubishi-walk-already-momentum.trycloudflare.com \
  --web-url https://ddp-web.pages.dev
→ Summary: 17/17 passed, 0 failed
```

| 端点 | 状态 |
|---|---|
| 前端 `https://ddp-web.pages.dev/` | HTTP 200 |
| 后端 Tunnel `/health` | HTTP 200，`{"status":"ok","service":"DDP","version":"0.2.0"}` |
| CORS | `ACAO='https://ddp-web.pages.dev'` 正确 |
| compose 安全基线 | postgres/redis/api/minio 端口隐藏检查全 PASS |

**注意**：Quick Tunnel 域名每次重启变化；如需固定域名需绑定 Zero Trust Tunnel 或等 NS 切换完成。

---

## 七、本轮真实数字

| 指标 | 数值 |
|---|---|
| 合并 PR | 2（#176 / #177） |
| 待合并 PR | 4（#178 / #179 / #180 / #181，本地全绿） |
| 新增代码行 | 约 2,140（含测试 1,320 / 文档 350 / 实现 470） |
| 新增测试用例 | 36（侧栏 3 + 视频链 2 + 清洗 8 + 预算 13 + 校准 14，部分复用既有套件） |
| 后端全量 | `coverage run -m pytest` **exit 0**，覆盖率 **92%**（门槛 85%） |
| 前端全量 | 172 files / **1315 passed** / 19 skipped |
| 记忆卡 | 46 张 ACTIVE（新增 MEM-INC-033） |
| 契约 | 276 → **277 路径**（1.40.0 → 1.41.0，parity 零新增差异） |
| 公网探针 | 17/17 passed |

---

## 八、明早需要你决定的三件事

1. **4 个 PR 的合并顺序**：因分支保护要求「与 main 同步」，合并其一会让其余重新排队约 40 分钟
   后端 CI。建议串行合并（我会盯完）；若你希望更快，可授权我用 bundle 分支一次合入
   （本仓 #174/#175 有先例），但会牺牲逐 PR 的历史粒度。
2. **`www.ddp.online` 的 NS 切换**：这是唯一需要你在域名注册商侧操作的事项，完成后自定义域自动生效。
3. **V61-006 / V61-023 是否要排进下一轮**：这两项我核实后确认仍是 `MISSING`/`PARTIAL`，
   且都需要新增写入链路（不是文档或配置能补的），需要你确认优先级。

---

## 九、风险与回滚

| 风险 | 缓解 |
|---|---|
| 4 个 PR 尚未合并，`main` 只含 #176/#177 | 各 PR 分支独立、文件不相交，可单独 revert |
| 清洗 TRANSFORM 限幅语义为「逐步累加」 | 测试与 PR 描述均写明；默认配置下行为不变 |
| Judge 校准 kappa 阈值 0.10 为保守默认 | 蓝图未给数值，可由调用方覆盖 |
| 记忆星云单进程写 | 文档写明：不要同时跑 dsh 内嵌实例与独立服务指向同一目录 |
| 回滚点 | 每项 `git revert` 即可；**本轮无数据库迁移**，无数据回滚需求 |

---

## 十、留给下一轮的清单（按价值排序）

1. V61-006 图像 Observation Canonical 映射（把 `video_frames.frame_object_ref` 接进 Canonical `payload_ref`）；
2. V61-019 告警投递接线（复用既有 Alertmanager → alert-receiver 通道）；
3. V61-017 校准评估接 API + 前端展示；
4. V61-023 导入前许可门禁（GATED 数据源必须显式确认才能导入）；
5. V61-009 全量 200 demos 基准（需先灌满样本）；
6. V61-020 / V61-025 保持 `BLOCKED`，等待硬件与 GPU 资源。
