# DDP V5.1 全面建设蓝图包

**版本定位：真实数据生产闭环与训练就绪版本。**  
**审计依据：**《DDP V4.1 全面审计报告（2026-11-24）》，事实基线 `origin/main @ c71c72a`。  
**核心原则：** 不新增顶层业务模块；不重复建设 V4.1 已 VERIFIED 能力；以真实数据链路和可复核证据验收。

## V5.1 一句话目标
让真实机器人数据进入 DDP 后，能够正确解析为逐帧、多模态、时间对齐的 Observation / Action / SensorStream，经过确定性质量处理、人工标注与质检，生成可由官方训练数据加载器读取的 Dataset，并能从训练样本反向追溯到原始文件、采集 Session、设备、标定和处理版本。

## 审计事实边界
- V4.1 基线：`c71c72a`；OpenAPI 1.33.0 / 256 paths；Alembic 0072；96 张真实数据库表。
- 后端 2921 用例；`scripts/check.sh` 9 步全部通过。
- Edge Agent FR-001~008 VERIFIED，但真实硬件采集仍 `HARDWARE_CAPTURE_NOT_TESTED`。
- FR-009~013 PARTIAL；FR-014 DATA_CLEANING 显式 SKIPPED；FR-015 PARTIAL。
- FR-016~018 VERIFIED；FR-019/020 PARTIAL。
- FR-026~028 VERIFIED；FR-025 证据区间可视化 PARTIAL。
- FR-034/035 已通过 `lerobot==0.3.3` 元数据加载验证，但 `loadable_scope=METADATA_ONLY`，不等于逐帧训练数据可读。
- FR-036/037 因外部训练平台 API 缺失 BLOCKED；FR-038 Staging、FR-039 硬件 Pilot 外部资源 BLOCKED。
- FR-040/041/043/044/045 仍有不同程度 PARTIAL。
- 风险登记册 64 项：P1 24 / P2 40。

## 文件组织
本包包含 27 份 Markdown：README + 26 份设计/执行文档，分别覆盖：建设纲领、审计继承、痛点根因、业务闭环、样本准入、SensorStream/Observation/Action、时间同步、Episode、Cleaning、QC、标注冲突、负载提示、LeRobot 逐帧验证、Lineage、RBAC E2E、恢复演练、Staging/Pilot、AI/训练外部依赖、运营监控、工程治理、FR 矩阵、实施 DAG、测试验收、风险决策、Claude Code 总 Prompt、首批任务拆解。

## 使用规则
1. 将本包放入仓库 `docs/roadmap/v5.1/`，先核对目录规范，不覆盖已有文件。
2. 第一轮由 Claude Code 做只读基线复核，不能直接大规模改代码。
3. 每项功能必须记录：当前实现、复用路径、实际差距、设计、API/DB 影响、权限、测试、回滚、E1/E2/E3 证据。
4. 没有真实样本时可开发通用框架和合成测试，但不能把合成测试标记为真实数据验收。
5. 外部依赖缺失的能力保持 BLOCKED，并写清解除条件。
6. 不以增加模块、页面、API 数量作为进度指标。

## 当前建议开工顺序
先做 S0 基线复核和样本盘点；并行启动不依赖硬件的 FR-044、FR-019、FR-020、FR-025、FR-043、FR-045；然后用真实样本完成 S3 数据物化主链。AI/GPU、训练平台 API、Staging 和真实硬件分别按依赖门禁推进。
