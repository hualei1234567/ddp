# DDP-216 复验证据包

- 任务：DDP-216「设备域整改复验与阶段 1 安全门禁重评」
- 复验对象：DDP-212（DB 兜底）、DDP-213（真实契约快照）、DDP-214（OpenAPI/CI 契约门禁）
- 复验基线：`main` 提交 `660d4bfbd240ebd0056e11036929f512d86eae9d`
  （0004 投影表/触发器、0005 PG JSON 比较修复、OpenAPI 0.2.0、真实响应快照）
- 执行日期：2026-11-08
- 数据等级：T2 合成数据；临时 SQLite；未连接 staging/生产数据库。
- QA 工作树：`/home/user/agent-team/ddp-team/worktrees/ddp-qa`（`chore/ddp-qa`，HEAD `15af240`）
- 说明：QA 工作树业务代码落后于 main；为遵守“只改 `docs/testing/**`、不改业务代码”，
  本次通过脚本参数 `--backend-dir` 使用 main 检出的后端代码，并在 main 检出运行
  契约检查；前端依赖只安装在 `ddp-fe` 工作树，已先逐字节核对关键文件。

## 1. 证据脚本

| 脚本 | 用途 |
|---|---|
| `target_and_immutability_probe.py` | 直接 ORM/SQL 写入：不存在目标、跨项目目标、已校验 checksum/状态直写 |
| `migration_0004_probe.py` | 0004/0005 升级、带数据降级、再升级、部分唯一索引、带数据降级到 0002 |
| `compare_frontend_files.py` | 对比 main 与 ddp-fe 前端契约相关文件是否逐字节一致 |

运行方式（在 QA 工作树根目录）：

```bash
backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-216/target_and_immutability_probe.py \
  --backend-dir /home/user/vscodeworkspace/DataPlatform/backend
backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-216/migration_0004_probe.py \
  --backend-dir /home/user/vscodeworkspace/DataPlatform/backend
backend/.venv/bin/python docs/testing/evidence/2026-11-08-ddp-216/compare_frontend_files.py
```

## 2. 实测输出摘要

### 2.1 target_and_immutability_probe.py

```text
[PASS] 数据库拒绝不存在 target_id: IntegrityError
[PASS] 数据库拒绝跨项目 target_id: IntegrityError
[PASS] 数据库拒绝篡改已校验 checksum: IntegrityError（calibration_record_immutable）
[PASS] 数据库拒绝已校验状态非法回退: IntegrityError（calibration_record_state_transition_invalid）
[PASS] 合法状态迁移未被误伤: VALIDATED -> ACTIVE 允许，checksum 保持不变
PostgreSQL 测试库已配置：否（BLOCKED）
```

### 2.2 migration_0004_probe.py

```text
[PASS] 0004 投影表存在
[PASS] 0004 触发器存在（target guard insert/update + immutable update）
[PASS] 部分唯一索引在升级后保留（uq_calibration_records_active_target + status = 'ACTIVE'）
[PASS] 同目标第二条 ACTIVE 被拒绝（UNIQUE constraint）
[PASS] 同目标 SUPERSEDED 未被误伤
[PASS] 带数据降级 0004 -> 0003（投影表/触发器删除，设备域数据与索引保留）
[PASS] 再升级 head 可重入（投影行 backfill 数量=1）
[PASS（WARN）] 带数据降级 0003 -> 0002（设备域表删除，身份域表保留，生产回滚前必须备份）
```

### 2.3 compare_frontend_files.py

10 个关键文件中 9 个逐字节一致；仅 `apps/web/scripts/device_contract_snapshot.py`
存在一处差异（ddp-fe 为旧版 `resolve()` 判断，main 改为比较父目录，避免 venv
符号链接误判）。该差异不影响快照内容；契约检查以 main 脚本为准。

## 3. 其他实测

```text
# 后端（main 检出）
329 passed, 4 skipped（PostgreSQL 测试未配置 DDP_TEST_POSTGRES_URL）
17 passed（migrations + test_calibration_target_guards.py）
ruff check: All checks passed!
ruff format --check: 109 files already formatted
Secret 扫描通过
Agent 守卫：23 passed

# 契约
后端设备契约与已提交快照一致（contract:check 等价直调）
Redocly lint：valid，4 warnings（info-license-strict、server localhost、2 个公开端点无 4XX）

# 前端（ddp-fe 工作树，契约文件已核对与 main 一致）
20 files / 123 tests passed（含 deviceContract.live.test.mjs 真实后端契约检查）
```

## 4. 环境限制

- 本地未配置 `DDP_TEST_POSTGRES_URL`；未启动 PostgreSQL（Docker 被守卫拒绝）。PG 证据为 BLOCKED。
- `oasdiff` 未安装（`~/go/bin` 不存在），`go` 不在 Agent Bash 白名单；兼容性 diff 为 BLOCKED。
- `alembic` CLI 被守卫禁止；迁移通过 Alembic Python API 在临时 SQLite 执行。
- 未执行 push、部署、生产迁移、数据删除；未读取或使用 staging/生产凭据。
