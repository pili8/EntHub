# CHANGELOG

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
