# CHANGELOG

## 2026-08-08 — v0.6.5 代码恢复 + 详情页样式优化

### 代码恢复（8/7 误操作 git restore 回退导致丢失的改动）

> 详见 `docs/code_recovery.md`，以下为实际恢复的改动。

**已恢复：**

1. **`routes/companies.py`** — `send_to_kinboard` payload 字段名 `公司名` → `企业名称`；新增 `follow_up`（跟进记录）字段，从 AJAX 请求体读取
2. **`templates/company_detail.html`** — `openKinboardModal` 弹窗新增「数据预览」区块（企业名称/地址/法人/主电话/其他电话/备注）和「跟进记录」textarea
3. **`static/style.css`** — `.phone-list` 桌面端添加 `padding-right: 12px`（补偿上方三列布局间隙，使列边界对齐）；768px / 480px 移动端断点添加 `padding-right: 0` 重置
4. **`static/style.css`** — 新增 `.imp-badge` CSS 类（橙色「重要」标签，用于联系方式标题）
5. **`static/style.css`** — 修复 `.dg-body .phone-row` 的 `margin-bottom: 8px` + `:last-child` 覆盖导致 Grid 两列布局下同行卡片高度不一致的 bug
6. **`templates/company_detail.html`** — 联系方式与地址位置交换：地址从基本信息 section 移出，作为独立 section 放在联系方式之后（基本信息 → 联系方式 → 地址信息 → 工商注册信息）

**未恢复（用户决定跳过）：**
- 电话号码拖拽排序功能（Session 7）— 与已存在的主号体系（`is_primary`）冲突

### 详情页关联企业卡片样式优化

1. **卡片样式统一** — 移除 `dg-title--rel` / `dg-body--rel` 专属类，关联企业卡片继承标准 `.dg-title` / `.dg-body` 样式（标题栏 `8px 16px`、内容区 `14px 16px`）
2. **去除「卡中卡」** — `.detail-group .ent-list` 去掉内层边框和圆角，列表行直接填充卡片内容区
3. **间距优化** — 删除 `.detail-section--rel { margin-top: 40px }`；标题 section 与卡片间距从 44px 缩小到 12px；元信息栏 `margin-bottom` 从 24px 缩小到 12px
4. **列表行调整** — `ent-row-rel` padding 从 `14px 16px` 改为 `10px 14px`，字号从 `12.5px` 改为 `13px`（与标准 `.ent-row` 一致）
5. **计数标签高亮** — `.rel-count` 从灰色纯文本改为主题色药丸式 badge（`--accent` 文字 + `--accent-light` 背景 + 圆角）
6. **`.dg-full:only-child`** — 唯一子元素时去掉多余 `margin-top`

## 2026-08-05 — 发送到多维表改造 + 状态栏菜单修复

### 发送到多维表（send_to_kinboard）

**Payload 字段变更：**

| 字段 | 说明 |
|------|------|
| `公司名` | 企业名称 |
| `地址` | 优先年报地址，没有则传注册地址 |
| `法人` | 张三（法人） |
| `主电话` | 星标主号（`is_primary=1`）且**必须为手机号**（11位 `1[3-9]` 开头）。非手机号或无主号时传空字符串 `""` |
| `其他电话` | 所有号码（含主号），每个号码一行，无号码时传空字符串 `""` |
| `备注` | 注册日期 + 资本（实缴） |
| `说明` | 用户在弹窗中输入的内容（选填，可为空字符串 `""`） |

> **主电话手机号限制规则**：星标（is_primary）是 UI 上的主号标记，一个企业可以没有星标主号。发送到多维表时，`主电话` 字段取星标主号，但额外校验必须为手机号——如果星标号码是座机/400/800 等非手机号，则 `主电话` 传空值，`其他电话` 仍包含所有号码。校验使用 `validate_phone(normalized_phone)`，只有 `phone_type` 以 `mobile` 开头才算通过。

**交互变更：**
- 原 form POST + redirect + flash 改为 AJAX + JSON 响应
- 点击「发送到多维表」弹出模态框（标题「发送到多维表」），内含「说明」textarea（3行固定高度，选填）
- 发送中按钮文字变「发送中…」并禁用，成功/失败用 `_enthubToast` 提示
- 原 `电话号码` 字段已删除，拆为 `主电话` + `其他电话`

**设置页变更：**
- 字段说明更新为新 7 字段结构
- 字段说明区域用 `<details>`/`<summary>` 默认折叠

### 状态栏菜单修复（menubar.py）

- 菜单项改名：「一键标注号码」→「一键标注」，「智能提取录入」→「智能录入」
- 修复点击报「服务未运行」的 bug：根因是密码保护启用时 API 返回 401，`HTTPError`（`URLError` 子类）被误捕获为连接失败
  - 将 `/api/phone_count_text` 和 `/api/quick-import/extract` 加入 `_PUBLIC_PATHS` 白名单
  - 错误处理分离 `HTTPError`（服务返回错误）和 `URLError`（无法连接）

### 操作按钮样式统一（company_detail.html）

- 复制/发送到多维表/工商查询/在线更新四个按钮统一使用 `page-btn` 类
- 去除发送到多维表的内联样式覆盖
- 在线更新从 `detail-refresh-btn` 改为 `page-btn`
- 按钮行与上方间距从 `10px` 增大到 `16px`

## 2026-07-30 — 邮箱独立表 + 电话号码校验

### 破坏性变更

- **`companies` 表移除 `email`、`normalized_email`、`other_email` 字段**
  - 邮箱数据迁移到独立的 `company_emails` 表（结构参照 `company_phones`）
  - 首次启动应用时 `init_db()` 自动创建新表，旧列残留不影响功能
  - **存量数据迁移**：需运行一次性迁移脚本将 `companies.email/other_email` 数据导入 `company_emails`

- **`other_phone` / `other_email` 字段移除**
  - 电话和邮箱各自合并为单个字段，多值通过分号 `;` 分隔
  - 导入时旧文件中的 `其他电话` / `其他邮箱` 列自动映射到 `phone` / `email`
  - 前端表单从两个输入框合并为一个（带"多个用 ; 分隔"提示）

### 新功能

- **电话号码格式校验 `validate_phone()`**
  - 校验规则：手机 11 位 `1[3-9]`、座机 7-8 位 / 10-12 位 `0` 开头、400/800 号码 10 位
  - 导入时自动跳过无效号码（不写入数据库）
  - 详情页无效号码显示橙色 ⚠ 格式异常标记
  - 录入/编辑页输入框 blur 时前端 JS 实时校验并提示

- **导入进度增加 `phones_invalid` 计数**
  - SSE 进度推送中新增 `phones_invalid` 字段，统计因格式异常被跳过的号码数量

- **MCP Server 详情接口增加 `emails` 字段**
  - `get_company_detail` 返回值增加 `emails` 列表（含 `dup_count`、`is_primary`）
  - `find_relations` 增加 `email` 关联类型，查询 `company_emails` 表

### 涉及文件（~25 个）

| 层次 | 文件 |
|------|------|
| 工具层 | `utils.py` |
| 数据库 | `db.py` |
| 数据工具 | `data_helpers.py` |
| 查询层 | `queries.py` |
| 路由 | `routes/companies.py`, `routes/import_flow.py`, `routes/quick_import.py`, `routes/cleanup_flow.py`, `routes/backup_flow.py`, `routes/pages.py`, `routes/api_legacy.py` |
| REST API | `api.py` |
| MCP | `mcp_server.py` |
| 外部集成 | `enthub_api.py` |
| 模板 | `templates/_company_form.html`, `templates/company_detail.html`, `templates/import_preview.html`, `templates/backup.html` |
| 测试 | `tests/test_phone_parsing.py` |

### API 变更

- `GET /api/companies/<id>` 返回值新增 `emails` 字段（`[{email, normalized_email, is_primary, dup_count, phone_valid, phone_type, phone_invalid_reason}, ...]`）
- `GET /api/relations?type=email&value=xxx` 现查询 `company_emails` 表
- `POST /api/companies` 请求体 `phone` 字段支持多号码分号分隔，新增 `email` 字段（多邮箱分号分隔）
- `PUT /api/companies/<id>` 同上
- MCP `get_company_detail` 返回值同 REST API
- MCP `find_relations(rel_type='email')` 查询 `company_emails` 表

### 数据迁移指南

如果已有数据库中 `companies` 表有 `email` / `other_email` 数据，需运行一次性迁移：

```sql
-- 将 companies 表中的邮箱数据迁移到 company_emails 表
INSERT INTO company_emails (company_id, email, normalized_email, is_primary)
SELECT id, email, lower(email), 1
FROM companies
WHERE email IS NOT NULL AND email <> '' AND email <> '-';

INSERT INTO company_emails (company_id, email, normalized_email, is_primary)
SELECT id, other_email, lower(other_email), 0
FROM companies
WHERE other_email IS NOT NULL AND other_email <> '' AND other_email <> '-';
```

迁移后可安全忽略 `companies` 表中残留的 `email` / `other_email` / `normalized_email` 列（SQLite 不支持 DROP COLUMN，但新代码不再读写这些列）。
