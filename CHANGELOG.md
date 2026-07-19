# Changelog

所有重要的版本变更记录。

## [v0.4.0] - 2026-07-19

### 新增

**电话重复数查询（核心特性）**

新增 3 个 API 端点：
- `GET /api/phone_count` - 单号码查询（返回号码、归一化、重复数）
- `POST /api/phone_count_batch` - 批量查询（号码数组，每号返回重复数）
- `POST /api/phone_count_text` - 文本标注（自动提取号码并标注重复数）

新增 3 个 MCP 工具：
- `check_phone_count` - 查询单个号码
- `check_phones_batch` - 批量查询
- `annotate_phones` - 文本标注

新增 Web 页面 `/phones`：
- Tab 1: 单号码查询（显示重复数 + 关联企业列表）
- Tab 2: 批量查询（表格展示，颜色标识）
- Tab 3: 文本标注（粘贴文本，自动标注，一键复制）

**导航栏更新**
- 新增「电话」入口
- 顺序调整为：浏览 → 电话 → 关联 → 标签 → MCP → 录入 → 导入

**归一化逻辑升级**
- 手机号：去掉 +86/86 前缀
- 南充座机（0817）：去掉 0817 区号
- 分机号：保留格式 XXXXXXX-XXX（短横线保留）
- 其他区号（0571、010 等）：保留完整

**颜色标识**
- 🟢 1 次（唯一/可信）
- 🟡 2-5 次（少量重复/可疑）
-  6+ 次（高度重复/中介号码）

### 技术细节

- 号码提取正则覆盖：手机号、座机（带/不带区号）、分机号、400/800
- 文本标注从后往前替换，避免位置偏移
- 重复数=1 也标注（用户确认）
- 所有 API 返回统一三段式 JSON

## [v0.3.0] - 2026-07-18

### 新增

**REST API 体系（核心特性）**
- 新增 `api.py` 模块，提供完整的 JSON API 接口
- 统一响应格式：`{"code": 0, "message": "ok", "data": {...}}`
- 企业列表 API：`GET /api/companies`
  - 支持筛选：城市、区县、行业、经营状态、成立年份、注册资本、社保人数
  - 支持排序：名称、ID、成立日期、注册资本等
  - 支持分页：page、per_page
  - 支持搜索：q 参数
- 企业详情 API：`GET /api/companies/<id>`
  - 返回完整企业信息
  - 包含所有关联电话及重复次数
  - 包含 5 类关联企业（同电话/同法人/同股东/同行业/同邮箱）
  - 包含关联数量统计
  - 包含企业标签
- 关联查询 API：`GET /api/relations`
  - 支持按电话、邮箱、法人、股东查询关联企业
  - 自动归一化电话号码
- 统计 API（3 个）
  - `GET /api/stats/legal_person` - 法人统计
  - `GET /api/stats/shareholder` - 股东统计
  - `GET /api/stats/industry` - 行业统计
  - 支持筛选最小关联企业数
  - 支持分页

**响应格式统一**
- `/api/search` - 搜索 API 改为三段式，新增返回 `legal_person`、`city` 字段
- `/api/phone_stats` - 电话统计 API 改为三段式
- `/api/tags` - 标签 CRUD 全部改为三段式
- `/api/companies/<id>/tags` - 企业标签关联 API 改为三段式
- `/api/companies/batch-delete` - 批量删除 API 改为三段式
- `/api/companies/batch-add-tag` - 批量添加标签 API 改为三段式

### 技术细节

- 使用 Flask Blueprint 组织 API 代码
- 统一错误码：1001（参数错误）、1002（资源不存在）、2001（服务器错误）
- 错误响应包含中文 message，便于调试
- 所有 API 自动继承 `before_request` 的数据库连接管理

### 使用示例

```bash
# 查询企业列表
curl "http://127.0.0.1:5210/api/companies?q=科技&city=杭州市&per_page=10"

# 查询企业详情
curl "http://127.0.0.1:5210/api/companies/2"

# 搜索
curl "http://127.0.0.1:5210/api/search?q=张三"

# 查询关联企业
curl "http://127.0.0.1:5210/api/relations?type=legal_person&value=张三"

# 统计
curl "http://127.0.0.1:5210/api/stats/industry?min_count=5"

# 标签管理
curl -X POST "http://127.0.0.1:5210/api/tags" \
  -H "Content-Type: application/json" \
  -d '{"name": "重要客户", "color": "#ef4444"}'
```

## [v0.2.0] - 2026-07-17

- 关联发现页面
- 批量操作功能
- 标签管理系统
- 数据库压缩功能

## [v0.1.0] - 2026-07-14

- 企业搜索
- 电话搜索
- Excel 导入
- 手动录入
