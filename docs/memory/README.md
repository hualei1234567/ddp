# DDP 记忆库（Memory Nebula）

> 把踩过的坑变成**可检索、可执行、可演进**的约束，而不是又一份没人读的文档。

---

## 一、这是什么

本项目积累了大量真实踩坑（事故、缺陷、反直觉约束）。散落在 ADR、commit message、
会话历史里，**下次遇到同类问题无法定位「上次怎么解决的」**。

本目录把这些问题沉淀为**记忆卡片**，并强制每张卡绑定一个**可执行检查**
（`check_ref`），让"记住"变成"拦住"。

**完整设计见 [ARCHITECTURE.md](./ARCHITECTURE.md)。**

---

## 二、怎么用（三条路径）

### 路径 1：我遇到了一个问题

```bash
# 按关键词搜
grep -rn "UniqueViolation" docs/memory/
grep -rn "PENDING" docs/memory/

# 或看索引按 tag 检索
grep "outbox" docs/memory/MEMORY_INDEX.md
```

### 路径 2：我要改某块代码，先查有没有相关约束

```bash
# 按技术栈/领域 tag 查
grep -i "migration\|postgres" docs/memory/MEMORY_INDEX.md

# 重点看 DEC（决策）与 REQ（口径）——这两类决定"能不能这么做"
```

### 路径 3：我在 OpenViking 里检索（跨会话）

记忆卡片会作为 resource 导入 OpenViking，可用语义检索召回：
- "上次那个备份校验为什么不生效"
- "Adapter 分级为什么不用现有枚举"

> **文件是权威源，OpenViking 只是索引。冲突时以文件为准。**

---

## 三、怎么新增一张卡片

```bash
# 1. 复制模板
cp docs/memory/templates/memory-card.md \
   docs/memory/incidents/MEM-INC-008-your-title.md

# 2. 填写（模板内有逐字段说明）
#    必填：ID / 类型 / 状态 / 版本血缘 / 适用范围 / 核心结论 / 关键依据 / 影响动作
#    工程字段：check_ref（INC/DEC/SOP 必填）/ recurrence / tags

# 3. 重新生成索引
python3 scripts/build_memory_index.py

# 4. 校验
python3 scripts/check_memory.py
```

**ID 规则**：`MEM-<类型>-<三位序号>-<短标题>.md`，序号**不复用**（删了留空号）。

---

## 四、什么时候该建卡片（入库门槛）

**必须入库**（任一满足）：
1. 造成过 ≥30 分钟排查时间
2. 造成过 CI 失败 / 数据错误 / 线上异常
3. 违反直觉（"看起来该这样，实际不能"）
4. 是不可逆操作（删除/迁移/发布）的前置条件
5. 是跨模块的隐性契约

**不入库**：一次性拼写错误、代码注释已说清且不会复发的小事、纯个人偏好。

> 记忆库的价值取决于**信噪比**。什么都要记，就等于什么都没记。

---

## 五、类型说明

| 类型 | 含义 | 必填 check_ref | 本项目示例 |
|---|---|---|---|
| `INC` | 事故/缺陷复盘 | ✅ | UPDATE 分支漏捕获 IntegrityError |
| `DEC` | 决策（含被否决方案） | ✅ | Adapter 分级用独立枚举 |
| `SOP` | 标准操作流程 | ✅ | 真实样本准入 GATE-0 |
| `REQ` | 需求/验收口径 | 选填 | METADATA_ONLY 不等于训练就绪 |
| `CHG` | 变更记录（含风险） | 选填 | 迁移 0072 归正 outbox |
| `OPS` | 运维参数/门禁 | 选填 | 备份恢复 7 个不变量 + RPO/RTO |

**为什么 INC/DEC/SOP 强制 check_ref**：这三类是**可执行**的——事故要有回归测试、
决策要有架构检查、流程要有脚本。只写文字不绑检查 = 日记，不是记忆。

---

## 六、状态机

```
DRAFT ──确认──> ACTIVE ──被取代──> SUPERSEDED
                  │
                  └──不再适用──> DEPRECATED
```

- **只有 `ACTIVE` 可被当作现行约束引用**
- `SUPERSEDED` 必须指向替代它的新卡片
- `DEPRECATED` 必须写明为什么不再适用

---

## 七、复发追踪（本系统的关键机制）

卡片的 `recurrence` 字段记录**同一个坑出现了几次**。

`recurrence > 1` 意味着**上一轮的记忆没生效**——此时应：
1. 检查现有 `check_ref` 为什么没拦住
2. 升级为更强的自动化约束
3. 在索引的「复发的坑」段落中被重点标注

> 当前 1 张：`MEM-INC-005`（CI 写死测试计数，已复发 2 次）。这说明仅靠"知道"不够，
> 需要更强的结构性约束。

---

## 八、维护节奏

| 频率 | 动作 |
|---|---|
| **每次发版前** | `python3 scripts/check_memory.py`（CI 已自动跑） |
| 每月 | 复审 `recurrence > 1` 的卡片，升级约束 |
| 每季度 | 复审 `DEPRECATED`，可归档 |

---

## 九、与其它文档的边界（不重复建设）

| 现有资产 | 边界 |
|---|---|
| `docs/adr/` | ADR 是完整论证；记忆卡只写**浓缩结论 + 引用**，不复述正文 |
| `docs/roadmap/*/` | 蓝图是规划、审计是时点事实；记忆是**跨时点的教训** |
| `docs/testing/evidence/` | 证据是某次验证产物；记忆是从中提取的**教训** |
| `CLAUDE.md` / `AGENTS.md` | 是给 Agent 的硬约束；记忆卡是**约束的由来与证据** |

**一条硬规则：记忆卡不复述已有文档，只写新增的教训 + 指向既有证据的引用。**

---

## 十、当前记忆库状态

见 [MEMORY_INDEX.md](./MEMORY_INDEX.md)（自动生成）。

> 索引由 `scripts/build_memory_index.py` 生成，**勿手改**——手改会被
> `scripts/check_memory.py` 判为漂移并让 CI 失败。
