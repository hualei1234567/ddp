### MEM-INC-029 | 断言 Webhook 投递时踩到的两个隐性事实：HTTP 头名大小写不敏感、SQLite 读回 datetime 无时区

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-27`
- **适用范围**：项目 `DDP` | 环节 `测试/出站联调` | 批次/资产 `backend/tests/api/` `backend/tests/workers/` | 密级 `INTERNAL`
- **核心结论**：验证「Webhook 真的送到下游」时，两个看似简单的断言会假失败——① HTTP header 名大小写不敏感，httpx 发出的是**小写**形式，用 `headers["X-DDP-Signature"]` 取会 `KeyError`；② SQLite **不保存时区**，读回的 `DateTime(timezone=True)` 是 naive，与 aware 的 `datetime.now(UTC)` 比较会抛 `TypeError`。
- **关键依据与细节**：
  - 实测（2026-11-27）：端到端用例 `assert headers["X-DDP-Signature"]` → `KeyError: 'X-DDP-Signature'`；改为小写键后通过。
  - 修法 ①：接收端统一把 header 键**转小写**再存（`{k.lower(): v for k, v in self.headers.items()}`），断言用小写键。
  - 修法 ②：比较前先归一：`if value.tzinfo is None: value = value.replace(tzinfo=UTC)`。
  - 附带发现：测试里 `secret_encrypted` 若写假值（如 `"Y2lwaGVydGV4dA=="`），解密时抛 `UnicodeDecodeError`，会被 Worker 当成「投递失败」重试——**看起来像下游挂了，实际是种子数据不对**。必须用 `encrypt_secret(plain, key)` 真实加密。
- **影响与后续动作**：
  - 任何「验证外部系统真的收到请求」的测试：header 键统一小写；种子里的加密字段必须真实加密。
  - 跨 SQLite 与 PostgreSQL 的 datetime 断言统一做 tz 归一，避免一库绿一库红。

#### 工程扩展字段

- **check_ref**：`backend/tests/api/test_webhook_outbound_e2e_api.py`（接收端 header 小写归一与端到端验签断言所在）
- **recurrence**：`1`
- **tags**：http-header-case,sqlite,naive-datetime,webhook,e2e-test,seed-data
