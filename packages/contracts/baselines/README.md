# 契约冻结基线

本目录存放**自包含**的 OpenAPI 基线（Redocly bundle 产物），供 `oasdiff breaking`
比较使用。基线文件不手工编辑。

## 生成

```bash
bash packages/contracts/scripts/check-stage2-freeze.sh --freeze
```

首次冻结生成：

- `openapi.stage2.v1.0.0.bundle.yaml`
- `openapi.stage2.v1.0.0.sha256`

## 检查

```bash
bash packages/contracts/scripts/check-stage2-freeze.sh --check
```

检查会：

1. 校验基线 SHA256 未变化；
2. 对 `packages/contracts/openapi.yaml` 执行 Redocly lint；
3. 执行 `oasdiff breaking <基线> <当前>`，发现 ERR 级破坏性变更即失败；
4. 将输出写入 `packages/contracts/reports/`。

## 变更流程

- 非破坏性变更：更新契约与测试，重新执行 `--check`。
- 破坏性变更：必须新建 ADR 并获用户批准，再执行 `--freeze --force` 生成新基线；
  旧基线保留在版本历史中。
- 基线文件与报告不得在未审阅的情况下提交。
