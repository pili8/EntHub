"""EntHub MCP Server

让 AI 工具（Claude、Cursor 等）能用自然语言查询企业工商信息。

启动模式：
1. stdio 模式（默认，Claude Desktop 自动拉起）：python mcp_server.py
2. HTTP 模式（手动启动，多端共享）：python mcp_server.py --http

工具列表：
- search_companies: 搜索企业（名称/电话/信用代码/法人/股东/邮箱/网站）
- get_company_detail: 获取企业详情（含关联企业+标签）
- find_relations: 查找关联企业（按电话/邮箱/法人/股东）
- get_companies_list: 企业列表（支持筛选/排序/分页）
- get_stats: 统计查询（法人/股东/行业）
"""
import sys
import sqlite3
from mcp.server.fastmcp import FastMCP
from db import get_db, DB_PATH
from utils import normalize_phone, normalize_credit_code, normalize_name, \
    normalize_person_name, normalize_email, validate_phone
from extract_service import extract_company_info
import math

# 复用 api.py 中已稳定的电话标注实现（避免重复维护导致 bug 漂移）
from api import (
    _phone_dup_count,
    _extract_and_annotate,
)
from queries import text_search, search_by_phone, search_by_credit_code

# 创建 MCP Server（host/port 仅 HTTP 模式生效）
mcp = FastMCP("EntHub", json_response=True, host="0.0.0.0", port=5310)

# EntHub Web 服务地址（用于生成企业详情页链接）
_BASE_URL = "http://127.0.0.1:5210"


# ── HTTP 模式认证中间件 ──────────────────────────────────────────────────────
# 复用 Web 设置的访问密码，不设密码时全放行
def _check_token(token: str) -> bool:
    """检查 token 是否匹配已设置的访问密码。未设密码时全放行。"""
    from config import is_password_enabled, verify_access_password
    if not is_password_enabled():
        return True  # 未设密码，全放行
    return verify_access_password(token)


class TokenAuthMiddleware:
    """ASGI 中间件：检查 ?token= 或 Authorization: Bearer"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # 从 query string 提取 token
        token = None
        qs = scope.get("query_string", b"").decode("utf-8", errors="replace")
        if qs:
            from urllib.parse import parse_qs
            params = parse_qs(qs)
            token = params.get("token", [None])[0]

        # 从 Authorization header 提取 token
        if not token:
            for key, val in scope.get("headers", []):
                if key == b"authorization":
                    val_str = val.decode("utf-8", errors="replace")
                    if val_str.startswith("Bearer "):
                        token = val_str[7:]
                    break

        # 验证
        if token and _check_token(token):
            await self.app(scope, receive, send)
            return

        # 未通过，返回 401
        resp_body = b'{"code": 401, "message": "\u9700\u8981\u8ba4\u8bc1\uff0c\u8bf7\u5728 URL \u52a0 ?token=\u5bc6\u7801 \u6216 Authorization: Bearer \u5bc6\u7801", "data": null}'
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(resp_body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": resp_body})


# ── 工具函数 ──────────────────────────────────────────────────

def _detect_query_type(q: str) -> str:
    """检测查询类型（电话/信用代码/文本）

    注意：与 queries.detect_query_type 略有不同——
    要求号码长度 >= 7 才识别为电话，避免短数字（如"100"）被误判。
    """
    q = q.strip()
    digits_only = normalize_phone(q)
    if digits_only and len(digits_only) >= 7 and digits_only.isdigit():
        return "phone"
    norm_q = normalize_credit_code(q)
    if norm_q and len(norm_q) == 18 and not q.isdigit():
        return "credit_code"
    return "text"


@mcp.tool()
def search_companies(q: str, limit: int = 20) -> dict:
    """搜索企业（名称/电话/信用代码/法人/股东/邮箱/网站）

    每条结果包含 detail_url 字段，可直接访问企业详情页。

    Args:
        q: 搜索关键词
        limit: 返回条数（默认 20，最大 50）

    Returns:
        搜索结果，包含 query/type/count/results，每条 result 含 detail_url
    """
    db = get_db()
    q = q.strip()
    limit = min(50, max(1, limit))

    if not q:
        return {"code": 0, "message": "ok", "data": {"query": "", "type": "text", "count": 0, "results": []}}

    query_type = _detect_query_type(q)

    if query_type == "phone":
        norm_q = normalize_phone(q)
        total, rows = search_by_phone(db, norm_q, limit, 0)
    elif query_type == "credit_code":
        norm_q = normalize_credit_code(q)
        total, rows = search_by_credit_code(db, norm_q, limit, 0)
    else:
        total, rows = text_search(db, q, limit, 0)

    results = []
    for r in rows:
        d = dict(r)
        d["detail_url"] = f"{_BASE_URL}/company/{d['id']}"
        results.append(d)

    return {"code": 0, "message": "ok", "data": {
        "query": q, "type": query_type,
        "count": total, "results": results
    }}


@mcp.tool()
def batch_match_companies(names: list, limit: int = 5) -> dict:
    """批量匹配企业名称，返回每条名称对应的企业 ID 和详情页链接。

    用于将外部表格中的企业名称批量关联到 EntHub：
    一次性传入所有名称，内部直接查数据库，一次返回全部匹配结果。
    852 条名称匹配仅需 1 次调用、不到 1 秒。

    匹配策略：
    - 精确匹配：normalized_name 完全一致（优先级最高）
    - 模糊匹配：normalized_name LIKE %name%（按相关度排序）
    - 未找到：返回空 results

    Args:
        names: 企业名称列表，最多 1000 个
        limit: 每条名称最多返回的模糊匹配条数（默认 5，仅精确匹配无结果时生效）

    Returns:
        匹配结果，包含 total/input_count/matched/partial/unmatched/results
        results 中每条含 input_name/match_type/matches[]
        每个 match 含 id/name/credit_code/city/detail_url
    """
    db = get_db()
    limit = min(20, max(1, limit))

    if not names:
        return {"code": 0, "message": "ok", "data": {
            "total": 0, "input_count": 0,
            "matched": 0, "partial": 0, "unmatched": 0,
            "results": []
        }}

    names = names[:1000]  # 安全上限

    # 一次性加载所有企业的 id/name/normalized_name/credit_code/city 到内存
    # 几千条数据也就几百 KB，内存匹配比逐条 SQL 快几个数量级
    all_companies = db.execute(
        "SELECT id, name, normalized_name, credit_code, city FROM companies"
    ).fetchall()

    # 构建 normalized_name → company 的索引（精确匹配用）
    exact_index = {}
    for c in all_companies:
        norm = c["normalized_name"]
        if norm:
            exact_index.setdefault(norm, []).append(c)

    results = []
    matched_count = 0
    partial_count = 0
    unmatched_count = 0

    for raw_name in names:
        raw_name = str(raw_name).strip()
        if not raw_name:
            continue

        norm_name = normalize_name(raw_name)

        # 1. 精确匹配
        if norm_name and norm_name in exact_index:
            companies = exact_index[norm_name]
            matches = []
            for c in companies[:limit]:
                matches.append({
                    "id": c["id"],
                    "name": c["name"],
                    "credit_code": c["credit_code"],
                    "city": c["city"],
                    "detail_url": f"{_BASE_URL}/company/{c['id']}",
                })
            results.append({
                "input_name": raw_name,
                "match_type": "exact",
                "matches": matches,
            })
            matched_count += 1
            continue

        # 2. 模糊匹配：normalized_name LIKE %name%
        like_pattern = f"%{norm_name}%" if norm_name else f"%{raw_name}%"
        fuzzy_rows = db.execute(
            """SELECT id, name, normalized_name, credit_code, city
               FROM companies
               WHERE normalized_name LIKE ?
               ORDER BY length(normalized_name) ASC  -- 短名优先（更精确的匹配）
               LIMIT ?""",
            [like_pattern, limit]
        ).fetchall()

        if fuzzy_rows:
            matches = []
            for c in fuzzy_rows:
                matches.append({
                    "id": c["id"],
                    "name": c["name"],
                    "credit_code": c["credit_code"],
                    "city": c["city"],
                    "detail_url": f"{_BASE_URL}/company/{c['id']}",
                })
            results.append({
                "input_name": raw_name,
                "match_type": "fuzzy",
                "matches": matches,
            })
            partial_count += 1
        else:
            results.append({
                "input_name": raw_name,
                "match_type": "none",
                "matches": [],
            })
            unmatched_count += 1

    return {"code": 0, "message": "ok", "data": {
        "total": len(results),
        "input_count": len(names),
        "matched": matched_count,
        "partial": partial_count,
        "unmatched": unmatched_count,
        "results": results,
    }}


@mcp.tool()
def get_company_detail(company_id: int) -> dict:
    """获取企业详情（含关联企业+标签）
    
    Args:
        company_id: 企业 ID
    
    Returns:
        企业详情，包含 company/phones/relations/relation_counts/tags
    """
    db = get_db()
    row = db.execute("SELECT * FROM companies WHERE id = ?", [company_id]).fetchone()
    if not row:
        return {"code": 1002, "message": "企业不存在", "data": None}
    
    company = dict(row)
    
    # 关联电话
    company_phones = db.execute("""
        SELECT cp.phone, cp.normalized_phone, cp.is_primary,
               (SELECT COUNT(DISTINCT company_id) FROM company_phones cp2
                WHERE cp2.normalized_phone = cp.normalized_phone) AS dup_count
        FROM company_phones cp
        WHERE cp.company_id = ?
        ORDER BY cp.is_primary DESC, cp.id
    """, [company_id]).fetchall()
    
    # 为电话附加校验状态
    phone_list = []
    for cp in company_phones:
        cp_dict = dict(cp)
        is_valid, phone_type, reason = validate_phone(cp['normalized_phone'])
        cp_dict['phone_valid'] = is_valid
        cp_dict['phone_type'] = phone_type
        cp_dict['phone_invalid_reason'] = reason if not is_valid else ""
        phone_list.append(cp_dict)

    # 关联邮箱
    company_emails = db.execute("""
        SELECT ce.email, ce.normalized_email, ce.is_primary,
               (SELECT COUNT(DISTINCT company_id) FROM company_emails ce2
                WHERE ce2.normalized_email = ce.normalized_email) AS dup_count
        FROM company_emails ce
        WHERE ce.company_id = ?
        ORDER BY ce.is_primary DESC
    """, [company_id]).fetchall()
    email_list = [dict(e) for e in company_emails]

    relations = {}
    relation_counts = {}
    
    # 关联电话（按 normalized_phone）
    phone_norms = [r['normalized_phone'] for r in company_phones if r['normalized_phone']]
    if phone_norms:
        placeholders = ','.join(['?'] * len(phone_norms))
        related = db.execute(f"""
            SELECT DISTINCT c.id, c.name, c.address,
                   (SELECT group_concat(cp2.phone, '; ')
                    FROM company_phones cp2
                    WHERE cp2.company_id = c.id
                    ORDER BY cp2.is_primary DESC, cp2.id) AS phone
            FROM companies c
            JOIN company_phones cp ON cp.company_id = c.id
            WHERE c.id <> ? AND cp.normalized_phone IN ({placeholders})
              AND cp.normalized_phone <> ''
            ORDER BY c.name LIMIT 10
        """, [company_id] + phone_norms).fetchall()
        relations['phones'] = [dict(r) for r in related]
    
    # 其他关联（法人/股东/行业）
    for field, key in [('legal_person', 'legal_person'),
                       ('shareholders', 'shareholders'),
                       ('industry', 'industry')]:
        val = row[field] if row[field] else None
        if val and val != '-':
            related = db.execute(f"""
                SELECT c.id, c.name, c.address,
                       (SELECT group_concat(phone, '; ')
                        FROM company_phones
                        WHERE company_id = c.id
                        ORDER BY is_primary DESC, id) AS phone
                FROM companies c
                WHERE c.id <> ? AND c.{field} = ? AND c.{field} <> ''
                ORDER BY c.name LIMIT 10
            """, [company_id, val]).fetchall()
            relations[key] = [dict(r) for r in related]
            cnt = db.execute(
                f"SELECT COUNT(*) FROM companies WHERE {field} = ?", [val]
            ).fetchone()[0]
            relation_counts[key] = cnt

    # 关联邮箱（查 company_emails 表）
    email_norms = [r['normalized_email'] for r in company_emails if r['normalized_email']]
    if email_norms:
        placeholders = ','.join(['?'] * len(email_norms))
        related = db.execute(f"""
            SELECT DISTINCT c.id, c.name, c.address,
                   (SELECT group_concat(phone, '; ')
                    FROM company_phones
                    WHERE company_id = c.id
                    ORDER BY is_primary DESC, id) AS phone
            FROM companies c
            JOIN company_emails ce ON ce.company_id = c.id
            WHERE c.id <> ? AND ce.normalized_email IN ({placeholders})
              AND ce.normalized_email <> ''
            ORDER BY c.name LIMIT 10
        """, [company_id] + email_norms).fetchall()
        relations['email'] = [dict(r) for r in related]
        relation_counts['email'] = len(relations['email'])
    
    # 标签
    tags = db.execute("""
        SELECT t.id, t.name, t.color FROM tags t
        JOIN company_tags ct ON ct.tag_id = t.id
        WHERE ct.company_id = ?
        ORDER BY t.name
    """, [company_id]).fetchall()
    
    return {"code": 0, "message": "ok", "data": {
        "company": company,
        "phones": phone_list,
        "emails": email_list,
        "relations": relations,
        "relation_counts": relation_counts,
        "tags": [dict(t) for t in tags]
    }}


@mcp.tool()
def find_relations(rel_type: str, value: str, limit: int = 20) -> dict:
    """查找关联企业（按电话/邮箱/法人/股东）
    
    Args:
        rel_type: 关联类型（phone/email/legal_person/shareholders）
        value: 查询值
        limit: 返回条数（默认 20，最大 100）
    
    Returns:
        关联企业列表
    """
    db = get_db()
    limit = min(100, max(1, limit))
    
    allowed_types = ('phone', 'email', 'legal_person', 'shareholders')
    if rel_type not in allowed_types:
        return {"code": 1001, "message": f"type 必须是 {'/'.join(allowed_types)} 之一", "data": None}
    if not value:
        return {"code": 1001, "message": "value 不能为空", "data": None}
    
    if rel_type == 'phone':
        norm_value = normalize_phone(value)
        rows = db.execute("""
            SELECT DISTINCT c.id, c.name, c.address,
                   (SELECT group_concat(cp2.phone, '; ')
                    FROM company_phones cp2
                    WHERE cp2.company_id = c.id
                    ORDER BY cp2.is_primary DESC, cp2.id) AS phone
            FROM companies c
            JOIN company_phones cp ON cp.company_id = c.id
            WHERE cp.normalized_phone = ?
            ORDER BY c.name LIMIT ?
        """, [norm_value, limit]).fetchall()
    elif rel_type == 'email':
        norm_value = normalize_email(value)
        rows = db.execute("""
            SELECT DISTINCT c.id, c.name, c.address,
                   (SELECT group_concat(cp2.phone, '; ')
                    FROM company_phones cp2
                    WHERE cp2.company_id = c.id
                    ORDER BY cp2.is_primary DESC, cp2.id) AS phone
            FROM companies c
            JOIN company_emails ce ON ce.company_id = c.id
            WHERE ce.normalized_email = ?
            ORDER BY c.name LIMIT ?
        """, [norm_value, limit]).fetchall()
    else:
        rows = db.execute(f"""
            SELECT c.id, c.name, c.address,
                   (SELECT group_concat(phone, '; ')
                    FROM company_phones
                    WHERE company_id = c.id
                    ORDER BY is_primary DESC, id) AS phone
            FROM companies c
            WHERE c.{rel_type} = ?
            ORDER BY c.name LIMIT ?
        """, [value, limit]).fetchall()
    
    return {"code": 0, "message": "ok", "data": {
        "type": rel_type,
        "value": value,
        "count": len(rows),
        "results": [dict(r) for r in rows]
    }}


@mcp.tool()
def get_companies_list(
    q: str = None,
    city: str = None,
    district: str = None,
    business_status: str = None,
    industry: str = None,
    year_from: str = None,
    year_to: str = None,
    cap_from: float = None,
    cap_to: float = None,
    insured_from: int = None,
    insured_to: int = None,
    sort: str = "id",
    dir: str = "desc",
    page: int = 1,
    per_page: int = 25
) -> dict:
    """企业列表（支持筛选/排序/分页）
    
    Args:
        q: 搜索关键词（名称/法人/信用代码/电话）
        city: 城市
        district: 区县
        business_status: 经营状态
        industry: 行业
        year_from/year_to: 成立年份区间（YYYY）
        cap_from/cap_to: 注册资本区间（万元）
        insured_from/insured_to: 社保人数区间
        sort: 排序字段（id/name/established_date/registered_capital 等）
        dir: 排序方向（asc/desc）
        page: 页码（默认 1）
        per_page: 每页条数（默认 25，最大 100）
    
    Returns:
        企业列表，包含 total/page/per_page/pages/results
    """
    db = get_db()
    page = max(1, page)
    per_page = min(500, max(10, per_page))
    
    clauses = []
    params = []
    
    # 文本搜索
    if q:
        like_q = f"%{q}%"
        norm_q = f"%{normalize_name(q)}%"
        clauses.append(
            "(normalized_name LIKE ? OR legal_person LIKE ? "
            "OR credit_code = ?)"
        )
        params.extend([norm_q, like_q, q])
    
    # 精确筛选
    if city:
        clauses.append("city = ?")
        params.append(city)
    if district:
        clauses.append("district = ?")
        params.append(district)
    if business_status:
        clauses.append("business_status = ?")
        params.append(business_status)
    if industry:
        clauses.append("industry = ?")
        params.append(industry)
    
    # 年份区间
    if year_from:
        clauses.append("established_date >= ?")
        params.append(f"{year_from}-01-01")
    if year_to:
        clauses.append("established_date <= ?")
        params.append(f"{year_to}-12-31")
    
    # 注册资本区间
    if cap_from is not None:
        clauses.append(
            "CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL) >= ?"
        )
        params.append(float(cap_from))
    if cap_to is not None:
        clauses.append(
            "CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL) <= ?"
        )
        params.append(float(cap_to))
    
    # 社保人数区间
    if insured_from is not None:
        clauses.append("CAST(insured_count AS INTEGER) >= ?")
        params.append(int(insured_from))
    if insured_to is not None:
        clauses.append("CAST(insured_count AS INTEGER) <= ?")
        params.append(int(insured_to))
    
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    
    # 排序
    allowed_sorts = {
        "id": "id", "name": "normalized_name", "province": "province",
        "city": "city", "established_date": "established_date",
        "business_status": "business_status", "created_at": "created_at",
        "legal_person": "legal_person",
        "registered_capital": "registered_capital"
    }
    sort_col = allowed_sorts.get(sort, "id")
    dir_sql = "ASC" if dir == "asc" else "DESC"
    
    # 总数
    total = db.execute(
        f"SELECT COUNT(*) FROM companies {where}", params
    ).fetchone()[0]
    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page
    
    # 查询
    rows = db.execute(f"""
        SELECT id, name, credit_code, legal_person, city, district,
               business_status, established_date, registered_capital,
               industry, enterprise_scale,
               (SELECT group_concat(phone, '; ')
                FROM company_phones
                WHERE company_id = companies.id
                ORDER BY is_primary DESC, id) AS phone
        FROM companies {where}
        ORDER BY {sort_col} {dir_sql}
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()
    
    return {"code": 0, "message": "ok", "data": {
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "results": [dict(r) for r in rows]
    }}


def _stats_grouped(field: str, page: int, per_page: int, min_count: int) -> dict:
    """通用分组统计"""
    db = get_db()
    total = db.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT {field} FROM companies
            WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
            GROUP BY {field}
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()[0]
    
    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page
    
    rows = db.execute(f"""
        SELECT {field} AS val, COUNT(*) AS cnt,
               GROUP_CONCAT(name) AS company_names
        FROM companies
        WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
        GROUP BY {field}
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ? OFFSET ?
    """, [min_count, per_page, offset]).fetchall()
    
    return {
        "field": field,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": pages,
        "min_count": min_count,
        "results": [dict(r) for r in rows]
    }


@mcp.tool()
def get_stats(
    stat_type: str,
    min_count: int = 2,
    page: int = 1,
    per_page: int = 25
) -> dict:
    """统计查询（法人/股东/行业）
    
    Args:
        stat_type: 统计类型（legal_person/shareholder/industry）
        min_count: 最小关联企业数（默认 2）
        page: 页码（默认 1）
        per_page: 每页条数（默认 25，最大 100）
    
    Returns:
        统计结果
    """
    page = max(1, page)
    per_page = min(500, max(10, per_page))
    min_count = max(2, min_count)
    
    field_map = {
        "legal_person": "legal_person",
        "shareholder": "shareholders",
        "industry": "industry"
    }
    
    if stat_type not in field_map:
        return {"code": 1001, "message": "stat_type 必须是 legal_person/shareholder/industry 之一", "data": None}
    
    field = field_map[stat_type]
    return {"code": 0, "message": "ok", "data": _stats_grouped(field, page, per_page, min_count)}


# ── 电话重复数查询 ───────────────────────────────────────────
# 注：_PHONE_RE、_phone_dup_count、_extract_and_annotate 已从 api.py 复用
# （在文件顶部导入），避免重复维护导致行为漂移。


@mcp.tool()
def check_phone_count(phone: str) -> str:
    """查询单个电话号码的重复数（被多少家企业共用）。
    重复数越高，该号码越可能是中介/代理记账号码。
    
    Args:
        phone: 电话号码（支持手机、座机、400/800）
    
    Returns:
        格式化的查询结果字符串
    """
    db = get_db()
    norm = normalize_phone(phone)
    if not norm:
        return " 无效的电话号码"
    
    count = _phone_dup_count(db, norm)
    
    if count == 0:
        return f" {phone} → 未在数据库中找到，共 0 家"
    elif count == 1:
        return f"📞 {phone}（归一化：{norm}）→ 共 1 家 ✅ 可信号码"
    elif count <= 5:
        return f"📞 {phone}（归一化：{norm}）→ 共 {count} 家 ⚠️ 少量重复"
    else:
        return f" {phone}（归一化：{norm}）→ 共 {count} 家 🔴 高度重复，可能是中介号码"


@mcp.tool()
def check_phones_batch(phones: list) -> str:
    """批量查询多个电话号码的重复数。
    
    Args:
        phones: 电话号码列表，如 ["13800138000", "0571-88889999"]
    
    Returns:
        批量查询结果，每行一个号码
    """
    db = get_db()
    if not phones or len(phones) > 200:
        return "❌ 请提供 1-200 个号码"
    
    lines = []
    for raw in phones:
        raw = str(raw).strip()
        if not raw:
            continue
        norm = normalize_phone(raw)
        if not norm:
            lines.append(f"  ❌ {raw}: 无效号码")
            continue
        count = _phone_dup_count(db, norm)
        if count == 0:
            lines.append(f"   {raw} → 共 0 家")
        elif count == 1:
            lines.append(f"  📞 {raw} → 共 1 家 ✅")
        elif count <= 5:
            lines.append(f"  📞 {raw} → 共 {count} 家 ⚠️")
        else:
            lines.append(f"  📞 {raw} → 共 {count} 家 🔴")
    
    return f"查询 {len(lines)} 个号码：\n" + "\n".join(lines)


@mcp.tool()
def annotate_phones(text: str) -> str:
    """从一段文本中提取所有电话号码，并标注每个号码的重复数。

    自动覆盖已有标注：如果文本里已经有 '电话 (N)' 格式的旧标注，
    会用最新的重复数覆盖，而不是叠加成 '(N) (N)'。

    Args:
        text: 任意文本（可能包含电话号码）

    Returns:
        标注后的文本，每个号码后面带有重复数
    """
    db = get_db()
    # 复用 api.py 的 _extract_and_annotate，行为与 /api/annotate 一致
    annotated, matches, unique_phones = _extract_and_annotate(db, text)

    if not matches:
        return "📞 文本中未发现电话号码"

    return f"发现 {len(matches)} 个号码（{len(unique_phones)} 个不重复）：\n\n{annotated}"


# ─ extract_and_import：智能提取录入 ──────────────────────────────────────────

@mcp.tool()
def extract_and_import(text: str, method: str = "auto") -> dict:
    """从文本中提取企业信息并录入数据库。

    Args:
        text: 包含企业信息的文本（如从天眼查、企查查复制的工商信息）
        method: 提取方式，可选 auto/regex/llm，默认 auto

    Returns:
        提取并录入结果
    """
    result = extract_company_info(text, method=method)
    fields = result["fields"]

    if not fields or not fields.get("name"):
        return {"error": "未能提取到企业名称"}

    # 连接数据库
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        from data_helpers import sync_phones, sync_emails, sync_shareholders
        from utils import normalize_name, normalize_person_name, normalize_email, normalize_credit_code

        # 更新时自动维护的 normalized 字段
        _NORMALIZED_MAP = {
        "name": ("normalized_name", normalize_name),
        "legal_person": ("normalized_legal_person", normalize_person_name),
        }

        name = fields["name"]
        norm_name = normalize_name(name)

        # 提取电话和股东
        phone_val = str(fields.pop("phone", "")).strip()
        email_val = str(fields.pop("email", "")).strip()
        shareholders_val = str(fields.pop("shareholders", "")).strip()
        fields.pop("taxpayer_id", None)

        # 重复检查
        existing = conn.execute(
            "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
            [norm_name]
        ).fetchone()

        if existing:
            # 更新已有记录
            company_id = existing["id"]
            for field, value in fields.items():
                if field in ("source", "status"):
                    continue
                conn.execute(
                    f"UPDATE companies SET {field} = ? WHERE id = ?",
                    [value, company_id]
                )
            # 更新规范化字段
            for field, (norm_field, norm_fn) in _NORMALIZED_MAP.items():
                if field in fields:
                    conn.execute(
                        f"UPDATE companies SET {norm_field} = ? WHERE id = ?",
                        [norm_fn(fields[field]), company_id]
                    )
            # 更新电话、邮箱和股东
            if phone_val:
                sync_phones(conn, company_id, phone_val)
            if email_val:
                sync_emails(conn, company_id, email_val)
            if shareholders_val:
                sync_shareholders(conn, company_id, shareholders_val)
            conn.commit()

            return {
                "action": "updated",
                "id": company_id,
                "name": name,
                "method_used": result["method_used"],
                "field_count": result["field_count"],
                "message": f"已更新: {name}",
            }

        # 新建记录
        fields["normalized_name"] = norm_name
        if "legal_person" in fields:
            fields["normalized_legal_person"] = normalize_person_name(fields["legal_person"])
        if "credit_code" in fields:
            fields["credit_code"] = normalize_credit_code(fields["credit_code"])
            if "taxpayer_id" not in fields:
                fields["taxpayer_id"] = fields["credit_code"]

        fields["source"] = "mcp"
        fields["status"] = "active"

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        cursor = conn.execute(
            f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
            list(fields.values())
        )
        company_id = cursor.lastrowid

        if phone_val:
            sync_phones(conn, company_id, phone_val)
        if email_val:
            sync_emails(conn, company_id, email_val)
        if shareholders_val:
            sync_shareholders(conn, company_id, shareholders_val)

        conn.commit()

        return {
            "action": "created",
            "id": company_id,
            "name": name,
            "method_used": result["method_used"],
            "field_count": result["field_count"],
            "message": f"已录入: {name}",
        }

    except Exception as e:
        return {"error": str(e)}
    finally:
        conn.close()


# ── 按地址查询企业 ───────────────────────────────────────────────────────────────

@mcp.tool()
def search_by_address(address: str, limit: int = 20) -> dict:
    """按地址查询企业，返回企业详情页链接。

    用于将外部表格中的企业地址与 EntHub 关联：
    输入地址关键词，返回匹配的企业及其详情页 URL。

    Args:
        address: 地址关键词（如“文三路100号”“西湖区”等）
        limit: 返回条数（默认 20，最大 50）

    Returns:
        匹配的企业列表，每条含 id/name/address/detail_url
    """
    db = get_db()
    address = address.strip()
    limit = min(50, max(1, limit))

    if not address:
        return {"code": 0, "message": "ok", "data": {"query": "", "count": 0, "results": []}}

    like_q = f"%{address}%"
    rows = db.execute(
        """SELECT c.id, c.name, c.address, c.credit_code,
                  c.legal_person, c.city, c.district,
                  (SELECT group_concat(phone, '; ')
                   FROM company_phones
                   WHERE company_id = c.id
                   ORDER BY is_primary DESC, id) AS phone
           FROM companies c
           WHERE c.address LIKE ? OR c.annual_report_address LIKE ?
              OR c.mailing_address LIKE ?
           ORDER BY c.id LIMIT ?""",
        [like_q, like_q, like_q, limit]
    ).fetchall()

    results = []
    for r in rows:
        d = dict(r)
        d["detail_url"] = f"{_BASE_URL}/company/{d['id']}"
        results.append(d)

    return {"code": 0, "message": "ok", "data": {
        "query": address,
        "count": len(results),
        "results": results
    }}


# ── 启动 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # 解析参数：默认 stdio 模式（Claude Desktop 兼容），--http 切换 HTTP 模式
    use_http = "--http" in sys.argv or "--transport" in sys.argv

    if use_http:
        print("🚀 EntHub MCP Server 启动中（HTTP 模式）...")
        print("📡 监听地址：http://localhost:5310/mcp")
        print("🔧 可用工具：")
        print("   - search_companies: 搜索企业")
        print("   - batch_match_companies: 批量匹配企业名称（最多 1000 个）")
        print("   - get_company_detail: 企业详情")
        print("   - find_relations: 关联企业查询")
        print("   - get_companies_list: 企业列表")
        print("   - get_stats: 统计查询")
        print("   - extract_and_import: 智能提取录入")
        print("   - check_phone_count: 查询号码重复数")
        print("   - check_phones_batch: 批量号码重复数")
        print("   - annotate_phones: 文本号码标注")
        print("   - search_by_address: 按地址查询企业，返回详情页链接")
        print("\n在 AI 工具中配置 MCP Server 地址后，可以用自然语言查询：")
        print('  "帮我找所有叫科技的公司"')
        print('  "查一下 ID 为 2 的企业详情"')
        print('  "找和张三有关联的企业"')
        print('  "统计出现 5 次以上的法人"')
        print('  "从这段文本提取企业信息并录入"')
        # 获取底层 Starlette app，包一层认证中间件
        starlette_app = mcp.streamable_http_app()
        authed_app = TokenAuthMiddleware(starlette_app)

        import uvicorn
        import anyio
        config = uvicorn.Config(authed_app, host="0.0.0.0", port=5310,
                                log_level="info")
        server = uvicorn.Server(config)
        anyio.run(server.serve)
    else:
        # stdio 模式（Claude Desktop 自动拉起，无需打印日志）
        mcp.run(transport="stdio")
