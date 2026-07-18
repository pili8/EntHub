# EntHub

轻量级企业工商信息管理工具。解决 Excel 卡顿、查询不便、电话可信度判断等问题。单机本地运行，零运维。

## 核心场景

1. **搜企业**：按名称搜索，查看完整工商信息
2. **搜电话**：输入任意号码，查看关联企业和重复次数，判断号码可信度
3. **批量导入**：Excel 上传，自动识别天眼查格式，去重预览后确认写入

## 快速启动

**方式一：双击启动**

双击 `EntHub.app`，自动打开终端启动服务并打开浏览器。

**方式二：命令行启动**

```bash
cd ~/AI/EntHub && ./start.sh
```

首次启动会自动创建虚拟环境并安装依赖（约 1~2 分钟）。后续启动直接进入。

启动后访问 http://127.0.0.1:5210

## 功能说明

### 企业搜索

- 支持模糊、前缀、精确三种匹配模式
- 结果列表展示企业名称、法定代表人、经营状态、电话、地区、信用代码
- 点击企业名进入详情页

### 企业详情

分五个区块展示全部工商信息：

- **基本信息**：经营状态、法定代表人、注册/实缴资本、成立/核准日期、营业期限、公司类型、所属行业、参保人数
- **联系方式**：关联电话（主电话蓝色、次电话橙色，点击可跳转搜索）、邮箱、网址
- **地址信息**：省市区、注册地址、最新年报地址
- **工商注册信息**：统一社会信用代码、纳税人识别号、注册号、组织机构代码
- **经营范围**：完整经营范围文本

底部展示关联记录（同名或同电话的其他企业）。

### 电话搜索

输入任意电话号码，查到所有关联该号码的企业。号码被越多企业共用，可信度越低。搜索范围覆盖主电话和其他电话字段中的所有号码。

### 电话重复统计

按重复次数降序排列所有被 2 家以上企业共用的号码。可筛选最低重复次数（2+/3+/5+/10+）。快速识别代理记账、中介号码。

### 手动录入

- 企业名称为必填，其余字段选填
- 支持"其他电话"字段，多个号码用分号 `;` 或逗号 `,` 分隔
- 录入后自动拆分所有号码到电话表，支持电话查重
- 状态可标记为"正常/待补全/失败"，为后期爬虫预留

### Excel 批量导入

1. 上传 `.xlsx` / `.xls` 文件
2. 自动识别天眼查导出格式（跳过水印行，第三行起为数据）
3. 列名自动映射（支持中英文列名：公司名称、电话、地址、统一社会信用代码等）
4. 去重预览：对比已有数据（信用代码优先，退回规范化公司名），标记重复行
5. 确认导入：可选择跳过重复记录
6. 导入后自动拆分所有电话号码到电话表

### JSON API

完整的 REST API 接口，供其他应用、AI 工具、MCP Server 调用。

**统一响应格式**：

```json
{
  "code": 0,        // 0=成功，1001=参数错误，1002=资源不存在，2001=服务器错误
  "message": "ok",  // 中文提示信息
  "data": { ... }   // 业务数据
}
```

**API 列表**：

| 方法 | 路由 | 说明 |
|------|------|------|
| GET | `/api/companies` | 企业列表（支持筛选/排序/分页/搜索） |
| GET | `/api/companies/<id>` | 企业详情（含关联+标签） |
| GET | `/api/search?q=关键词` | 统一搜索（自动识别类型） |
| GET | `/api/relations?type=xxx&value=yyy` | 关联查询（电话/邮箱/法人/股东） |
| GET | `/api/stats/legal_person` | 法人统计 |
| GET | `/api/stats/shareholder` | 股东统计 |
| GET | `/api/stats/industry` | 行业统计 |
| GET | `/api/phone_stats` | 电话重复统计 |
| GET | `/api/tags` | 标签列表 |
| POST | `/api/tags` | 创建标签 |
| PUT | `/api/tags/<id>` | 修改标签 |
| DELETE | `/api/tags/<id>` | 删除标签 |
| POST | `/api/companies/<id>/tags` | 企业添加标签 |
| DELETE | `/api/companies/<id>/tags/<tag_id>` | 企业移除标签 |
| POST | `/api/companies/batch-delete` | 批量删除企业 |
| POST | `/api/companies/batch-add-tag` | 批量添加标签 |

**使用示例**：

```bash
# 企业列表（筛选+分页）
curl "http://127.0.0.1:5210/api/companies?city=杭州市&per_page=10&page=2"

# 企业详情
curl "http://127.0.0.1:5210/api/companies/2"

# 搜索
curl "http://127.0.0.1:5210/api/search?q=科技"

# 关联查询
curl "http://127.0.0.1:5210/api/relations?type=legal_person&value=张三"

# 行业统计
curl "http://127.0.0.1:5210/api/stats/industry?min_count=5"
```

> 详细文档见 [docs/API.md](docs/API.md)

### MCP Server（AI 工具集成）

让 AI 工具（Claude、Cursor 等）能用自然语言查询企业工商信息。

**启动 MCP Server**：

```bash
# 安装依赖（已包含在 requirements.txt）
pip install "mcp[cli]>=1.27,<2"

# 启动服务
python mcp_server.py
```

服务监听 `http://localhost:8000/mcp`

**可用工具**：

| 工具名 | 功能 |
|--------|------|
| `search_companies` | 搜索企业（名称/电话/信用代码/法人/股东/邮箱/网站） |
| `get_company_detail` | 获取企业详情（含关联企业+标签） |
| `find_relations` | 查找关联企业（按电话/邮箱/法人/股东） |
| `get_companies_list` | 企业列表（支持筛选/排序/分页） |
| `get_stats` | 统计查询（法人/股东/行业） |

**使用示例**（在 AI 工具中）：

```
"帮我找所有叫科技的公司"
"查一下 ID 为 2 的企业详情"
"找和张三有关联的企业"
"统计出现 5 次以上的法人"
```

在 AI 工具中配置 MCP Server 地址后即可使用。

## 技术架构

### 存储方案

SQLite，数据库文件位于 `data/enthub.db`。无服务端，备份只需复制文件。

### 数据模型

**companies 表**（34 个字段）

| 字段 | 说明 |
|---|---|
| name / normalized_name | 企业名称 / 规范化名称（去空格、统一括号、小写） |
| phone / normalized_phone | 主电话 / 规范化电话（纯数字、去 +86） |
| other_phone | 其他电话（分号分隔，导入时自动拆分） |
| address / annual_report_address | 注册地址 / 最新年报地址 |
| credit_code | 统一社会信用代码 |
| taxpayer_id / registration_no / org_code | 纳税人识别号 / 注册号 / 组织机构代码 |
| legal_person | 法定代表人 |
| registered_capital / paid_capital | 注册资本 / 实缴资本 |
| established_date / approved_date / business_term | 成立日期 / 核准日期 / 营业期限 |
| province / city / district | 省 / 市 / 区 |
| company_type / industry | 公司类型 / 所属行业 |
| insured_count | 参保人数 |
| business_scope | 经营范围 |
| former_name | 曾用名 |
| website / email / other_email | 网址 / 邮箱 / 其他邮箱 |
| business_status | 经营状态（存续/注销等） |
| status | 记录状态（active/pending/failed） |
| source | 数据来源（manual/import） |
| created_at / updated_at | 创建时间 / 更新时间 |

**company_phones 表**（电话拆分表）

| 字段 | 说明 |
|---|---|
| company_id | 关联企业 ID |
| phone | 原始号码 |
| normalized_phone | 规范化号码 |
| is_primary | 1=主电话，0=其他电话 |

每个号码单独存一行。主电话和其他电话合并拆分，同一公司内去重。

### 去重策略

- 导入时优先按统一社会信用代码去重
- 无信用代码时退回规范化公司名匹配
- 支持批内去重（同一批 Excel 内的重复）

### 电话拆分逻辑

- `other_phone` 字段按 `;` `；` `,` 分隔符拆分
- 主电话和其他电话合并后，同公司内去重
- 手动录入和批量导入都会自动触发拆分（`sync_phones`）
- 座机号码（如 0571-88889999）也会被正确提取和规范化

### 列名映射

导入时自动识别常见列名，支持天眼查等中文导出格式。内置别名表覆盖：

- 企业名称：公司名称、公司、企业名称、name、company
- 电话：电话、联系电话、手机、phone、tel、mobile
- 地址：地址、住址、注册地址、address
- 信用代码：统一社会信用代码、信用代码、credit_code
- 以及其余 22 个字段的中文/英文别名

## 项目结构

```
EntHub/
  EntHub.app/              # macOS 快捷启动（双击启动）
    Contents/
      Info.plist
      MacOS/launch         # 通过 Terminal 调用 start.sh
  data/
    enthub.db              # SQLite 数据库
  templates/               # 页面模板（中文界面）
    base.html              # 导航栏布局
    index.html             # 首页仪表盘
    search_company.html    # 企业搜索
    search_phone.html      # 电话搜索
    stats_phone.html        # 电话重复统计
    company_detail.html    # 企业详情
    add.html               # 手动录入
    import.html            # Excel 上传
    import_preview.html    # 导入去重预览
  static/
    style.css              # 样式
  app.py                   # Flask 主程序
  db.py                    # 数据库层
  utils.py                 # 规范化工具 + 列名映射
  start.sh                 # 一键启动脚本
  requirements.txt         # 依赖：flask, pandas, openpyxl
  venv/                    # Python 虚拟环境
```

## 技术栈

- **后端**：Flask + SQLite + pandas
- **前端**：原生 HTML/CSS + Lucide 图标
- **启动**：macOS .app bundle + bash 脚本

## 当前数据

- 5000 家企业记录（天眼查导出）
- 7004 条电话记录（主电话 + 其他电话拆分）
- 5825 个独立号码
- 34 个工商字段全覆盖

## 后续规划

- **爬虫模块**：对接数据源 API，自动补全"待补全"记录的工商信息
- **MCP 接口**：让 AI 能查询企业数据和电话可信度
- **更多导入源**：适配不同来源的 Excel 列名格式
- **导出功能**：查询结果导回 Excel
