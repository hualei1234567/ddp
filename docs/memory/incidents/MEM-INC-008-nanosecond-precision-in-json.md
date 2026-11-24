### MEM-INC-008 | 纳秒时间戳经 JSON 传输会静默丢精度

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `契约/前端/时间字段` | 批次/资产 `全部 *_ns 字段` | 密级 `INTERNAL`
- **核心结论**：纳秒时间戳（约 `1.8e18`）**远超 IEEE-754 双精度安全整数上限 `2^53`**，经浏览器 `JSON.parse` 会**静默改值**且不报错。契约对外必须用**十进制字符串**，数据库必须用 `BIGINT`（浮点列会在写入时就丢精度，更隐蔽）。
- **关键依据与细节**：
  - `2^53 = 9,007,199,254,740,992`；当前时间纳秒约 `1.7e18`，超上限约 188 倍
  - `JSON.parse('{"ns":1700000000123456789}')` → `1700000000123456800`（末尾数字丢失）
  - 这类错误**不抛异常**——只在比对时发现「两个值看起来一样但并不相等」，极难排查
  - 落地（V51-010）：`QualityRunFinding.evidence_location` 时间字段对外用字符串；
    迁移 0073 用 `BIGINT`（`evidence_start_time_ns` / `evidence_end_time_ns`）
  - Canonical 层早有正确设计（`*_ns` 内部 int、`to_dict` 输出十进制字符串），
    本坑是没在业务字段上贯彻同一原则
- **影响与后续动作**：
  - 任何 `*_ns` 字段：DB 用 `BIGINT`，API 对外用 `string`
  - 契约 schema 写 `type: string` 而非 `type: integer`
  - 前端类型定义用 `string` 而非 `number`
  - 责任人：全体；**已在 V51-010 贯彻**

---

#### 工程扩展字段

- **check_ref**：`backend/tests/api/test_quality_evidence_api.py`
- **recurrence**：`1`
- **tags**：`nanosecond,timestamp,json,precision,bigint,contract,2^53`
