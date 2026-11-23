# DDP Edge Agent 本地核心（V31-012a）

> **边界声明**：本模块为端侧采集 Agent 的本地运行时核心（FR-004/005/006/007/008），
> 遵循 ADR-0047（纯拉取边界、零入站连接、本地轻量 SQLite 状态库、不直连 DDP PostgreSQL）。
>
> - **`HARDWARE_CAPTURE_NOT_TESTED`**：本阶段未连接真实机器人/采集硬件。
> - **显式测试替身**：与 DDP 服务端的交互通过 `edge_agent.upload.gateway.ServerGateway` Protocol 抽象，
>   本阶段测试使用显式标注的 `tests/fake_gateway.py`（`InMemoryServerGateway`）验证可靠性、断网续传、
>   URL 过期重签与篡改隔离，待 V31-012b 接入真实 V31-011 + Upload HTTP API 联调。

## 模块结构

- `edge_agent/config.py`：TOML/映射配置加载与阈值校验
- `edge_agent/durable_fs.py`：`fsync`（文件+目录）、`hashlib.file_digest` SHA256、原子目录重命名
- `edge_agent/redaction.py`：日志/诊断脱敏过滤（严禁泄露配对凭证/Agent Token/JWT/签名）
- `edge_agent/manifest/`：确定性 Canonical JSON Manifest 构建、字段校验与禁止内容红线（FR-006）
- `edge_agent/spool/`：SQLite (WAL) 状态机、落盘原子迁移、崩溃恢复与原始副本保护（FR-007）
- `edge_agent/adapters/`：`CaptureAdapter` 协议与 `FilesystemExportAdapter`（FR-005，明确 `realtime_control=False`、`realtime_capture=False`）
- `edge_agent/preflight/`：11 项 Preflight 检查与不可绕过的 `BLOCKED` 启动门禁 + `WARN` 确认证据（FR-004）
- `edge_agent/upload/`：`ServerGateway` Protocol 与可续传分片上传驱动（FR-007/008）

## 依赖与许可证

| 依赖 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| `packaging` | `24.2` | Apache-2.0 OR BSD-2-Clause | PEP 440 Agent 版本比较（Preflight） |
| `pytest` (dev) | `8.3.4` | MIT | 单元测试与故障注入测试 |
| `ruff` (dev) | `0.8.6` | MIT | 代码规范与安全检查 |

其余能力（SQLite WAL、SHA256、MIME 推断、TOML 解析、JSON、Pathlib）均复用 Python 3.11+ 标准库。

## 本地验证

```bash
cd services/edge-agent
pytest
ruff check edge_agent tests
```
