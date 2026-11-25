### MEM-INC-017 | Redocly 路径参数三坑：动态段必须显式声明、组件必须落进 parameters: 段、fragment 内 $ref 相对解析

- **类型与状态**：`INC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-25`
- **适用范围**：项目 `DDP` | 环节 `API 契约 lint` | 批次/资产 `packages/contracts/fragments/paths.*.yaml` | 密级 `INTERNAL`
- **核心结论**：路径含 `{param}` 动态段时 Redocly 有三个反直觉要求：①必须在路径级或操作级**显式声明 `parameters`**，只写 URL 不声明会报错；②复用参数组件时 `$ref` 的组件必须定义在 `components.yaml` 的 **`parameters:` 段内**——把参数定义追加到文件尾会落进 `responses:` 段，触发 `no-unresolved-refs`（实测）；③fragment 文件里的 `$ref` 相对解析基准是 **fragment 所在目录**，跨文件引用要写 `../openapi.yaml#/...` 而非同目录路径。
- **关键依据与细节**：
  - 实测（2026-11-25，PR #134）：`paths.stage10.yaml` 新增带 `{annotation_id}` 的路径后，Redocly lint 连续报错三轮才定位齐——第一轮缺 parameters 声明；第二轮把参数组件追加到 `components.yaml` 尾部，实际落进了 `responses:` 段，报 `no-unresolved-refs`；第三轮修正 fragment 内 `$ref` 的相对基准为 `../openapi.yaml#/...` 后通过。
  - 门禁入口是 `packages/contracts/redocly.yaml`（extends recommended，`tag-description: error`）；lint 失败与 parity 红灯（MEM-INC-016）叠加，契约 PR 几乎无法先行合入。
  - 经验法则：fragment 里出现 `{xxx}` 先补 parameters，再写 `$ref`，写完用 `../openapi.yaml#/components/parameters/...` 形式核对一遍段落归属。
- **影响与后续动作**：
  - 新增契约路径时按「声明参数 → 组件入 `parameters:` 段 → fragment 相对 `$ref`」三步自查；lint 退出码非 0 不许推。
  - 责任人：涉及契约的各 Agent；每次新增/修改契约路径时执行。

---

#### 工程扩展字段

- **check_ref**：`packages/contracts/redocly.yaml`
- **recurrence**：`1`
- **tags**：api-contract,openapi,redocly,ref-resolution,swagger-lint,yaml
