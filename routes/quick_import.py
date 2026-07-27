"""快速录入：从大段文本中提取工商信息并导入数据库。

支持三种提取方式：
- 正则提取：适用于天眼查等规整格式
- 大模型提取：适用于任意文本
- 自动模式：先正则，不足时 fallback 到 LLM
"""
from flask import g, request, render_template, jsonify

from utils import (
    normalize_name, normalize_credit_code,
    normalize_person_name, normalize_email,
)
from data_helpers import sync_phones, sync_shareholders
from extract_service import extract_company_info, count_extracted_fields
from ._base import make_bp

bp = make_bp("quick_import")


# ── 字段中文标签 ─────────────────────────────────────────────────────────────
FIELD_LABELS = {
    "name": "企业名称",
    "credit_code": "统一社会信用代码",
    "legal_person": "法定代表人",
    "registered_capital": "注册资本",
    "paid_capital": "实缴资本",
    "established_date": "成立日期",
    "approved_date": "核准日期",
    "business_term": "营业期限",
    "business_status": "经营状态",
    "company_type": "公司类型",
    "industry": "所属行业",
    "insured_count": "参保人数",
    "province": "省份",
    "city": "城市",
    "district": "区县",
    "address": "注册地址",
    "business_scope": "经营范围",
    "former_name": "曾用名",
    "website": "网址",
    "email": "邮箱",
    "phone": "电话",
    "other_phone": "其他电话",
    "org_code": "组织机构代码",
    "registration_no": "注册号",
    "shareholders": "股东",
    "taxpayer_id": "纳税人识别号",
}


# ── 页面 ────────────────────────────────────────────────────────────────────

@bp.route("/quick-import")
def quick_import_page():
    """快速录入页面 → 已集成到录入页，重定向。"""
    from flask import redirect, url_for
    return redirect(url_for("companies_bp.add_company"))


# ── API：提取（不写库） ────────────────────────────────────────────────────

@bp.route("/api/quick-import/extract", methods=["POST"])
def extract_only():
    """从文本提取字段，不写入数据库。用于预览。"""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    method = (data.get("method") or "auto").strip()

    if not text:
        return jsonify({"code": 1001, "message": "请输入文本", "data": None})
    if len(text) > 50000:
        return jsonify({"code": 1001, "message": "文本过长（最多 50000 字）", "data": None})
    if method not in ("auto", "regex", "llm"):
        return jsonify({"code": 1001, "message": "method 必须是 auto/regex/llm", "data": None})

    result = extract_company_info(text, method=method)

    # 为前端准备带标签的字段列表
    fields_labeled = []
    for key, value in result["fields"].items():
        if key == "taxpayer_id":
            continue  # 自动补充的，不显示
        fields_labeled.append({
            "key": key,
            "label": FIELD_LABELS.get(key, key),
            "value": value,
        })

    # 重复检查
    name = result["fields"].get("name")
    existing = None
    if name:
        norm_name = normalize_name(name)
        row = g.db.execute(
            "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
            [norm_name]
        ).fetchone()
        if row:
            existing = {"id": row["id"], "name": row["name"]}

    return jsonify({
        "code": 0,
        "message": "ok",
        "data": {
            "method_used": result["method_used"],
            "field_count": result["field_count"],
            "fields": result["fields"],
            "fields_labeled": fields_labeled,
            "existing": existing,
            "error": result["error"],
        },
    })


# ── API：提取并导入 ────────────────────────────────────────────────────────

@bp.route("/api/quick-import/submit", methods=["POST"])
def extract_and_import():
    """从文本提取并写入数据库。"""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    method = (data.get("method") or "auto").strip()
    overwrite = bool(data.get("overwrite", False))
    # 前端可以传入修改后的字段
    manual_fields = data.get("fields") or {}

    if not text:
        return jsonify({"code": 1001, "message": "请输入文本", "data": None})
    if len(text) > 50000:
        return jsonify({"code": 1001, "message": "文本过长", "data": None})

    # 如果前端传了修改后的字段，直接用；否则提取
    if manual_fields and manual_fields.get("name"):
        fields = manual_fields
    else:
        result = extract_company_info(text, method=method)
        fields = result["fields"]

    if not fields or not fields.get("name"):
        return jsonify({"code": 1002, "message": "未能提取到企业名称", "data": None})

    name = fields["name"]
    norm_name = normalize_name(name)

    # 提取电话和股东
    phone_val = str(fields.pop("phone", "")).strip()
    other_phone_val = str(fields.pop("other_phone", "")).strip()
    shareholders_val = str(fields.pop("shareholders", "")).strip()
    fields.pop("taxpayer_id", None)

    # 重复检查
    existing = g.db.execute(
        "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
        [norm_name]
    ).fetchone()

    if existing:
        if not overwrite:
            return jsonify({
                "code": 0,
                "data": {
                    "action": "exists",
                    "existing_id": existing["id"],
                    "existing_name": existing["name"],
                },
                "message": f"企业已存在: {existing['name']}",
            })

        # 覆盖更新
        company_id = existing["id"]
        _NORMALIZED_MAP = {
            "name": ("normalized_name", normalize_name),
            "legal_person": ("normalized_legal_person", normalize_person_name),
            "email": ("normalized_email", normalize_email),
        }
        for field, value in fields.items():
            if field in ("source", "status"):
                continue
            g.db.execute(
                f"UPDATE companies SET {field} = ? WHERE id = ?",
                [value, company_id]
            )
        for field, (norm_field, norm_fn) in _NORMALIZED_MAP.items():
            if field in fields:
                g.db.execute(
                    f"UPDATE companies SET {norm_field} = ? WHERE id = ?",
                    [norm_fn(fields[field]), company_id]
                )
        if "credit_code" in fields:
            g.db.execute(
                "UPDATE companies SET credit_code = ? WHERE id = ?",
                [normalize_credit_code(fields["credit_code"]), company_id]
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

        return jsonify({
            "code": 0,
            "data": {"action": "updated", "id": company_id, "name": name},
            "message": f"已更新: {name}",
        })

    # 新建
    fields["normalized_name"] = norm_name
    if "legal_person" in fields:
        fields["normalized_legal_person"] = normalize_person_name(fields["legal_person"])
    if "email" in fields:
        fields["normalized_email"] = normalize_email(fields["email"])
    if "credit_code" in fields:
        fields["credit_code"] = normalize_credit_code(fields["credit_code"])
        if "taxpayer_id" not in fields:
            fields["taxpayer_id"] = fields["credit_code"]

    fields["source"] = "quick_import"
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

    return jsonify({
        "code": 0,
        "data": {"action": "created", "id": company_id, "name": name},
        "message": f"已录入: {name}",
    })
