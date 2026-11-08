# JWT 认证与 staging 启动

> 适用版本：DDP-211（依赖 DDP-201 认证 fail-closed 基线）
> 最后更新：2026-11-08

## 1. 认证方式

API 接受 **HS256 Bearer JWT**：

```http
Authorization: Bearer <token>
```

校验规则：

- 只接受 `alg=HS256`，拒绝 `none`、非对称算法与未处理的 `crit` 扩展；
- 使用 `DDP_JWT_SECRET` 校验 HMAC-SHA256 签名，签名比较使用恒定时间算法；
- 必须存在且校验通过：`exp`、`iss`、`aud`；
- 可选校验 `nbf`（存在时生效）；
- `iss` 必须等于 `DDP_JWT_ISSUER`，`aud` 必须包含 `DDP_JWT_AUDIENCE`；
- 所有校验失败统一返回 `401`，错误码 `not_authenticated`，不向客户端区分过期、签名错误等细节。

## 2. Claims 契约

| Claim | 必填 | 说明 |
|---|---|---|
| `sub` | 是 | 用户 UUID，映射为 `Principal.user_id`；非 UUID 拒绝 |
| `platform_role` | 否 | `member`（默认）或 `admin`；未知值拒绝，映射为 `Principal.platform_role` |
| `exp` | 是 | 过期时间（Unix 秒） |
| `iss` | 是 | 签发方，必须与 `DDP_JWT_ISSUER` 一致 |
| `aud` | 是 | 受众，字符串或字符串数组，必须包含 `DDP_JWT_AUDIENCE` |
| `nbf` | 否 | 生效时间（Unix 秒） |
| `iat` | 否 | 签发时间（Unix 秒），当前仅记录，不参与校验 |

平台角色只来自签名后的 `platform_role` 声明。任何请求头（如 `X-Platform-Role`）都不能提升权限。

## 3. 配置项

| 环境变量 | 默认值 | 非 local 启动要求 |
|---|---|---|
| `DDP_ENV` | `production` | 非 `local/dev/development` 时启用 JWT 启动自检 |
| `DDP_JWT_SECRET` | `replace-me-in-production` | 必填，去空白后至少 32 个字符，不能是占位符，且不得含首尾空白 |
| `DDP_JWT_ISSUER` | `ddp` | 必须非空 |
| `DDP_JWT_AUDIENCE` | `ddp-api` | 必须非空 |
| `DDP_JWT_LEEWAY_SECONDS` | `30` | `0..300` |
| `DDP_JWT_EXPIRE_MINUTES` | `720` | 签发策略预留项，不参与校验 |
| `DDP_LOCAL_HEADER_AUTH` | `false` | 非 local 环境必须为 `false`，否则拒绝启动 |

生成强随机密钥的示例（不要把真实密钥写入仓库）：

```bash
openssl rand -hex 32
```

## 4. 启动自检与本地 Header 后门

- 非 local 环境：JWT 配置完整才允许启动；缺失、占位符、密钥过短、`issuer/audience` 为空、
  或误开 `DDP_LOCAL_HEADER_AUTH` 都会抛 `RuntimeError`，进程不启动。
- local 环境：允许启动；Bearer JWT 仍可正常使用。
- `X-User-Id` Header 认证必须同时满足：
  1. `DDP_ENV=local`（或 `dev/development`）；
  2. `DDP_LOCAL_HEADER_AUTH=true`；
  3. 请求来源为 loopback（`127.0.0.1`、`::1`、`localhost`；测试客户端 `testclient`）。
- 一旦请求携带 `Authorization`，就只按 Bearer 处理；格式错误或令牌无效直接 401，
  **不会回退**到 `X-User-Id`。
- `X-User-Id` 身份固定为 `member`，不能通过请求头获得平台管理员。

## 5. staging 重建与健康检查

前提：目标机器已安装 Docker Compose，代码与 `.env` 已就位；`.env` 不入库。

1. 准备 `.env`（从 `.env.example` 复制），至少设置：

   ```dotenv
   DDP_ENV=staging
   DDP_JWT_SECRET=<至少 32 字符的强随机值>
   DDP_JWT_ISSUER=ddp
   DDP_JWT_AUDIENCE=ddp-api
   DDP_LOCAL_HEADER_AUTH=false
   ```

2. 重建并启动 API：

   ```bash
   docker compose --profile app build api
   docker compose --profile app up -d api
   ```

3. 检查容器与健康端点：

   ```bash
   docker compose ps api
   curl -fsS http://127.0.0.1:8000/health
   ```

   期望返回 `200`，响应示例：

   ```json
   {"status":"ok","service":"DDP","version":"0.1.0","env":"staging"}
   ```

4. 验证认证边界（使用任一受保护接口）：

   - 不带令牌：`401`，`error.code=not_authenticated`；
   - 携带有效 JWT：进入项目权限校验；
   - 过期、篡改、错误签名、错误 `iss/aud`：均为 `401`，且消息一致。

5. 配置错误排查：若 API 容器反复重启，先看日志中的启动自检错误：

   ```bash
   docker compose logs --tail=100 api
   ```

## 6. 回滚

1. 配置回滚：恢复上一版 `.env`，再执行 `docker compose --profile app up -d api`。
2. 镜像回滚：使用上一版镜像重新启动 API；回滚不覆盖数据库数据。
3. 应用回滚前确认 JWT 签发方仍接受旧 `DDP_JWT_SECRET`；轮换密钥时应保留双密钥过渡期，
   当前版本仅支持单密钥，需先切换签发方再切换 API。

## 7. 验证状态与限制

- 单元/接口测试已覆盖：有效令牌映射、过期、篡改、错误签名、缺失、错误 `iss/aud`、
  `alg=none`、未知角色、本地 Header 后门边界，以及 staging 配置下 `/health` 返回 200。
- 当前 Agent 执行环境禁止 `docker` 命令，**实际容器重建与 staging `/health` 探测未执行（BLOCKED）**；
  需由运维/用户按第 5 节在目标机执行并留存命令输出。
- 未实现令牌签发、密钥轮换、JWKS/OIDC；当前只支持静态 HS256 密钥。
