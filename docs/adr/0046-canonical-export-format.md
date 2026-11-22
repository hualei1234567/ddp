# ADR-0046 Canonical 导出格式：与 LeRobot 语义分离

- 状态：**ACCEPTED**
- 日期：2026-11-22
- 决策者：System Architect + 后端；批准人：用户（长期授权）
- 关联：ADR-0040（Canonical 共享 JSON Schema）、V31-017、V31-041
- 约束：不新增表、不新增迁移、不新增错误码、不改 LeRobot 产物语义

## 1. 背景

`ExportFormat` 只有 `LE_ROBOT` 一个值。但 LeRobot 产物明确标注
（`domain/datasets/exporting.py` 模块文档）：

```
未实现真实 LeRobot 帧级映射、视频/observation/action 对齐与 Loader 兼容性验证；
因此 verification_status 固定 UNVERIFIED，不得宣称已兼容 LeRobot 生态。
```

也就是说，当前产物是**元数据 profile**，不是能被 LeRobot Loader 直接加载的
训练数据。下游拿到 `format=LE_ROBOT` 会误以为可以直接喂给 Loader。

同时平台已有 Canonical 规范（ADR-0040 的共享 JSON Schema），它是平台自己的
格式，语义与 LeRobot 不同。

## 2. 候选方案

| 方案 | 做法 | 问题 |
|---|---|---|
| A. 继续只有 `LE_ROBOT`，用配置区分 | `config.mode = "canonical"` | 下游必须读配置才知道产物是什么；`format` 字段失去意义 |
| B. 新增 `CANONICAL` 枚举值（本方案） | 格式与版本成对，产物 sidecar 如实声明 | 需要维护格式 ↔ 版本映射 |
| C. 等真实 LeRobot Loader 兼容后再加 | 暂不新增 | V31-041 无法交付；Canonical 产物无处表达 |

## 3. 决策

**选方案 B。**

### 3.1 格式与版本必须成对

```python
EXPORT_FORMAT_VERSIONS = {
    ExportFormat.LE_ROBOT: "ddp.lerobot/1.0.0",
    ExportFormat.CANONICAL: "ddp.canonical/1.0.0",
}
```

交叉组合（`CANONICAL` + `ddp.lerobot/1.0.0`）抛
`ExportFormatVersionUnsupportedError`。理由：`format_version` 是下游判断
「这份产物是什么」的依据，写错会让 Loader 用错解析器。**不能只校验版本
字符串非空**——那等于允许任意组合。

### 3.2 产物 sidecar 必须声明格式

`ddp/sidecar.json` 新增 `format` / `format_version` 字段。sidecar 会随产物
一起流转；只从任务表推断格式，产物离开平台后就无法自证。

### 3.3 Canonical 不复用 LeRobot 的验证口径

`frame_level_mapping: false` 保留（Canonical 也不做帧级映射），但
`verification_status` 的语义不同：

- LeRobot 的 `UNVERIFIED` 表示「尚未通过真实 Loader 验证」；
- Canonical 是平台自有格式，**没有外部 Loader 可验证**。

因此 Canonical 产物不宣称「待验证」，而是如实标注格式。这是
「不得把规划能力描述成已上线能力」的具体应用。

### 3.4 未知格式仍显式拒绝

新增 `CANONICAL` 不得放宽 `PARQUET` 等未知格式的拒绝——错误码仍是
`export_format_unsupported`。

## 4. 后果

- `ExportFormat` 新增 `CANONICAL`；`export_validation.py` 新增
  `EXPORT_FORMAT_VERSIONS` 与 `expected_format_version()`。
- `ExportJob.__post_init__` 改用映射校验，不再硬编码 LeRobot 版本。
- 契约：`schemas.stage4.yaml` 的 `ExportFormat` 枚举与
  `export-job.v1.schema.json` 的 `format` / `format_version` 同步扩展。
- 不新增表、不新增迁移、不新增错误码。
- 前端导出格式选项与 V31-042（真实 LeRobot Loader 兼容）不在本 ADR 范围。

## 5. 残余风险

| 风险 | 说明 |
|---|---|
| Canonical 产物尚无真实消费者 | 格式已就绪，但还没有 Canonical Loader；由 V31-044 端到端验收覆盖 |
| 前端未提供格式选择 | 契约已就绪，前端入口另立任务 |
| LeRobot 仍 `UNVERIFIED` | 本 ADR 不改变这一点；V31-042 才做真实 Loader 兼容 |
