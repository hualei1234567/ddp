# ADR-0033 契约与实现一致性核查工具（`check_contract_parity.py`）

- 状态：**ACCEPTED**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0014（复用优先与代码质量标准）、`docs/architecture/契约与实现一致性缺口登记-2026-11-15.md`
- 影响范围：仅新增只读脚本；未改契约、未改业务代码
- 未接入 CI（原因见 §5）

## 1. 问题

契约（`packages/contracts/openapi.yaml` + `fragments/`）与 FastAPI 实现之间
没有人能回答"现在差多少"。具体表现为三类问题长期靠人工记忆：

1. **实现超前**：新写的路由没进契约，客户端无法生成类型、也无法按 code 分支；
2. **契约超前**：契约里声明了错误码，实现却从未产生它——客户端写了分支永远收不到；
3. **字段级漂移**：必填性、可空性、字段集三者在两侧不一致。

人工比对不现实：契约 181 条路径、实现 186 条、错误码 210 个声明 / 150 个有产生点。

## 2. 决策

新增 `scripts/check_contract_parity.py`（只读，不修改任何文件），做三件事：

### 2.1 路径与字段比对

直接调 `create_app().openapi()` 拿运行时契约，与 fragment 展开后的契约逐操作比对。
三个必须做的归一化，否则输出全是噪声：

| 陷阱 | 处理 |
|---|---|
| `paths.<x>` 的值是 `$ref` 指向 fragment，方法名不在原处 | 先解析 `$ref` 再枚举方法 |
| 端点未声明 `response_model` 时导出 `{"type":"object","additionalProperties":true}` | 单列为 `untyped_responses`，不与"契约多出 N 字段"混算 |
| 契约写 `type: [string, "null"]`、运行时导出 `anyOf` | 统一归一为"可空"，不计差异 |

### 2.2 错误码可达性（AST 扫描，不靠 grep）

"契约声明了 code"与"实现真的会产生这个 code"是两件事。用 AST 找**产生点**，
共六类——少一类就会误报"无源"：

1. 领域异常类的 `code = "..."` 类属性（只声明，不算产生点）；
2. `SomeError(...)` 构造调用；
3. `AppError("code", ...)` 直接构造；
4. `super().__init__("code", ...)`（基类注入 code 的子类走这条路）；
5. 原始 JSON 响应里的 `{"code": "..."}` 字面量（限流中间件走这条路）；
6. **helper 位置参数传码**：`self._partial(..., "export_partial", ...)`。

第 6 类是实测补出来的：首版只认 `XxxError(...)` 构造，把 `export_partial` /
`export_job_failed` 误报成"无产生点"，实际它们在
`application/datasets/export_execution.py:160/204` 与 `:118/130/169/244/248`
以位置参数传入。**这个误报的教训值得记下：正则与 AST 都只能看到它被教过要看的形态，
"没有产生点"的结论必须先抽样人工复核**。

### 2.3 六类产生点的判定不能只看形态

第 6 类若只按"字符串字面量"匹配，会把日志文案、枚举名也算进去。判据取三条的交集：
匹配 `^[a-z][a-z0-9_]{2,}$`、出现在 `event_type`/`code` 等语义位置、且该值在映射表中存在。

## 3. 复用的现有能力

- `create_app()`：复用应用工厂，拿真实路由表，不重复维护一份路径清单；
- 契约 fragment 展开：复用 `packages/contracts/` 既有的编辑期组织方式，不引入第二套；
- 错误码来源：复用 `schemas.common.yaml` 的 `ErrorCode` 枚举与两份 JSON 注册表。

**没有**新建契约副本、没有把契约内容复制进脚本。

## 4. 后果

正向：
- 三类缺口从"靠人记"变成"一条命令"；`--json` 可被后续 CI 消费；
- 本次据此修掉 2 个真实可达性缺口（`calibration_active_conflict`、`calibration_checksum_mismatch`）；
- 暴露出 112 个未声明 `response_model` 的端点——这是当前契约与实现之间最大的单点结构性问题。

负向 / 未做：
- 脚本依赖 `backend/.venv`（`yaml` + `app.main`），不能裸跑系统 Python；
- `--strict` 目前会因 6 个已知的 stage7 端点缺口返回 1，**故未接入 `make check`**。

## 5. 为什么暂不接入 CI

接入 CI 的前提是"零差异"或"差异有明确白名单"。当前有 6 个真实缺口（stage7 的
assets / quality-report / review×2 / review-decisions / media-assets-playback），
补契约是纯记录工作但会动 `paths.stage7.yaml`（共享文件、单一 Owner）。

在补完契约之前强行接入，只会让人习惯性忽略红灯——比不接入更糟。
**处置**：缺口登记在 `docs/architecture/契约与实现一致性缺口登记-2026-11-15.md`，
补契约完成后用 `--strict` 接入 `make check` 第 8 步。

## 6. 不做什么

- 不尝试自动生成契约（`response_model` 缺失 112 处，生成出来的是空壳）；
- 不把 contract-draft 草稿码收进生效契约（会把"契约超前"从 70 涨到 119）；
- 不用它替代 oasdiff 做破坏性变更检查（两者职责不同：本脚本查"现状差异"，
  oasdiff 查"基线到当前是否 breaking"）。
