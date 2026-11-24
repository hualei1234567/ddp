### MEM-INC-009 | 多个 check.sh 并发跑同一 PostgreSQL 会互相污染

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `测试/并发执行` | 批次/资产 `本机多 worktree 场景` | 密级 `INTERNAL`
- **核心结论**：多个 worktree 同时跑 `bash scripts/check.sh` 会**争用同一个本地 PostgreSQL（55432）与 MinIO（9000）**，导致随机测试失败：迁移测试、时间敏感测试、外键依赖测试都可能因别人的数据/锁而失败。**并发验证必须串行化，或使用独立的库/桶。**
- **关键依据与细节**：
  - 实测现象（本轮多次复现）：
    - `test_review_evidence_column_migration` 失败 → 单独重跑 3 次全过
    - 前端 jsdom 测试偶发 5000ms 超时（机器被后端 pytest 占满）
    - `test_dataset_grant_api.py` 2 例失败（模块级 `NOW = datetime.now()`，套件连跑 2 小时后 `NOW + 1h` 变成过去时间）
  - 根因分类：
    1. **共享数据库**：多个 check.sh 同时写同一库 → 数据互相干扰
    2. **CPU 争用**：并行跑让单测耗时暴涨 → 触发超时（见 MEM-INC-005 的同类问题）
    3. **时间敏感**：`datetime.now()` 在模块导入时求值，长跑后过期
  - 判定方法：失败用例**单独重跑通过** + **失败集合每次不同** → 大概率是争用，不是真 bug
  - **不要**因为并发争用就放宽断言（那会掩盖真问题）
- **影响与后续动作**：
  - 多 worktree 并行开发时，**串行跑 check.sh**（一次一个）
  - 必须并行时：给每个 worktree 独立的 `DDP_DATABASE_URL`（不同库名）+ MinIO bucket
  - CI 天然隔离（每次全新容器），不受此影响
  - 责任人：全体；**已记录，执行策略为串行**
  - 附带发现：`test_dataset_grant_api.py` 的模块级 `NOW` 是真实缺陷（时间敏感），
    应改为 fixture 内 `datetime.now()`；不在本轮范围，已记录待修

---

#### 工程扩展字段

- **check_ref**：无（流程性约束，无法自动化断言；靠并发执行策略规避）
- **recurrence**：`3`
- **tags**：`test-contention,postgres,minio,parallel,flaky,time-sensitive,worktree`
