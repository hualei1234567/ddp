### MEM-DEC-002 | 偏好对比标注用 JSON 约定值而非新增标签类型

- **类型与状态**：`DEC` | 状态：`ACTIVE`
- **版本与血缘**：当前版本 `v1.0` | 替代旧版 `无` | 生效时间 `2026-11-24`
- **适用范围**：项目 `DDP` | 环节 `标注平台/LLM 场景` | 批次/资产 `PREFERENCE_PAIR 标注` | 密级 `INTERNAL`
- **核心结论**：偏好对比标注（chosen/rejected + criteria + rationale）用**现有 `JSON` 标签类型的约定值规范**表达，**不新增 `PREFERENCE_PAIR` 标签类型**。理由：值结构可表达，新增类型会扩散到 Schema/校验/导出/前端全链。
- **关键依据与细节**：
  - 现有 `AnnotationLabelType` 已有 `FREE_TEXT` / `STRING` / `JSON`（`domain/annotation/enums.py`）
  - 偏好对的可表达结构（作为 JSON 标签的值规范）：
    ```json
    {
      "winner": "A|B|TIE",
      "criteria": {"helpfulness": 1, "harmlessness": 0},
      "rationale": "...",
      "position_seed": "0x...",
      "shown_order": ["A","B"]
    }
    ```
  - **必须记录位置随机化**：`position_seed` + `shown_order`——不随机化模型会学到位置偏差（真实工程陷阱）
  - 若新增标签类型：需改 `AnnotationLabelType` 枚举、`_validate_label_type_value`、JSON Schema、LeRobot/导出映射、前端渲染、既有数据集兼容——成本远高于收益
  - 约束：约定值规范必须写进 Schema preset 并用 `json_schema` 校验，不能只靠口头约定
- **影响与后续动作**：
  - 建 `PREFERENCE_PAIR` 的 Schema preset（含 JSON 值规范）
  - 前端偏好对比面板必须用 `shown_order` 渲染，不能假设 A 在前
  - 导出 DPO 格式时从 JSON 值解析 `chosen`/`rejected`
  - 责任人：标注平台；**设计已定，待实施**

---

#### 工程扩展字段

- **check_ref**：`docs/roadmap/v5.2/design-plan.md`（§4.1.2）+ 待建 schema preset 测试
- **recurrence**：`1`
- **tags**：`preference,pairwise,json-label,schema,position-bias,dpo`
