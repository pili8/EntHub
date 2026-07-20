"""企业 CRUD：详情、编辑、删除、新增。"""
from flask import Blueprint, g, request, render_template, redirect, url_for, flash, abort

from utils import (
    normalize_name, normalize_credit_code,
    normalize_person_name, normalize_email,
)
from data_helpers import sync_phones, sync_shareholders

bp = Blueprint('companies_bp', __name__)


# 全量字段列表（与导入共用）
IMPORT_FIELDS = [
    "name", "phone", "other_phone", "address", "annual_report_address",
    "credit_code", "taxpayer_id", "registration_no", "org_code",
    "legal_person", "registered_capital", "paid_capital",
    "established_date", "approved_date", "business_term",
    "province", "city", "district", "insured_count",
    "company_type", "industry", "former_name", "website",
    "email", "other_email", "business_scope", "business_status",
    "enterprise_scale", "shareholders", "mailing_address",
    "english_name", "tags", "source_file",
]


# ── 详情 ────────────────────────────────────────────────────────────────────

@bp.route("/company/<int:company_id>")
def company_detail(company_id):
    row = g.db.execute(
        "SELECT * FROM companies WHERE id = ?", [company_id]
    ).fetchone()
    if not row:
        abort(404)

    # 该公司的所有电话（含重复数）
    company_phones = g.db.execute(
        """SELECT cp.phone, cp.normalized_phone, cp.is_primary, cp.is_recommended,
               (SELECT COUNT(DISTINCT company_id)
                FROM company_phones cp2
                WHERE cp2.normalized_phone = cp.normalized_phone) AS dup_count
           FROM company_phones cp
           WHERE cp.company_id = ?
           ORDER BY cp.is_primary DESC, cp.is_recommended DESC""",
        [company_id]
    ).fetchall()

    # 同电话关联企业
    phone_norms = [r["normalized_phone"] for r in company_phones] if company_phones else []
    phone_placeholders = ",".join(["?"] * len(phone_norms)) if phone_norms else "''"

    related_phones = g.db.execute(f"""
        SELECT DISTINCT c.id, c.name, c.city, c.business_status
        FROM companies c
        JOIN company_phones cp ON cp.company_id = c.id
        WHERE c.id <> ? AND cp.normalized_phone IN ({phone_placeholders})
        AND cp.normalized_phone <> ''
        ORDER BY c.name LIMIT 10
    """, [company_id] + phone_norms).fetchall()

    # 同法人
    related_legal_person = []
    if row["normalized_legal_person"]:
        related_legal_person = g.db.execute("""
            SELECT id, name, city, business_status
            FROM companies
            WHERE normalized_legal_person = ? AND id != ?
            ORDER BY normalized_legal_person
            LIMIT 10
        """, [row["normalized_legal_person"], company_id]).fetchall()

    # 同股东（通过 company_shareholders 表反查）
    related_shareholders = []
    shareholders = g.db.execute("""
        SELECT s.name, s.normalized_name
        FROM company_shareholders s
        WHERE s.company_id = ?
    """, [company_id]).fetchall()
    if shareholders:
        norm_names = [s["normalized_name"] for s in shareholders]
        placeholders = ",".join(["?"] * len(norm_names))
        related_shareholders = g.db.execute(f"""
            SELECT DISTINCT c.id, c.name, c.city, c.business_status
            FROM company_shareholders s2
            JOIN companies c ON s2.company_id = c.id
            WHERE s2.normalized_name IN ({placeholders})
              AND s2.company_id != ?
            ORDER BY s2.normalized_name
            LIMIT 10
        """, norm_names + [company_id]).fetchall()

    # 同邮箱
    related_email = []
    if row["normalized_email"]:
        related_email = g.db.execute("""
            SELECT id, name, city, business_status
            FROM companies
            WHERE normalized_email = ? AND id != ? AND normalized_email != ''
            ORDER BY normalized_email
            LIMIT 10
        """, [row["normalized_email"], company_id]).fetchall()

    # 同行业
    related_industry = []
    if row["industry"] and row["industry"] != '-':
        related_industry = g.db.execute("""
            SELECT id, name, city, business_status
            FROM companies
            WHERE industry = ? AND id != ? AND industry != ''
            ORDER BY industry
            LIMIT 10
        """, [row["industry"], company_id]).fetchall()

    # 统计关联数量：法人 / 股东 / 行业 / 邮箱
    field_counts = {}
    norm_field_map = {
        "legal_person": "normalized_legal_person",
        "shareholders": None,  # 通过 company_shareholders 表
        "industry": "industry",
        "email": "normalized_email",
    }
    for f, norm_f in norm_field_map.items():
        if norm_f is None:
            cnt = g.db.execute(
                "SELECT COUNT(DISTINCT company_id) FROM company_shareholders "
                "WHERE company_id = ?",
                [company_id]
            ).fetchone()[0]
            if cnt:
                field_counts[f] = cnt
        else:
            val = row[norm_f] if row[norm_f] else None
            if val and val != '-':
                cnt = g.db.execute(
                    f"SELECT COUNT(*) FROM companies WHERE {norm_f} = ?", [val]
                ).fetchone()[0]
                field_counts[f] = cnt

    return render_template("company_detail.html", company=row,
                           related_phones=related_phones,
                           related_legal_person=related_legal_person,
                           related_shareholders=related_shareholders,
                           related_industry=related_industry,
                           related_email=related_email,
                           company_phones=company_phones,
                           shareholders=shareholders,
                           field_counts=field_counts)


# ── 编辑 ────────────────────────────────────────────────────────────────────

@bp.route("/company/<int:company_id>/edit", methods=["GET", "POST"])
def edit_company(company_id):
    row = g.db.execute("SELECT * FROM companies WHERE id = ?", [company_id]).fetchone()
    if not row:
        abort(404)

    if request.method == "POST":
        fields = {}
        for f in IMPORT_FIELDS:
            val = request.form.get(f, "").strip()
            fields[f] = val if val else ""

        fields["normalized_name"] = normalize_name(fields.get("name", ""))
        fields["normalized_legal_person"] = normalize_person_name(fields.get("legal_person", ""))
        fields["normalized_email"] = normalize_email(fields.get("email", ""))
        if fields.get("credit_code"):
            fields["credit_code"] = normalize_credit_code(fields["credit_code"])

        # 电话/股东字段仅存入关联表，不在 companies 表中
        phone_val = fields.pop("phone", "")
        other_phone_val = fields.pop("other_phone", "")
        shareholders_val = fields.pop("shareholders", "")

        set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
        g.db.execute(f"UPDATE companies SET {set_clause} WHERE id = ?",
                     list(fields.values()) + [company_id])

        sync_phones(g.db, company_id, phone_val, other_phone_val)
        sync_shareholders(g.db, company_id, shareholders_val)
        g.db.commit()

        flash(f"已更新：{fields.get('name', '')}", "success")
        return redirect(url_for("companies_bp.company_detail", company_id=company_id))

    # 从 company_phones 表回填 phone/other_phone 用于表单展示
    phones = g.db.execute(
        """SELECT phone, is_primary FROM company_phones
           WHERE company_id = ? ORDER BY is_primary DESC, id""",
        [company_id]
    ).fetchall()
    if phones:
        primary = [p["phone"] for p in phones if p["is_primary"]]
        others = [p["phone"] for p in phones if not p["is_primary"]]
        row_dict = dict(row)
        row_dict["phone"] = primary[0] if primary else ""
        row_dict["other_phone"] = ";".join(others)
        return render_template("edit.html", company=row_dict)

    return render_template("edit.html", company=dict(row))


# ── 删除 ────────────────────────────────────────────────────────────────────

@bp.route("/company/<int:company_id>/delete", methods=["POST"])
def delete_company(company_id):
    """删除企业记录"""
    g.db.execute("DELETE FROM company_phones WHERE company_id = ?", (company_id,))
    g.db.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    g.db.commit()
    flash("企业已删除", "success")
    return redirect(url_for("pages_bp.browse"))


# ── 手动录入 ────────────────────────────────────────────────────────────────

@bp.route("/add", methods=["GET", "POST"])
def add_company():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("请填写企业名称", "error")
            return redirect(url_for("companies_bp.add_company"))

        fields = {}
        for f in IMPORT_FIELDS:
            val = request.form.get(f, "").strip()
            if val:
                fields[f] = val

        fields["normalized_name"] = normalize_name(name)
        fields["normalized_legal_person"] = normalize_person_name(fields.get("legal_person", ""))
        fields["normalized_email"] = normalize_email(fields.get("email", ""))
        if fields.get("credit_code"):
            fields["credit_code"] = normalize_credit_code(fields["credit_code"])

        fields["status"] = request.form.get("status", "active")
        fields["source"] = "manual"

        # 电话/股东字段仅存入关联表
        phone_val = fields.pop("phone", "")
        other_phone_val = fields.pop("other_phone", "")
        shareholders_val = fields.pop("shareholders", "")

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        cursor = g.db.execute(
            f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
            list(fields.values())
        )
        sync_phones(g.db, cursor.lastrowid, phone_val, other_phone_val)
        sync_shareholders(g.db, cursor.lastrowid, shareholders_val)
        g.db.commit()
        flash(f"已录入：{name}", "success")
        return redirect(url_for("companies_bp.add_company"))

    return render_template("add.html", company={})
