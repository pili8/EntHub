# WPS 多维表 API 验证记录

> 此目录仅用于验证 WPS OpenAPI 的可用性，不写入项目本身。
> 验证通过后再考虑集成到 EntHub。

## 1. 认证

| 项目 | 值 |
|------|-----|
| Token 端点 | `POST https://openapi.wps.cn/oauth2/token` |
| 认证方式 | OAuth2 client_credentials |
| App ID | `AK20260727DHYCQL` |
| App Secret | `6d29fea0cfe8200caa2ff30a69a117d4` |
| 参数格式 | `application/x-www-form-urlencoded` |
| 参数名 | `client_id` / `client_secret` / `grant_type=client_credentials` |
| Token 有效期 | 7199 秒（约 2 小时） |
| 认证状态 | ✅ 已验证，成功拿到 access_token |

### 认证命令

```bash
curl -s "https://openapi.wps.cn/oauth2/token" \
  -X POST \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=AK20260727DHYCQL&client_secret=6d29fea0cfe8200caa2ff30a69a117d4&grant_type=client_credentials"
```

### JWT Token Payload

```json
{
  "aid": 1878450836,
  "atp": "sp",
  "ats": "MmVz1Nd",
  "bui": false,
  "cid": 626835914,
  "cli": "AK20260727DHYCQL",
  "coa": 0,
  "exp": 1785124963,
  "jst": false,
  "spi": 1878450836
}
```

## 2. API Base URL

`https://openapi.wps.cn`

所有 DBSheet API 路径前缀：`/v7/coop/dbsheet/` 或 `/v7/dbsheet/`

## 3. DBSheet API 路径列表

| 功能 | 方法 | 路径 | 所需 scope |
|------|------|------|-----------|
| 获取表结构 | GET | `/v7/coop/dbsheet/{file_id}/schema` | `kso.dbsheet.read` |
| 查询记录 | GET | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records` | `kso.dbsheet.read` |
| 新增记录 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/create` | `kso.dbsheet.readwrite` |
| 更新记录 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/update` | `kso.dbsheet.readwrite` |
| 批量删除记录 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/batch_delete` | `kso.dbsheet.readwrite` |
| 删除字段 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/fields/delete` | `kso.dbsheet.readwrite` |
| 创建工作表 | POST | `/v7/coop/dbsheet/{file_id}/sheets/create` | `kso.dbsheet.readwrite` |
| 删除工作表 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{existing_id}/delete` | `kso.dbsheet.readwrite` |
| 列出视图 | GET | `/v7/dbsheet/{file_id}/sheets/{sheet_id}/views` | `kso.dbsheet.read` |
| 获取视图 | GET | `/v7/dbsheet/{file_id}/sheets/{sheet_id}/views/{view_id}` | `kso.dbsheet.read` |
| 创建视图 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/views` | `kso.dbsheet.readwrite` |
| 更新视图 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/views/{view_id}/update` | `kso.dbsheet.readwrite` |
| 删除视图 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/views/{view_id}/delete` | `kso.dbsheet.readwrite` |
| 列出 Webhook | GET | `/v7/coop/dbsheet/{file_id}/hooks` | `kso.dbsheet.read` |
| 创建 Webhook | POST | `/v7/coop/dbsheet/{file_id}/hooks/create` | `kso.dbsheet.readwrite` |
| 删除 Webhook | POST | `/v7/coop/dbsheet/{file_id}/hooks/{hook_id}/delete` | `kso.dbsheet.readwrite` |
| 分享状态 | GET | `/v7/dbsheet/{file_id}/sheets/{sheet_id}/views/{view_id}/sharedlinks/status` | `kso.dbsheet.read` |
| 开启分享 | POST | `/v7/dbsheet/{file_id}/sheets/{sheet_id}/views/{view_id}/sharedlinks/open` | `kso.dbsheet.readwrite` |
| 关闭分享 | POST | `/v7/dbsheet/{file_id}/sheets/{sheet_id}/views/{view_id}/sharedlinks/{share_id}/close` | `kso.dbsheet.readwrite` |
| 仪表盘列表 | GET | `/v7/dbsheet/{file_id}/dashboards` | `kso.dbsheet.read` |
| 复制仪表盘 | POST | `/v7/dbsheet/{file_id}/dashboards/{dashboard_id}/copy` | `kso.dbsheet.readwrite` |
| 链接解析 | GET | `/v7/links/{link_id}/meta` | `kso.file_link.readwrite` |

## 4. 验证状态

### 4.1 API 端点可达性 — ✅ 已验证

以下端点用 client_credentials token 测试，返回 403（端点存在，只是缺 scope）：

- `/v7/coop/dbsheet/{file_id}/schema` → 403: 需要 `kso.dbsheet.read`
- `/v7/links/{link_id}/meta` → 403: 需要 `kso.file_link.readwrite`
- `/v7/users` → 403: 需要 `kso.contact.read`

403 而非 404 = 端点真实存在，只是权限不够。

### 4.2 Scope 权限 — ❌ 待解决

**问题：** `client_credentials` 模式拿到的 token 默认不带 DBSheet scope。

API 返回错误：
```
The requested scope 'kso.dbsheet.readwrite or kso.dbsheet.read' 
has not been granted or is not allowed to be requested.
```

**可能的解决方案：**
1. 在开发者后台给应用申请 DBSheet scope（`kso.dbsheet.read`）
2. 使用 OAuth2 用户授权流程获取 user token

## 5. API 路径来源

- 从 GitHub 开源项目 `shenxl/wpscli`（Rust，WPS 365 OpenAPI CLI）源码中提取
- 文件：`src/helpers/dbsheet.rs`、`src/executor.rs`、`src/link_resolver.rs`
- 文档：`docs/wpscli-dbsheet-guide.md`

## 6. OAuth2 用户授权流程

### 授权端点

| 项目 | 值 |
|------|-----|
| Authorize URL | `https://openapi.wps.cn/oauth2/auth` |
| Token URL | `https://openapi.wps.cn/oauth2/token` |
| Grant Type | `authorization_code` |
| 默认 redirect_uri | `http://localhost:53682/callback` |
| 推荐 scope | `kso.dbsheet.read`（读）或 `kso.dbsheet.readwrite`（读写） |

### 授权流程

```
1. 用户浏览器访问:
   https://openapi.wps.cn/oauth2/auth?client_id=AK...&response_type=code&scope=kso.dbsheet.read&redirect_uri=http://localhost:53682/callback

2. 用户登录 WPS 账号并授权

3. 浏览器重定向到:
   http://localhost:53682/callback?code=AUTH_CODE

4. 后端用 code 换 token:
   POST https://openapi.wps.cn/oauth2/token
   grant_type=authorization_code&code=AUTH_CODE&client_id=...&client_secret=...&redirect_uri=...

5. 拿到带 dbsheet scope 的 user token
```

### 当前问题

OAuth2 授权端点测试返回 400 — 应用未配置预注册的 redirect_uri。

需要在开发者后台配置回调地址后才能走通用户授权流程。

## 7. 验证记录

| 日期 | 内容 | 结果 |
|------|------|------|
| 2026-07-27 | OAuth2 client_credentials 认证 | ✅ 成功拿到 token |
| 2026-07-27 | API 端点探测 | ✅ 确认端点存在（403 而非 404） |
| 2026-07-27 | Scope 权限测试 | ⚠️ client_credentials 不带 dbsheet scope |
| 2026-07-27 | token 请求带 scope | ❌ client_credentials 不允许请求 dbsheet scope |
| 2026-07-27 | OAuth2 authorize 端点 | ✅ 端点存在，但需配置 redirect_uri |
| 2026-07-27 | 验证脚本 wps_api_test.py | ✅ 可复现验证 |

## 8. 下一步操作

需要你在 WPS 开发者后台 (`https://open.wps.cn`) 完成：

1. **配置 OAuth2 回调地址** — 在应用设置中添加 `http://localhost:53682/callback`
2. **确认 DBSheet 权限** — 看应用是否有「权限申请」入口，能否勾选 `kso.dbsheet.read`
3. **配置好后告诉我** — 我来跑 OAuth2 用户授权流程，拿到带 scope 的 user token
4. **用真实 file_id 测试** — 拿你一个多维表的分享链接（`https://365.kdocs.cn/l/xxxx`），通过 `/v7/links/{link_id}/meta` 解析出 file_id，然后调 schema 和 records API
