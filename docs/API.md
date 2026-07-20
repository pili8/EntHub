# EntHub REST API 设计文档（草案）

> 版本：v0.3 规划稿 ｜ 创建时间：2026-07-18
> 状态：**待确认**，确认后开始实施

---

## 一、设计原则

1. **复用现有查询逻辑**：每个 API 都对应现有 Web 路由，把 SQL 抽到共享函数，避免重写
2. **统一返回格式**：所有接口返回 JSON，包含 `code` / `message` / `data` 三段
3. **中文字段名**：响应字段全部使用现有数据库字段名（snake_case 英文），但错误提示用中文
4. **分页统一**：默认 `per_page=25`，最大 `100`
5. **错误处理**：400 参数错误 / 404 未找到 / 500 服务异常
6. **鉴权**：当前阶段不加，未来加 API Key（`X-API-Key` 头部）

---

## 二、统一响应格式

### ✅ 成功

```json
{
  "code": 0,
  "message": "ok",
  "data": { ... }
}
```

### ❌ 失败

```json
{
  "code": 4001,
  "message": "企业ID不存在",
  "data": null
}
```

### 错误码表

| code | 含义 |
|------|------|
| 0 | 成功 |
| 1001 | 参数错误 |
| 1002 | 资源不存在 |
| 2001 | 服务器内部错误 |

---

## 三、API 端点清单

### 1️⃣ 企业列表：`GET /api/companies`

**对应 Web 路由**：`/browse`

**请求参数**（全部可选）：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `page` | int | 1 | 页码 |
| `per_page` | int | 25 | 每页条数（10-100） |
| `sort` | string | id | 排序字段：id / name / province / city / established_date / business_status / created_at / phone / legal_person / registered_capital |
| `dir` | string | desc | 排序方向：asc / desc |
| `q` | string | - | 模糊搜索（名称/法人/信用代码） |
| `city` | string | - | 市筛选 |
| `district` | string | - | 区筛选 |
| `business_status` | string | - | 经营状态 |
| `industry` | string | - | 行业 |
| `year_from` / `year_to` | string | - | 成立年份区间（YYYY） |
| `cap_from` / `cap_to` | float | - | 注册资本区间（万元） |
| `insured_from` / `insured_to` | int | - | 社保人数区间 |

**返回字段**（results 数组）：

```
id, name, phone, credit_code, legal_person, city, district,
business_status, established_date, registered_capital, industry, enterprise_scale
```

**示例响应**：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "total": 5000,
    "page": 1,
    "per_page": 25,
    "pages": 200,
    "results": [
      {
        "id": 123,
        "name": "XX科技有限公司",
        "phone": "0571-88889999",
        "credit_code": "91330100MA...",
        "legal_person": "张三",
        "city": "杭州市",
        "district": "西湖区",
        "business_status": "存续",
        "established_date": "2015-03-12",
        "registered_capital": "1000万元",
        "industry": "软件和信息技术服务业",
        "enterprise_scale": "微型"
      }
    ]
  }
}
```

---

### 2️⃣ 企业详情：`GET /api/companies/<id>`

**对应 Web 路由**：`/company/<id>`

**路径参数**：`id`（企业 ID）

**返回数据**：

```
{
  "company": { ...完整 34 个字段... },
  "phones": [
    { "phone": "...", "normalized_phone": "...", "is_primary": 0/1, "is_recommended": 0/1, "dup_count": 3 }
  ],
  "relations": {
    "phones":         [{ id, name, phone, address }],
    "legal_person":  [{ id, name, phone, address }],
    "shareholders":    [{ id, name, phone, address }],
    "industry":      [{ id, name, phone, address }],
    "email":        [{ id, name, phone, address }]
  },
  "relation_counts": {
    "legal_person": 5,
    "shareholders": 2,
    "industry": 18,
    "email": 3
  },
  "tags": [{ "id": 1, "name": "重点客户", "color": "#ef4444" }]
}
```

---

### 3️⃣ 统一搜索：`GET /api/search`（已存在，需扩展）

**对应现有**：`/api/search`

**请求参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `q` | string | **必填** | 搜索关键词 |
| `limit` | int | 20 | 返回条数（最大 50） |
| `fields` | string | - | 返回字段控制（逗号分隔，默认全返回） |

**自动识别类型**（与现有一致）：
- 纯数字 → 电话搜索
- 18 位混合字符 → 信用代码精确匹配
- 其他 → 7 字段模糊匹配

**返回字段**：

```
id, name, phone, address, credit_code, legal_person, city, matched_field
```

> ⚠️ 改动点：在现有返回里补上 `legal_person` / `city` 字段（现版没有）

---

### 4️⃣ 关联查询：`GET /api/relations`

**对应 Web 路由**：`/relations`

**请求参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `type` | string | **必填** | 关联类型：phone / email / legal_person / shareholders |
| `value` | string | **必填** | 查询值（如电话号码 / 邮箱 / 法人名） |
| `limit` | int | 20 | 返回条数（最大 100） |

**返回**：

```json
{
  "code": 0,
  "data": {
    "type": "phone",
    "value": "0571-88889999",
    "count": 3,
    "results": [
      { "id": 1, "name": "...", "phone": "...", "address": "..." }
    ]
  }
}
```

---

### 5️⃣ 统计类 API（3 个）

#### `GET /api/stats/legal_person` — 法人统计

按法人名聚合，返回关联企业数 >= 2 的法人列表，降序。

```json
{
  "data": {
    "total": 120,
    "results": [
      { "legal_person": "张三", "company_count": 8, "companies": [{ id, name }] }
    ]
  }
}
```

#### `GET /api/stats/shareholder` — 股东统计

同上结构，字段为 `shareholder`。

#### `GET /api/stats/industry` — 行业统计

同上结构，字段为 `industry`。

**通用参数**：

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `min_count` | int | 2 | 最小关联企业数 |
| `page` / `per_page` | int | - | 分页 |

---

### 6️⃣ 电话重复统计：`GET /api/phone_stats`（已存在）

> 现状保持不变，仅在响应外层包裹 `code` / `message` / `data`。

---

### 7️⃣ 标签管理：`/api/tags`（已存在）

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/tags` | 标签列表 |
| POST | `/api/tags` | 新建标签 |
| PUT | `/api/tags/<id>` | 修改标签 |
| DELETE | `/api/tags/<id>` | 删除标签 |
| POST | `/api/companies/<id>/tags` | 给企业加标签 |
| DELETE | `/api/companies/<id>/tags/<tag_id>` | 移除企业标签 |

> ⚠️ 改动点：现有 PUT / DELETE 是否已实现需确认；响应格式统一加 `code` 外层。

---

### 8️⃣ 电话重复数查询（3 个端点）

#### `GET /api/phone_count` — 单号码查询

```bash
curl "http://127.0.0.1:5210/api/phone_count?phone=13800138000"
```

```json
{
  "code": 0,
  "data": {
    "phone": "13800138000",
    "normalized": "13800138000",
    "count": 3
  }
}
```

#### `POST /api/phone_count_batch` — 批量号码查询

```bash
curl -X POST "http://127.0.0.1:5210/api/phone_count_batch" \
  -H "Content-Type: application/json" \
  -d '{"phones": ["13800138000", "0571-88889999"]}'
```

```json
{
  "code": 0,
  "data": {
    "results": [
      {"phone": "13800138000", "normalized": "13800138000", "count": 3},
      {"phone": "0571-88889999", "normalized": "057188889999", "count": 0}
    ]
  }
}
```

#### `POST /api/phone_count_text` — 文本标注（JSON）

```bash
curl -X POST "http://127.0.0.1:5210/api/phone_count_text" \
  -H "Content-Type: application/json" \
  -d '{"text": "联系张三 13800138000 或李四 0571-88889999"}'
```

```json
{
  "code": 0,
  "data": {
    "original_text": "...",
    "annotated_text": "联系张三 13800138000 (3) 或李四 0571-88889999 (0)",
    "phones": [...],
    "phone_count": 2
  }
}
```

---

### 9️⃣ 快速标注：`GET/POST /api/annotate` ⭐ 推荐

**纯文本接口**（非 JSON），命令行友好，管道友好。

#### 入参方式（自动识别）

| Content-Type | 入参 | 适用场景 |
|------|------|---------|
| GET `?text=xxx` | URL 参数 | 短文本，浏览器测试 |
| `application/json` | `{"text": "..."}` | 程序调用 |
| `text/plain` 或无 | raw body | curl --data-binary |
| 管道 stdin | raw body | `pbpaste \| curl` |

#### 用法示例

```bash
# 1. 📋 剪贴板一键标注（最常用）
pbpaste | curl -s -X POST http://127.0.0.1:5210/api/annotate --data-binary @- | pbcopy

# 2. 🌐 GET 方式
curl "http://127.0.0.1:5210/api/annotate?text=联系 13800138000"
# 返回：联系 13800138000 (3)

# 3. 📄 从文件读取
cat 联系人.txt | curl -s -X POST http://127.0.0.1:5210/api/annotate --data-binary @-

# 4. 🔧 POST JSON
curl -X POST http://127.0.0.1:5210/api/annotate \
  -H "Content-Type: application/json" \
  -d '{"text": "联系 13800138000"}'
```

#### 返回值

- ✅ 成功：`HTTP 200`，纯文本（标注后的原文）
- ❌ 失败：`HTTP 400/500`，纯文本错误信息（如 `错误：文本为空`）

#### 推荐用法

加到 `~/.zshrc`：

```bash
alias annotate='pbpaste | curl -s -X POST http://127.0.0.1:5210/api/annotate --data-binary @- | pbcopy && echo "已标注并复制到剪贴板"'
```

复制文本后，终端输入 `annotate` 即可。

---

## 四、实施顺序

| 阶段 | 内容 | 依赖 |
|------|------|------|
| **P1** | 重构查询逻辑为共享函数（`queries.py`） | - |
| **P2** | 企业列表 + 详情 API | P1 |
| **P3** | 搜索 API 字段扩展 + 关联查询 API | P1 |
| **P4** | 3 个统计 API | P1 |
| **P5** | 响应格式统一 + 标签 API 补全 | P2-P4 |

---

## 五、待确认事项

1. **响应格式**：是否采纳 `{code, message, data}` 三段式？还是直接返回数据？
   - 三段式优点：统一、好错误处理 / 缺点：略冗余
   - 直返式优点：简洁 / 缺点：错误处理不一致
2. **fields 参数**：是否需要支持按需返回字段（减少传输量）？
3. **鉴权时机**：当前不加 Key，未来加在哪层？（API Key / IP 白名单 / 不加）
4. **中文还是英文字段名**：当前草案用英文字段名（与数据库一致），但 `matched_field` 里的值是中文。是否需要全中文化？
5. **分页大小上限**：当前草案 100，是否够用？

---

*本文档为草案，确认后开始实施。所有改动会先列清单再动手。*
