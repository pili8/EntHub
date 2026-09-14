# EntHub 插件化架构改造 — 最终执行方案(定稿)

> 文档性质:给执行者(开发者 / AI)的完整施工蓝图,自包含、可照做。
> 技术栈:**Flask + SQLite + Jinja2**。项目现状:约 12 个 Blueprint 集中在 `routes/` 目录。
> 版本:v1.0 · 日期:2026-09-08

---

## 一、目标

以「**公司**」为核心实体,把系统改造成「**平台 + 可插拔模块**」结构。验收要求:

1. **新增功能**:新建一个模块文件即可,不改任何现有代码。
2. **停用功能**:设置页关开关 → 路由、菜单、建表都不加载,但数据保留,可随时再开。
3. **删除功能**:设置页点删除 → 代码移除,数据是否清空由用户二次确认。
4. **核心不可关闭**:工商查询本体(搜索、公司详情、导入)常驻,不受开关影响。

---

## 二、目标架构(三层)

```
核心层(稳定,基本不再动)
├── 自动注册器        routes/__init__.py 扫描模块、读开关、注册蓝图
├── 公司主数据        company 表 + 统一查询接口(所有模块的地基)
├── 公共能力          飞书 / 配置 / 日志 / 鉴权 / 数据库连接
└── 页面骨架          base.html 统一导航 + 样式 + 移动端适配

模块接口契约(所有模块必须遵守)
└── MODULE_META 自描述协议(见第三节)

功能模块层(可插拔,增删都在这)
├── 核心模块(core=True,不可关):pages / companies / import_flow / tags
├── 可选模块(可开关):phone_tags / phone_wechat / mcp_flow / backup / cleanup / quick_import / api_legacy
└── 未来模块(直接丢进来):recruit(招聘) / followup(客户跟进) / 其他小工具
```

**核心思想:模块之间不互相依赖,只依赖核心层。** 删掉任何模块,其他模块和核心层毫发无损。

---

## 三、模块自描述协议(MODULE_META)

每个模块 `.py` 文件**顶部**声明一个字典,是注册器、导航、开关面板的共同数据源:

```python
MODULE_META = {
    "id": "recruit",           # 唯一标识,建议同文件名
    "name": "招聘信息",         # 菜单显示名
    "icon": "briefcase",       # 图标名(Lucide 图标集)
    "url": "/recruit",         # 主入口 URL
    "group": "data",           # 菜单分组:data / ops / system
    "order": 30,               # 分组内排序,数字越小越靠前
    "core": False,             # True=核心模块,不受开关控制
    "depends_on": [],          # 依赖的模块 id 列表(注册器校验)
    "tables": [],              # 建表 SQL 字符串列表(可选)
}

# 可选:模块自己的建表函数(优先级高于 tables 字段)
def init_module(db):
    db.execute("""CREATE TABLE IF NOT EXISTS recruit_jobs (...)""")
```

字段约定:
- `id` 唯一、稳定,一经发布不要改(开关配置按 id 存)。
- 无 `MODULE_META` 的 `.py` 文件视为内部工具模块,仍走手动注册兜底,不被扫描注册。

---

## 四、核心层清单(稳定不动)

### 4.1 公共能力下沉清单

判定标准(三问):符合**任意一条**就放核心层 ——
1. 会被 ≥2 个模块用到?
2. 是横切能力(日志/鉴权/配置/数据库连接)?
3. 是「公司」核心概念?

具体下沉项:

| 公共能力 | 说明 |
|---------|------|
| 公司主数据 | `company` 表 + `query_company()` / `get_company()` 统一接口 |
| 飞书服务 | 推送/同步(招聘、跟进等模块都要用,必须放核心层,不能做成某模块私有) |
| 配置管理 | `settings` 表读写(config.py) |
| 鉴权 | 登录验证、session |
| 日志 | 统一 logger |
| 数据库连接 | `db.py` 连接 + 核心表迁移 |

### 4.2 下沉纪律(Rule of Three)

**第一次先放模块内,别急着抽;第二个模块真的要用到时再下沉。**
理由:重复代码比错误的抽象更便宜;过早抽象会拖累所有模块。

---

## 五、自动注册机制

改造 `routes/__init__.py`,从「手动列举」改为「自动扫描」:

```python
def register_modules(app):
    enabled = get_module_config()          # 读 settings 表 module_config(默认 True)
    for f in routes_dir.glob("*.py"):
        meta = load_meta(f)                 # 读取文件顶部的 MODULE_META
        if meta is None:
            continue                        # 无声明,跳过(内部工具模块)
        if meta["core"]:
            register(f)                     # 核心模块无条件注册
            continue
        if enabled.get(meta["id"], True):
            if check_deps(meta, enabled):   # 依赖未启用则告警并跳过
                register(f)                 # 注册蓝图 + 收集菜单 + 调用 init_module
```

注册动作含三件事:注册 Flask Blueprint、收集菜单项到列表、调用 `init_module(db)` 建表。

---

## 六、导航动态化

1. `app.py` 的 `context_processor` 注入已启用模块列表:

```python
@app.context_processor
def inject_modules():
    return {"ENABLED_MODULES": get_enabled_module_menu()}
```

2. `base.html` 导航改为循环渲染(桌面端、移动端抽屉、移动端底部 Tab 三处用同一数据源):

```jinja2
{% for m in ENABLED_MODULES if m.group == 'data' %}
<a href="{{ m.url }}" class="nav-link">
  <i data-lucide="{{ m.icon }}"></i>{{ m.name }}
</a>
{% endfor %}
```

---

## 七、模块自治建表

- 模块需要自己的表 → 用 `init_module(db)` 函数(或 `MODULE_META["tables"]`)自建。
- 表名加模块前缀避免冲突(如 `recruit_jobs`、`followup_records`)。
- **关模块不删表**(数据不丢),只不加载路由;重新开启数据还在。
- 核心表(`company` 等)仍留在 `db.py` 的核心迁移部分,不拆。

---

## 八、模块开关 + 删除

### 8.1 开关配置

存在 `settings` 表,key=`module_config`,value 为 JSON:

```json
{ "phone_tags": true, "phone_wechat": true, "recruit": false }
```

### 8.2 设置页「模块管理」面板

- **开关(toggle)**:停用 = 不注册路由 + 菜单消失 + 不建表;代码和数据原样保留。
- **删除按钮**:二次确认弹窗,内含勾选项「同时清空该模块的数据」。
  - 默认只删代码文件/目录,数据表保留(防止误删)。
  - 勾选后额外执行该模块的 `drop_module_tables()`(若定义)清空数据。
- 开关/删除**均需重启服务生效**(不做热加载)。

---

## 九、公司核心实体(数据模型)

`company` 表放核心层,是唯一的「地基」。建议字段:

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 内部主键 |
| credit_code | TEXT UNIQUE | 统一社会信用代码(对外唯一标识) |
| name | TEXT | 公司名称 |
| legal_person | TEXT | 法定代表人 |
| reg_capital | TEXT | 注册资本 |
| reg_date | TEXT | 成立日期 |
| status | TEXT | 经营状态 |
| industry | TEXT | 所属行业 |
| address | TEXT | 注册地址 |
| phone | TEXT | 联系电话(可 JSON 存多个) |
| source_url | TEXT | 来源链接 |
| tags | TEXT | 标签 |
| remark | TEXT | 备注 |
| created_at / updated_at | TEXT | 时间戳 |

**各模块表一律通过 `company_id` 外键挂到 company 表**,例如:

```sql
CREATE TABLE IF NOT EXISTS recruit_jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INTEGER NOT NULL,
  title TEXT, salary TEXT, source_url TEXT,
  FOREIGN KEY(company_id) REFERENCES company(id)
);
```

这样删掉招聘模块,公司主数据和客户跟进数据完全不受影响。

---

## 十、明确不做(避免过度设计)

- ❌ 热加载(改代码/开关即时生效)——复杂度高,单用户不值得,开关后重启即可。
- ❌ 插件间事件总线——跨模块联动需求出现前不上。
- ❌ 第三方插件市场 / 远程安装插件——所有模块都是项目自带的。
- ❌ 拆成独立 pip 包——模块就是项目内一个文件,够用。
- ❌ 通用权限系统——单用户本地部署不需要。

---

## 十一、实施步骤(每阶段可中断,系统保持可用)

| 阶段 | 内容 | 涉及文件 |
|------|------|---------|
| 1 | 定义 MODULE_META 协议 + 自动扫描注册 + 导航动态化 + settings 初始化 module_config | `routes/_base.py`、`routes/__init__.py`、`app.py`、`templates/base.html` |
| 2 | 给 12 个现有 `routes/*.py` 顶部补 MODULE_META;核心业务模块标 `core:True`;独立建表逻辑拆到 `init_module` | 各 `routes/*.py` |
| 3 | 设置页新增「模块管理」区:开关 + 删除按钮(二次确认 + 数据清空勾选) | `routes/settings_flow.py` |
| 4 | 验证(见第十二节) | — |
| 5(后续) | 按规范接入招聘、客户跟进模块 | `routes/recruit.py`、`routes/followup.py` |

---

## 十二、验收标准(自检清单)

1. 新建测试模块(如 `hello_world.py`,含 MODULE_META),重启后菜单自动出现。
2. 关闭一个可选模块开关,重启后:路由 404、菜单消失、**数据仍在**,重新开启数据还在。
3. 删除测试模块:代码移除;未勾选清空时数据表保留,勾选后数据清空。
4. 核心模块(搜索/详情/导入)始终存在,不受任何开关影响。
5. 回归:搜索、Excel 导入、备份、对外 API(`api_legacy` 被 Chrome 插件/MCP 依赖)全部正常。

---

## 十三、风险与对策

| 风险 | 对策 |
|------|------|
| `api_legacy` 被 Chrome 插件 / MCP 依赖,误关导致外部调用失败 | `api_legacy` 标 `core:True`,或设置页对其做「外部依赖」提示 |
| 模板内跨模块硬编码跳转(如 `url_for('xxx_bp.yyy')`)在停用后报错 | 导航走动态渲染;跨模块跳转逐步改为经核心层接口或加存在性判断 |
| 建表从 `db.py` 拆出时遗漏 | `db.py` 的 `_migrate` 调用保留,只拆「新建表」语句,迁移逻辑不动 |
| 模块间依赖(如 phone_wechat 依赖 phone_tags 的表) | `depends_on` 字段,注册器校验依赖是否启用 |

---

## 附录:完整模块示例(`routes/recruit.py`)

```python
"""招聘信息模块 —— 挂在公司主数据下"""
from flask import Blueprint, render_template
from ._base import make_bp   # 模块蓝图工厂(项目已有约定)

MODULE_META = {
    "id": "recruit",
    "name": "招聘信息",
    "icon": "briefcase",
    "url": "/recruit",
    "group": "data",
    "order": 30,
    "core": False,
    "depends_on": [],          # 依赖核心层的 company 数据,不依赖其他模块
    "tables": [
        """CREATE TABLE IF NOT EXISTS recruit_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            title TEXT, salary TEXT, source_url TEXT,
            FOREIGN KEY(company_id) REFERENCES company(id)
        )"""
    ],
}

def init_module(db):
    """注册器会自动调用;这里用 tables 字段已能覆盖,无需额外逻辑"""
    pass

bp = make_bp("recruit", __name__, url_prefix="/recruit")

@bp.route("/")
def index():
    # 业务逻辑:调用核心层提供的 company 查询接口,而非直接碰 db
    return render_template("recruit.html")
```
