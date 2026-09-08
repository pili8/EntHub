# WPS 多维表 API 验证 — 交接文档

> 本文档供接手的浏览器自动化工具/Agent 使用。
> 目标：在 WPS 开发者后台完成 OAuth2 回调地址配置，拿到带 DBSheet 权限的 User Token。

---

## 一、当前状态总览

| 项目 | 状态 |
|------|------|
| App 认证（client_credentials） | ✅ 已通过，能拿到 access_token |
| API 端点可达性 | ✅ 全部确认存在（返回 403 而非 404） |
| DBSheet 数据读取 | ❌ 被卡住 — client_credentials token 不带 DBSheet scope |
| OAuth2 用户授权流程 | ❌ 被卡住 — 应用未配置 redirect_uri（回调地址） |
| **当前卡点** | **需要在 WPS 开发者后台配置 OAuth2 回调地址** |

---

## 二、凭证信息

| 项目 | 值 |
|------|-----|
| App ID (client_id) | `AK20260727DHYCQL` |
| App Secret (client_secret) | `6d29fea0cfe8200caa2ff30a69a117d4` |
| 开发者后台地址 | `https://open.wps.cn` |
| API Base URL | `https://openapi.wps.cn` |

---

## 三、需要在浏览器中完成的事

### 任务 1：配置 OAuth2 回调地址（最关键）

1. 用浏览器登录 `https://open.wps.cn`（WPS 开发者后台）
2. 找到应用 `AK20260727DHYCQL` 对应的管理页面
3. 找到 **OAuth2 配置** 或 **授权设置** 或 **回调地址** 相关的设置项
4. 添加回调地址：`http://localhost:53682/callback`
5. 保存

> 用户反馈：用户之前找到了一个"授权回调地址"的页面，但不确定具体位置。
> 可能的入口路径：
> - 开发者后台 → 我的应用 → 应用详情 → 认证设置 / 安全设置
> - 或：开发者后台 → 我的应用 → 应用详情 → OAuth2 配置
> - 或：开发者后台 → 我的应用 → 应用详情 → 回调地址管理

### 任务 2：确认 DBSheet 权限

1. 在同一个应用管理页面，寻找 **权限管理** 或 **Scope 申请** 入口
2. 查看是否能勾选 / 申请以下权限：
   - `kso.dbsheet.read`（多维表读权限）
   - `kso.dbsheet.readwrite`（多维表读写权限）
3. 如果有申请入口，申请 `kso.dbsheet.read`（先只读即可）
4. 如果没有看到权限申请入口，记录下页面上能看到的所有权限/scope 列表

### 任务 3：截图/记录关键信息

- 回调地址配置页面截图
- 权限列表截图
- 应用详情页的完整信息（应用名称、状态、已配置的 scope 等）

---

## 四、已验证的 API 信息（供后续使用）

### 4.1 认证流程

**已验证可用 — client_credentials 模式：**

```bash
curl -s "https://openapi.wps.cn/oauth2/token" \
  -X POST \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "client_id=AK20260727DHYCQL&client_secret=6d29fea0cfe8200caa2ff30a69a117d4&grant_type=client_credentials"
```

- 返回 access_token，有效期 7199 秒（约 2 小时）
- **但此 token 不带 DBSheet scope**，无法读取多维表数据

**待验证 — authorization_code 模式（配置好回调地址后可用）：**

```
步骤 1: 浏览器访问授权 URL（用户登录 WPS 账号并授权）
  https://openapi.wps.cn/oauth2/auth?client_id=AK20260727DHYCQL&response_type=code&scope=kso.dbsheet.read&redirect_uri=http://localhost:53682/callback

步骤 2: 浏览器重定向到回调地址，URL 中带 code 参数
  http://localhost:53682/callback?code=AUTH_CODE

步骤 3: 后端用 code 换 token
  POST https://openapi.wps.cn/oauth2/token
  Content-Type: application/x-www-form-urlencoded
  Body: grant_type=authorization_code&code=AUTH_CODE&client_id=AK20260727DHYCQL&client_secret=6d29fea0cfe8200caa2ff30a69a117d4&redirect_uri=http://localhost:53682/callback

步骤 4: 返回的 access_token 应该带 kso.dbsheet.read scope
```

### 4.2 DBSheet API 路径

> 以下路径已通过 403 响应确认端点真实存在，只是当前 token 缺少 scope。

Base URL: `https://openapi.wps.cn`

| 功能 | 方法 | 路径 | 所需 scope |
|------|------|------|-----------|
| 获取表结构 | GET | `/v7/coop/dbsheet/{file_id}/schema` | `kso.dbsheet.read` |
| 查询记录 | GET | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records` | `kso.dbsheet.read` |
| 新增记录 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/create` | `kso.dbsheet.readwrite` |
| 更新记录 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/update` | `kso.dbsheet.readwrite` |
| 批量删除记录 | POST | `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records/batch_delete` | `kso.dbsheet.readwrite` |
| 列出视图 | GET | `/v7/dbsheet/{file_id}/sheets/{sheet_id}/views` | `kso.dbsheet.read` |
| 列出 Webhook | GET | `/v7/coop/dbsheet/{file_id}/hooks` | `kso.dbsheet.read` |
| 链接解析 | GET | `/v7/links/{link_id}/meta` | `kso.file_link.readwrite` |

**调用方式：**
```
GET /v7/coop/dbsheet/{file_id}/schema
Authorization: Bearer {access_token}
```

### 4.3 API 路径来源

- GitHub 开源项目 `shenxl/wpscli`（Rust 实现的 WPS 365 OpenAPI CLI）
- 文件：`src/helpers/dbsheet.rs`、`src/executor.rs`、`src/link_resolver.rs`

---

## 五、验证脚本

验证脚本位于：`/Users/gm/AI/EntHub/wps_api_verify/wps_api_test.py`

运行方式：
```bash
cd /Users/gm/AI/EntHub/wps_api_verify
python3 wps_api_test.py
```

脚本功能：
1. 用 client_credentials 获取 token（已验证成功）
2. 测试各 API 端点可达性（已验证全部存在）
3. 测试 OAuth2 授权端点（返回 400，需配置回调地址）

> 注意：macOS Python 可能缺少 SSL 证书，脚本中已用 `ssl.CERT_NONE` 绕过。

---

## 六、收费政策

- WPS 多维表 API 对个人用户授权场景 **免费**（已通过文档确认）
- `client_credentials`（企业应用）模式可能涉及企业版授权
- 推荐用 `authorization_code`（个人用户授权）模式，免费且能拿到 DBSheet scope

---

## 七、完整后续流程（回调地址配好之后）

1. **配置回调地址** ← 当前卡点，需浏览器操作
2. 跑 OAuth2 用户授权流程，拿到带 `kso.dbsheet.read` 的 user token
3. 用户提供一个多维表分享链接（如 `https://365.kdocs.cn/l/xxxx`）
4. 用 `/v7/links/{link_id}/meta` 解析链接，拿到 file_id
5. 用 `/v7/coop/dbsheet/{file_id}/schema` 获取表结构
6. 用 `/v7/coop/dbsheet/{file_id}/sheets/{sheet_id}/records` 读取数据
7. 验证通过后，考虑集成到 EntHub 项目

---

## 八、注意事项

- 所有验证代码在 `/Users/gm/AI/EntHub/wps_api_verify/` 目录，**不涉及项目本身代码**
- EntHub 项目有三个文件有未提交改动（`routes/import_flow.py`、`routes/pages.py`、`templates/import.html`），那是之前"导入模板下载"功能的改动，与本次 WPS 验证无关
- 回调地址 `http://localhost:53682/callback` 是本地地址，配置后需要在本地启动一个 HTTP 服务监听 53682 端口来接收回调
