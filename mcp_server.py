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
from mcp.server.fastmcp import FastMCP
from db import get_db
from utils import normalize_phone, normalize_credit_code, normalize_name
import math

# 复用 api.py 中已稳定的电话标注实现（避免重复维护导致 bug 漂移）
from api import (
    _phone_dup_count,
    _extract_and_annotate,
)

# 创建 MCP Server（host/port 仅 HTTP 模式生效）
mcp = FastMCP("EntHub", json_response=True, host="0.0.0.0", port=8000)


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
    
    Args:
        q: 搜索关键词
        limit: 返回条数（默认 20，最大 50）
    
    Returns:
        搜索结果，包含 query/type/count/results
    """
    db = get_db()
    q = q.strip()
    limit = min(50, max(1, limit))
    
    if not q:
        return {"code": 0, "message": "ok", "data": {"query": "", "type": "text", "count": 0, "results": []}}
    
    query_type = _detect_query_type(q)
    
    if query_type == "phone":
        norm_q = normalize_phone(q)
        rows = db.execute(
            """SELECT c.id, c.name, c.address, c.credit_code,
                      c.legal_person, c.city,
                      (SELECT group_concat(phone, '; ')
                       FROM company_phones
                       WHERE company_id = c.id
                       ORDER BY is_primary DESC, is_recommended DESC) AS phone,
                      '电话' AS matched_field
               FROM company_phones cp
               JOIN companies c ON cp.company_id = c.id
               WHERE cp.normalized_phone = ?
               ORDER BY c.name LIMIT ?""",
            [norm_q, limit]
        ).fetchall()
        return {"code": 0, "message": "ok", "data": {
            "query": q, "type": "phone",
            "count": len(rows), "results": [dict(r) for r in rows]
        }}
    elif query_type == "credit_code":
        norm_q = normalize_credit_code(q)
        rows = db.execute(
            """SELECT c.id, c.name, c.address, c.credit_code,
                      c.legal_person, c.city,
                      (SELECT group_concat(phone, '; ')
                       FROM company_phones
                       WHERE company_id = c.id
                       ORDER BY is_primary DESC, is_recommended DESC) AS phone,
                      '信用代码' AS matched_field
               FROM companies c WHERE c.credit_code = ?
               LIMIT ?""",
            [norm_q, limit]
        ).fetchall()
        return {"code": 0, "message": "ok", "data": {
            "query": q, "type": "credit_code",
            "count": len(rows), "results": [dict(r) for r in rows]
        }}
    else:
        norm_q_name = normalize_name(q)
        like_q = "%" + q + "%"
        like_name = "%" + norm_q_name + "%"
        rows = db.execute(
            """SELECT c.id, c.name, c.address, c.credit_code,
                      c.legal_person, c.city,
                      (SELECT group_concat(phone, '; ')
                       FROM company_phones
                       WHERE company_id = c.id
                       ORDER BY is_primary DESC, is_recommended DESC) AS phone,
                      m.matched_field
               FROM (
                   SELECT id, '名称' AS matched_field, 1 AS priority FROM companies WHERE normalized_name LIKE ?
                   UNION ALL
                   SELECT id, '曾用名', 2 FROM companies WHERE former_name LIKE ?
                   UNION ALL
                   SELECT id, '地址', 3 FROM companies WHERE address LIKE ?
                   UNION ALL
                   SELECT id, '法人', 4 FROM companies WHERE legal_person LIKE ?
                   UNION ALL
                   SELECT id, '股东', 5 FROM companies WHERE shareholders LIKE ?
                   UNION ALL
                   SELECT id, '邮箱', 6 FROM companies WHERE email LIKE ?
                   UNION ALL
                   SELECT id, '网站', 7 FROM companies WHERE website LIKE ?
               ) m
               JOIN companies c ON c.id = m.id
               GROUP BY c.id ORDER BY m.priority, c.name LIMIT ?""",
            [like_name, like_q, like_q, like_q, like_q, like_q, like_q, limit]
        ).fetchall()
        return {"code": 0, "message": "ok", "data": {
            "query": q, "type": "text",
            "count": len(rows), "results": [dict(r) for r in rows]
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
        SELECT cp.phone, cp.normalized_phone, cp.is_primary, cp.is_recommended,
               (SELECT COUNT(DISTINCT company_id) FROM company_phones cp2
                WHERE cp2.normalized_phone = cp.normalized_phone) AS dup_count
        FROM company_phones cp
        WHERE cp.company_id = ?
        ORDER BY cp.is_primary DESC, cp.is_recommended DESC
    """, [company_id]).fetchall()
    
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
                    ORDER BY cp2.is_primary DESC, cp2.is_recommended DESC) AS phone
            FROM companies c
            JOIN company_phones cp ON cp.company_id = c.id
            WHERE c.id <> ? AND cp.normalized_phone IN ({placeholders})
              AND cp.normalized_phone <> ''
            ORDER BY c.name LIMIT 10
        """, [company_id] + phone_norms).fetchall()
        relations['phones'] = [dict(r) for r in related]
    
    # 其他关联（法人/股东/行业/邮箱）
    for field, key in [('legal_person', 'legal_person'),
                       ('shareholders', 'shareholders'),
                       ('industry', 'industry'),
                       ('email', 'email')]:
        val = row[field] if row[field] else None
        if val and val != '-' and (field != 'email' or '@' in val):
            related = db.execute(f"""
                SELECT c.id, c.name, c.address,
                       (SELECT group_concat(phone, '; ')
                        FROM company_phones
                        WHERE company_id = c.id
                        ORDER BY is_primary DESC, is_recommended DESC) AS phone
                FROM companies c
                WHERE c.id <> ? AND c.{field} = ? AND c.{field} <> ''
                ORDER BY c.name LIMIT 10
            """, [company_id, val]).fetchall()
            relations[key] = [dict(r) for r in related]
            cnt = db.execute(
                f"SELECT COUNT(*) FROM companies WHERE {field} = ?", [val]
            ).fetchone()[0]
            relation_counts[key] = cnt
    
    # 标签
    tags = db.execute("""
        SELECT t.id, t.name, t.color FROM tags t
        JOIN company_tags ct ON ct.tag_id = t.id
        WHERE ct.company_id = ?
        ORDER BY t.name
    """, [company_id]).fetchall()
    
    return {"code": 0, "message": "ok", "data": {
        "company": company,
        "phones": [dict(p) for p in company_phones],
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
                    ORDER BY cp2.is_primary DESC, cp2.is_recommended DESC) AS phone
            FROM companies c
            JOIN company_phones cp ON cp.company_id = c.id
            WHERE cp.normalized_phone = ?
            ORDER BY c.name LIMIT ?
        """, [norm_value, limit]).fetchall()
    else:
        rows = db.execute(f"""
            SELECT c.id, c.name, c.address,
                   (SELECT group_concat(phone, '; ')
                    FROM company_phones
                    WHERE company_id = c.id
                    ORDER BY is_primary DESC, is_recommended DESC) AS phone
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
                ORDER BY is_primary DESC, is_recommended DESC) AS phone
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
        return f" {phone} → 未在数据库中找到，重复 0 次"
    elif count == 1:
        return f"📞 {phone}（归一化：{norm}）→ 重复 1 次 ✅ 可信号码"
    elif count <= 5:
        return f"📞 {phone}（归一化：{norm}）→ 重复 {count} 次 ⚠️ 少量重复"
    else:
        return f" {phone}（归一化：{norm}）→ 重复 {count} 次 🔴 高度重复，可能是中介号码"


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
            lines.append(f"   {raw} → 0 次")
        elif count == 1:
            lines.append(f"  📞 {raw} → 1 次 ✅")
        elif count <= 5:
            lines.append(f"  📞 {raw} → {count} 次 ⚠️")
        else:
            lines.append(f"  📞 {raw} → {count} 次 🔴")
    
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


# ── 启动 ──────────────────────────────────────────────────────

if __name__ == "__main__":
    # 解析参数：默认 stdio 模式（Claude Desktop 兼容），--http 切换 HTTP 模式
    use_http = "--http" in sys.argv or "--transport" in sys.argv

    if use_http:
        print("🚀 EntHub MCP Server 启动中（HTTP 模式）...")
        print("📡 监听地址：http://localhost:8000/mcp")
        print("🔧 可用工具：")
        print("   - search_companies: 搜索企业")
        print("   - get_company_detail: 企业详情")
        print("   - find_relations: 关联企业查询")
        print("   - get_companies_list: 企业列表")
        print("   - get_stats: 统计查询")
        print("\n在 AI 工具中配置 MCP Server 地址后，可以用自然语言查询：")
        print('  "帮我找所有叫科技的公司"')
        print('  "查一下 ID 为 2 的企业详情"')
        print('  "找和张三有关联的企业"')
        print('  "统计出现 5 次以上的法人"')
        mcp.run(transport="streamable-http")
    else:
        # stdio 模式（Claude Desktop 自动拉起，无需打印日志）
        mcp.run(transport="stdio")
