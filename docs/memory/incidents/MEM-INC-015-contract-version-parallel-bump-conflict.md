### MEM-INC-015 | 并行 PR 各自 bump 契约版本必然冲突，version 是共享单点

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `API 契约演进/并行开发` | 批次/资产 `packages/contracts/openapi.yaml` | 密级 `INTERNAL`
- **核心结论**：`openapi.yaml` 的 `info.version` 是全仓共享单点。多个并行 PR 各自把版本 bump 到同一个值时，后合入的一方必然在 `openapi.yaml` 与 `README.md` changelog 上产生合并冲突。处置只有两条路：**资源预分配**（开工前由 Owner 给每个 PR 独占分配下一个版本号）或 **fragments + `$ref` 延迟 bump**（改动只落在 fragments/`$ref` 引用的片段，版本号由最终合入方统一顺延）。
- **关键依据与细节**：
  - 实测场景（2026-11-25 并行开发期）：PR #116（QC 证据定位）先合入 main 并占用契约版本 `1.35.0`；另一个并行分支（Frozen Dataset 引用保护）也把自己的 bump 写成 `1.35.0`，合入时 `openapi.yaml` 的 `info.version` / `x-ddp-contract.version` 与 README changelog 三处同时冲突。
  - 当时的解法（merge commit `7bf1535`「merge: 合入 main 并顺延契约版本至 1.36.0」）：保留两边完整历史，人工顺延——`1.35.0` 归 QC 证据定位（已发布），`1.36.0` 归 Frozen 引用保护（当前），再跑 `check_contract_parity --strict` 确认 exit 0。
  - 冲突面不止一处：`openapi.yaml` 内 `info.version` 与 `x-ddp-contract.version` 两处 + `README.md` changelog 表，三处必须同步顺延，人工合并极易漏改其中一处。
  - 本轮 V5.1 有 6 个 PR（#126–#131）并行开发，其中 #129 涉及 Canonical JSON Schema 导出物；若各自顺延版本号必然重演此冲突。
- **影响与后续动作**：
  - 并行开发期的版本号分配纳入资源预分配表（见 MEM-SOP-002）；单个 PR 开工前先查 main 最新版本号，禁止「顺手 +0.1.0」。
  - 大型契约变更优先走 fragments + `$ref`，把 `info.version` 的 bump 推迟到合入前最后一步。

---

#### 工程扩展字段

- **check_ref**：`packages/contracts/scripts/check-compatibility.sh + scripts/check_contract_parity.py`
- **recurrence**：`1`
- **tags**：`api-contract,merge-conflict,openapi,parallel-pr,resource-preallocation,single-point,version-bump`
