### MEM-INC-021 | JSONB 回读类型不宽容 + Decimal 金额纪律：JSON 字段读回是 ISO 字符串、金额全程 Decimal 量化 4 位小数对外输出字符串

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `领域模型/成本台账` | 批次/资产 `backend/app/domain/annotation/` | 密级 `INTERNAL`
- **核心结论**：JSON/JSONB 列回读**不会**还原 Python 类型——存进去的 `datetime` 读回来是 ISO 字符串，消费方必须容错解析（如 `_coerce_aware_datetime`）；金额字段全程用 **Decimal** 并量化到 4 位小数，对外输出一律转字符串，禁止 float 直接参与金额计算或序列化。
- **关键依据与细节**：
  - JSON 回读（2026-11-25，PR #134 PRM 步级标注）：`backend/app/domain/annotation/prm_step.py` 中 `step_mark.marked_at` 存于 JSON 字段，读回是 ISO 字符串；第 97 行的 `_coerce_aware_datetime`（容忍 datetime / ISO 字符串 / None）在第 138 行对 `marked_at` 做强制收口，避免调用方拿到裸字符串。
  - Decimal 金额（2026-11-25，PR #135 Token 成本台账）：judge/PRM 调用的 Token 单价与成本归集全程 Decimal、量化 4 位小数（`Decimal.quantize(Decimal("0.0001"))`）、对外 API 输出字符串；与 MEM-INC-008（纳秒精度浮点进 JSON 的精度陷阱）同族——浮点/时间这类「序列化会变形」的值，边界处必须显式规范化。
  - 共性根因：把 JSONB 当「对象快照」假设读回类型不变；实际只有字符串/数字/布尔/嵌套结构，任何领域类型都要在读取边界显式重建。
- **影响与后续动作**：
  - 新增含 JSON 字段的领域模型时，datetime/Decimal 类字段必须在模型层提供 coerce/quantize 收口，禁止把回读值直接透给 API。
  - 金额类字段（成本、计费、配额）一律 Decimal(4 位) + 字符串输出；评审按此红线检查。
  - 责任人：Backend Engineer；每次新增 JSONB 字段或金额字段时执行。

---

#### 工程扩展字段

- **check_ref**：`backend/app/domain/annotation/prm_step.py`
- **recurrence**：`2`
- **tags**：decimal,jsonb,precision,serialization,type-coercion,datetime
