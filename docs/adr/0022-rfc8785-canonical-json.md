# ADR-0022 确定性 JSON 规范化统一为 RFC 8785 JCS（G-15）

- 状态：**ACCEPTED（已实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：`CLAUDE.md` 第二/三节；审计报告 G-15；契约 `packages/contracts/schemas/manifest.v1.schema.json`
- 影响范围：`backend/app/domain/common/`（新增）；标定 checksum、Episode/Dataset Manifest、导出产物、幂等指纹

## 1. 背景

DDP 有 4 处独立实现的"JSON 规范化 + SHA256"：

| 位置 | 用途 | 是否声明 JCS |
|---|---|---|
| `app/domain/datasets/canonical.py` | DatasetVersion manifest、导出产物 | **声明** `RFC8785_JCS` |
| `app/domain/episodes/manifest.py` | Episode manifest | 未声明，但契约同类 |
| `app/domain/modelops/canonical.py` | 训练交付、回调 | 未声明 |
| `app/application/idempotency.py` | 幂等请求指纹 | 未声明 |

四份实现写法相近但都是 `json.dumps(sort_keys=True, separators=(",",":"))`。
审计（G-15）指出：**这不是 RFC 8785**，而契约里已经冻结了
`"canonicalization": {"const": "RFC8785_JCS"}`。

实测差异（用 `rfc8785` 0.1.4 对拍，见 `tests/unit/test_canonical_json.py`）：

| 输入 | `json.dumps(sort_keys=True)` | RFC 8785 |
|---|---|---|
| `900.0` | `900.0` | `900` |
| `1.0` | `1.0` | `1` |
| `1e-07` | `1e-07` | `1e-7` |
| `1e16` | `1e+16` | `10000000000000000` |
| `-0.0` | `-0.0` | `0` |
| 键 `"￿"` vs `"😀"` | 码点序（`￿` 在前） | UTF-16 码元序（`😀` 在前） |

后果不是"Hash 看起来不同"这么轻：Manifest 的 `manifest_sha256` 会被
外部训练平台按 RFC 8785 独立重算。算法不一致时，**未篡改的产物会被判成篡改**，
而真正被篡改的产物在另一种实现下可能恰好"验过"。这是同一性判定的地基。

标定的 `checksum` 还叠加了第二个问题：只在 `DRAFT` 更新时重算，
`submit` / `validate` / `activate` 三个状态命令前不重算比对，
因此绕过用例层直写的篡改可以一路推进到 `ACTIVE`。

## 2. 决策

**规范化算法全仓只有一份实现：`app/domain/common/canonical_json.py`，基于 `rfc8785`。**

- 4 处调用点全部改为指向它；`datasets` / `modelops` 保留同名模块作为领域入口
  （17 处 import 路径不变），只做转发，不再各写一份。
- 标定 checksum 改为 JCS 计算，并在 `submit` / `validate` / `activate` 前
  重算比对，不一致抛 `DomainValidationError`（→ HTTP 422）。
- 新增 `CHECKSUM_ALGORITHM = "RFC8785_JCS"` 常量，便于审计方识别算法版本。

### 2.1 整数域限制是本决策的一部分

RFC 8785 要求数值可按 IEEE-754 double 往返，因此 **±2^53 之外的整数不可表示**。
DDP 的纳秒时间戳约 `1.8e18`，直接进 JCS 会抛 `IntegerDomainError`。

这不是要绕过的障碍，而是契约早已规定的事：Episode 契约里
`start_time_ns` / `end_time_ns` / `duration_ns` 的类型是 `int64_string`。
`_prepare()` 因此显式拒绝超范围整数，并把错误写成**可执行的话**：

```
整数 1794700800000000000 超出 JCS 可精确表示范围（±2^53）；
纳秒时间戳等大整数必须先按契约转成字符串
```

选择"显式拒绝 + 指明改法"，而不是静默转成 float（会丢精度）或静默转字符串
（会改变 Hash 语义）——后两者都会让同一份数据在不同模块得到不同 Hash。

## 3. 依赖评估（`CLAUDE.md` §三）

| 项 | 结论 |
|---|---|
| 用途 | RFC 8785 JSON Canonicalization Scheme 序列化 |
| 替代方案 | 手写 JCS（浮点最短往返、UTF-16 键序、指数阈值三处都要自己实现并对拍 ECMAScript `JSON.stringify`，属于"手写已知有标准解法的底层能力"）；`json.dumps(sort_keys=True)`（现状，**不符合 JCS**，已实测）；`orjson`（快但不是 JCS 语义，需自行处理浮点格式）；`canonicaljson`（非 JCS 标准，键序规则不同） |
| 版本固定 | `rfc8785==0.1.4`（`requirements.txt` 精确固定） |
| 体积/性能 | 纯 Python，**零传递依赖**；实现约 7KB。单次序列化微秒级，与 `json.dumps` 同数量级；Manifest/checksum 均非热路径 |
| 许可证 | Apache-2.0 |
| 安全公告 | 0.1.4 为当前最新（2024-09）；无历史 CVE。维护方 Trail of Bits，提供 `py.typed` 与官方测试向量 |
| 维护活跃度 | 版本号仍是 0.x（Development Status: Beta），但实现范围极小且由规范文本完全确定；本项目另有 27 条对拍用例锁定行为，即使上游停更也不会漂移 |

补充：`rfc8785` 对 `datetime`、`MappingProxyType`、`Decimal` 等 Python 特有类型
直接拒绝。本实现先做一遍**带路径**的 `_prepare()` 前置检查再交给库，
错误信息因此能定位到 `items/3/time_range/start_ns`，而不是一句
`unsupported type: <class 'mappingproxy'>`。

## 4. 影响与回滚

- **已存数据无需迁移**：本机库 `calibration_records` / `episodes` /
  `dataset_versions` / `export_jobs` 均为 0 条（`psql` 实测），
  没有需要重算的历史 Hash。
- **对外契约不变**：`canonicalization` 字段的 `const` 本来就是 `RFC8785_JCS`，
  本次是让实现追上契约，不是改契约。
- **接口行为收紧一处**：`submit`/`validate`/`activate` 遇到 checksum 不符会 422
  并拒绝推进。这是修 Bug——篡改检测原本缺失。
- **回滚点**：`app/domain/common/canonical_json.py` 是新增文件；
  回滚只需把 4 处调用改回原实现并移除该文件，无数据库变更。

## 5. 验收证据

| 项 | 结果 |
|---|---|
| JCS 对拍用例（含"与旧实现不同"证明） | 27 passed |
| 标定 checksum 可由外部按 JCS 独立重算 | `test_checksum_matches_rfc8785_jcs_of_fixed_fields` |
| 直写篡改后 submit 被拒且状态未被推进 | `test_submit_rejects_record_tampered_after_creation` |
| SQLite 全量 | 见 `make check` |
| 真实 PostgreSQL 全量 | 945 passed / 0 skipped |
| `make check` | 通过 |
