"""共享查询层 - 给 Web 路由和 REST API 复用。

把 browse / search / api 等多处重复的 SQL 收敛到这里，
保证筛选、排序、统计、号码查询的一致性。
"""
import math
import time

from datetime import datetime, timedelta

from utils import (
    normalize_phone, normalize_credit_code, normalize_name,
)

# ── 常量 ────────────────────────────────────────────────────────────────────

DEFAULT_PER_PAGE = 25

# 允许的排序字段（白名单，防 SQL 注入）
ALLOWED_SORTS = {
"id": "id",
"name": "normalized_name",
"province": "province",
"city": "city",
"established_date": "established_date",
"business_status": "business_status",
"created_at": "created_at",
"updated_at": "updated_at",
"legal_person": "legal_person",
"registered_capital": "registered_capital",
"phone": "phone",
"industry": "industry",
}

# 文本搜索字段（按优先级排序）。第一项是 normalized_name，匹配时用归一化值。
TEXT_SEARCH_FIELDS = [
    ("normalized_name", "名称", 1),
    ("former_name",     "曾用名", 2),
    ("address",         "地址",  3),
    ("legal_person",    "法人",  4),
    ("shareholders",    "股东",  5),
    ("email",           "邮箱",  6),
    ("website",         "网站",  7),
]

# 精确筛选字段
EXACT_FILTERS = ("city", "district", "business_status", "industry", "company_type")

# 列表查询的标准字段（含电话聚合子查询）
COMPANY_LIST_PHONE_SUBQUERY = (
    "(SELECT group_concat(phone, '; ') "
    "FROM company_phones "
    "WHERE company_id = c.id "
    "ORDER BY is_primary DESC, is_recommended DESC) AS phone"
)

COMPANY_LIST_COLUMNS = f"""
    c.id, c.name, c.address, c.credit_code,
    c.legal_person, c.business_status, c.province, c.city,
    c.district, c.established_date, c.registered_capital,
    c.industry, c.enterprise_scale, c.created_at,
    {COMPANY_LIST_PHONE_SUBQUERY}
"""


# ── 查询类型识别 ────────────────────────────────────────────────────────────

def detect_query_type(q):
    """自动识别查询类型：'phone' / 'credit_code' / 'text'。

    - 纯数字（允许 + - 空格）→ phone
    - 18 位字母数字 → credit_code
    - 其他 → text
    """
    stripped = q.replace(" ", "").replace("-", "").replace("+", "")
    if stripped.isdigit():
        return "phone"
    norm_cc = normalize_credit_code(q)
    if len(norm_cc) == 18 and norm_cc.isalnum():
        return "credit_code"
    return "text"


# ── 号码相关 ────────────────────────────────────────────────────────────────

def phone_dup_count(db, normalized_phone):
    """查询号码关联了多少家不同企业。"""
    if not normalized_phone:
        return 0
    return db.execute(
        "SELECT COUNT(DISTINCT company_id) FROM company_phones "
        "WHERE normalized_phone = ?",
        [normalized_phone]
    ).fetchone()[0]


# ── 筛选器构造 ──────────────────────────────────────────────────────────────

def build_filter_clause(args):
    """从请求参数构造筛选 WHERE 子句。

    支持的参数：
        city, district, business_status, industry  - 精确匹配
        year_from, year_to                         - 成立年份区间
        cap_from, cap_to                           - 注册资本区间（万元）
        insured_from, insured_to                   - 社保人数区间

    返回: (clauses_list, params_list)
    """
    clauses = []
    params = []

    # 精确筛选
    for f in EXACT_FILTERS:
        val = (args.get(f) or "").strip()
        if val:
            clauses.append(f"{f} = ?")
            params.append(val)

    # 成立年份区间
    year_from = (args.get("year_from") or "").strip()
    year_to = (args.get("year_to") or "").strip()
    if year_from:
        clauses.append("established_date >= ?")
        params.append(f"{year_from}-01-01")
    if year_to:
        clauses.append("established_date <= ?")
        params.append(f"{year_to}-12-31")

    # 注册资本区间（剥离"万元"/"万"后转 REAL）
    cap_from = (args.get("cap_from") or "").strip()
    cap_to = (args.get("cap_to") or "").strip()
    cap_expr = (
        "CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL)"
    )
    if cap_from:
        clauses.append(f"{cap_expr} >= ?")
        params.append(float(cap_from))
    if cap_to:
        clauses.append(f"{cap_expr} <= ?")
        params.append(float(cap_to))

    # 社保人数区间
    insured_from = (args.get("insured_from") or "").strip()
    insured_to = (args.get("insured_to") or "").strip()
    if insured_from:
       clauses.append("CAST(insured_count AS INTEGER) >= ?")
       params.append(int(insured_from))
    if insured_to:
       clauses.append("CAST(insured_count AS INTEGER) <= ?")
       params.append(int(insured_to))

    # 录入时间（created_at）年龄段分桶：相邻不重叠的区间
    # 一天=0~1天 / 一周=1~7天 / 一月=7~30天 / 一年=30~365天 / 一年以上=>365天
    # created_at 为 TEXT（YYYY-MM-DD HH:MM:SS），字符串比较与时间顺序一致。
    created_bucket = (args.get("created_at") or "").strip()
    if created_bucket:
        now = datetime.now()
        # (age_lo, age_hi) 天：年龄落在此区间的记录
        #   created_at >= now - age_hi  （更老的一侧）
        #   created_at <= now - age_lo  （更新的一侧）
        bucket_map = {
            "1d":    (0, 1),       # 一天以内
            "7d":    (1, 7),       # 一天 ~ 一周
            "30d":   (7, 30),      # 一周 ~ 一个月
            "365d":  (30, 365),    # 一个月 ~ 一年
            "365d+": (365, None),  # 一年以上
        }
        lo, hi = bucket_map.get(created_bucket, (None, None))
        fmt = "%Y-%m-%d %H:%M:%S"
        if hi is not None:
            clauses.append("created_at >= ?")
            params.append((now - timedelta(days=hi)).strftime(fmt))
        if lo:
            clauses.append("created_at <= ?")
            params.append((now - timedelta(days=lo)).strftime(fmt))

    return clauses, params


def build_sort_clause(args, default="updated_at", default_dir="desc"):
    """从请求参数构造排序 SQL。

    默认按更新时间（updated_at）倒序，即最近添加或更新的企业排最前。
    返回 (sort_col, dir_sql)。排序字段必须在白名单中，否则回退到 default。
    """
    sort = args.get("sort", default)
    dir_param = args.get("dir", default_dir)
    sort_col = ALLOWED_SORTS.get(sort, ALLOWED_SORTS[default])
    dir_sql = "ASC" if dir_param == "asc" else "DESC"
    return sort_col, dir_sql


def where_sql(clauses):
    """把 clauses 列表合成 'WHERE a AND b' 或空字符串。"""
    if not clauses:
        return ""
    return "WHERE " + " AND ".join(clauses)


# ── 企业列表查询 ────────────────────────────────────────────────────────────

def query_company_list(db, where_clause, where_params,
                       sort_col, dir_sql, per_page, offset):
    """查询企业列表，带聚合电话字段。"""
    return db.execute(f"""
        SELECT {COMPANY_LIST_COLUMNS}
        FROM companies c
        {where_clause}
        ORDER BY {sort_col} {dir_sql}
        LIMIT ? OFFSET ?
    """, where_params + [per_page, offset]).fetchall()


# ── 筛选器选项（菜单选项） ─────────────────────────────────────────────────

# 简易内存缓存：避免每次刷新浏览页都跑 4 次 DISTINCT + 1 次年份聚合
# 数据变动少（导入时才变），TTL 5 分钟足够；导入完成后第一次访问会自动重算
_CACHE = {}
_CACHE_TTL = 300  # 秒


def _cache_get(key):
    """读缓存，过期返回 None。"""
    item = _CACHE.get(key)
    if not item:
        return None
    if time.time() - item[1] > _CACHE_TTL:
        return None
    return item[0]


def _cache_set(key, value):
    _CACHE[key] = (value, time.time())


def invalidate_cache():
    """主动清空缓存（导入/清理完成后调用）。"""
    _CACHE.clear()


def get_filter_options(db, limit=50):
    """读取筛选下拉框的去重值（带 5 分钟缓存）。"""
    cached = _cache_get("filter_options")
    if cached is not None:
        return cached

    options = {}
    for f in EXACT_FILTERS:
        rows = db.execute(f"""
            SELECT DISTINCT {f} FROM companies
            WHERE {f} IS NOT NULL AND {f} <> '' AND {f} <> '-'
            ORDER BY {f} LIMIT ?
        """, [limit]).fetchall()
        options[f] = [r[f] for r in rows]

    _cache_set("filter_options", options)
    return options


def get_year_bounds(db, fallback_min=1950, fallback_max=2026):
    """读取 established_date 的最小/最大年份（带 5 分钟缓存）。"""
    cached = _cache_get("year_bounds")
    if cached is not None:
        return cached

    row = db.execute("""
        SELECT MIN(SUBSTR(established_date, 1, 4)) AS min_y,
               MAX(SUBSTR(established_date, 1, 4)) AS max_y
        FROM companies
        WHERE established_date IS NOT NULL AND established_date <> ''
          AND established_date NOT IN ('nan','NaN','NAN')
          AND SUBSTR(established_date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'
    """).fetchone()
    min_y = int(row["min_y"]) if row and row["min_y"] else fallback_min
    max_y = int(row["max_y"]) if row and row["max_y"] else fallback_max
    result = (min_y, max_y)
    _cache_set("year_bounds", result)
    return result


def build_year_ranges(min_year, max_year):
    """构造递增年份区间（1/2/3/4/5/6... 年）。

    从最近年份往回递增，每段长度递增。返回 [(start, end), ...]。
    """
    ranges = []
    y = max_year
    span = 1
    while y >= min_year:
        start = max(min_year, y - span + 1)
        ranges.append((str(start), str(y)))
        y = start - 1
        span += 1
    return ranges


# ── 通用分组统计（法人/股东/行业） ──────────────────────────────────────────

# 统计字段白名单（防 SQL 注入）
STATS_FIELDS = {"legal_person", "shareholders", "industry"}


def stats_grouped(db, field, page, per_page, min_count):
    """按某字段分组统计，返回 (total, pages, rows)。

    field 必须在 STATS_FIELDS 白名单中。
    rows 中每行: {val, cnt, company_names}
    """
    if field not in STATS_FIELDS:
        raise ValueError(f"非法统计字段: {field}")

    total = db.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT {field} FROM companies
            WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
            GROUP BY {field}
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages = max(1, math.ceil(total / per_page)) if per_page else 1
    offset = (page - 1) * per_page

    rows = db.execute(f"""
        SELECT {field} AS val,
               COUNT(*) AS cnt,
               GROUP_CONCAT(name) AS company_names
        FROM companies
        WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
        GROUP BY {field}
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ? OFFSET ?
    """, [min_count, per_page, offset]).fetchall()

    return total, pages, rows


# ── 电话号码重复统计 ────────────────────────────────────────────────────────

def phone_stats_grouped(db, page, per_page, min_count):
    """按 normalized_phone 分组统计重复号码，返回 (total, pages, rows)。"""
    total = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT normalized_phone FROM company_phones
            WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
            GROUP BY normalized_phone
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    # 子查询 LIMIT 10 截断：避免大组（如某号关联 532 家）GROUP_CONCAT 拖慢，
    # 同时绕过 SQLite 3.51+ 不支持 GROUP_CONCAT(DISTINCT col, sep) 的限制
    rows = db.execute("""
        SELECT t.normalized_phone,
               t.display_phone,
               t.cnt,
               (SELECT GROUP_CONCAT(cname, '; ') FROM (
                   SELECT c.name AS cname
                   FROM company_phones cp2
                   JOIN companies c ON c.id = cp2.company_id
                   WHERE cp2.normalized_phone = t.normalized_phone
                   LIMIT 10
               )) AS company_names
        FROM (
            SELECT cp.normalized_phone,
                   MIN(cp.phone) AS display_phone,
                   COUNT(*) AS cnt
            FROM company_phones cp
            WHERE cp.normalized_phone IS NOT NULL AND cp.normalized_phone <> ''
            GROUP BY cp.normalized_phone
            HAVING cnt >= ?
            ORDER BY cnt DESC
            LIMIT ? OFFSET ?
        ) t
    """, [min_count, per_page, offset]).fetchall()

    return total, pages, rows


# ── 文本搜索（多字段带优先级） ──────────────────────────────────────────────

def text_search(db, q, per_page, offset):
    """7 字段文本搜索，按命中字段优先级排序。

    返回 (total, rows)，rows 中含 matched_field 字段。
    """
    norm_q = normalize_name(q)
    like_q = "%" + q + "%"
    like_name = "%" + norm_q + "%"

    # total: UNION 去重
    # 第一个字段是 normalized_name，用归一化值匹配；其他用原文 LIKE。
    total_sql = "SELECT COUNT(*) FROM (\n    " + "\n    UNION\n    ".join(
        f"SELECT id FROM companies WHERE {field} LIKE ?"
        for field, _, _ in TEXT_SEARCH_FIELDS
    ) + "\n)"
    # 第一个参数用归一化的 like_name，其余用 like_q
    total_params = [like_name] + [like_q] * (len(TEXT_SEARCH_FIELDS) - 1)
    total = db.execute(total_sql, total_params).fetchone()[0]

    # 排序查询：UNION ALL + GROUP BY id + MIN(priority)
    branches = []
    for field, label, priority in TEXT_SEARCH_FIELDS:
        param = "like_name" if field == "normalized_name" else "like_q"
        branches.append(
            f"            SELECT id, '{label}' AS matched_field, {priority} AS priority "
            f"FROM companies WHERE {field} LIKE ?"
        )
    branches_sql = "\n            UNION ALL\n".join(branches)

    rows = db.execute(f"""
        SELECT {COMPANY_LIST_COLUMNS},
               m.matched_field
        FROM (
{branches_sql}
        ) m
        JOIN companies c ON c.id = m.id
        GROUP BY c.id
        ORDER BY MIN(m.priority), c.name
        LIMIT ? OFFSET ?
    """, total_params + [per_page, offset]).fetchall()

    return total, rows


# ── 电话/信用代码精确搜索 ───────────────────────────────────────────────────

def search_by_phone(db, norm_phone, per_page, offset, matched_label="电话"):
    """按 normalized_phone 搜索企业。"""
    total = db.execute(
        "SELECT COUNT(*) FROM company_phones WHERE normalized_phone = ?",
        [norm_phone]
    ).fetchone()[0]
    rows = db.execute(f"""
        SELECT {COMPANY_LIST_COLUMNS},
               ? AS matched_field
        FROM company_phones cp
        JOIN companies c ON cp.company_id = c.id
        WHERE cp.normalized_phone = ?
        ORDER BY c.name LIMIT ? OFFSET ?
    """, [matched_label, norm_phone, per_page, offset]).fetchall()
    return total, rows


def search_by_credit_code(db, norm_code, per_page, offset,
                          matched_label="信用代码"):
    """按统一社会信用代码精确搜索。"""
    total = db.execute(
        "SELECT COUNT(*) FROM companies WHERE credit_code = ?",
        [norm_code]
    ).fetchone()[0]
    rows = db.execute(f"""
        SELECT {COMPANY_LIST_COLUMNS},
               ? AS matched_field
        FROM companies c
        WHERE c.credit_code = ?
        LIMIT ? OFFSET ?
    """, [matched_label, norm_code, per_page, offset]).fetchall()
    return total, rows


# ── 分页工具 ────────────────────────────────────────────────────────────────

def paginate(total, page, per_page):
    """返回 (pages, offset)。pages 至少为 1。"""
    pages = max(1, math.ceil(total / per_page)) if per_page else 1
    offset = (page - 1) * per_page
    return pages, offset


def sanitize_page(args, key="page", default=1):
    """从 args 中安全读取页码（>=1）。"""
    try:
        return max(1, int(args.get(key, default)))
    except (TypeError, ValueError):
        return default


def sanitize_per_page(args, default=DEFAULT_PER_PAGE,
                      minimum=10, maximum=500):
    """从 args 中安全读取每页条数（夹在 [minimum, maximum]）。"""
    try:
        return max(minimum, min(maximum, int(args.get("per_page", default))))
    except (TypeError, ValueError):
        return default


def sanitize_min_count(args, default=2, minimum=2):
    """从 args 中安全读取 min_count。"""
    try:
        return max(minimum, int(args.get("min", default)))
    except (TypeError, ValueError):
        return default
