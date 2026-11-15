# ADR-0032 标定配置业务版本字段更名 `version` → `profile_version`

- 状态：**ACCEPTED**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0020（版本语义与快照）、ADR-0026（Profile 路由）、差距项 G-03
- 影响范围：`calibration_profiles` 表、设备域 Domain / ORM / 仓储 / 用例、OpenAPI 契约描述
- 编号说明：本 ADR 起草时拟为 0031，与同日另一会话的
  `0031-distributed-rate-limit-via-redis.md` 撞号，故改为 0032。
  **迁移编号 0032 与 ADR 编号 0032 是巧合**，两者各自独立编号

## 1. 问题

`CalibrationProfile.version` 与 `CalibrationRecord.version` 同名不同义：

| 实体 | 字段 | 语义 |
|---|---|---|
| `CalibrationProfile.version` | 业务版本号 | "这是同一配置的第几版"，从 1 起，允许跳号（由 `supersedes_id` 连续性校验） |
| `CalibrationRecord.version` | 乐观锁 | "这行被写过多少次"，每次写入 +1 |

两者在代码里挨着出现，最典型的是 `application/devices/calibrations.py`：
记录快照取 `profile.version`，而同文件的乐观锁比对用 `record.version`。
读代码的人必须停下来确认"这个 version 是哪个 version"。ADR-0020 已经承认
这个隐患（§3 明确写了"G-03 `profile_version` 更名"暂缓），当时没做的理由是
**改动面大、且不是记录快照链路的阻塞项**。

现在做，是因为三点同时成立：

1. **对外契约早已是 `profile_version`**。G-01（ADR-0026）上线 Profile 路由时，
   `CalibrationProfileOut` / `CalibrationProfileCreate` 用的就是 `profile_version`，
   由 schema 层从 `profile.version` 读出来。也就是说 **API 与 DB 一直存在名称落差**，
   每新增一个读 Profile 的地方都要重复一次"记得映射"。这笔税一直在交。
2. **Profile 已不可变**（ADR-0020 / G-07，迁移 0029）。行不可变意味着
   "乐观锁"在 Profile 上没有意义——不存在并发写同一行。保留一个叫 `version`
   的列，只会让人误以为它有乐观锁语义。
3. **改名是纯内部收敛**，对外 API 零变化（见 §4）。破坏性仅落在 DB 列名，
   且没有外部消费者直连本表。

## 2. 决策

`CalibrationProfile` 的业务版本字段在 **Domain / ORM / 仓储 / 用例 / DB 列** 统一叫
`profile_version`；`CalibrationRecord.version`（乐观锁）保持不变。

### 2.1 ORM 层结构随之调整

原来 `CalibrationProfile` 混入 `ProjectScopedMixin`，而该 Mixin 同时提供
`project_id` / 审计列 / 乐观锁 `version`。改名后如果只把列改名，Profile 会
**多出一个叫 `profile_version` 的业务列，同时又保留 Mixin 的 `version` 乐观锁列**——
这正是要消除的混淆，等于换个名字又犯一次。

因此把 Mixin 拆成两半：

| Mixin | 内容 | 使用者 |
|---|---|---|
| `ProjectScopedMixin` | `project_id` + `created_at/updated_at/created_by/updated_by` | 所有设备域表 |
| `LockVersionMixin` | 乐观锁 `version` | `Robot` / `Device` / `Sensor` / `CalibrationRecord` / 数据源四表 |

`CalibrationProfile` 只混入前者。列名与迁移后的库结构一致，`create_all`
（测试路径）与 Alembic（真实库路径）不会再漂移。

### 2.2 约束改名

| 对象 | 变更 |
|---|---|
| 唯一键 `uq_calibration_profiles_name_target_version` | 列引用改为 `profile_version`；**名字保留**（"version" 在这里指业务版本，语义仍准确，且改索引名会让依赖名字的运维脚本失配） |
| CHECK `ck_calibration_profiles_version_positive` | 名字改为 `ck_calibration_profiles_profile_version_positive`，表达式改为 `profile_version >= 1` |

## 3. 迁移策略（迁移 0032）

### 3.1 为什么是直接更名而不是 expand/contract

标准做法是 expand/contract（加新列 → 双写 → 切读 → 删旧列），为的是
**新老代码可以同时在线**。本项不适用：

- 单体应用 + 单次发布的个人开发阶段，没有"新旧版本并行"的窗口；
- 列改名保留数据与类型，是原子的 DDL，比"加列 + 回填 + 切读 + 删列"四步更安全；
- 无外部系统直连 `calibration_profiles`。

一旦进入 staging/生产多副本阶段，同类列改名应改用 expand/contract。这一条
写在这里，是为了下次不要照抄本 ADR 的"直接改名"。

### 3.2 约束与索引：不重建

**已实测**（不是推断）：PostgreSQL 与 SQLite ≥ 3.25 的
`ALTER TABLE ... RENAME COLUMN` 都会自动重写 CHECK 与索引中的列引用。
实测结果：

- `CHECK (profile_version >= 1)` 仍生效（插入 0 被拒）；
- `UNIQUE (project_id, name, target_id, profile_version)` 仍生效（重复插入被拒）；
- 四个引用本表的复合外键、两个触发器均完好。

因此迁移**不重建**唯一索引与 CHECK，也就不存在"重建期间约束缺位"的窗口。
唯一额外动作是把 CHECK 的**名字**对齐（PostgreSQL 用 `ALTER TABLE ... RENAME
CONSTRAINT`）。

### 3.3 为什么不用 `batch_alter_table`

batch 在 SQLite 上是 copy-and-move：临时改名表 → 建新表 → 拷贝 → 删旧表。
对本表这意味着：

- 丢掉两个不可变/禁删触发器（迁移 0029），必须手动重装；
- 在 `robots` / `devices` / `sensors` / `calibration_records` 四张表的
  复合外键注视下改表名，SQLite 的 `legacy_alter_table` 行为会成为变量。

原生改名不触碰触发器，也不需要移动数据。当前解释器
（CPython 3.11 + SQLite 3.53）支持原生改名，已在迁移测试中覆盖。

### 3.4 SQLite 的已知差异（显式登记，不掩盖）

SQLite 没有"重命名约束"的 DDL。因此：

- **迁移过的 SQLite 库**：CHECK 表达式已指向 `profile_version`（语义正确），
  但约束名仍是 `ck_calibration_profiles_version_positive`；
- **`create_all` 建出的 SQLite 库**：约束名是新名。

只差名字、行为一致。PG 是唯一验收目标，SQLite 仅测试用；该差异已写入迁移
docstring 与迁移测试注释，避免后续误判为漂移。

## 4. 兼容性影响

| 面 | 影响 |
|---|---|
| 对外 HTTP API | **无变化**。`profile_version` 自 G-01 起就是契约字段名，请求/响应形状一字不改 |
| 旧客户端 | 不受影响。没有任何客户端曾经见过 DB 列名 |
| 前端类型 | 无变化（`apps/web/src/types/device.ts` 早已是 `profile_version`） |
| 契约 `schemas.stage1.yaml` | 仅 description 措辞去掉了"表列仍叫 version，G-03 才改名"这句临时说明 |
| 直连 SQL 的脚本 / 报表 | **会坏**。工作区内已核对：无生产脚本直连该列；`docs/testing/evidence/` 下的历史探针脚本属归档证据，不执行 |

## 5. 回滚方案

`alembic downgrade 0031` 是严格逆操作：列名回 `version`，CHECK 名回旧名，
数据不动。已在真实 PostgreSQL 上验证 `0031 → 0032 → 0031 → 0032` 往返，
并在迁移测试里覆盖数据保留、约束行为与触发器存活。

若代码已发布而需回滚 DB：必须**同时**回滚应用代码，否则 ORM 找不到
`profile_version` 列会直接报错。这是直接改名的固有代价，也是 §3.1 记录的
"多副本阶段应改走 expand/contract"的原因。

## 6. 不做的部分

| 项 | 原因 |
|---|---|
| 把唯一索引名里的 `version` 也改掉 | 本表语境下 "version" 即业务版本，名字不产生歧义；改索引名是纯运维噪声 |
| 给 Profile 保留乐观锁列 | 行不可变（ADR-0020 / G-07），并发写同一行不存在，保留即误导 |
| SQLite 上重建表以对齐约束名 | 代价（丢触发器 + 外键风险）远大于收益（仅名字不同） |
