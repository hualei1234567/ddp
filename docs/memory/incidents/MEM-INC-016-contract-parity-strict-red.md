### MEM-INC-016 | 契约门禁强制 parity：契约超前于 API 实现会被 `--strict` 判红，契约与实现必须同批交付

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `API 契约演进/CI 门禁` | 批次/资产 `packages/contracts + backend/app/api` | 密级 `INTERNAL`
- **核心结论**：`scripts/check_contract_parity.py --strict` 把「契约超前」（契约里声明了路径、运行时没有对应端点）判为红灯。因此契约改动与 API 实现必须**同一批 PR 交付**，禁止「先写契约占位、实现后补」——补的那段时间 CI 一直是红的。
- **关键依据与细节**：
  - 实测（2026-11-25，PR #134 PRM 步级标注）：`paths.stage10.yaml` 新增的路径带着 `{annotation_id}` 动态段，契约先行合入后 parity 检查报 `path-parameters-defined`，CI 红。根因是契约声明了路径而运行时端点尚未注册，`--strict` 模式把「契约有、实现无」与「实现有、契约无」同等对待。
  - 处置：契约改动与 API 端点实现、测试放进同一个 PR，不拆批交付；本地提交前先跑 `python3 scripts/check_contract_parity.py --strict` 再推。
  - 与 MEM-INC-015（契约版本并行 bump 冲突）互补：015 管版本号单点，本卡管「契约/实现时序」单点。
- **影响与后续动作**：
  - 涉及契约的任务卡必须把 API 实现、契约 fragment、parity 校验列入同一验收批；契约变更先报 System Architect 的纪律不变。
  - 责任人：各 Agent（执行）+ System Architect（契约门禁规则）；每次契约演进生效。

---

#### 工程扩展字段

- **check_ref**：`scripts/check_contract_parity.py`
- **recurrence**：`2`
- **tags**：api-contract,ci-gate,parity,strict-mode,contract-first

#### 追加案例（2026-11-26，PR #140 评测失败案例回流）

- **裸容器放弃契约校验**：请求体写成 `list[dict[str, Any]]` 时实现侧无字段声明，检查器报「契约多字段」+「契约必填但实现非必填」两条差异。改用强类型 Pydantic 模型（`BackflowCaseIn`，7 个显式字段）后 `--strict` 通过。
- **结论**：parity 按**实现侧 Pydantic 模型**比对字段可达性；请求/响应里出现 `dict[str, Any]` / `list[dict]` 等于主动放弃校验能力。新增端点的请求/响应模型一律显式字段声明。
- **recurrence 更新**：`2`
