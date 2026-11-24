### [MEMORY_ID] | [TITLE]

- **类型与状态**：`REQ / DEC / SOP / INC / CHG / OPS` | 状态：`DRAFT / ACTIVE / SUPERSEDED / DEPRECATED`
- **版本与血缘**：当前版本 `vX.Y` | 替代旧版 `<OLD_ID 或 无>` | 生效时间 `<YYYY-MM-DD>`
- **适用范围**：项目 `<PRJ_ID>` | 环节 `<STAGE>` | 批次/资产 `<BATCH/ASSET>` | 密级 `<SENSITIVITY>`
- **核心结论（1-3句话说清是什么、定什么）**：
- **关键依据与细节（数据、实验、试采或排障事实）**：
- **影响与后续动作（对工期、成本、质量、SOP、脚本的影响及责任人/截止时间）**：

---

#### 工程扩展字段（必填项见 ARCHITECTURE.md §4.2）

- **check_ref**：`<拦它的测试/脚本路径:行号 或 无>`
- **recurrence**：`<复发次数，默认 1>`
- **tags**：`<领域/技术栈/阶段，逗号分隔>`

---

<!--
模板使用说明（提交前删除本注释块）

1. 复制本文件到对应类型目录：
   - INC → docs/memory/incidents/
   - DEC → docs/memory/decisions/
   - REQ → docs/memory/requirements/
   - SOP → docs/memory/sop/
   - CHG → docs/memory/changes/
   - OPS → docs/memory/ops/

2. 重命名为：MEM-<类型>-<三位序号>-<短标题>.md
   例：MEM-INC-001-update-branch-missing-integrity-error.md

3. 填写规则：
   - **核心结论**必须 1-3 句，能让没看过上下文的人看懂
   - **关键依据**写真实数据/事实，不写推测
   - **check_ref**：
     * INC / DEC / SOP **必填**——没有可执行检查的记忆只是日记
     * 指向真实存在的测试函数、脚本路径或 CI 步骤
     * 确实无法自动化时写 `无（原因：...）`，但会被 review 质询
   - **recurrence**：同一个坑第 2 次出现时改为 2，并考虑升级约束强度

4. 建卡后运行：python3 scripts/build_memory_index.py
-->
