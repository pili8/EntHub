"""EntHub REST API

提供 JSON 接口，供其他应用、AI 工具、MCP Server 调用。
所有响应格式统一：{"code": 0, "message": "ok", "data": {...}}
"""
import math
from flask import Blueprint, request, jsonify, g

from utils import normalize_phone, normalize_credit_code, normalize_name

api_bp = Blueprint('api_bp', __name__)


# ── 响应工具 ──────────────────────────────────────────────────

def _ok(data=None, message="ok"):
    return jsonify({"code": 0, "message": message, "data": data})


def _err(code, message, status=200):
    return jsonify({"code": code, "message": message, "data": None}), status


# ── 企业列表 ──────────────────────────────────────────────────

@api_bp.route('/api/companies')
def companies():
    """企业列表 + 筛选 + 分页 + 排序"""
    try:
        page = max(1, request.args.get('page', 1, type=int))
        per_page = min(100, max(10, request.args.get('per_page', 25, type=int)))

        clauses = []
        params = []

        # 文本搜索（名称/法人/信用代码/电话）
        q = request.args.get('q', '').strip()
        if q:
            like_q = f"%{q}%"
            norm_q = f"%{normalize_name(q)}%"
            clauses.append(
                "(normalized_name LIKE ? OR legal_person LIKE ? "
                "OR credit_code = ? OR phone = ?)"
            )
            params.extend([norm_q, like_q, q, q])

        # 精确筛选
        for f in ('city', 'district', 'business_status', 'industry'):
            val = request.args.get(f, '').strip()
            if val:
                clauses.append(f"{f} = ?")
                params.append(val)

        # 年份区间
        year_from = request.args.get('year_from', '').strip()
        year_to = request.args.get('year_to', '').strip()
        if year_from:
            clauses.append("established_date >= ?")
            params.append(f"{year_from}-01-01")
        if year_to:
            clauses.append("established_date <= ?")
            params.append(f"{year_to}-12-31")

        # 注册资本区间（万元）
        cap_from = request.args.get('cap_from', '').strip()
        cap_to = request.args.get('cap_to', '').strip()
        if cap_from:
            clauses.append(
                "CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL) >= ?"
            )
            params.append(float(cap_from))
        if cap_to:
            clauses.append(
                "CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL) <= ?"
            )
            params.append(float(cap_to))

        # 社保人数区间
        insured_from = request.args.get('insured_from', '').strip()
        insured_to = request.args.get('insured_to', '').strip()
        if insured_from:
            clauses.append("CAST(insured_count AS INTEGER) >= ?")
            params.append(int(insured_from))
        if insured_to:
            clauses.append("CAST(insured_count AS INTEGER) <= ?")
            params.append(int(insured_to))

        where = "WHERE " + " AND ".join(clauses) if clauses else ""

        # 排序
        allowed_sorts = {
            "id": "id", "name": "normalized_name", "province": "province",
            "city": "city", "established_date": "established_date",
            "business_status": "business_status", "created_at": "created_at",
            "phone": "phone", "legal_person": "legal_person",
            "registered_capital": "registered_capital"
        }
        sort = request.args.get('sort', 'id')
        dir_param = request.args.get('dir', 'desc')
        sort_col = allowed_sorts.get(sort, "id")
        dir_sql = "ASC" if dir_param == "asc" else "DESC"

        # 总数
        total = g.db.execute(
            f"SELECT COUNT(*) FROM companies {where}", params
        ).fetchone()[0]
        pages = max(1, math.ceil(total / per_page))
        offset = (page - 1) * per_page

        # 查询
        rows = g.db.execute(f"""
            SELECT id, name, phone, credit_code, legal_person, city, district,
                   business_status, established_date, registered_capital,
                   industry, enterprise_scale
            FROM companies {where}
            ORDER BY {sort_col} {dir_sql}
            LIMIT ? OFFSET ?
        """, params + [per_page, offset]).fetchall()

        return _ok({
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": pages,
            "results": [dict(r) for r in rows]
        })
    except (ValueError, TypeError) as e:
        return _err(1001, f"参数错误：{e}")
    except Exception as e:
        return _err(2001, f"查询失败：{e}")


# ── 企业详情 ──────────────────────────────────────────────────

@api_bp.route('/api/companies/<int:company_id>')
def company_detail(company_id):
    """企业详情 + 关联 + 标签"""
    row = g.db.execute(
        "SELECT * FROM companies WHERE id = ?", [company_id]
    ).fetchone()
    if not row:
        return _err(1002, "企业不存在", 404)

    company = dict(row)

    # 关联电话
    company_phones = g.db.execute("""
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
        related = g.db.execute(f"""
            SELECT DISTINCT c.id, c.name, c.phone, c.address
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
            related = g.db.execute(f"""
                SELECT id, name, phone, address FROM companies
                WHERE id <> ? AND {field} = ? AND {field} <> ''
                ORDER BY name LIMIT 10
            """, [company_id, val]).fetchall()
            relations[key] = [dict(r) for r in related]
            cnt = g.db.execute(
                f"SELECT COUNT(*) FROM companies WHERE {field} = ?", [val]
            ).fetchone()[0]
            relation_counts[key] = cnt

    # 标签
    tags = g.db.execute("""
        SELECT t.id, t.name, t.color FROM tags t
        JOIN company_tags ct ON ct.tag_id = t.id
        WHERE ct.company_id = ?
        ORDER BY t.name
    """, [company_id]).fetchall()

    return _ok({
        "company": company,
        "phones": [dict(p) for p in company_phones],
        "relations": relations,
        "relation_counts": relation_counts,
        "tags": [dict(t) for t in tags]
    })


# ── 关联查询 ──────────────────────────────────────────────────

@api_bp.route('/api/relations')
def relations():
    """按关联类型查询企业"""
    rel_type = request.args.get('type', '').strip()
    value = request.args.get('value', '').strip()
    limit = min(100, request.args.get('limit', 20, type=int))

    allowed_types = ('phone', 'email', 'legal_person', 'shareholders')
    if rel_type not in allowed_types:
        return _err(1001, f"type 必须是 {'/'.join(allowed_types)} 之一")
    if not value:
        return _err(1001, "value 不能为空")

    if rel_type == 'phone':
        norm_value = normalize_phone(value)
        rows = g.db.execute("""
            SELECT DISTINCT c.id, c.name, c.phone, c.address
            FROM companies c
            JOIN company_phones cp ON cp.company_id = c.id
            WHERE cp.normalized_phone = ?
            ORDER BY c.name LIMIT ?
        """, [norm_value, limit]).fetchall()
    else:
        rows = g.db.execute(f"""
            SELECT id, name, phone, address FROM companies
            WHERE {rel_type} = ?
            ORDER BY name LIMIT ?
        """, [value, limit]).fetchall()

    return _ok({
        "type": rel_type,
        "value": value,
        "count": len(rows),
        "results": [dict(r) for r in rows]
    })


# ── 通用统计（法人/股东/行业）──────────────────────────────────

def _stats_grouped(field, page, per_page, min_count):
    total = g.db.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT {field} FROM companies
            WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
            GROUP BY {field}
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    rows = g.db.execute(f"""
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


@api_bp.route('/api/stats/legal_person')
def stats_legal_person():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(10, request.args.get('per_page', 25, type=int)))
    min_count = max(2, request.args.get('min', 2, type=int))
    return _ok(_stats_grouped('legal_person', page, per_page, min_count))


@api_bp.route('/api/stats/shareholder')
def stats_shareholder():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(10, request.args.get('per_page', 25, type=int)))
    min_count = max(2, request.args.get('min', 2, type=int))
    return _ok(_stats_grouped('shareholders', page, per_page, min_count))


@api_bp.route('/api/stats/industry')
def stats_industry():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(100, max(10, request.args.get('per_page', 25, type=int)))
    min_count = max(2, request.args.get('min', 2, type=int))
    return _ok(_stats_grouped('industry', page, per_page, min_count))
