"""EntHub REST API

提供 JSON 接口，供其他应用、AI 工具、MCP Server 调用。
所有响应格式统一：{"code": 0, "message": "ok", "data": {...}}
"""
import math
import re
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
        per_page = min(500, max(10, request.args.get('per_page', 25, type=int)))

        clauses = []
        params = []

        # 文本搜索（名称/法人/信用代码）
        q = request.args.get('q', '').strip()
        if q:
            like_q = f"%{q}%"
            norm_q = f"%{normalize_name(q)}%"
            clauses.append(
                "(normalized_name LIKE ? OR legal_person LIKE ? "
                "OR credit_code = ?)"
            )
            params.extend([norm_q, like_q, q])

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
            "legal_person": "legal_person",
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
            related = g.db.execute(f"""
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
            SELECT DISTINCT c.id, c.name, c.address,
                   (SELECT group_concat(phone, '; ')
                    FROM company_phones
                    WHERE company_id = c.id
                    ORDER BY is_primary DESC, is_recommended DESC) AS phone
            FROM companies c
            JOIN company_phones cp ON cp.company_id = c.id
            WHERE cp.normalized_phone = ?
            ORDER BY c.name LIMIT ?
        """, [norm_value, limit]).fetchall()
    else:
        rows = g.db.execute(f"""
            SELECT c.id, c.name, c.address,
                   (SELECT group_concat(phone, '; ')
                    FROM company_phones
                    WHERE company_id = c.id
                    ORDER BY is_primary DESC, is_recommended DESC) AS phone
            FROM companies c
            WHERE c.{rel_type} = ?
            ORDER BY c.name LIMIT ?
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
    per_page = min(500, max(10, request.args.get('per_page', 25, type=int)))
    min_count = max(2, request.args.get('min', 2, type=int))
    return _ok(_stats_grouped('legal_person', page, per_page, min_count))


@api_bp.route('/api/stats/shareholder')
def stats_shareholder():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(500, max(10, request.args.get('per_page', 25, type=int)))
    min_count = max(2, request.args.get('min', 2, type=int))
    return _ok(_stats_grouped('shareholders', page, per_page, min_count))


@api_bp.route('/api/stats/industry')
def stats_industry():
    page = max(1, request.args.get('page', 1, type=int))
    per_page = min(500, max(10, request.args.get('per_page', 25, type=int)))
    min_count = max(2, request.args.get('min', 2, type=int))
    return _ok(_stats_grouped('industry', page, per_page, min_count))


# ── 电话重复数查询 ────────────────────────────────────────────

# 号码识别正则（按优先级匹配）
_PHONE_RE = re.compile(
    r'(?<!\d)'
    r'(?:'
    r'1[3-9]\d{9}'
    r'|'
    r'\d{3,4}-\d{7,8}(?:-\d{1,6})?'
    r'|'
    r'\d{7,8}(?:-\d{1,6})?'
    r'|'
    r'400-\d{3}-\d{4}'
    r'|'
    r'800-\d{3}-\d{4}'
    r')'
    r'(?!\d)',
    re.ASCII
)

# 号码 + 已有标注：匹配 "电话号码 (N)" 整体，用于覆盖旧标注而非叠加
# 例：13800138000 (3)、0817-5552288 (0)
_PHONE_WITH_ANNOTATION_RE = re.compile(
    _PHONE_RE.pattern +              # 已含号码边界 (?!\d)
    r'(\s*\(\d+\))?',                # 可选的旧标注
    re.ASCII
)


def _phone_dup_count(db, normalized_phone):
    """查询号码的重复数（从 company_phones 表）"""
    if not normalized_phone:
        return 0
    return db.execute(
        "SELECT COUNT(DISTINCT company_id) FROM company_phones WHERE normalized_phone = ?",
        [normalized_phone]
    ).fetchone()[0]


@api_bp.route('/api/phone_count')
def phone_count():
    """查询单个号码的重复数"""
    phone = request.args.get('phone', '').strip()
    if not phone:
        return _err(1001, "phone 参数不能为空")

    norm = normalize_phone(phone)
    if not norm:
        return _err(1001, "无效的电话号码")

    count = _phone_dup_count(g.db, norm)
    return _ok({
        "phone": phone,
        "normalized": norm,
        "count": count
    })


@api_bp.route('/api/phone_count_batch', methods=['POST'])
def phone_count_batch():
    """批量查询号码重复数"""
    try:
        data = request.get_json() or {}
        phones = data.get('phones', [])
        if not phones or not isinstance(phones, list):
            return _err(1001, "phones 必须是号码数组")
        if len(phones) > 200:
            return _err(1001, "一次最多查询 200 个号码")
    except Exception:
        return _err(1001, "请求体必须是 JSON 格式")

    results = []
    for raw in phones:
        raw = str(raw).strip()
        if not raw:
            continue
        norm = normalize_phone(raw)
        if not norm:
            continue
        count = _phone_dup_count(g.db, norm)
        results.append({
            "phone": raw,
            "normalized": norm,
            "count": count
        })

    return _ok({"results": results})


@api_bp.route('/api/phone_count_text', methods=['POST'])
def phone_count_text():
    """文本中提取号码并标注重复数"""
    try:
        data = request.get_json() or {}
        text = data.get('text', '')
        if not text:
            return _err(1001, "text 不能为空")
        if len(text) > 10000:
            return _err(1001, "文本不能超过 10000 字")
    except Exception:
        return _err(1001, "请求体必须是 JSON 格式")

    annotated, matches, unique_phones = _extract_and_annotate(g.db, text)

    return _ok({
        "original_text": text,
        "annotated_text": annotated,
        "phones": unique_phones,
        "phone_count": len(unique_phones)
    })


# ── 快速标注（纯文本接口）────────────────────────────────────


def _extract_and_annotate(db, text):
    """共享函数：提取号码 + 标注文本（自动覆盖已有标注，不叠加）

    输入：
        "电话 13800138000 (3) 另一个 13900139000"
    输出：
        "电话 13800138000 (新count) 另一个 13900139000 (新count)"

    不会变成 "13800138000 (3) (新count)"。

    返回：(annotated_text, matches, unique_phones)
    """
    matches = []
    for m in _PHONE_WITH_ANNOTATION_RE.finditer(text):
        # m.group(0) 是"号码 + 可选旧标注"整体，剥离掉末尾的 "(N)" 得到纯号码
        raw = re.sub(r'\s*\(\d+\)$', '', m.group(0)).rstrip()
        norm = normalize_phone(raw)
        if not norm:
            continue
        count = _phone_dup_count(db, norm)
        matches.append({
            "phone": raw,
            "normalized": norm,
            "count": count,
            # 替换范围覆盖旧标注，整体替换为新标注，避免叠加
            "position": [m.start(), m.end()]
        })

    # 去重
    seen_norms = {}
    for m in matches:
        seen_norms.setdefault(m["normalized"], m)
    unique_phones = list(seen_norms.values())

    # 从后往前替换，避免位置偏移
    annotated = text
    for m in reversed(matches):
        replacement = f"{m['phone']} ({m['count']})"
        annotated = annotated[:m["position"][0]] + replacement + annotated[m["position"][1]:]

    return annotated, matches, unique_phones


@api_bp.route('/api/annotate', methods=['GET', 'POST'])
def quick_annotate():
    """快速标注：返回纯文本（管道友好）

    支持多种入参方式：
    - GET: ?text=xxx（短文本，浏览器/curl 测试）
    - POST JSON: {"text": "xxx"}
    - POST 纯文本: body 是原始文本（curl --data-binary）
    - POST form: text=xxx

    返回：
    - 成功：标注后的纯文本（text/plain）
    - 失败：错误信息纯文本 + 4xx/5xx 状态码
    """
    # GET
    if request.method == 'GET':
        text = request.args.get('text', '')
    else:
        # POST: 根据Content-Type 解析
        ct = (request.content_type or '').lower()
        if 'application/json' in ct:
            text = (request.get_json(silent=True) or {}).get('text', '')
        elif 'application/x-www-form-urlencoded' in ct or 'multipart/form-data' in ct:
            text = request.form.get('text', '')
        else:
            # 纯文本（curl --data-binary / 管道）
            text = request.get_data(as_text=True)

    # 校验
    if not text or not text.strip():
        return "错误：文本为空", 400, {'Content-Type': 'text/plain; charset=utf-8'}
    if len(text) > 10000:
        return "错误：文本超过 10000 字", 400, {'Content-Type': 'text/plain; charset=utf-8'}

    try:
        annotated, _, _ = _extract_and_annotate(g.db, text)
        return annotated, 200, {'Content-Type': 'text/plain; charset=utf-8'}
    except Exception as e:
        return f"错误：{e}", 500, {'Content-Type': 'text/plain; charset=utf-8'}
