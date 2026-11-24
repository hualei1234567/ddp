### MEM-INC-006 | 契约/DB 约束/代码三者脱节：UPLOADED_BY 边无生产者

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `Lineage/血缘` | 批次/资产 `raw_asset_links` | 密级 `INTERNAL`
- **核心结论**：`UPLOADED_BY` 边类型**在契约里有定义、在 DB 约束里被允许，但全仓没有任何代码写入它**。结果是 `DatasetVersion → CaptureSession` 这段血缘**永远查不到数据**，而所有静态检查都通过——因为契约合规、约束满足。
- **关键依据与细节**：
  - 实测发现：`grep -rn "UPLOADED_BY" backend/app` 只找到读取/枚举，无写入点
  - 真实栈验证：`Episode.capture_session_id` = 0/4，进入数据集版本的真实资产 **100% 来自 IMPORT**（HF 导入），该链路不产生上传/采集会话
  - 处置：按数据源链同一取舍，补**读时投影**（`raw_asset_links` 未 REVOKED → 边，uuid5 确定性 id，derived=true）；不加迁移、不写持久化边
  - **这不是"加个写入就完事"**：历史数据里根本没有上传会话链路，补写入也只对将来生效
  - 结论：FR-040 保持 PARTIAL（7/8 段 PASS，Session 段 UNVERIFIABLE），不升级
- **影响与后续动作**：
  - **三层对齐检查**：契约定义 / DB 约束 / 代码生产者，三者必须都能找到。只有前两者存在 = 悬空能力
  - 建议加静态检查：枚举里的每种边类型/事件类型，必须能在应用层找到构造点（outbox 已有类似检查 `scripts/check_outbox_events.py`，Lineage 边类型还没有）
  - 责任人：后端；**部分完成**（投影已补，真实链路仍缺数据）

---

#### 工程扩展字段

- **check_ref**：`backend/tests/api/test_lineage_session_projection_api.py`
- **recurrence**：`1`
- **tags**：`lineage,contract,db-constraint,dead-code,session,projection`
