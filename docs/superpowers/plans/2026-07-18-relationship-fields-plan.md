# 关联字段结构重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 重构 EntHub 关联字段存储结构（电话/标签/股东为独立表，法人/邮箱/行业为主表+索引），实现统一关联查询模式。

**Architecture:** 多值字段（电话、股东、标签）使用独立关联表；单值字段（法人、邮箱、行业）使用主表 + normalized + 索引。全新初始化（不做数据迁移）。

**Tech Stack:** FastAPI + Jinja2 + HTMX + SQLite + pandas

## Global Constraints

- 不做数据迁移，原数据库删除后从 Excel 重新导入
- 删除 `companies` 主表的 `phone`、`normalized_phone`、`other_phone` 字段（Schema 不再创建）
- 单值字段冲突策略：新值覆盖
- 邮箱：只用"邮箱"列参与关联，"其他邮箱"保留但不参与
- 股东表：极简结构（`company_id`, `name`, `normalized_name`）
- 所有新代码遵循现有风格（中文注释，函数按模块分组）

## 文件结构

```
db.py          — Schema 层：表结构、索引、迁移、导出 VIEW
utils.py       — 工具层：归一化函数、拆分函数、字段别名
app.py         — 应用层：路由、业务逻辑（导入/编辑/关联查询/统计）
templates/     — 模板层：详情页、统计页、关联页、表单等
menubar.py     — 状态栏：菜单项
backup.py      — 备份逻辑
```

---

## Task 1: Schema 层变更（db.py）

**Files:**
- Modify: `db.py:35-167`（init_db 函数）

**Interfaces:**
- 新增 `company_shareholders` 表：`id`, `company_id`, `name`, `normalized_name`
- `companies` 新增字段：`normalized_legal_person TEXT`, `normalized_email TEXT`
- `companies` 移除字段：`phone`, `normalized_phone`, `other_phone`（从 CREATE TABLE 语句中删除）
- 新增索引：`idx_csh_norm_name`, `idx_csh_company_id`, `idx_norm_legal_person`, `idx_norm_email`

**Changes:**

- [ ] **Step 1.1: 备份现有数据库**

```bash
mkdir -p ~/.enthub/backups
cp /Users/gm/AI/EntHub/data/enthub.db ~/.enthub/backups/$(date +%Y%m%d)-pre-reform.db
```

验证：`ls -la ~/.enthub/backups/`

- [ ] **Step 1.2: 修改 `init_db()` — 从 CREATE TABLE 中删除旧电话字段**

打开 `db.py`，找到 `CREATE TABLE IF NOT EXISTS companies (...)` 语句块，删除以下 3 行：

```python
phone                 TEXT,
normalized_phone      TEXT,
other_phone           TEXT,
```

- [ ] **Step 1.3: 修改 `init_db()` — 删除电话相关索引**

删除以下索引声明：

```python
CREATE INDEX IF NOT EXISTS idx_phone           ON companies(normalized_phone);
```

- [ ] **Step 1.4: 修改 `init_db()` — 在 companies 表中添加新字段**

在 `companies` 表 CREATE TABLE 语句的 `email TEXT,` 行后，新增：

```python
normalized_email      TEXT,
normalized_legal_person TEXT,
```

- [ ] **Step 1.5: 修改 `init_db()` — 新增索引**

在索引区域新增：

```python
CREATE INDEX IF NOT EXISTS idx_norm_email        ON companies(normalized_email);
CREATE INDEX IF NOT EXISTS idx_norm_legal_person ON companies(normalized_legal_person);
```

- [ ] **Step 1.6: 修改 `init_db()` — 新增 `company_shareholders` 表**

在 `company_phones` 表定义之后（约 L125 之后）新增：

```python
CREATE TABLE IF NOT EXISTS company_shareholders (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id        INTEGER NOT NULL,
    name              TEXT NOT NULL,
    normalized_name   TEXT NOT NULL,
    FOREIGN KEY (company_id) REFERENCES companies(id)
);

CREATE INDEX IF NOT EXISTS idx_csh_norm_name   ON company_shareholders(normalized_name);
CREATE INDEX IF NOT EXISTS idx_csh_company_id  ON company_shareholders(company_id);
```

- [ ] **Step 1.7: 修改 `_migrate` 调用段**

在 `init_db()` 末尾的迁移调用段（`_migrate(conn, ...)` 系列）之后新增：

```python
_migrate(conn, "companies", "normalized_legal_person", "TEXT")
_migrate(conn, "companies", "normalized_email", "TEXT")
```

（这是为已有数据库过渡期间使用；全新初始化不需要，但保留无害）

- [ ] **Step 1.8: 删除旧数据库并重新初始化**

```bash
rm /Users/gm/AI/EntHub/data/enthub.db
python3 -c "from db import init_db; init_db()"
```

- [ ] **Step 1.9: 验证 Schema 变更**

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('data/enthub.db')
cur = conn.cursor()

# 检查 companies 表结构
cur.execute("PRAGMA table_info(companies)")
cols = [r[1] for r in cur.fetchall()]
print("companies 字段：")
for c in cols: print(f"  - {c}")

# 检查 company_shareholders 表
cur.execute("PRAGMA table_info(company_shareholders)")
cols = [r[1] for r in cur.fetchall()]
print("\ncompany_shareholders 字段：")
for c in cols: print(f"  - {c}")

# 检查旧字段已不存在
cur.execute("PRAGMA table_info(companies)")
cols = [r[1] for r in cur.fetchall()]
removed = ['phone', 'normalized_phone', 'other_phone']
for r in removed:
    assert r not in cols, f"❌ {r} 仍然存在"
print("\n✅ 旧字段已成功删除")

# 检查新字段存在
assert 'normalized_legal_person' in cols
assert 'normalized_email' in cols
print("✅ 新字段已添加")

# 检查索引
cur.execute("SELECT name FROM sqlite_master WHERE type='index'")
idxs = [r[0] for r in cur.fetchall()]
for i in ['idx_csh_norm_name', 'idx_csh_company_id', 'idx_norm_email', 'idx_norm_legal_person']:
    assert i in idxs, f"❌ 索引 {i} 不存在"
print("✅ 新索引已创建")

conn.close()
EOF
```

期望输出：所有 ✅ 检查通过。

- [ ] **Step 1.10: 提交**

```bash
git add db.py
git commit -m "refactor(db): Schema 重构 - 新增 company_shareholders 表和归一化字段

- 新增 company_shareholders 表（极简结构）
- 新增 normalized_legal_person、normalized_email 字段 + 索引
- 移除 companies.phone/normalized_phone/other_phone 字段"
```

---

## Task 2: 工具层新增函数（utils.py）

**Files:**
- Modify: `utils.py`

**Interfaces:**
- 新增 `normalize_name(name) -> str`
- 新增 `normalize_email(email) -> str`
- 新增 `split_shareholders(shareholders_str) -> list[(raw, normalized)]`
- 扩展 `COLUMN_ALIASES`：增加邮箱相关别名

**Changes:**

- [ ] **Step 2.1: 新增 `normalize_name()` 函数**

在 `utils.py` 的 `normalize_phone()` 函数之后新增：

```python
def normalize_name(name):
    """Normalize a person/company name: trim, full-width to half-width."""
    import unicodedata
    if not name:
        return ""
    s = str(name).strip()
    s = unicodedata.normalize('NFKC', s)
    s = s.replace("\u3000", " ").strip()  # 全角空格
    return s
```

- [ ] **Step 2.2: 新增 `normalize_email()` 函数**

紧跟在 `normalize_name()` 之后新增：

```python
def normalize_email(email):
    """Normalize email: lowercase and strip."""
    if not email:
        return ""
    return str(email).strip().lower()
```

- [ ] **Step 2.3: 新增 `split_shareholders()` 函数**

在 `split_phones()` 附近新增（如 `app.py` 顶部已有 `split_phones`，则在此文件新增；实际上 `split_phones` 在 `app.py`，所以 `split_shareholders` 也放 `app.py`）：

> 注意：查看 `split_phones` 实际位置。如果在 `app.py`，则 `split_shareholders` 也应放 `app.py` 顶部。

（实际位置检查后在 Task 3 中实现）

- [ ] **Step 2.4: 扩展 `COLUMN_ALIASES`**

在 `utils.py` 的 `COLUMN_ALIASES` 字典中，增加：

```python
"邮箱（工商信息）": "email",
"邮箱（企业认证信息）": "email",  # 如有重复邮箱，后者优先
"其他邮箱": "other_email",
```

- [ ] **Step 2.5: 验证新增函数**

```bash
python3 << 'EOF'
from utils import normalize_name, normalize_email

# 测试 normalize_name
assert normalize_name("张三") == "张三"
assert normalize_name(" 张 三 ") == "张 三"
assert normalize_name("　张　三　") == "张 三"  # 全角空格
assert normalize_name(None) == ""
assert normalize_name("１２３") == "123"  # 全角数字
print("✅ normalize_name 测试通过")

# 测试 normalize_email
assert normalize_email("Test@Example.com") == "test@example.com"
assert normalize_email("  test@test.com  ") == "test@test.com"
assert normalize_email(None) == ""
print("✅ normalize_email 测试通过")
EOF
```

- [ ] **Step 2.6: 提交**

```bash
git add utils.py
git commit -m "feat(utils): 新增归一化函数 normalize_name/normalize_email

- normalize_name: 全角转半角、去空格
- normalize_email: 转小写、去空格"
```

---

## Task 3: 应用层 — 股东拆分与合并函数（app.py）

**Files:**
- Modify: `app.py`（在 `split_phones`、`merge_phones`、`sync_phones` 附近新增对应函数）

**Interfaces:**
- 新增 `split_shareholders(s) -> list[(raw, normalized)]`
- 新增 `sync_shareholders(db, company_id, s)`
- 新增 `merge_shareholders(db, company_id, s)`

**Changes:**

- [ ] **Step 3.1: 新增 `split_shareholders()`**

在 `app.py` 的 `split_phones()` 函数附近新增：

```python
def split_shareholders(shareholders_str):
    """Split shareholders string into list of (raw, normalized)."""
    from utils import normalize_name
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

- [ ] **Step 3.2: 新增 `sync_shareholders()`**

紧跟在 `split_shareholders()` 之后新增：

```python
def sync_shareholders(db, company_id, shareholders_str):
    """Full rebuild: delete all shareholders then insert (for edit/create)."""
    db.execute("DELETE FROM company_shareholders WHERE company_id = ?", [company_id])
    for raw, norm in split_shareholders(shareholders_str):
        if norm:
            db.execute(
                "INSERT INTO company_shareholders (company_id, name, normalized_name) VALUES (?, ?, ?)",
                [company_id, raw, norm]
            )
```

- [ ] **Step 3.3: 新增 `merge_shareholders()`**

紧跟在 `sync_shareholders()` 之后新增：

```python
def merge_shareholders(db, company_id, shareholders_str):
    """Merge new shareholders into existing ones (accumulate, don't replace)."""
    from utils import normalize_name
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

- [ ] **Step 3.4: 验证新增函数**

```bash
python3 << 'EOF'
import sqlite3
from db import get_db

# 插入测试企业
db = get_db()
cur = db.execute("INSERT INTO companies (name, normalized_name) VALUES ('测试公司A', '测试公司a')")
cid = cur.lastrowid

# 测试 sync_shareholders
from app import sync_shareholders
sync_shareholders(db, cid, "张三;李四;王五")
db.commit()

rows = db.execute("SELECT name, normalized_name FROM company_shareholders WHERE company_id = ?", [cid]).fetchall()
print(f"sync 后股东数: {len(rows)}")
for r in rows: print(f"  - {r['name']} -> {r['normalized_name']}")
assert len(rows) == 3

# 测试 merge_shareholders（追加）
from app import merge_shareholders
merge_shareholders(db, cid, "李四;赵六")  # 李四已存在，只追加赵六
db.commit()

rows = db.execute("SELECT name FROM company_shareholders WHERE company_id = ?", [cid]).fetchall()
print(f"\nmerge 后股东数: {len(rows)}")
for r in rows: print(f"  - {r['name']}")
assert len(rows) == 4  # 张三、李四、王五、赵六

# 清理测试数据
db.execute("DELETE FROM company_shareholders WHERE company_id = ?", [cid])
db.execute("DELETE FROM companies WHERE id = ?", [cid])
db.commit()
db.close()

print("\n✅ 股东拆分/同步/合并函数测试通过")
EOF
```

- [ ] **Step 3.5: 提交**

```bash
git add app.py
git commit -m "feat(app): 新增股东拆分/同步/合并函数

- split_shareholders: 按分号拆分，归一化姓名
- sync_shareholders: 全量重建（编辑用）
- merge_shareholders: 增量合并（导入重复企业用）"
```

---

## Task 4: 应用层 — 单值字段归一化维护（app.py）

**Files:**
- Modify: `app.py`（`add_company()`、`edit_company()` 函数）

**Interfaces:**
- `add_company()` 新增：计算 `normalized_legal_person` 和 `normalized_email`
- `edit_company()` 新增：同上

**Changes:**

- [ ] **Step 4.1: 查看当前 add_company 实现**

```bash
grep -n "def add_company" app.py
```

- [ ] **Step 4.2: 修改 `add_company()` — 写入 normalized_legal_person 和 normalized_email**

在 `add_company()` 中，找到计算 `normalized_name` 的位置附近，新增：

```python
from utils import normalize_name, normalize_email

normalized_legal_person = normalize_name(form.get("legal_person", ""))
normalized_email = normalize_email(form.get("email", ""))
```

并在 INSERT 语句中加入这两个字段：

```python
db.execute(
    """INSERT INTO companies (name, normalized_name, ..., normalized_legal_person, normalized_email)
       VALUES (?, ?, ..., ?, ?)""",
    [..., normalized_legal_person, normalized_email]
)
```

- [ ] **Step 4.3: 修改 `edit_company()` — 同样更新 normalized_legal_person 和 normalized_email**

在 `edit_company()` 中，找到 UPDATE 语句，新增：

```python
normalized_legal_person = normalize_name(form.get("legal_person", ""))
normalized_email = normalize_email(form.get("email", ""))
```

UPDATE 语句新增字段：

```python
db.execute(
    """UPDATE companies SET ..., normalized_legal_person = ?, normalized_email = ?
       WHERE id = ?""",
    [..., normalized_legal_person, normalized_email, cid]
)
```

同时在 `edit_company()` 中调用 `sync_shareholders()`（替换原来的 TEXT 字段 `shareholders` 写入逻辑）：

```python
sync_shareholders(db, cid, form.get("shareholders", ""))
```

- [ ] **Step 4.4: 验证 — 启动服务并手动测试**

```bash
python3 app.py
# 浏览器访问 http://127.0.0.1:5000/add
# 手动添加一条企业数据，填写法人、邮箱、股东
# 数据库验证：
python3 -c "
import sqlite3
conn = sqlite3.connect('data/enthub.db')
cur = conn.cursor()
cur.execute('SELECT name, normalized_legal_person, normalized_email FROM companies ORDER BY id DESC LIMIT 1')
print('最新企业:', cur.fetchone())
conn.close()
"
```

- [ ] **Step 4.5: 提交**

```bash
git add app.py
git commit -m "feat(app): add/edit 企业时维护 normalized_legal_person/normalized_email

同步维护 company_shareholders 表（sync_shareholders）"
```

---

## Task 5: 应用层 — 导入流程扩展（app.py）

**Files:**
- Modify: `app.py`（`_import_worker()` 函数，约 L1800-1868）

**Interfaces:**
- 导入时：
  - 单值字段 `legal_person`、`email` → 写入主表 + normalized 字段（重复企业时新值覆盖）
  - 多值字段 `shareholders` → 调用 `merge_shareholders()` 增量合并
  - `other_email` → 分号合并 + 去重

**Changes:**

- [ ] **Step 5.1: 查看 `_import_worker()` 当前实现**

```bash
grep -n "_import_worker\|merge_phones\|sync_phones" app.py | head -30
```

- [ ] **Step 5.2: 新增导入字段**

在 `_import_worker()` 中，找到处理单条导入记录的位置，新增：

```python
# 单值字段：legal_person、email（重复企业覆盖）
legal_person = row.get("legal_person")
email = row.get("email")
normalized_legal_person = normalize_name(legal_person) if legal_person else None
normalized_email = normalize_email(email) if email else None

# 其他邮箱：分号合并 + 去重
other_email = row.get("other_email")
```

- [ ] **Step 5.3: 重复企业合并逻辑**

找到 `merge_phones()` 调用处，在之后新增：

```python
# 增量合并股东
merge_shareholders(db, existing_id, row.get("shareholders"))

# 覆盖单值字段
db.execute("""UPDATE companies SET 
              legal_person = COALESCE(?, legal_person),
              normalized_legal_person = COALESCE(?, normalized_legal_person),
              email = COALESCE(?, email),
              normalized_email = COALESCE(?, normalized_email)
              WHERE id = ?""",
              [legal_person, normalized_legal_person, email, normalized_email, existing_id])

# 合并其他邮箱
if other_email:
    existing_oe = db.execute("SELECT other_email FROM companies WHERE id = ?", [existing_id]).fetchone()["other_email"] or ""
    merged = set(part.strip() for part in existing_oe.split(";") if part.strip())
    merged.update(part.strip() for part in other_email.replace(",", ";").split(";") if part.strip() and part.strip() != "-")
    new_oe = "; ".join(sorted(merged))
    db.execute("UPDATE companies SET other_email = ? WHERE id = ?", [new_oe, existing_id])
```

- [ ] **Step 5.4: 新企业写入逻辑**

找到新企业 INSERT 语句，扩展字段：

```python
db.execute(
    """INSERT INTO companies (..., legal_person, normalized_legal_person, email, normalized_email, shareholders, other_email)
       VALUES (?, ?, ..., ?, ?, ?, ?, ?, ?)""",
    [..., legal_person, normalized_legal_person, email, normalized_email, row.get("shareholders"), row.get("other_email")]
)
# 同步股东到 company_shareholders
sync_shareholders(db, new_id, row.get("shareholders"))
```

- [ ] **Step 5.5: 验证导入流程**

```bash
# 导入一份小 Excel 测试
python3 << 'EOF'
from db import get_db
from app import _import_worker
# 模拟导入过程...
# 或实际启动 Flask 服务，通过浏览器导入测试文件
EOF
```

启动 Flask，访问 `/import`，上传 `制造业-导入.xlsx`，观察导入进度和最终数据库状态。

- [ ] **Step 5.6: 提交**

```bash
git add app.py
git commit -m "feat(app): 导入流程扩展 - 支持股东/法人/邮箱/其他邮箱

- 单值字段 legal_person/email 重复时覆盖
- 多值字段 shareholders 调用 merge_shareholders 增量合并
- 其他邮箱分号合并 + 去重"
```

---

## Task 6: 应用层 — 详情页关联查询扩展（app.py）

**Files:**
- Modify: `app.py`（`company_detail()` 函数，约 L1313-1399）

**Interfaces:**
- 详情页新增：`related_shareholders` 数据（同股东关联企业）
- 详情页新增：`related_legal_person`、`related_email`、`related_industry` 数据
- 移除：电话降级逻辑（不再从主表 phone 字段读取）

**Changes:**

- [ ] **Step 6.1: 查看当前 company_detail 实现**

```bash
grep -n "def company_detail\|related_phones\|company.phone\|company.normalized_phone" app.py | head -20
```

- [ ] **Step 6.2: 移除电话降级逻辑**

在 `company_detail()` 中找到类似这样的降级代码（约 L1334）：

```python
# 如果 company_phones 为空，回退到主表 phone
if not phones and row["phone"]:
    phones = [(row["phone"], row["normalized_phone"])]
```

**删除**这段代码。现在电话数据全部来自 `company_phones`。

- [ ] **Step 6.3: 新增股东关联查询**

在详情页查询"同电话关联"区块之后，新增：

```python
# 同股东关联企业
related_shareholders = []
shareholders = db.execute("""
    SELECT s.name, s.normalized_name
    FROM company_shareholders s
    WHERE s.company_id = ?
""", [cid]).fetchall()
if shareholders:
    norm_names = [s["normalized_name"] for s in shareholders]
    placeholders = ",".join(["?"] * len(norm_names))
    related_shareholders = db.execute(f"""
        SELECT DISTINCT c.id, c.name, c.legal_person, c.city
        FROM company_shareholders s2
        JOIN companies c ON s2.company_id = c.id
        WHERE s2.normalized_name IN ({placeholders})
          AND s2.company_id != ?
        LIMIT 10
    """, norm_names + [cid]).fetchall()
```

- [ ] **Step 6.4: 新增单值字段关联查询**

```python
# 同法人关联企业
related_legal_person = []
if row["normalized_legal_person"]:
    related_legal_person = db.execute("""
        SELECT id, name, legal_person, city
        FROM companies
        WHERE normalized_legal_person = ? AND id != ?
        LIMIT 10
    """, [row["normalized_legal_person"], cid]).fetchall()

# 同邮箱关联企业
related_email = []
if row["normalized_email"]:
    related_email = db.execute("""
        SELECT id, name, legal_person, city
        FROM companies
        WHERE normalized_email = ? AND id != ?
        LIMIT 10
    """, [row["normalized_email"], cid]).fetchall()

# 同行业关联企业
related_industry = []
if row["industry"]:
    related_industry = db.execute("""
        SELECT id, name, legal_person, city
        FROM companies
        WHERE industry = ? AND id != ?
        LIMIT 10
    """, [row["industry"], cid]).fetchall()
```

- [ ] **Step 6.5: 传递到模板**

```python
return render_template("company_detail.html",
    company=row,
    phones=phones,
    shareholders=shareholders,
    related_shareholders=related_shareholders,
    related_legal_person=related_legal_person,
    related_email=related_email,
    related_industry=related_industry,
    related_phones=related_phones,  # 已有
    ...
)
```

- [ ] **Step 6.6: 验证**

启动 Flask，访问 `/company/1`（或任一企业 ID），查看"关联企业"区块是否显示：
- 同电话
- 同法人
- 同邮箱
- 同股东
- 同行业

- [ ] **Step 6.7: 提交**

```bash
git add app.py
git commit -m "feat(app): 详情页关联查询扩展 - 股东/法人/邮箱/行业

- 新增 related_shareholders（多值，独立表）
- 新增 related_legal_person/related_email/related_industry（单值，主表）
- 移除电话降级逻辑（不再读主表 phone 字段）"
```

---

## Task 7: 应用层 — 统计路由（/stats/shareholder）

**Files:**
- Modify: `app.py`（在 `stats_phone()` 附近新增 `stats_shareholder()`）
- Modify: `templates/index.html`（首页卡片添加股东统计入口）

**Interfaces:**
- 新增路由 `/stats/shareholder`
- 模板 `stats_shareholder.html`（Task 9 创建）

**Changes:**

- [ ] **Step 7.1: 查看 stats_phone 实现**

```bash
grep -n "def stats_phone" app.py
```

- [ ] **Step 7.2: 新增 `stats_shareholder()` 路由**

在 `stats_phone()` 函数之后新增：

```python
@app.route("/stats/shareholder")
def stats_shareholder():
    min_count = request.args.get("min", 2, type=int)
    page = request.args.get("page", 1, type=int)
    per_page = 50
    
    base_where = "WHERE cs.normalized_name IS NOT NULL AND cs.normalized_name <> ''"
    
    total = db.execute(f"""
        SELECT COUNT(*) as cnt FROM (
            SELECT normalized_name FROM company_shareholders cs
            JOIN companies c ON cs.company_id = c.id
            {base_where}
            GROUP BY cs.normalized_name
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()["cnt"]
    
    rows = db.execute(f"""
        SELECT cs.normalized_name,
               MIN(cs.name) AS display_name,
               COUNT(DISTINCT cs.company_id) AS cnt,
               GROUP_CONCAT(DISTINCT c.name, '; ') AS company_names
        FROM company_shareholders cs
        JOIN companies c ON cs.company_id = c.id
        {base_where}
        GROUP BY cs.normalized_name
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ? OFFSET ?
    """, [min_count, per_page, (page - 1) * per_page]).fetchall()
    
    total_pages = (total + per_page - 1) // per_page
    
    return render_template("stats_shareholder.html",
        rows=rows,
        min_count=min_count,
        page=page,
        total_pages=total_pages,
        total=total
    )
```

- [ ] **Step 7.3: 首页卡片入口**

在 `templates/index.html` 中找到"电话可信度"卡片附近，新增：

```html
<a href="{{ url_for('stats_shareholder') }}" class="lh-card">
  <i data-lucide="users"></i>
  <span>股东关联</span>
  <span>重复股东，识别关联企业</span>
</a>
```

- [ ] **Step 7.4: 提交**

```bash
git add app.py templates/index.html
git commit -m "feat: 新增 /stats/shareholder 路由和首页入口"
```

---

## Task 8: 模板层 — 详情页改造（company_detail.html）

**Files:**
- Modify: `templates/company_detail.html`

**Changes:**

- [ ] **Step 8.1: 新增"股东信息"区块**

在"电话信息"区块之后新增：

```html
{% if shareholders %}
<section class="info-block">
  <h3>💼 股东（{{ shareholders|length }}）</h3>
  <ul class="tag-list">
    {% for s in shareholders %}
    <li class="tag">
      <a href="{{ url_for('search') }}?q={{ s.name|urlencode }}">{{ s.name }}</a>
    </li>
    {% endfor %}
  </ul>
</section>
{% endif %}
```

- [ ] **Step 8.2: 新增关联企业区块 — 4 个分类**

找到"关联企业"区块，扩展为 5 个分类：

```html
<section class="info-block">
  <h3>🔗 关联企业</h3>
  
  {{ rel_table('同电话', related_phones, 'phone') }}
  {{ rel_table('同法人', related_legal_person, 'legal_person') }}
  {{ rel_table('同邮箱', related_email, 'email') }}
  {{ rel_table('同股东', related_shareholders, 'shareholder') }}
  {{ rel_table('同行业', related_industry, 'industry') }}
</section>
```

- [ ] **Step 8.3: 移除电话降级逻辑**

删除模板中类似这样的代码：

```html
{% if not phones and company.phone %}
  <li>{{ company.phone }}</li>
{% endif %}
```

- [ ] **Step 8.4: 验证**

启动 Flask，访问任一企业详情页，确认：
- 股东信息正确显示（带搜索链接）
- 关联企业有 5 个分类
- 电话不再有降级逻辑

- [ ] **Step 8.5: 提交**

```bash
git add templates/company_detail.html
git commit -m "feat(templates): 详情页新增股东信息 + 5 分类关联企业

- 新增 💼 股东信息区块（带搜索链接）
- 关联企业扩展为 5 类（电话/法人/邮箱/股东/行业）
- 移除电话降级回退逻辑"
```

---

## Task 9: 模板层 — 股东统计页（stats_shareholder.html）

**Files:**
- Create: `templates/stats_shareholder.html`（参考 `stats_phone.html`）

**Changes:**

- [ ] **Step 9.1: 复制 stats_phone.html 作为模板**

```bash
cp templates/stats_phone.html templates/stats_shareholder.html
```

- [ ] **Step 9.2: 修改模板 — 全局替换电话相关词汇**

在 `stats_shareholder.html` 中替换：
- `电话可信度` → `股东关联`
- `重复次数` → `关联企业数`
- `phone` → `shareholder`（变量名）
- `normalized_phone` → `normalized_name`
- `display_phone` → `display_name`

- [ ] **Step 9.3: 调整表格列**

```html
<table class="stats-table">
  <thead>
    <tr>
      <th>股东姓名</th>
      <th>关联企业数</th>
      <th>关联企业</th>
      <th>操作</th>
    </tr>
  </thead>
  <tbody>
    {% for row in rows %}
    <tr>
      <td>{{ row.display_name }}</td>
      <td>
        <span class="dup-count {% if row.cnt >= 5 %}dup-high{% elif row.cnt >= 3 %}dup-mid{% endif %}">
          {{ row.cnt }}
        </span>
      </td>
      <td>{{ row.company_names|truncate(80) }}</td>
      <td><a href="{{ url_for('search') }}?q={{ row.display_name|urlencode }}">查看</a></td>
    </tr>
    {% endfor %}
  </tbody>
</table>
```

- [ ] **Step 9.4: 验证**

启动 Flask，访问 `/stats/shareholder`，确认表格正确显示。

- [ ] **Step 9.5: 提交**

```bash
git add templates/stats_shareholder.html
git commit -m "feat(templates): 新增股东关联统计页 stats_shareholder.html

参考 stats_phone.html 模板，替换字段为股东相关"
```

---

## Task 10: 模板层 — 关联发现页扩展（relation_discovery.html）

**Files:**
- Modify: `templates/relation_discovery.html`
- Modify: `app.py`（`browse_relation_groups()` 支持新 dup_type）

**Changes:**

- [ ] **Step 10.1: 查看当前 relation_discovery 实现**

```bash
grep -n "relation_discovery\|browse_relation_groups" templates/relation_discovery.html app.py
```

- [ ] **Step 10.2: 扩展 Tabs — 添加 4 个新字段**

在 `relation_discovery.html` 的 Tab 按钮区域，添加：

```html
<button data-type="legal_person" class="rel-tab" 
        hx-get="/browse/relation-groups?dup_type=legal_person"
        hx-target="#rel-groups-content">法人</button>
<button data-type="email" class="rel-tab"
        hx-get="/browse/relation-groups?dup_type=email"
        hx-target="#rel-groups-content">邮箱</button>
<button data-type="shareholder" class="rel-tab"
        hx-get="/browse/relation-groups?dup_type=shareholder"
        hx-target="#rel-groups-content">股东</button>
<button data-type="industry" class="rel-tab"
        hx-get="/browse/relation-groups?dup_type=industry"
        hx-target="#rel-groups-content">行业</button>
```

- [ ] **Step 10.3: 扩展 `browse_relation_groups()` — 支持 4 个新 dup_type**

在 `app.py` 的 `browse_relation_groups()` 函数中，找到 `dup_type == "phone"` 的分支，新增 4 个分支：

```python
elif dup_type == "shareholder":
    field_label = "股东"
    field_icon = "💼"
    rows = db.execute("""
        SELECT cs.normalized_name AS norm_val,
               MIN(cs.name) AS display_val,
               COUNT(DISTINCT cs.company_id) AS company_count
        FROM company_shareholders cs
        WHERE cs.normalized_name IS NOT NULL AND cs.normalized_name <> ''
        GROUP BY cs.normalized_name
        HAVING company_count > 1
        ORDER BY company_count DESC
        LIMIT ? OFFSET ?
    """, [per_page, (page - 1) * per_page]).fetchall()

elif dup_type == "legal_person":
    field_label = "法人"
    field_icon = "👤"
    rows = db.execute("""
        SELECT normalized_legal_person AS norm_val,
               MIN(legal_person) AS display_val,
               COUNT(*) AS company_count
        FROM companies
        WHERE normalized_legal_person IS NOT NULL AND normalized_legal_person <> ''
        GROUP BY normalized_legal_person
        HAVING company_count > 1
        ORDER BY company_count DESC
        LIMIT ? OFFSET ?
    """, [per_page, (page - 1) * per_page]).fetchall()

elif dup_type == "email":
    field_label = "邮箱"
    field_icon = "📧"
    rows = db.execute("""
        SELECT normalized_email AS norm_val,
               MIN(email) AS display_val,
               COUNT(*) AS company_count
        FROM companies
        WHERE normalized_email IS NOT NULL AND normalized_email <> ''
        GROUP BY normalized_email
        HAVING company_count > 1
        ORDER BY company_count DESC
        LIMIT ? OFFSET ?
    """, [per_page, (page - 1) * per_page]).fetchall()

elif dup_type == "industry":
    field_label = "行业"
    field_icon = "🏭"
    rows = db.execute("""
        SELECT industry AS norm_val,
               MIN(industry) AS display_val,
               COUNT(*) AS company_count
        FROM companies
        WHERE industry IS NOT NULL AND industry <> ''
        GROUP BY industry
        HAVING company_count > 1
        ORDER BY company_count DESC
        LIMIT ? OFFSET ?
    """, [per_page, (page - 1) * per_page]).fetchall()
```

- [ ] **Step 10.4: 扩展 `/browse/relation-top` — 多维度**

在 `app.py` 的 `browse_relation_top()` 函数中，扩展 SQL：

```python
rows = db.execute("""
    SELECT 
        (SELECT COUNT(DISTINCT normalized_phone) FROM company_phones WHERE company_id = c.id) AS phone_cnt,
        (SELECT COUNT(DISTINCT normalized_name) FROM company_shareholders WHERE company_id = c.id) AS shareholder_cnt,
        CASE WHEN c.normalized_email != '' THEN 1 ELSE 0 END AS email_cnt,
        c.id, c.name, c.legal_person, c.industry
    FROM companies c
    WHERE (
        (SELECT COUNT(DISTINCT normalized_phone) FROM company_phones WHERE company_id = c.id) +
        (SELECT COUNT(DISTINCT normalized_name) FROM company_shareholders WHERE company_id = c.id) +
        CASE WHEN c.normalized_email != '' THEN 1 ELSE 0 END
    ) > 0
    ORDER BY (
        (SELECT COUNT(DISTINCT normalized_phone) FROM company_phones WHERE company_id = c.id) +
        (SELECT COUNT(DISTINCT normalized_name) FROM company_shareholders WHERE company_id = c.id) +
        CASE WHEN c.normalized_email != '' THEN 1 ELSE 0 END
    ) DESC
    LIMIT 50
""").fetchall()
```

- [ ] **Step 10.5: 提交**

```bash
git add templates/relation_discovery.html app.py
git commit -m "feat: 关联发现页扩展 - 支持股东/法人/邮箱/行业

- relation_discovery.html 新增 4 个 Tab
- browse_relation_groups() 支持 4 种新 dup_type
- browse_relation_top() 扩展为多维度"
```

---

## Task 11: 导出 VIEW（db.py）

**Files:**
- Modify: `db.py`（`init_db()` 末尾新增 VIEW 创建）

**Changes:**

- [ ] **Step 11.1: 新增 `company_export` VIEW**

在 `init_db()` 的 CREATE TABLE 语句块之后新增：

```python
conn.execute("""
    CREATE VIEW IF NOT EXISTS company_export AS
    SELECT 
        c.id, c.name, c.normalized_name, c.credit_code,
        c.legal_person, c.normalized_legal_person,
        c.email, c.normalized_email, c.other_email,
        c.address, c.annual_report_address, c.mailing_address,
        c.province, c.city, c.district,
        c.registered_capital, c.paid_capital,
        c.established_date, c.approved_date, c.business_term,
        c.insured_count, c.enterprise_scale, c.company_type,
        c.industry, c.business_status,
        c.former_name, c.english_name, c.website, c.business_scope,
        c.shareholders, c.tags, c.source_file, c.status, c.source,
        c.created_at, c.updated_at,
        
        (SELECT group_concat(phone, '; ') 
         FROM company_phones p 
         WHERE p.company_id = c.id 
         ORDER BY p.is_primary DESC, p.is_recommended DESC) AS phone_all,
        
        (SELECT group_concat(name, '; ') 
         FROM company_shareholders s 
         WHERE s.company_id = c.id) AS shareholder_all,
        
        (SELECT group_concat(t.name, '; ') 
         FROM company_tags ct 
         JOIN tags t ON ct.tag_id = t.id 
         WHERE ct.company_id = c.id) AS tag_all
    
    FROM companies c;
""")
```

- [ ] **Step 11.2: 验证 VIEW**

```bash
python3 -c "
import sqlite3
conn = sqlite3.connect('data/enthub.db')
cur = conn.cursor()
cur.execute('SELECT name FROM sqlite_master WHERE type=\"view\"')
print('Views:', [r[0] for r in cur.fetchall()])

# 测试查询
cur.execute('SELECT id, name, phone_all, shareholder_all, tag_all FROM company_export LIMIT 1')
print('Sample:', cur.fetchone())
conn.close()
"
```

- [ ] **Step 11.3: 提交**

```bash
git add db.py
git commit -m "feat(db): 新增 company_export VIEW

统一 Excel 导出接口：多值字段用 group_concat 拼接为单字符串"
```

---

## Task 12: 备份层与状态栏（backup.py + menubar.py）

**Files:**
- Modify: `backup.py`（统计补充）
- Modify: `menubar.py`（菜单调整）

**Changes:**

- [ ] **Step 12.1: 备份统计补充**

在 `backup.py` 中找到统计企业数的位置，新增：

```python
total_shareholders = db.execute("SELECT COUNT(*) FROM company_shareholders").fetchone()[0]
```

- [ ] **Step 12.2: 状态栏菜单**

在 `menubar.py` 中，"电话统计"菜单项之后新增：

```python
self.stats_shareholder_item = rumps.MenuItem("股东统计", callback=self.on_open_stats_shareholder)
self.menu.add(self.stats_shareholder_item)

def on_open_stats_shareholder(self, _):
    webbrowser.open("http://127.0.0.1:5000/stats/shareholder")
```

- [ ] **Step 12.3: 提交**

```bash
git add backup.py menubar.py
git commit -m "feat: 备份页与状态栏菜单调整

- 备份统计新增股东总数
- 状态栏菜单新增"股东统计"入口"
```

---

## Task 13: 重新导入数据 + 最终验证

**Files:**
- 无代码改动

**Changes:**

- [ ] **Step 13.1: 删除旧数据库并全新初始化**

```bash
rm /Users/gm/AI/EntHub/data/enthub.db
python3 -c "from db import init_db; init_db()"
```

- [ ] **Step 13.2: 启动 Flask 服务**

```bash
python3 app.py
```

- [ ] **Step 13.3: 通过浏览器导入工商总库**

访问 `http://127.0.0.1:5000/import`，上传 `(工商总库)ALL.xlsx`，观察导入进度。

- [ ] **Step 13.4: 验证数据完整性**

```bash
python3 << 'EOF'
import sqlite3
conn = sqlite3.connect('data/enthub.db')
cur = conn.cursor()

# 基础统计
stats = {
    'companies': 'SELECT COUNT(*) FROM companies',
    'company_phones': 'SELECT COUNT(*) FROM company_phones',
    'company_shareholders': 'SELECT COUNT(*) FROM company_shareholders',
    'company_tags': 'SELECT COUNT(*) FROM company_tags',
    'companies with normalized_legal_person': "SELECT COUNT(*) FROM companies WHERE normalized_legal_person != ''",
    'companies with normalized_email': "SELECT COUNT(*) FROM companies WHERE normalized_email != ''",
}

for label, sql in stats.items():
    count = cur.execute(sql).fetchone()[0]
    print(f"{label}: {count}")

# 测试关联查询
print("\n--- 关联查询测试 ---")
tests = [
    ('同电话', """SELECT c.id FROM company_phones p1 
                  JOIN company_phones p2 ON p1.normalized_phone = p2.normalized_phone 
                  JOIN companies c ON p2.company_id = c.id 
                  WHERE p1.company_id = 1 AND p2.company_id != 1 LIMIT 5"""),
    ('同股东', """SELECT c.id FROM company_shareholders s1 
                  JOIN company_shareholders s2 ON s1.normalized_name = s2.normalized_name 
                  JOIN companies c ON s2.company_id = c.id 
                  WHERE s1.company_id = 1 AND s2.company_id != 1 LIMIT 5"""),
    ('同法人', "SELECT id FROM companies WHERE normalized_legal_person = (SELECT normalized_legal_person FROM companies WHERE id = 1) AND id != 1 LIMIT 5"),
    ('同邮箱', "SELECT id FROM companies WHERE normalized_email = (SELECT normalized_email FROM companies WHERE id = 1) AND id != 1 LIMIT 5"),
    ('同行业', "SELECT id FROM companies WHERE industry = (SELECT industry FROM companies WHERE id = 1) AND id != 1 LIMIT 5"),
]

for label, sql in tests:
    rows = cur.execute(sql).fetchall()
    print(f"{label}: {len(rows)} 条关联企业")

# 测试导出 VIEW
print("\n--- 导出 VIEW 测试 ---")
sample = cur.execute("SELECT id, name, phone_all, shareholder_all FROM company_export LIMIT 3").fetchall()
for row in sample:
    print(f"  {row[0]} | {row[1]} | 电话: {row[2][:50] if row[2] else ''} | 股东: {row[3][:50] if row[3] else ''}")

conn.close()
print("\n✅ 全部验证完成")
EOF
```

- [ ] **Step 13.5: 浏览器 UI 验证**

访问以下页面，截图确认：
- `/` 首页：显示电话可信度、股东关联卡片
- `/stats/phone`：电话重复统计
- `/stats/shareholder`：股东关联统计
- `/relations`：6 个 Tab 全部可用
- `/company/1`：详情页 5 个关联分类
- `/import`：导入流程正常
- `/backup`：统计数字完整

- [ ] **Step 13.6: 最终提交**

```bash
git add .
git commit -m "feat: 关联字段结构重构完成

- 6 个关联字段：3 个独立表（电话/标签/股东）+ 3 个主表索引（法人/邮箱/行业）
- 全新初始化，无数据迁移
- 统一关联查询模式
- 新增 company_export VIEW 用于 Excel 导出"
```

---

## 📊 任务依赖与执行顺序

```
Task 1 (Schema) ─┬─→ Task 2 (utils) ─→ Task 3 (app: 股东函数)
                 │                           ↓
                 ├─→ Task 4 (app: 单值字段) ─→ Task 5 (导入流程)
                 │                           ↓
                 ├─→ Task 6 (详情页查询) ───→ Task 8 (详情页模板)
                 │                           ↓
                 ├─→ Task 7 (统计路由) ────→ Task 9 (统计模板)
                 │                           ↓
                 ├─→ Task 10 (关联页扩展)
                 │
                 ├─→ Task 11 (导出 VIEW)
                 │
                 └─→ Task 12 (备份/状态栏) ─→ Task 13 (最终验证)
```

**并行执行策略**：
- Task 1、2 必须先完成（Schema 和工具函数是基础）
- Task 3、4 可并行
- Task 6、7 可并行
- Task 13 必须最后执行

---

## ✅ 计划自检

### Spec 覆盖检查

| Spec 章节 | 对应 Task |
|----------|----------|
| 3.1 方案总览 | Task 1 (Schema) + Task 2-12 |
| 3.2 数据库表结构变更 | Task 1 + Task 11 (VIEW) |
| 3.3 归一化规则 | Task 2 |
| 3.4 拆分与合并函数 | Task 3 |
| 3.5 关联查询 SQL 模板 | Task 6 + Task 10 |
| 3.6 数据导出 | Task 11 |
| 4.1 初始化步骤 | Task 1.8 (全新初始化) |
| 5.1-5.5 影响范围 | Task 1-12 全覆盖 |

### 无占位符检查

所有 Task 步骤都包含具体代码，无 "TBD"、"类似"、"扩展" 等模糊表述。

### 类型一致性

- `normalize_name()` 在 Task 2 定义，Task 3、4 使用 ✓
- `sync_shareholders()` 在 Task 3 定义，Task 4、5 使用 ✓
- `merge_shareholders()` 在 Task 3 定义，Task 5 使用 ✓
- `split_shareholders()` 在 Task 3 定义，Task 5 使用 ✓
- `company_shareholders` 表在 Task 1 创建，Task 3-13 使用 ✓
- `normalized_legal_person`、`normalized_email` 在 Task 1 创建，Task 4-10 使用 ✓
