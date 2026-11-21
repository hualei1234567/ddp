# V3.1 任务卡、Pull Request 模板与 Definition of Done

## 1. Task Card

```text
Task ID:
Title:
Stage:
Business Goal:
Current Evidence:
Existing Implementation:
Reusable Components:
Gap:
Proposed Change:
Affected Domains:
Affected APIs:
DB / Migration:
Frontend Routes:
Permissions:
Events / Outbox:
Risks:
Test Plan:
Acceptance Criteria:
Rollback:
Owner:
Dependencies:
```

## 2. Pull Request 模板

### Summary
- 业务目标：
- 任务 ID：
- 变更范围：

### Existing / Reuse
- 已有能力：
- 复用点：
- 未复用原因：

### Technical Impact
- Domain：
- Application：
- API / OpenAPI：
- DB / Migration：
- Frontend：
- Event / Outbox：
- Permission：

### Tests
- Unit：
- API：
- PostgreSQL：
- Integration：
- Frontend interaction：
- E2E：
- Command / Exit Code：

### Evidence
- Screenshot / Recording：
- Logs：
- Manifest / Hash：
- API response：

### Risk / Rollback
- 风险：
- 数据兼容：
- 回滚方式：

## 3. Definition of Done

- [ ] 需求和验收标准明确；
- [ ] 现状与复用评估完成；
- [ ] 状态机与权限在后端强制执行；
- [ ] DB / Domain / API / OpenAPI / Frontend 类型一致；
- [ ] 幂等、并发、审计和 Outbox 已覆盖；
- [ ] PostgreSQL Migration 升降级通过；
- [ ] 前端交互测试通过；
- [ ] 关键 E2E 真实数据通过；
- [ ] 文档与 Capability Matrix 更新；
- [ ] 无未解释 P0/P1 风险；
- [ ] 用户验收。

## 4. 禁止完成口径

只写代码、只通过 SQLite、只渲染静态页面、只用 Mock、只验证单个 API、只把文件上传到 MinIO、只创建 AnnotationTask 但没有实际分配、只提交标注但没有审核、只创建 Dataset 但没有冻结 Manifest、只写 LeRobot 导出代码但没有官方 Reader 回读验证，均不得标记阶段完成。
