# ADR-0019 传感器坐标系字段迁移：`coordinate_frame` → `frame_id` + `frame_convention`

- 状态：**ACCEPTED（分两阶段执行）**
- 日期：2026-11-15
- 决策者：System Architect；批准人：用户（长期授权）
- 关联：ADR-0009（设备域契约冻结）、G-08（差距清单）、`docs/data-standards/Canonical草案.md`
- 影响范围：`sensors` 表、设备域 API、前端类型、契约快照

## 1. 背景与问题

ADR-0009 冻结的设备域契约要求传感器提供两个字段：

| 字段 | 含义 |
|---|---|
| `frame_id` | 坐标系标识，如 `camera_link`、`base_link` |
| `frame_convention` | 坐标约定（如 `ROS_REP_103`、`OPENCV`） |

当前实现只有一个 `coordinate_frame`（自由文本，最长 200）。问题在于：

1. **同一字符串既当标识又当约定**：`camera_link` 是标识，但"这个坐标系用什么约定"
   无从得知，Canonical 转换时无法确定朝向；
2. **无法校验**：自由文本可以是任意字符串，跨厂商数据合并时会撞名或歧义；
3. **与冻结契约漂移**：G-08 把它列为 P0。

## 2. 为什么不能简单改名

`coordinate_frame` 出现在 11 个文件、契约快照与前端类型里。直接改名意味着：

- 已冻结的 `ddp.device/1.0.0` 契约发生破坏性变更；
- 已上线（本地验收）的接口响应字段消失；
- 前端解析器与快照测试同时失败。

ADR-0009 明确「破坏性变更仍需新建 ADR」。本 ADR 就是这个 ADR。

## 3. 决策：两阶段迁移，不做一次性改名

### 阶段 1（本 ADR 执行）

**新增两个字段，保留旧字段并继续写入**：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `frame_id` | string(200) | 是 | 新字段；由 `coordinate_frame` 的值初始化（回填） |
| `frame_convention` | string(32)，枚举 | 是 | `ROS_REP_103` / `OPENCV` / `UNITY_LHS` / `UNSPECIFIED` |
| `coordinate_frame` | string(200) | 是 | **保留**，标记为 DEPRECATED，继续写入以保持兼容 |

处理规则：

- 新建传感器时，`frame_id` 必填；若客户端只给 `coordinate_frame`，
  服务端用其值填 `frame_id`（向后兼容）；
- 两者都给且不一致时返回 422——**不允许静默择一**；
- `frame_convention` 缺省为 `UNSPECIFIED`（明确表达"不知道"，而非猜一个）；
- 响应同时返回三个字段，旧客户端不受影响。

### 阶段 2（独立任务，不在本 ADR 内）

- 契约版本升到 `ddp.device/2.0.0`，移除 `coordinate_frame`；
- 前端删除对旧字段的读取；
- 迁移脚本删列。

**触发条件**：阶段 1 部署后至少经过一次完整数据导入验证，且确认无外部消费方。

## 4. 为什么选 `UNSPECIFIED` 而不是默认 `ROS_REP_103`

该平台接入的是多厂商第一人称数据（Human-1M 来自多种采集设备）。
默认成 ROS 约定会让"实际不是 ROS 约定"的数据被错误标注——
这比明确说"不知道"更危险。**宁可返回 UNSPECIFIED，也不猜。**

## 5. 数据库变更

迁移 0027：

```sql
ALTER TABLE sensors ADD COLUMN frame_id VARCHAR(200);
ALTER TABLE sensors ADD COLUMN frame_convention VARCHAR(32) NOT NULL DEFAULT 'UNSPECIFIED';
UPDATE sensors SET frame_id = coordinate_frame WHERE frame_id IS NULL;
ALTER TABLE sensors ALTER COLUMN frame_id SET NOT NULL;
CREATE CHECK (frame_convention IN ('ROS_REP_103','OPENCV','UNITY_LHS','UNSPECIFIED'));
```

SQLite 不支持 `ALTER ... SET NOT NULL`，迁移里用 batch 或跳过 NOT NULL 约束
（由应用层保证），并在真实 PostgreSQL 上验证完整语义。

## 6. 影响与回滚

| 项 | 影响 |
|---|---|
| 旧客户端 | 无影响（`coordinate_frame` 仍在响应里） |
| 新客户端 | 可只读 `frame_id` |
| 前端 | 阶段 1 读 `frame_id ?? coordinate_frame`，向后兼容 |
| 契约快照 | 需更新快照并重新生成 stage1 基线——**属预期内变更，本 ADR 授权** |
| 回滚 | 迁移 0027 的 downgrade 删除两列；代码回滚到只写 `coordinate_frame` |

## 7. 未做的事

- 不做 `frame_convention` 的语义校验（如"ROS_REP_103 下 z 轴朝上"）：
  这属于 Canonical 转换层的职责，放在设备域会越界；
- 不迁移 `Robot` / `Device` 的坐标系字段：它们没有同类字段，G-08 只涉及 Sensor。
