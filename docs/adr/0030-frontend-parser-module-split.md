# ADR-0030 前端标注解析器按业务簇拆分（R-5 部分收敛）

- 状态：**ACCEPTED（部分实现）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：审计 R-5；`CLAUDE.md` 第五节（800 行原则上拆分）
- 影响范围：`apps/web/src/services/annotation*Parsers.ts`

## 1. 背景

审计 R-5 记录前端有 2 个 >800 行文件：

| 文件 | 行数 | 性质 |
|---|---|---|
| `services/annotation.ts` | 916 | 35 个 API 方法 + 大量共享辅助的单一工厂 |
| `services/annotationResultParsers.ts` | 840 | 约 20 个响应解析器 |

后端两个 >800 行文件在此前的专项批次已清零。

## 2. 已完成：`annotationResultParsers.ts` 拆分

按**业务簇**拆成四份，原文件变成 34 行的 re-export 入口：

| 文件 | 行数 | 内容 |
|---|---|---|
| `annotationParseBase.ts` | 106 | 共享读取工具（JSON 值、数组、标签、证据） |
| `annotationRecordParsers.ts` | 229 | 标注记录与提交 |
| `annotationReviewParsers.ts` | 231 | 复核与仲裁 |
| `annotationQualityParsers.ts` | 348 | 黄金集、质检样本、IAA 报告 |
| `annotationResultParsers.ts` | 34 | re-export 入口（调用点不变） |

拆分边界按**业务对象**而不是按行数切：一个解析器只依赖同簇的兄弟解析器，
跨簇依赖只有两处（质检复用复核的 `parseReviewIssue`/`parseChecklistItem`），
显式 import 即可。

验证：`tsc --noEmit` 0 错误；前端 350 个测试全通过（含真实后端契约快照比对）。

## 3. 未做：`annotation.ts`（916 行）

**不做**，理由如下：

1. **它不是"多份重复逻辑"，而是一个协议适配器**：35 个方法各自 3–8 行，
   共同闭包共享 `client`、`projectPath`、幂等头构造等。拆成多个工厂意味着
   这些共享件要变成显式参数或再抽一层，收益是"文件变小"，成本是
   "每个调用点都要重新确认上下文"。
2. **它是前端日志里出错率最高的文件**：35 个方法对应 35 个后端端点，
   任何拆分都要保证 URL 拼接与请求头不变。当前缺少针对该文件的
   请求层单测覆盖（只有集成性质的使用），拆分的回归风险高于收益。
3. **审计 R-5 的原文是"排入专项拆分批次，不顺手重构"**——这正是当前情形。
   在数据平面链路刚跑通的时点做大范围前端重构，不符合增量迭代。

已登记为已知技术债务（见 §4），条件具备时再做。

## 4. 技术债务登记

| 项 | 内容 |
|---|---|
| Debt ID | D-003 |
| 位置 | `apps/web/src/services/annotation.ts`（916 行） |
| 影响 | 单文件超阈值，阅读与合并冲突成本高 |
| 根因 | 35 个端点方法 + 共享闭包，拆分需同时重构调用约定 |
| 前置条件 | ~~先补该文件的请求层单测~~ **已满足**：`annotation.requests.test.ts` 37 条用例逐方法锁定路径 / HTTP 方法 / If-Match / Idempotency-Key / 请求体 / 查询参数 |
| Owner | 前端 |
| 计划版本 | 当前批次（前置条件已满足，拆分进行中） |

## 5. 一个过程中的教训

拆分时我用 `prettier --write` 批量格式化，结果它连带重排了**未参与拆分**的
`annotationParsers.ts` 的导入块（-50 行）。该文件本来就不符合当前 prettier 配置，
属于既有状态；批量格式化会把它混进本次 diff，让审查无法分辨"拆分"与"格式化"。

已 `git checkout` 恢复，改为手写拆分、不跑全仓格式化。
**教训**：改动范围外的文件，即使工具"顺手"改得更规范，也不该混进本次提交。

## 6. 前置条件已满足（2026-11-15 续）

拆分 `annotation.ts` 的最大风险是"路径/请求头写错但无人发现"，因为该文件
原先没有请求层单测。现在补了 `apps/web/src/services/annotation.requests.test.ts`：

- **37 条用例**覆盖 35 个方法；
- 每个方法至少断言完整路径与 HTTP 方法；
- 写动作额外断言 `If-Match: "<version>"` 与 `Idempotency-Key` 存在；
- 列表方法断言查询参数（如 `task_id` 不得漏传）；
- 缺 ID / 空动作名必须在**发请求之前**报错（不会打到服务端）。

这套测试就是拆分的安全网：无论文件怎么重排，请求契约都不允许漂移。
