# 关联字段结构设计

**日期**：2026-07-18
**作者**：EntHub 团队
**状态**：待评审

---

## 一、背景与目标

### 1.1 当前现状

EntHub 目前对"电话"字段采用了**双轨存储**模式：

- `companies` 主表存储 `phone`、`normalized_phone`、`other_phone` 字段
- `company_phones` 独立表存储多电话记录（含 `is_primary`、`is_recommended` 标记）

这种双轨设计存在以下问题：

1. **同步成本高**：编辑/导入时需要同时维护主表字段和关联表，容易出现数据不一致
2. **详情页降级逻辑暴露问题**：`company_detail.html` 中存在"无 company_phones 数据时回退到主表 phone 字段"的逻辑，印证了历史不一致问题
3. **模式不统一**：电话有独立表，法人/邮箱/股东/行业等同样需要关联能力的字段没有独立表，无法用统一模式处理

### 1.2 设计目标

1. **统一关联查询模式**：让 6 个字段（电话、标签、法人、邮箱、股东、行业）的关联查询能用一致的 SQL 模板
2. **单一数据源（SSOT）**：避免双轨存储带来的同步问题
3. **基于真实数据特征设计**：不凭空设计字段，依据工商源文件实际数据形态
4. **简化导入逻辑**：字段识别、拆分、合并规则清晰可复用
5. **支持增量合并**：导入重复企业时，新值追加到关联表（去重），不丢失历史数据

---

## 二、真实数据特征分析

通过对 `工商源文件/` 目录下多份 Excel 文件（工商总库 14.15 万行、启信宝格式 546 行、天眼查 27 列等）的实际分析：

| 字段 | 单/多值 | 数据覆盖率 | 实际数据形态 |
|------|---------|---------|-----------|
| 电话 | 多值 | 96.8% 有"其他电话" | 主电话单列 + 其他电话分号分隔 + 启信宝联系电话1~10 多列 + 推荐电话列 |
| 股东 | 多值 | 68.8% 多股东 | 分号分隔的字符串（仅姓名，无出资比例、金额、类型）|
| 标签 | 多值 | 用户自定义 | 已实现独立表 + 字典表（`tags` + `company_tags`）|
| 法人 | 单值 | 100% | "法定代表人"单列，单值 |
| 邮箱 | 单值 | 18.8% 有邮箱 | "邮箱"单列（"-" 表示无），另有"其他邮箱"列不参与关联 |
| 行业 | 单值 | 99.5% 有行业 | "所属行业"单列（无行业代码）|

### 2.1 关键结论

- **真正需要独立表的只有 3 个**：电话、股东、标签（多值字段）
- **单值字段用主表 + normalized + 索引即可**：法人、邮箱、行业
- **股东表结构极简**：实际数据只有姓名，无需出资比例等扩展字段

---

## 三、最终设计方案

### 3.1 方案总览

| 类型 | 字段 | 存储模式 | 导入合并策略 |
|------|------|---------|-----------|
| **A 类（独立表）** | 电话 | `company_phones`（已有）| 增量合并：`merge_phones()` 已实现 |
| **A 类（独立表）** | 标签 | `company_tags` + `tags`（已有）| 已实现 |
| **A 类（独立表）** | 股东 | `company_shareholders`（**新建**）| 增量合并：`merge_shareholders()` 新建 |
| **B 类（主表+索引）** | 法人 | `companies.legal_person` + `normalized_legal_person` | 新值覆盖 |
| **B 类（主表+索引）** | 邮箱 | `companies.email` + `normalized_email` | 新值覆盖 |
| **B 类（主表+索引）** | 行业 | `companies.industry`（已有索引）| 新值覆盖 |
| **特殊** | 其他邮箱 | `companies.other_email`（TEXT 字段保留）| 分号分隔合并 + 去重 |

### 3.2 数据库表结构变更

#### 3.2.1 新建表：`company_shareholders`

```sql
CREATE TABLE IF NOT EXISTS company_shareholders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id         INTEGER NOT NULL,
    name              TEXT NOT NULL,
    normalized_name   TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_csh_norm_name   ON company_shareholders(normalized_name);
CREATE INDEX IF NOT EXISTS idx_csh_company_id  ON company_shareholders(company_id);
```

#### 3.2.2 主表新增字段：`companies`

```sql
ALTER TABLE companies ADD COLUMN normalized_legal_person TEXT;
ALTER TABLE companies ADD COLUMN normalized_email       TEXT;

CREATE INDEX IF NOT EXISTS idx_norm_legal_person ON companies(normalized_legal_person);
CREATE INDEX IF NOT EXISTS idx_norm_email        ON companies(normalized_email);
```

#### 3.2.3 废弃字段（数据迁移后移除）

`companies` 主表中的以下字段在数据迁移到 `company_phones` 后删除：

- `companies.phone`
- `companies.normalized_phone`
- `companies.other_phone`

### 3.3 归一化规则

| 字段 | 归一化函数 | 规则 |
|------|---------|-----|
| 电话 | `normalize_phone()`（已有）| 去非数字字符，去 +86 前缀 |
| 股东姓名 | `normalize_name()`（**新建**）| 去首尾空格，全角转半角，统一大小写 |
| 法人姓名 | `normalize_name()`（同上）| 同上 |
| 邮箱 | `normalize_email()`（**新建**）| 转小写，去前后空格 |
| 行业 | 无需归一化 | 直接字符串匹配 |

`normalize_name()` 函数定义（utils.py 新增）：

```python
def normalize_name(name):
    """Normalize a person/company name: trim, full-width to half-width."""
    if not name:
        return ""
    s = str(name).strip()
    # 全角转半角
    s = s.translate(str.maketrans('　', ' ',
                                    '０１２３４５６７８９'
                                    'ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ'
                                    'ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｺ'))
    return s
```

`normalize_email()` 函数定义（utils.py 新增）：

```python
def normalize_email(email):
    """Normalize email: lowercase and strip."""
    if not email:
        return ""
    return str(email).strip().lower()
```

### 3.4 拆分与合并函数

#### 3.4.1 股东拆分函数 `split_shareholders()`

```python
def split_shareholders(shareholders_str):
    """Split shareholders string into list of (raw, normalized)."""
    if not shareholders_str:
        return []
    parts = str(shareholders_str).replace("；", ";").replace(",", ";").replace("，", ";").split(";")
    result = []
    for p in parts:
        raw = p.strip()
        if raw and raw != "-":
            norm = normalize_name(raw)
            if norm:
                result.append((raw, norm))
    return result
```

#### 3.4.2 股东增量合并函数 `merge_shareholders()`

```python
def merge_shareholders(db, company_id, shareholders_str):
    """Merge new shareholders into existing ones (accumulate, don't replace)."""
    existing = db.execute(
        "SELECT normalized_name FROM company_shareholders WHERE company_id = ?",
        [company_id]
    ).fetchall()
    existing_norms = {row["normalized_name"] for row in existing}
    
    for raw, norm in split_shareholders(shareholders_str):
        if norm and norm not in existing_norms:
            db.execute(
                "INSERT INTO company_shareholders (company_id, name, normalized_name) VALUES (?, ?, ?)",
                [company_id, raw, norm]
            )
            existing_norms.add(norm)
```

#### 3.4.3 股东全量重建函数 `sync_shareholders()`

```python
def sync_shareholders(db, company_id, shareholders_str):
    """Full rebuild: delete all then insert (for edit/create)."""
    db.execute("DELETE FROM company_shareholders WHERE company_id = ?", [company_id])
    for raw, norm in split_shareholders(shareholders_str):
        if norm:
            db.execute(
                "INSERT INTO company_shareholders (company_id, name, normalized_name) VALUES (?, ?, ?)",
                [company_id, raw, norm]
            )
```

### 3.5 关联查询 SQL 模板

#### 3.5.1 多值字段关联查询（独立表）

```sql
-- 模板：找同 X 企业（X 为多值字段）
SELECT DISTINCT c.* 
FROM {relation_table} t1
JOIN {relation_table} t2 ON t1.{normalized_field} = t2.{normalized_field}
JOIN companies c ON t2.company_id = c.id
WHERE t1.company_id = ?   -- 当前企业 ID
  AND t2.company_id != t1.company_id
  AND t1.{normalized_field} IS NOT NULL
  AND t1.{normalized_field} <> ''
```

应用：
- 同电话：`{relation_table}=company_phones`, `{normalized_field}=normalized_phone`
- 同股东：`{relation_table}=company_shareholders`, `{normalized_field}=normalized_name`
- 同标签：`{relation_table}=company_tags JOIN tags`（已有实现）

#### 3.5.2 单值字段关联查询（主表）

```sql
-- 模板：找同 X 企业（X 为单值字段）
SELECT * FROM companies
WHERE {normalized_field} = (
    SELECT {normalized_field} FROM companies WHERE id = ?
)
AND id != ?
AND {normalized_field} IS NOT NULL
AND {normalized_field} <> ''
```

应用：
- 同法人：`{normalized_field}=normalized_legal_person`
- 同邮箱：`{normalized_field}=normalized_email`
- 同行业：`{normalized_field}=industry`（无需 normalized，直接字符串匹配）

### 3.6 数据导出（Excel）

通过 SQL VIEW 封装导出逻辑，对外保持"一行一企业"的 Excel 友好格式：

```sql
CREATE VIEW company_export AS
SELECT 
    c.id, c.name, c.credit_code, c.address, c.legal_person,
    c.email, c.industry, c.other_email,
    -- 其他主表字段...
    
    -- 多值字段用 group_concat 拼接
    (SELECT group_concat(phone, '; ') 
     FROM company_phones p 
     WHERE p.company_id = c.id 
     ORDER BY p.is_primary DESC, p.is_recommended DESC) AS phone,
    
    (SELECT group_concat(name, '; ') 
     FROM company_shareholders s 
     WHERE s.company_id = c.id) AS shareholders,
    
    (SELECT group_concat(t.name, '; ') 
     FROM company_tags ct 
     JOIN tags t ON ct.tag_id = t.id 
     WHERE ct.company_id = c.id) AS tags
    
FROM companies c;
```

导出代码：

```python
import pandas as pd
df = pd.read_sql("SELECT * FROM company_export", conn)
df.to_excel("export.xlsx", index=False)
```

---

## 四、数据迁移方案

### 4.1 迁移步骤

1. **备份数据库**：`cp data/enthub.db data/enthub.db.backup.YYYYMMDD`
2. **新增表和字段**：执行 schema 变更
3. **回填 normalized 字段**：
   ```sql
   UPDATE companies SET normalized_legal_person = ... WHERE legal_person IS NOT NULL;
   UPDATE companies SET normalized_email = ... WHERE email IS NOT NULL;
   ```
   （通过 Python 脚本调用 normalize 函数后 UPDATE）
4. **迁移股东数据**：遍历 companies，对每行的 `shareholders` 字段调用 `sync_shareholders()`
5. **删除废弃字段**（可选，建议保留观察期 1-2 周）：
   ```sql
   -- 验证 company_phones 数据完整后执行
   ALTER TABLE companies DROP COLUMN phone;
   ALTER TABLE companies DROP COLUMN normalized_phone;
   ALTER TABLE companies DROP COLUMN other_phone;
   ```
6. **创建导出 VIEW**：执行 `CREATE VIEW company_export AS ...`

### 4.2 迁移验证

迁移后进行以下检查：
- [ ] `SELECT COUNT(*) FROM company_phones` 与原主表电话总数一致
- [ ] `SELECT COUNT(*) FROM company_shareholders` 与原股东总数合理（拆分后变多）
- [ ] 详情页展示无降级回退（不再走主表 phone 字段）
- [ ] 导出 Excel 列数和格式与原导入文件对应

---

## 五、影响范围与代码改动

### 5.1 数据库层（db.py）

- ✅ 新增 `company_shareholders` 表
- ✅ 新增 `companies.normalized_legal_person` 列
- ✅ 新增 `companies.normalized_email` 列
- ✅ 新增对应索引
- ✅ 移除电话相关字段（迁移后）

### 5.2 工具层（utils.py）

- ✅ 新增 `normalize_name()` 函数
- ✅ 新增 `normalize_email()` 函数
- ✅ 扩展 `COLUMN_ALIASES`：
  - 添加"邮箱（工商信息）"、"邮箱（企业认证信息）" → `email`
  - 添加"其他邮箱" → `other_email`

### 5.3 应用层（app.py）

#### 新增函数
- ✅ `split_shareholders()` — 股东拆分
- ✅ `merge_shareholders()` — 股东增量合并（导入用）
- ✅ `sync_shareholders()` — 股东全量重建（编辑用）

#### 修改函数
- ✅ `index()` — 首页统计补充独立电话数（已有）、股东总数
- ✅ `company_detail()` — 详情页股东关联区块
- ✅ `search()` — 支持股东、邮箱、法人搜索（如需）
- ✅ `edit_company()` / `add_company()` — 调用 `sync_shareholders()`、维护 normalized_legal_person/email
- ✅ `_import_worker()` — 导入流程增加股东处理、单值字段覆盖逻辑
- ✅ `delete_company()` — 级联删除 `company_shareholders`

#### 新增/修改路由
- ✅ `/stats/shareholder` — 新建股东重复统计页（参考 `/stats/phone` 模板）
- ✅ `/browse/relation-groups?dup_type=shareholder` — 关联发现页新增股东 Tab
- ✅ `/browse/relation-groups?dup_type=legal_person` — 关联发现页新增法人 Tab（基于主表）
- ✅ `/browse/relation-groups?dup_type=email` — 关联发现页新增邮箱 Tab（基于主表）
- ✅ `/browse/relation-top` — 扩展为多维度 TOP 50（当前只算电话，需补全）

### 5.4 模板层（templates/）

#### 新增模板
- ✅ `stats_shareholder.html` — 股东重复统计页（参考 `stats_phone.html`）
- ✅ `_stats_legal_person.html`、`_stats_email.html`、`_stats_industry.html` — 单值字段统计片段（可选，复用模板）

#### 修改模板
- ✅ `relation_discovery.html` — 增加 4 个 Tab（电话/股东/法人/邮箱/行业/标签）
- ✅ `company_detail.html` — 增加"同股东"区块，移除电话降级逻辑
- ✅ `base.html` — 调整统计页导航（视情况）

### 5.5 备份与状态栏

- ✅ `backup.py` / `backup.html` — 备份页统计补充股东总数
- ✅ `menubar.py` — 状态栏菜单视情况增加股东统计入口

---

## 六、关键决策记录

| # | 决策点 | 选择 | 理由 |
|---|-------|------|------|
| 1 | 多值字段存储 | 独立关联表 | 多值必须独立表，主表无法高效查询 |
| 2 | 单值字段存储 | 主表 + normalized + 索引 | 单值不需要独立表，避免过度设计 |
| 3 | 单值字段冲突策略 | 新值覆盖 | 用户确认，无历史追溯需求 |
| 4 | 邮箱是否多值 | 单值 | 用户确认，"其他邮箱"保留但不参与关联 |
| 5 | 股东表是否扩展 | 极简结构 | 真实数据仅含姓名，无出资比例 |
| 6 | 主表电话字段 | 迁移后删除 | 单一数据源，消除双轨 |
| 7 | 导出方案 | SQL VIEW 封装 | 对外保持"一行一企业"，导出代码简洁 |

---

## 七、风险与缓解

### 7.1 数据迁移风险

**风险**：删除主表电话字段后，发现 `company_phones` 数据不完整
**缓解**：
- 迁移后保留旧字段观察 1-2 周
- 提供回滚脚本（从主表恢复到 `company_phones`）

### 7.2 关联查询性能

**风险**：主表 `normalized_legal_person` 索引查询慢（14 万行）
**缓解**：
- 已加索引，SQLite 单机场景下毫秒级
- 若后续数据量增长，可考虑加 `collate nocase` 提升大小写不敏感查询

### 7.3 股东姓名歧义

**风险**：同名不同人（"张三" 在多家企业当股东，但实际是不同人）
**缓解**：
- 关联查询结果作为"线索"，由用户人工判断
- 在详情页标注"以下为同名股东企业，请人工核实"

### 7.4 单值字段覆盖丢数据

**风险**：法人变更后，旧法人信息丢失
**缓解**：
- 用户已确认无历史追溯需求
- 工商源文件本身只提供当前法人（非历史）
- 如未来需要，可扩展为 `legal_person_history` 表

---

## 八、不在本方案范围内的事项

以下事项不在本次设计范围，未来需要时再单独设计：

1. **历史法人追溯**（需新建 `legal_person_history` 表）
2. **行业代码标准化**（如国民经济行业分类 GB/T 4754）
3. **股东结构化扩展**（出资比例、股东类型、出资金额）
4. **地址关联**（同地址企业查询，地址规范化难度高）
5. **关联图谱可视化**（节点关系图、关系强度计算）

---

## 九、下一步

本设计文档评审通过后，将进入实施阶段：

1. 使用 `writing-plans` skill 制定详细实施计划
2. 按计划分阶段实施（建议顺序：schema → 工具函数 → 主表字段 → 股东表 → 关联查询 → 统计页面 → 数据迁移 → 测试）
3. 每个阶段完成后进行验证

---

## 附录 A：当前数据库结构（参考）

详见 `db.py` 的 `init_db()` 函数（L35-167）。

## 附录 B：工商源文件字段映射参考

详见 `utils.py` 的 `COLUMN_ALIASES` 常量（L63-68）和 `map_columns()` 函数。
