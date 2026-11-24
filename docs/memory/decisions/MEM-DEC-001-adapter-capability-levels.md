### MEM-DEC-001 | Adapter 能力分级用独立枚举，不改现有五值枚举

- **类型与状态**：`DEC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `数据物化/格式适配` | 批次/资产 `全部 Adapter` | 密级 `INTERNAL`
- **核心结论**：现有 `AdapterCapability` 五值（`PARSE/DETECT/FETCH/VERIFY/WRITE`）回答的是「**接口支持哪些操作**」；V5.1 蓝图要求的六级（`DETECT/INSPECT/PAYLOAD_PARSE/STREAM_EXTRACT/EPISODE_BUILD/ROUND_TRIP`）回答的是「**解析到了多深**」。两者正交，**新增独立枚举并存，不改现有**。
- **关键依据与细节**：
  - 现有五值语义来自 `AdapterSpec` 上可选 callable 是否为 None，是"能力有无"而非"深度"
  - 若强行合并：`PARSE` / `FETCH` / `VERIFY` / `WRITE` 在六级里无处安放
  - 痛点二根因正是「Adapter 注册成功被误认为完整支持」——需要**独立维度**表达深度
  - S0 实测档案：hdf5/rosbag/mp4/parquet/lerobot 全部止步 **INSPECT**；mcap 完全无支持
  - 强制规则：级别必须**连续**（宣称 EPISODE_BUILD 必须先满足 STREAM_EXTRACT），不连续在构造时抛错
  - `evidence` 为空时不允许 `max_level > DETECT`（无证据不能宣称深度）
  - 详见 `docs/adr/0048-adapter-capability-levels.md`
- **影响与后续动作**：
  - 新增 `FormatCapabilityLevel` 枚举 + `FormatCapabilityProfile`；`AdapterSpec` 加可选字段
  - 两套概念需文档解释（ADR §3.3 承担）
  - UI/文档的能力展示只读此来源，禁止"支持 X 格式"的模糊表述
  - 责任人：数据平台；**ADR 已提交待确认**

---

#### 工程扩展字段

- **check_ref**：`scripts/check_adapter_capability.py`（待建）+ `docs/adr/0048-adapter-capability-levels.md`
- **recurrence**：`1`
- **tags**：`adapter,capability,level,enum,adr,format`
