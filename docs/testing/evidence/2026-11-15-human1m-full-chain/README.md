# NVIDIA PhysicalAI-Human-1M-100K：10 条完整 episode 全链路验收

- 日期：2026-11-15
- 结论：**PASS（VERIFIED）**，真实 Hugging Face 下载 / 真实 PostgreSQL / 真实 MinIO，无 mock
- 脚本：`scripts/e2e/human1m-full-chain.py`
- 运行日志：本目录 `run.log`（主记录）、`repro-run.log`（第二次独立复现）。
  两者均为本机日志，被 `.gitignore` 的 `*.log` 规则排除，不入库；下方结论均从日志逐行摘录。

## 1. 验收口径

**10 条完整 episode**，即：

- episode 条数 = 10（不是 15、不是 39 个视频文件）；
- 每条 episode 的**全部机位**都落库，不允许"只导了一半机位"的残缺 episode；
- 抽样确定性：取官方 `preview/metadata.jsonl` 中**首次出现顺序的前 10 条**，
  不做随机抽样、不做重排，因此任何一次执行选中的都是同一批数据（可复现）。

因此本批口径的两个硬数字是：**10 条 episode / 30 个媒体资产 / 31,216,910 字节**。

数据集 preview 全量为 15 条 episode / 39 个视频（46,335,568 字节）。
本次**只取前 10 条**，未拉取后 5 条（`demo_013/016/017/018/019`），
符合"不要拉取全部数据，只拉取 10 条完整的即可"的要求。

## 2. 环境

| 项 | 值 |
|---|---|
| 数据集 | `nvidia/PhysicalAI-Human-1M-100K`（Hugging Face，public preview） |
| revision（commit sha） | `7e312e654818d1e0d3ebec3b78e50ee833b429f8`（40 位，已由脚本断言） |
| 许可证 / 访问条件 | CC-BY-4.0 / PUBLIC / 非 gated |
| 数据库 | Docker `ddp-postgres`（`postgres:16-alpine`），`PostgreSQL 16.16`，`localhost:55432/ddp` |
| 迁移版本 | `alembic_version = 0032` |
| 对象存储 | Docker `ddp-minio`（`bitnamilegacy/minio:2025.7.23-debian-12-r5`），`127.0.0.1:9000`，桶 `ddp-raw` |
| Python / 依赖 | 3.11.17；`httpx 0.28.1`、`boto3 1.35.90`（`backend/.venv`） |
| 脚本 | `scripts/e2e/human1m-full-chain.py` |

## 3. 抽样方式与理由

metadata.jsonl 共 39 行（一行一视频），按 `example_id` 聚合成 15 条 episode。
脚本对"前 10 条"用了**两条互相独立的判据**，任一侧不一致即失败：

1. **人工期望值**（脚本内 `EXPECTED_CAMERAS` 常量，10 条 episode 的机位集合写死）；
2. **官方派生的期望值**：脚本直接下载官方 `preview/metadata.jsonl`，
   用 `ddp_data.lerobot.parse_preview_metadata`（与生产链路同一解析器）
   推导前 10 条的机位集合。

两条判据一致才继续，避免"期望值本身写错导致假通过"。
输出行：`→ 双判据一致：10 条 episode / 30 个资产`。

为什么取前 10 条而不是随机 10 条：验收要能复现。随机抽样会让"上次 10 条通过、
这次 10 条失败"无法归因；按元数据首次出现顺序取前 N 条是纯粹的、可重放的规则。

被选中的 10 条 episode 与机位覆盖：

| # | episode | 任务 | 资产数 | 机位 | 字节 |
|---|---|---|---|---|---|
| 1 | demo_001 | Sew lace fabric | 3 | main, right, stereo | 2,342,116 |
| 2 | demo_002 | Repair a motorcycle brake | 3 | main, right, stereo | 4,296,430 |
| 3 | demo_003 | Fill dumpling wrappers | 3 | main, right, stereo | 2,244,772 |
| 4 | demo_004 | Polish a dental crown | 3 | main, right, stereo | 2,472,489 |
| 5 | demo_005 | Assemble a toy gear workshop | 5 | main, right, stereo, wrist_left, wrist_right | 4,640,995 |
| 6 | demo_007 | Organize a desktop charging station | 5 | main, right, stereo, wrist_left, wrist_right | 5,462,991 |
| 7 | demo_008 | Test components with a multimeter | 5 | main, right, stereo, wrist_left, wrist_right | 3,862,461 |
| 8 | demo_009 | Pack and seal shipping cartons | 1 | main | 1,138,092 |
| 9 | demo_011 | Chop cabbage | 1 | main | 972,616 |
| 10 | demo_012 | Harvest tomatoes | 1 | main | 3,783,948 |
| | **合计** | | **30** | | **31,216,910** |

机位覆盖度：`main` 10 条（100%）、`right` 7、`stereo` 7、
`wrist_left` 3、`wrist_right` 3。**注意**：本批 episode 的机位数并不均匀
（3/5/1 机位混杂），这是数据集本身的结构，不是导入缺失——脚本逐条对账，
任一条缺机位即失败。

## 4. 结果

### 4.1 全链路步骤

| # | 步骤 | 结果 |
|---|---|---|
| 1 | 创建项目 + 提交人（editor）/ 审核人（owner） | `project=c8a55df7-f8ad-49b4-a394-088bfc3facc2` |
| 2 | 登记数据源（revision 40 位 sha 断言） | `source=471a6096-b8de-416a-8086-6d62d5b0440c`，`code=hf-human1m10-c8a55d` |
| 3 | 创建导入任务，`scope=SAMPLE, sample_limit=10` | `import=2c7d78c8-e982-4301-b200-2d1a384a6711`，初始状态 `PENDING` |
| 4 | 取官方基准（metadata + SHA256SUMS，独立于被测链路） | metadata 20,972 字节 / 15 条 episode；SHA256SUMS 136 条 |
| 5 | 真实下载 → 内容签名校验 → sha256 → 存 MinIO → 建媒体资产 | `episodes=10 assets=30 bytes=31,216,910` |
| 6 | 完整性校验（每条 episode 全机位） | 10 条全部齐备 |
| 7 | 独立交叉校验（官方 SHA256SUMS + MinIO 回读） | 30/30 一致；回读 31,216,910 字节一致 |
| 8 | 质量报告 | 规则 `1.0.0`，范围 `SAMPLE`，6/6 PASS |
| 9 | 人工审核（先证自审被拒，再由独立审核人裁决） | `DomainValidationError`（自审被拒）→ `APPROVED / ACCEPT` |
| 10 | 预签名播放 URL + 真实 `curl -r 0-1023` | `HTTP/1.1 206 Partial Content` |
| 11 | 真实 HTTP 路由复核 | 4 个端点全部 200，字段与用例层一致 |

### 4.2 质量规则明细（规则版本 1.0.0，范围 SAMPLE）

| 规则 | 结果 | 明细（日志原文） |
|---|---|---|
| `assets_present` | PASS | 共 30 条媒体资产 |
| `asset_size_positive` | PASS | 全部资产大小为正 |
| `asset_sha256_valid` | PASS | 全部资产校验和格式合法 |
| `episodes_have_assets` | PASS | 共 10 个片段，均有媒体资产 |
| `main_camera_present` | PASS | 机位集合：`['main', 'right', 'stereo', 'wrist_left', 'wrist_right']` |
| `timing_fields_valid` | PASS | 片段起点/时长/帧率均合法 |

总体 `passed=True`。报告同时断言 `sample_scope=SAMPLE`、`total_episodes=10`、
`total_assets=30`，即报告口径与验收口径对齐，而不是"任意导入都能通过"。

### 4.3 审核结论

| 项 | 值 |
|---|---|
| 提交人 | `afa13600-3b55-4c5f-86e0-6185a387a5af`（editor） |
| 审核人 | `5af1d560-c590-4419-bd3c-3c21b54b1540`（owner，**≠ 提交人**） |
| 自审尝试 | 被拒：`DomainValidationError: 审核人不得是导入提交人（职责分离）` |
| 最终状态 | `APPROVED` / 裁决 `ACCEPT` / `version=2` |
| 裁决理由 | 前 10 条 episode 全机位齐备、内容签名与 sha256 校验通过、质量规则全部 PASS |

职责分离是**先证明被拒绝**再裁决，不是只断言最终人是两个 ID。

### 4.4 206 验证输出（真实 curl）

主视角资产 `videos/demo_001_0b4a67b0_main.mp4`（754,668 字节），预签名 URL 含 `X-Amz-Signature`：

```
HTTP/1.1 206 Partial Content
Content-Length: 1024
Content-Range: bytes 0-1023/754668
```

脚本额外断言 `Content-Range` 的**总长度与库内 `size_bytes` 相等**，
因此不是"只要 206 就算过"——206 必须对应到正确对象。

### 4.5 HTTP 路由复核（前端页面实际使用的端点）

| 端点 | 状态 | 关键字段 |
|---|---|---|
| `GET .../data-source-imports/{id}/quality-report` | 200 | `rule_version=1.0.0`、`passed=True`、`episodes=10`、`assets=30` |
| `GET .../data-source-imports/{id}/review` | 200 | `status=APPROVED`、`decision=ACCEPT`、`version=2` |
| `GET .../data-source-imports/{id}/assets?limit=1000` | 200 | `count=30` |
| `POST .../media-assets/{asset_id}/playback` | 200 | `ttl=900s`、`size=754,668` |

脚本逐项把 HTTP 返回与用例层结果对齐（规则版本、样本范围、审核人、资产数、
资产字节数），不一致即失败。

### 4.6 落库与审计旁证

| 检查 | 结果 |
|---|---|
| `data_source_imports` | `status=SUCCEEDED`、`imported_row_count=10`、`imported_file_count=30` |
| `data_source_media_assets`（本导入） | 30 行，`min=636,281` / `max=3,783,948` / 合计 31,216,910 |
| `data_source_reviews` | 1 行，`submitted_by ≠ reviewer_id`，`version=2` |
| `audit_logs` | `create(PENDING)` → `start(RUNNING)` → `succeed(SUCCEEDED)` |
| `outbox_events` | `import_requested` / `import.start` / `import.succeed` / `review.created` / `review.decided` |
| MinIO 前缀 `data-sources/<project>/<source>/<revision>/` | 30 个对象，合计 31,216,910 字节（boto3 独立列举） |

对象键形如
`data-sources/c8a55df7-.../471a6096-.../7e312e65.../videos/demo_001_0b4a67b0_main.mp4`，
revision 在键里，换版本不会覆盖旧对象。

## 5. 脚本改了什么、为什么

原脚本（444 行）能跑通主链路，但存在三个"声称与实现不符"或"断言过弱"的问题：

1. **docstring 声称用官方 SHA256SUMS 交叉校验，代码里没有这一步。**
   这是最严重的问题：证据会声称"独立校验通过"，实际只信了第一遍下载时自己算的 hash。
   → 新增步骤 3 与步骤 6：真实下载 `SHA256SUMS.txt`，逐资产比对官方值。
2. **"完整 episode"只有单一判据**（脚本内写死的期望机位表）。
   期望值写错会假通过。→ 增加官方 metadata 派生期望值，与人工期望值互为对照。
3. **只校验了 206 状态码，没校验 `Content-Range` 的实际总长。**
   → 断言 `bytes 0-1023/<库内 size_bytes>`。
4. 追加 **MinIO 对象回读**（重新算 sha256）：证明"库里的 hash"与"存储里的字节"一致，
   而不是只信下载时的内存结果。
5. 追加对 `imported_row_count` / `imported_file_count` / 总字节 / HTTP 字段的一致性断言。
6. 显式 `DDP_RATE_LIMIT_ENABLED=false`：TestClient 段落在同一 IP 高频调用，
   限流会误伤，而这个脚本验证的不是限流。默认关闭优于"恰好没触发"。

未改动的部分：主链路调用顺序、被测业务代码一行未改。

脚本内可单测的纯逻辑（清单解析、双判据自洽、URL 固定 revision）已锁进
`scripts/tests/test_human1m_full_chain.py`（8 个用例），因为脚本本身要连
HF / PostgreSQL / MinIO，不能进 CI 常规回归。

## 6. 数据链路侧的既有修复（本批依赖）

`backend/app/application/datasources/import_runner.py` 的 `select_episodes`
是本批"10 条"能成立的前提：此前 `sample_limit` 只写入任务行与事件负载，
**执行时从未被读取**，`sample_limit=10` 实际仍拉全量 15 条。
该修复按域模型既有语义实现（`SAMPLE` 取前 N 条、`SPLIT`/`FULL` 不截断、
抽样单位是 episode 而非视频文件），测试见
`backend/tests/unit/test_import_sampling.py`（8 个用例，全部通过）。

本次实跑的 `episodes=10 assets=30`（而非历史记录的 `15 / 39`）正是该修复生效的证据。

## 7. 复现命令

```bash
# 1) 基础设施
docker compose up -d postgres minio

# 2) 迁移
cd backend && .venv/bin/alembic upgrade head && cd ..
#   期望：alembic_version = 0032

# 3) 跑全链路（约 2 分钟，主要耗时在 30 个视频的真实下载与回读）
backend/.venv/bin/python scripts/e2e/human1m-full-chain.py

# 4) 纯逻辑回归（无需外部系统）
backend/.venv/bin/python -m pytest scripts/tests/test_human1m_full_chain.py -q
backend/.venv/bin/python -m pytest backend/tests/unit/test_import_sampling.py -q
```

每次运行都会**新建**一个项目与一个数据源（code 带 `project_id` 前 6 位，
如 `hf-human1m10-c8a55d`），因此不会与既有数据冲突，也不会删改历史数据。
连跑多次（`run.log` 与 `repro-run.log` 为其中两次）均得到完全相同的
`10 / 30 / 31,216,910` 与同一组 episode 顺序，确认抽样确定性。
`repro-run.log` 是脚本经 `ruff format` 折行调整后的复跑结果，用于证明
格式化未改变行为。

## 8. 与同目录 `bucket-api.json` 的关系

`bucket-api.json` 是**数据集规模探查**的一次快照，与本批验收**不是同一件事**，
也不参与本批任何断言：

- 内容：HF bucket API 对 `nvidia/human-1m-100k-oss` 的响应
  （`totalFiles=2,563,889`、`size=1,321,059,593,743,168` ≈ 1.2 PB、`repoType=bucket`），
  末尾 `200` 是 HTTP 状态码；
- 用途：说明**全量数据集有多大**，从而论证"只取 10 条预览"是必要且已足够的选择，
  而非过度拉取；
- 注意：那是 `human-1m-100k-oss`（bucket 全量仓库），本批实际导入的是
  `PhysicalAI-Human-1M-100K` 的 `preview/` 子集，两者不是同一路径。
  本批未对全量仓库做任何下载。

保留该文件作为"为什么只取 10 条"的量化依据；它不构成本批 PASS 的证据链。

## 9. 未覆盖项与风险

| 项 | 状态 | 说明 |
|---|---|---|
| 全量数据集（1.2 PB / 2.56M 文件） | `NOT RUN` | 明确不在本批范围；仅导入 preview 前 10 条 episode |
| preview 中后 5 条 episode（`demo_013/016/017/018/019`） | `NOT RUN` | 按用户指示只取 10 条 |
| 视频内容语义正确性 | `NOT VERIFIED` | 只验证字节级（大小/签名/sha256/机位/时长字段），未解码视频帧或人工观看内容 |
| 浏览器端 E2E | `NOT RUN` | 未安装 Playwright；前端由组件测试覆盖，本批用 HTTP 路由复核替代 |
| staging / 生产部署 | `BLOCKED` | 本批只针对本机 Docker；部署与发布需单独批准 |
| Redis 限流集成、PostgreSQL 触发器迁移回归（默认跳过项） | 已单独执行 PostgreSQL 部分 | `DDP_TEST_POSTGRES_URL` 指向本机库后 `tests/migrations` 36 passed；Redis 集成测试未执行（`DDP_TEST_REDIS_URL` 未配置） |
| `backend/app/core/config.py`、`backend/tests/api/test_rate_limit_middleware.py` 的 `ruff format --check` 不通过 | **已修复（2026-11-15 17:2x）** | 该问题源自提交 `6c191e8`（分布式限流）当时未跑 `ruff format`，与本批数据链路无关。现已对这两个文件单独执行格式化，`ruff format --check` 通过；未对全仓批量 `--fix` |

### 风险提示

1. **preview 不是正式训练集**：这批数据可用于验证链路与审看流程，
   但不能代表 NVIDIA Human-1M 全量质量，不要据此给出数据集质量结论。
2. **机位数不均**（3/5/1）：下游若假设"每条 episode 机位一致"会出错，
   消费方应始终按 episode 实际机位集合处理。
3. **外网依赖**：脚本依赖 `huggingface.co` 可达。断网或 revision 被移除时脚本失败，
   这是预期行为（不降级为本地缓存冒充）。
4. **库里并存多个数据源**：本批收尾时库内共 **12 个 data_source / 384 个资产 /
   434,097,958 字节**，构成为：6 个历史 `hf-human1m-*` 源（各 15 条 / 39 资产，
   46,335,568 字节）、6 个 `hf-human1m10-*` 源（各 10 条 / 30 资产，31,216,910 字节）。
   本批共完整执行 3 次（`run.log` 与两次复跑），每次新建 1 个源、每次新增同一批
   30 个对象，因此对象存储里有内容相同的多份副本（对象键含 project_id，互不覆盖）。
   **未删除任何历史数据**，清理残留需单独授权。
