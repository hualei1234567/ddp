# ADR-0023 设备域 ID 改用 UUIDv7（G-18）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：`CLAUDE.md` 第二/三节；审计报告 G-18
- 影响范围：设备/传感器/标定记录的 ID 生成；数据库列类型与契约不变

## 1. 背景

设备域三处 ID 生成用 `uuid4()`（`devices.py:150`、`sensors.py:167`、
`calibrations.py:137`）。

UUIDv4 完全随机，主键在 B-tree 中随机分布：时间相邻的写入落在索引的随机页上，
写放大、缓存命中率低；`ORDER BY id` 也不代表创建顺序，分页只能依赖
`created_at`，同一毫秒多条记录时排序不稳定。

审计（G-18，P2）要求改用 UUIDv7。

## 2. 决策

**新增 `app/domain/common/ids.py` 作为全仓唯一 ID 生成入口，设备域三处改用它。**

UUIDv7 前 48 位是毫秒时间戳，其余位保证同毫秒内单调递增，因此：

- 主键索引近似顺序写入；
- `ORDER BY id` 等价于按创建时间排序，游标分页天然稳定；
- ID 自带时间信息，排障不需要再关联 `created_at`。

**只改"新生成的 ID"**：v7 与 v4 都是 128 位、都被 `uuid.UUID` 接受，
数据库列类型（`Uuid`）、约束、契约里的 `$defs/uuid` 全部不变，旧数据无需迁移。

## 3. 为什么选 `uuid-utils` 而不是 `uuid6`

先试的是 `uuid6`（PyPI 上名字更直接的那个），实测发现它**为了"保证唯一"
每次生成都把时间戳前推 1ms**：

```
连续生成 200 个 ID 后，最后一个 ID 内嵌时间比实际当前时间快 200ms
```

后果是 ID 里的时间不再等于创建时间。批量导入时这会累积成秒级偏差——
按 ID 排序排障会得到一条**错的时间线**，而这条时间线是排障时唯一的顺序依据。
这是不能接受的语义偷换，因此弃用。

`uuid-utils` 的行为符合预期：同一毫秒内多个 ID 时间戳相同、尾部随机位递增，
实际时间不偏移。

## 4. 依赖评估（`CLAUDE.md` §三）

| 项 | 结论 |
|---|---|
| 用途 | 生成 UUIDv7 |
| 替代方案 | 手写 v7（48 位时间戳 + 版本位 + 随机位，需自行处理同毫秒单调递增与时钟回拨，属于"手写已知有标准解法"）；`uuid6`（实测时间戳前推，弃用）；`uuid7` 包（维护活跃度低）；等 Python 3.14 内置 `uuid.uuid7`（当前运行时是 3.11） |
| 版本固定 | `uuid-utils==1.2.0`（`requirements.txt` 精确固定） |
| 体积/性能 | Rust 扩展（abi3 wheel，约 576KB，macOS/Linux/Windows 全平台 wheel 齐全，无编译需求）；比纯 Python 实现快一个数量级，ID 生成在写路径上 |
| 许可证 | BSD-3-Clause |
| 安全公告 | 1.2.0 为当前版本；无历史 CVE。Rust `uuid` crate 的官方 Python 绑定，同时是 Pydantic/FastAPI 生态常用件 |
| 维护活跃度 | Production/Stable 分类；支持 Python 3.10–3.15；维护者 Amin Alaee（Pydantic 生态贡献者） |

## 5. 影响与回滚

- **API/DB 契约不变**：仍是 UUID 字符串，仍是 128 位；前端解析逻辑无需改动。
- **已有数据**：本机库设备域相关表为 0 行（`psql` 实测），无需回填；
  即便有 v4 历史数据，v4 与 v7 可以共存，`is_uuid_v7()` 只用于新增校验。
- **测试**：`tests/unit/test_ids.py` 5 条性质用例（版本位、时间有序、内嵌时间
  等于当前时间、v4 不被误认、跨毫秒排序）；`test_device_domain_ids_are_uuid_v7`
  在 API 层验证三类 ID 实际都是 v7。
- **回滚点**：`app/domain/common/ids.py` 是新增文件；回滚只需把三处 `new_id()`
  改回 `uuid4()` 并移除该文件与依赖，无数据库变更。

## 6. 验收证据

| 项 | 结果 |
|---|---|
| UUIDv7 性质用例 | 5 passed |
| 设备/传感器/标定 ID 均为 v7（API 层） | 通过 |
| 真实 PostgreSQL 全量 | 950 passed / 0 skipped |
| `make check` | 通过 |
