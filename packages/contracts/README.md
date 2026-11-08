# 契约包

## 原则

- 契约优先：先定义 OpenAPI / JSON Schema，再实现两端。
- 破坏性变更必须版本化；新增字段优先保持兼容。
- `packages/contracts` 由 System Architect 作为唯一 Owner；其他角色不得并行修改。

## 文件

| 文件 | 内容 |
|---|---|
| `openapi.yaml` | HTTP API 契约（阶段 0 仅健康检查/元信息） |
| `schemas/*.json` | 可复用 JSON Schema（错误体等） |

## 生成

阶段 0 手工维护并与 FastAPI 的 `/openapi.json` 对照；后续接入自动 diff 检查：

```bash
curl -s http://localhost:8000/openapi.json > /tmp/ddp-openapi.json
# 对比 packages/contracts/openapi.yaml 的路径与必填字段
```

## 变更流程

1. 提出差异与影响（前端/后端/测试/文档）。
2. Architect 评审并给出 ADR 或版本号。
3. 更新契约与测试。
4. 通知所有相关角色。
