"""EntHub REST API

提供 JSON 接口，供其他应用、AI 工具、MCP Server 调用。
所有响应格式统一：{"code": 0, "message": "ok", "data": {...}}
"""
import math
import re
from flask import Blueprint, request, jsonify, g

from utils import (
    normalize_phone, normalize_credit_code, normalize_name,
    normalize_person_name, normalize_email,
)
from data_helpers import sync_phones, sync_shareholders
from extract_service import extract_company_info

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


# ── 企业新建/更新 ──────────────────────────────────────────────

# 全量字段列表（与 routes/companies.py 保持一致）
_COMPANY_FIELDS = [
    "name", "address", "annual_report_address",
    "credit_code", "taxpayer_id", "registration_no", "org_code",
    "legal_person", "registered_capital", "paid_capital",
    "established_date", "approved_date", "business_term",
    "province", "city", "district", "insured_count",
    "company_type", "industry", "former_name", "website",
    "email", "other_email", "business_scope", "business_status",
    "enterprise_scale", "mailing_address", "english_name",
]

# 更新时自动维护的 normalized 字段
_NORMALIZED_MAP = {
    "name": ("normalized_name", normalize_name),
    "legal_person": ("normalized_legal_person", normalize_person_name),
    "email": ("normalized_email", normalize_email),
}


def _build_company_dict(data):
    """从请求数据中提取公司字段（过滤掉非公司字段）。"""
    fields = {}
    for f in _COMPANY_FIELDS:
        val = data.get(f)
        if val is not None:
            val = str(val).strip()
            if val and val not in ("-", "--", "无", "N/A", "null"):
                fields[f] = val
    # 信用代码归一化
    if "credit_code" in fields:
        fields["credit_code"] = normalize_credit_code(fields["credit_code"])
    return fields


@api_bp.route('/api/companies', methods=['POST'])
def create_company():
    """新建企业。

    请求 JSON: {
        "name": "企业名称",        // 必填
        "credit_code": "...",
        "legal_person": "...",
        ...其他工商字段...
        "phone": "138xxx; 028-xxx",  // 可选，存入 company_phones
        "other_phone": "...",
        "shareholders": "张三; 李四",  // 可选，存入 company_shareholders
        "source": "api",             // 来源标识，默认 api
    }
    """
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    if not name:
        return _err(1001, "企业名称不能为空")

    fields = _build_company_dict(data)
    if "name" not in fields:
        return _err(1001, "企业名称不能为空")

    # 设置 normalized 字段
    fields["normalized_name"] = normalize_name(name)
    if "legal_person" in fields:
        fields["normalized_legal_person"] = normalize_person_name(fields["legal_person"])
    if "email" in fields:
        fields["normalized_email"] = normalize_email(fields["email"])
    if "credit_code" in fields:
        fields["credit_code"] = normalize_credit_code(fields["credit_code"])

    # 来源
    fields["source"] = (data.get("source") or "api").strip() or "api"
    fields["status"] = "active"

    # 提取电话和股东
    phone_val = str(data.get("phone", "")).strip()
    other_phone_val = str(data.get("other_phone", "")).strip()
    shareholders_val = str(data.get("shareholders", "")).strip()

    # 重复检查
    norm_name = fields["normalized_name"]
    existing = g.db.execute(
        "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
        [norm_name]
    ).fetchone()
    if existing:
        return _err(1002, f"企业已存在: {existing['name']} (ID: {existing['id']})", 409)

    # 插入
    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    cursor = g.db.execute(
        f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
        list(fields.values())
    )
    company_id = cursor.lastrowid

    # 电话
    if phone_val or other_phone_val:
        sync_phones(g.db, company_id, phone_val, other_phone_val)

    # 股东
    if shareholders_val:
        sync_shareholders(g.db, company_id, shareholders_val)

    g.db.commit()

    return _ok({"id": company_id, "name": name}, f"已创建: {name}")


@api_bp.route('/api/companies/<int:company_id>', methods=['PUT'])
def update_company(company_id):
    """更新企业信息。

    请求 JSON: {
        "mode": "overwrite" | "merge",  // 默认 merge
        "name": "...",
        "phone": "...",
        ...其他字段...
    }
    """
    row = g.db.execute("SELECT id FROM companies WHERE id = ?", [company_id]).fetchone()
    if not row:
        return _err(1002, "企业不存在", 404)

    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "merge")

    fields = _build_company_dict(data)
    if not fields:
        return _err(1001, "没有提供任何字段")

    # 提取电话和股东
    phone_val = str(data.get("phone", "")).strip()
    other_phone_val = str(data.get("other_phone", "")).strip()
    shareholders_val = str(data.get("shareholders", "")).strip()

    # 电话/股东不入 companies 主表
    fields.pop("phone", None)
    fields.pop("other_phone", None)
    fields.pop("shareholders", None)

    updated_fields = []

    if mode == "overwrite":
        # 覆盖模式：直接更新所有字段
        for field, value in fields.items():
            g.db.execute(
                f"UPDATE companies SET {field} = ? WHERE id = ?",
                [value, company_id]
            )
            updated_fields.append(field)
    else:
        # merge 模式：仅填充空字段
        for field, value in fields.items():
            current = g.db.execute(
                f"SELECT {field} FROM companies WHERE id = ?",
                [company_id]
            ).fetchone()
            current_val = (current[0] or "").strip() if current else ""
            if not current_val or current_val in ("", "-", "--"):
                g.db.execute(
                    f"UPDATE companies SET {field} = ? WHERE id = ?",
                    [value, company_id]
                )
                updated_fields.append(field)

    # 更新 normalized 字段
    for field, (norm_field, norm_fn) in _NORMALIZED_MAP.items():
        if field in updated_fields:
            norm_val = norm_fn(fields.get(field, ""))
            g.db.execute(
                f"UPDATE companies SET {norm_field} = ? WHERE id = ?",
                [norm_val, company_id]
            )

    # 电话
    if phone_val or other_phone_val:
        sync_phones(g.db, company_id, phone_val, other_phone_val)
        updated_fields.append("phone")

    # 股东
    if shareholders_val:
        sync_shareholders(g.db, company_id, shareholders_val)
        updated_fields.append("shareholders")

    # 更新时间戳
    g.db.execute(
        "UPDATE companies SET updated_at = datetime('now', 'localtime') WHERE id = ?",
        [company_id]
    )
    g.db.commit()

    return _ok({"updated_fields": updated_fields}, f"已更新 {len(updated_fields)} 个字段")


# ── 快速文本提取 ─────────────────────────────────────────────────

@api_bp.route('/api/extract', methods=['POST'])
def extract_text():
    """从文本中提取工商信息（不写入数据库）。

    请求 JSON: {
        "text": "...",                  // 必填
        "method": "auto|regex|llm"       // 可选，默认 auto
    }

    返回: {code, message, data: {method_used, fields, field_count, error}}
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    method = (data.get("method") or "auto").strip()

    if not text:
        return _err(1001, "text 不能为空")
    if len(text) > 50000:
        return _err(1001, "文本不能超过 50000 字")
    if method not in ("auto", "regex", "llm"):
        return _err(1001, "method 必须是 auto/regex/llm 之一")

    result = extract_company_info(text, method=method)

    return _ok({
        "method_used": result["method_used"],
        "fields": result["fields"],
        "field_count": result["field_count"],
        "error": result["error"],
    })


@api_bp.route('/api/quick_import', methods=['POST'])
def quick_import():
    """快速录入：从文本提取工商信息并写入数据库。

    请求 JSON: {
        "text": "...",                    // 必填
        "method": "auto|regex|llm",       // 可选，默认 auto
        "overwrite": false                 // 可选，已存在时是否覆盖
    }

    流程：提取 → 重复检查 → 新建或更新
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    method = (data.get("method") or "auto").strip()
    overwrite = bool(data.get("overwrite", False))

    if not text:
        return _err(1001, "text 不能为空")
    if len(text) > 50000:
        return _err(1001, "文本不能超过 50000 字")
    if method not in ("auto", "regex", "llm"):
        return _err(1001, "method 必须是 auto/regex/llm 之一")

    # 1. 提取
    result = extract_company_info(text, method=method)
    fields = result["fields"]
    if not fields or not fields.get("name"):
        return _err(1002, "未能提取到企业名称，请检查输入文本", 200)

    name = fields["name"]
    norm_name = normalize_name(name)

    # 电话/股东从 fields 中提取
    phone_val = fields.pop("phone", "")
    other_phone_val = fields.pop("other_phone", "")
    shareholders_val = fields.pop("shareholders", "")
    fields.pop("taxpayer_id", None)  # 自动补充的，不单独处理

    # 2. 重复检查
    existing = g.db.execute(
        "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
        [norm_name]
    ).fetchone()

    if existing:
        if not overwrite:
            return _ok({
                "action": "exists",
                "existing_id": existing["id"],
                "existing_name": existing["name"],
                "extracted_fields": fields,
                "field_count": result["field_count"],
                "method_used": result["method_used"],
            }, f"企业已存在: {existing['name']}，设置 overwrite=true 可覆盖更新")

        # 覆盖更新
        company_id = existing["id"]
        for field, value in fields.items():
            if field in ("source", "status"):
                continue
            g.db.execute(
                f"UPDATE companies SET {field} = ? WHERE id = ?",
                [value, company_id]
            )

        # 更新 normalized 字段
        for field, (norm_field, norm_fn) in _NORMALIZED_MAP.items():
            if field in fields:
                g.db.execute(
                    f"UPDATE companies SET {norm_field} = ? WHERE id = ?",
                    [norm_fn(fields[field]), company_id]
                )

        if phone_val or other_phone_val:
            sync_phones(g.db, company_id, phone_val, other_phone_val)
        if shareholders_val:
            sync_shareholders(g.db, company_id, shareholders_val)

        g.db.execute(
            "UPDATE companies SET updated_at = datetime('now', 'localtime') WHERE id = ?",
            [company_id]
        )
        g.db.commit()

        return _ok({
            "action": "updated",
            "id": company_id,
            "name": name,
            "field_count": result["field_count"],
            "method_used": result["method_used"],
        }, f"已更新: {name}")

    # 3. 新建
    fields["normalized_name"] = norm_name
    if "legal_person" in fields:
        fields["normalized_legal_person"] = normalize_person_name(fields["legal_person"])
    if "email" in fields:
        fields["normalized_email"] = normalize_email(fields["email"])
    if "credit_code" in fields:
        fields["credit_code"] = normalize_credit_code(fields["credit_code"])
        if "taxpayer_id" not in fields:
            fields["taxpayer_id"] = fields["credit_code"]

    fields["source"] = f"quick_{result['method_used']}"
    fields["status"] = "active"

    cols = ", ".join(fields.keys())
    placeholders = ", ".join(["?"] * len(fields))
    cursor = g.db.execute(
        f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
        list(fields.values())
    )
    company_id = cursor.lastrowid

    if phone_val or other_phone_val:
        sync_phones(g.db, company_id, phone_val, other_phone_val)
    if shareholders_val:
        sync_shareholders(g.db, company_id, shareholders_val)

    g.db.commit()

    return _ok({
        "action": "created",
        "id": company_id,
        "name": name,
        "field_count": result["field_count"],
        "method_used": result["method_used"],
    }, f"已创建: {name}")


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
