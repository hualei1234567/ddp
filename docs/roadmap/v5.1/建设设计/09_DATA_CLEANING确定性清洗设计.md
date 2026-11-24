# 09｜DATA_CLEANING 确定性清洗设计

## 1. 目标和红线
V4.1 将 DATA_CLEANING 标记为显式 SKIPPED。V5.1 开始实现，但清洗不能改变原始 RawAsset，也不能为了提高通过率删除困难样本。所有清洗都产生版本化衍生 Artifact、质量标签或隔离记录。每条规则必须可解释、可关闭、可复现。

## 2. 规则分类
- 文件级：Hash 不符、文件截断、MIME/magic bytes 不一致、空文件、损坏媒体。
- 时间级：非单调/重复时间戳、时间跳变、采样率异常、时钟映射超限。
- 数值级：NaN/Inf、越界、突变、常量通道、缺失率过高。
- 媒体级：黑帧/冻结帧、解码失败、帧率与元数据不一致。
- 机器人级：关节范围、速度/加速度异常、姿态不连续（必须有设备规格/项目阈值）。
- Episode 级：空/过短片段、必要流缺失、时间覆盖不足、重复 Episode。
- 语义级：Action 与 Observation 时间错配、Schema/单位/坐标系不兼容。

## 3. 规则定义
每条 Rule 包含 rule_id、version、scope、input modality、predicate、threshold、severity、action（FLAG / TRANSFORM / QUARANTINE / REJECT）、evidence template、owner、effective date。

## 4. 三种处理动作
FLAG：保留数据并标记风险。TRANSFORM：生成衍生数据，保存原始与转换参数。QUARANTINE：从正常 Dataset 候选排除，保留恢复入口。REJECT：只用于明确不可用/非法数据，并保留拒绝证据。默认不做不可逆删除。

## 5. 清洗流水线
原始数据校验 → 时间规则 → 数值规则 → 媒体规则 → 机器人规格规则 → Episode 规则 → 语义一致性规则 → 汇总质量报告。相同输入、规则版本和参数得到相同输出；随机算法固定 seed 并记录。

## 6. 阈值治理
阈值按 Robot Model / Sensor Type / Project Profile 配置；禁止所有设备共用未经验证的关节范围或采样率阈值。配置变更有版本、审批和生效时间。

## 7. UI
Processing Report 展示规则命中、影响样本、严重度、证据、处理动作、是否改变衍生数据、规则版本。支持按规则筛选、查看原始值与清洗值差异、重新运行规则。

## 8. 验收
已知异常 fixture 的规则结果符合预期；RawAsset Hash 不变；Transform 有输入输出 Hash；Quarantine 可恢复；阈值版本可追溯；无规则的异常不被静默改写。
