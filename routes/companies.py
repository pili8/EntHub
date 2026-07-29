"""企业 CRUD：详情、编辑、删除、新增、API 获取工商信息。"""
import re
from datetime import datetime
from flask import Blueprint, g, request, render_template, redirect, url_for, flash, \
                   abort, jsonify

from utils import (
    normalize_name, normalize_credit_code,
    normalize_person_name, normalize_email,
    phone_location, get_phone_tags, get_phone_tags_batch,
)
from data_helpers import (
    sync_phones, sync_shareholders,
    merge_phones, merge_shareholders,
    split_phones, split_shareholders,
)
from config import is_provider_ready
import enthub_api

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

    # 为电话附加归属地和标签（跟号码走，不跟企业走）
    phone_tag_map = get_phone_tags_batch(
        g.db, [r["normalized_phone"] for r in company_phones]
    ) if company_phones else {}
    company_phones_enriched = []
    for cp in company_phones:
        cp_dict = dict(cp)
        cp_dict["location"] = phone_location(cp["phone"])
        cp_dict["tag"] = phone_tag_map.get(cp["normalized_phone"])
        company_phones_enriched.append(cp_dict)

    # 同电话关联企业
    phone_norms = [r["normalized_phone"] for r in company_phones] if company_phones else []
    phone_placeholders = ",".join(["?"] * len(phone_norms)) if phone_norms else "''"

    related_phones = g.db.execute(f"""
        SELECT DISTINCT c.id, c.name, c.district, c.legal_person, c.business_status
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
            SELECT id, name, district, legal_person, business_status
            FROM companies
            WHERE normalized_legal_person = ? AND id != ?
            ORDER BY normalized_legal_person
            LIMIT 10
        """, [row["normalized_legal_person"], company_id]).fetchall()

    # 同股东（通过 company_shareholders 表反查）
    related_shareholders = []
    shareholders = g.db.execute("""
        SELECT s.name, s.normalized_name, s.position
        FROM company_shareholders s
        WHERE s.company_id = ?
    """, [company_id]).fetchall()
    if shareholders:
        norm_names = [s["normalized_name"] for s in shareholders]
        placeholders = ",".join(["?"] * len(norm_names))
        related_shareholders = g.db.execute(f"""
            SELECT DISTINCT c.id, c.name, c.district, c.legal_person, c.business_status
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
            SELECT id, name, district, legal_person, business_status
            FROM companies
            WHERE normalized_email = ? AND id != ? AND normalized_email != ''
            ORDER BY normalized_email
            LIMIT 10
        """, [row["normalized_email"], company_id]).fetchall()

    # 同行业
    related_industry = []
    if row["industry"] and row["industry"] != '-':
        related_industry = g.db.execute("""
            SELECT id, name, district, legal_person, business_status
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
                           company_phones=company_phones_enriched,
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
        is_ajax = request.form.get("_ajax") == "1"
        action = request.form.get("_action", "")
        existing_id_raw = request.form.get("_existing_id", "")

        name = request.form.get("name", "").strip()
        if not name:
            msg = "请填写企业名称"
            if is_ajax:
                return jsonify({"code": 1001, "message": msg})
            flash(msg, "error")
            return redirect(url_for("companies_bp.add_company"))

        # ── 收集表单字段 ──
        fields = {}
        for f in IMPORT_FIELDS:
            val = request.form.get(f, "").strip()
            if val:
                fields[f] = val

        fields["normalized_name"] = normalize_name(name)
        fields["normalized_legal_person"] = normalize_person_name(
            fields.get("legal_person", ""))
        fields["normalized_email"] = normalize_email(fields.get("email", ""))
        if fields.get("credit_code"):
            fields["credit_code"] = normalize_credit_code(fields["credit_code"])

        # 电话/股东字段仅存入关联表，不写入 companies 主表
        phone_val = fields.pop("phone", "")
        other_phone_val = fields.pop("other_phone", "")
        shareholders_val = fields.pop("shareholders", "")

        # ── 操作：覆盖更新已有企业 ──
        if action == "overwrite" and existing_id_raw:
            eid = int(existing_id_raw)
            row = g.db.execute(
                "SELECT * FROM companies WHERE id = ?", [eid]
            ).fetchone()
            if not row:
                msg = "目标企业不存在"
                if is_ajax:
                    return jsonify({"code": 1002, "message": msg})
                flash(msg, "error")
                return redirect(url_for("companies_bp.add_company"))

            # 字段级 UPDATE：仅写入非空且与库内不同的字段（空值不覆盖）
            updates = {}
            for f_name in IMPORT_FIELDS:
                if f_name in ("phone", "other_phone", "shareholders",
                              "source_file", "tags", "other_email"):
                    continue
                inc_val = fields.get(f_name, "")
                if inc_val and inc_val != (row[f_name] or ""):
                    updates[f_name] = inc_val

            # 派生归一化字段
            if "name" in updates:
                updates["normalized_name"] = fields["normalized_name"]
            if "legal_person" in updates:
                updates["normalized_legal_person"] = fields["normalized_legal_person"]
            if "email" in updates:
                updates["normalized_email"] = fields["normalized_email"]

            # other_email 增量合并（不覆盖已有）
            oe = fields.get("other_email", "")
            if oe:
                existing_oe = row["other_email"] or ""
                merged_oe = set(p.strip() for p in existing_oe.split(";")
                                if p.strip())
                merged_oe.update(p.strip() for p in oe.replace(",", ";").split(";")
                                 if p.strip() and p.strip() != "-")
                updates["other_email"] = "; ".join(sorted(merged_oe))

            if updates:
                updates["updated_at"] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                g.db.execute(
                    f"UPDATE companies SET {set_clause} WHERE id = ?",
                    list(updates.values()) + [eid]
                )

            # 电话 & 股东：增量合并（只追加新号码/新股东）
            merge_phones(g.db, eid, phone_val, other_phone_val)
            merge_shareholders(g.db, eid, shareholders_val)
            g.db.commit()

            msg = f"已更新：{fields.get('name', row['name'])}"
            redirect_url = url_for("companies_bp.company_detail", company_id=eid)
            if is_ajax:
                return jsonify({"code": 0, "message": msg,
                                "redirect": redirect_url})
            flash(msg, "success")
            return redirect(redirect_url)

        # ── 操作：仅合并新电话号码 + 新股东 ──
        if action == "merge_phones" and existing_id_raw:
            eid = int(existing_id_raw)
            row = g.db.execute(
                "SELECT name FROM companies WHERE id = ?", [eid]
            ).fetchone()
            if not row:
                msg = "目标企业不存在"
                if is_ajax:
                    return jsonify({"code": 1002, "message": msg})
                flash(msg, "error")
                return redirect(url_for("companies_bp.add_company"))

            merge_phones(g.db, eid, phone_val, other_phone_val)
            merge_shareholders(g.db, eid, shareholders_val)
            g.db.commit()

            msg = f"已合并新数据到：{row['name']}"
            redirect_url = url_for("companies_bp.company_detail", company_id=eid)
            if is_ajax:
                return jsonify({"code": 0, "message": msg,
                                "redirect": redirect_url})
            flash(msg, "success")
            return redirect(redirect_url)

        # ── 默认：重复检查 ──
        existing = g.db.execute(
            "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
            [fields["normalized_name"]]
        ).fetchone()

        if existing:
            # 分析新电话号码
            existing_phones = g.db.execute(
                "SELECT normalized_phone FROM company_phones WHERE company_id = ?",
                [existing["id"]]
            ).fetchall()
            existing_norms = {r["normalized_phone"] for r in existing_phones}

            new_phones = []
            for raw, norm in split_phones(phone_val, other_phone_val):
                if norm and norm not in existing_norms:
                    new_phones.append(raw)

            # 分析新股东
            existing_sh = g.db.execute(
                "SELECT normalized_name FROM company_shareholders WHERE company_id = ?",
                [existing["id"]]
            ).fetchall()
            existing_sh_norms = {r["normalized_name"] for r in existing_sh}
            new_shareholders = []
            for raw, norm, pos in split_shareholders(shareholders_val):
                if norm and norm not in existing_sh_norms:
                    new_shareholders.append(raw)

            if is_ajax:
                return jsonify({
                    "code": 409,
                    "message": f"该企业已存在：{existing['name']}",
                    "data": {
                        "existing_id": existing["id"],
                        "existing_name": existing["name"],
                        "new_phones": new_phones,
                        "has_new_phones": len(new_phones) > 0,
                        "new_shareholders": new_shareholders,
                        "has_new_shareholders": len(new_shareholders) > 0,
                    }
                })
            flash(f"该企业已存在：{existing['name']}", "warning")
            return redirect(url_for("companies_bp.company_detail",
                                    company_id=existing["id"]))

        # ── 无重复：正常录入 ──
        fields["status"] = request.form.get("status", "active")
        fields["source"] = "manual"

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        cursor = g.db.execute(
            f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
            list(fields.values())
        )
        sync_phones(g.db, cursor.lastrowid, phone_val, other_phone_val)
        sync_shareholders(g.db, cursor.lastrowid, shareholders_val)
        g.db.commit()

        msg = f"已录入：{name}"
        if is_ajax:
            return jsonify({"code": 0, "message": msg,
                            "redirect": url_for("companies_bp.add_company")})
        flash(msg, "success")
        return redirect(url_for("companies_bp.add_company"))

    return render_template("add.html", company={})


# ── API 获取/更新工商信息 ────────────────────────────────────────────────────

# 映射后的字段中，哪些需要更新 normalized_* 辅助字段
_NORMALIZED_FIELDS = {
    "name": "normalized_name",
    "legal_person": "normalized_legal_person",
    "email": "normalized_email",
}


@bp.route("/api/company/fetch-info", methods=["POST"])
def fetch_company_info_api():
    """通过企业名称从鲸海数据 API 获取工商信息（用于录入页自动填充）。

    不写入数据库，仅返回映射后的字段数据供前端填充表单。
    每次调用消耗 1 次 API 配额。

    请求 JSON：{"name": "企业名称"}
    返回 JSON：{code, message, data: {mapped_fields...}}
    """
    if not is_provider_ready("jinghai"):
        return jsonify({
            "code": 2001,
            "message": "API 未配置或未启用，请到设置页面配置鲸海数据 API 密钥",
            "data": None,
        })

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()

    if not name:
        return jsonify({
            "code": 1001,
            "message": "请提供企业名称",
            "data": None,
        })

    result = enthub_api.fetch_company_info(company_name=name)

    if not result["success"]:
        return jsonify({
            "code": 2001,
            "message": result.get("error", "获取失败"),
            "data": None,
        })

    return jsonify({
        "code": 0,
        "message": "ok",
        "data": {
            "mapped": result["mapped"],
            "raw": result.get("raw"),  # 返回原始数据供调试
        },
    })


@bp.route("/api/company/<int:company_id>/update-info", methods=["POST"])
def update_company_info_api(company_id):
    """通过鲸海数据 API 更新已存在企业的工商信息（用于详情页更新按钮）。

    请求 JSON：{"mode": "overwrite" | "merge"}
      - overwrite: API 返回的字段覆盖数据库旧值
      - merge: 仅填充数据库中为空的字段，不覆盖已有值

    每次调用消耗 1 次 API 配额。
    """
    if not is_provider_ready("jinghai"):
        return jsonify({
            "code": 2001,
            "message": "API 未配置或未启用，请到设置页面配置",
            "data": None,
        })

    # 检查企业是否存在
    row = g.db.execute(
        "SELECT id, name, credit_code FROM companies WHERE id = ?",
        [company_id]
    ).fetchone()
    if not row:
        return jsonify({
            "code": 1002,
            "message": "企业不存在",
            "data": None,
        })

    body = request.get_json(silent=True) or {}
    mode = body.get("mode", "merge")  # 默认 merge 模式（仅填充空字段）

    # 优先用信用代码查询，其次用名称
    result = enthub_api.fetch_company_info(
        company_name=row["name"],
        credit_code=row["credit_code"] if row["credit_code"] else None,
    )

    if not result["success"]:
        return jsonify({
            "code": 2001,
            "message": result.get("error", "获取失败"),
            "data": None,
        })

    mapped = result["mapped"]
    if not mapped:
        return jsonify({
            "code": 2001,
            "message": "API 返回数据为空，可能是未找到该企业",
            "data": None,
        })

    # 按模式更新数据库
    updated_fields = []
    skipped_fields = []

    # 这些字段不入 companies 主表
    SKIP_FIELDS = {"phone", "other_phone", "shareholders", "tags", "source_file"}

    for field, value in mapped.items():
        if field in SKIP_FIELDS:
            continue

        if mode == "overwrite":
            # 覆盖模式：直接更新
            g.db.execute(
                f"UPDATE companies SET {field} = ? WHERE id = ?",
                [value, company_id]
            )
            updated_fields.append(field)
        else:
            # merge 模式：仅填充空字段
            current = g.db.execute(
                f"SELECT {field} FROM companies WHERE id = ?",
                [company_id]
            ).fetchone()
            current_val = current[0] if current else None
            if not current_val or current_val.strip() in ("", "-", "--"):
                g.db.execute(
                    f"UPDATE companies SET {field} = ? WHERE id = ?",
                    [value, company_id]
                )
                updated_fields.append(field)
            else:
                skipped_fields.append(field)

    # 更新 normalized_* 辅助字段
    for field, norm_field in _NORMALIZED_FIELDS.items():
        if field in updated_fields:
            val = mapped.get(field, "")
            if field == "name":
                norm_val = normalize_name(val)
            elif field == "legal_person":
                norm_val = normalize_person_name(val)
            elif field == "email":
                norm_val = normalize_email(val)
            else:
                continue
            g.db.execute(
                f"UPDATE companies SET {norm_field} = ? WHERE id = ?",
                [norm_val, company_id]
            )

    # 更新信用代码归一化
    if "credit_code" in updated_fields and mapped.get("credit_code"):
        norm_cc = normalize_credit_code(mapped["credit_code"])
        g.db.execute(
            "UPDATE companies SET credit_code = ? WHERE id = ?",
            [norm_cc, company_id]
        )

    # 更新电话（增量合并，不覆盖已有主号）
    if mapped.get("phone") or mapped.get("other_phone"):
        from data_helpers import merge_phones
        merge_phones(
            g.db, company_id,
            mapped.get("phone", ""),
            mapped.get("other_phone", ""),
        )
        updated_fields.append("phone")

    # 更新股东（增量合并）
    if mapped.get("shareholders"):
        from data_helpers import merge_shareholders
        merge_shareholders(g.db, company_id, mapped["shareholders"])
        updated_fields.append("shareholders")

    # 更新 updated_at 时间戳
    g.db.execute(
        "UPDATE companies SET updated_at = datetime('now', 'localtime') WHERE id = ?",
        [company_id]
    )

    g.db.commit()

    return jsonify({
        "code": 0,
        "message": f"已更新 {len(updated_fields)} 个字段"
                   + (f"，跳过 {len(skipped_fields)} 个已有字段" if skipped_fields else ""),
        "data": {
            "updated_fields": updated_fields,
            "skipped_fields": skipped_fields,
            "mode": mode,
            "mapped": mapped,
        },
    })


# ── 合并端点：一键获取工商信息 ─────────────────────────────────────────

@bp.route("/api/company/check-duplicate")
def check_duplicate():
    """检查公司名是否已存在于数据库（不消耗 API 配额）。"""
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"code": 0, "data": {"exists": False}})

    norm_name = normalize_name(name)
    existing = g.db.execute(
        "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
        [norm_name]
    ).fetchone()

    if existing:
        return jsonify({
            "code": 0,
            "data": {"exists": True, "id": existing["id"], "name": existing["name"]}
        })
    return jsonify({"code": 0, "data": {"exists": False}})


@bp.route("/api/company/fetch-all", methods=["POST"])
def fetch_all_api():
    """录入页：一键获取工商信息 + 联系方式（不写库，返回字段供前端填充）。

    先检查数据库是否已有该公司，再调用 API。聯系方式接口不可用时不影响工商信息结果。
    """
    if not is_provider_ready("jinghai"):
        return jsonify({"code": 2001, "message": "API 未配置或未启用，请到设置页面配置", "data": None})

    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"code": 1001, "message": "请提供企业名称", "data": None})

    # 0. 重复检查：查询数据库是否已有该公司
    norm_name = normalize_name(name)
    existing = g.db.execute(
        "SELECT id, name FROM companies WHERE normalized_name = ? LIMIT 1",
        [norm_name]
    ).fetchone()
    if existing:
        return jsonify({
            "code": 2002,
            "message": f"该公司已存在于数据库中",
            "data": {"existing_id": existing["id"], "existing_name": existing["name"]}
        })

    # 1. 工商信息
    biz_result = enthub_api.fetch_company_info(company_name=name)
    if not biz_result["success"]:
        return jsonify({"code": 2001, "message": biz_result.get("error", "获取失败"), "data": None})

    mapped = dict(biz_result.get("mapped") or {})

    return jsonify({"code": 0, "message": "ok", "data": {"mapped": mapped}})


# 字段中文标签（用于对比展示）
FIELD_LABELS = {
    "name": "企业名称", "legal_person": "法定代表人", "credit_code": "统一社会信用代码",
    "taxpayer_id": "纳税人识别号", "registration_no": "注册号", "org_code": "组织机构代码",
    "registered_capital": "注册资本", "paid_capital": "实缴资本",
    "established_date": "成立日期", "approved_date": "核准日期",
    "business_term": "营业期限", "business_status": "经营状态",
    "company_type": "公司类型", "industry": "所属行业", "insured_count": "参保人数",
    "province": "省份", "city": "城市", "district": "区县",
    "address": "注册地址", "business_scope": "经营范围",
    "former_name": "曾用名", "phone": "电话", "email": "邮箱", "website": "网址",
}


def _normalize_for_compare(field, val):
    """归一化字段值用于对比（避免格式差异导致的假阳性变更）。"""
    if not val:
        return ""
    s = str(val).strip()
    # 合并多个空格
    s = re.sub(r'\s+', ' ', s)
    # 全角转半角（常见中文标点）
    trans = str.maketrans('（）：，；', '():,;')
    s = s.translate(trans)
    # 数字归一化：去掉无意义的 .00（如 "10.00万" → "10万"）
    s = re.sub(r'(\d+)\.0+(\D)', r'\1\2', s)
    s = re.sub(r'(\d+)\.0+$', r'\1', s)
    # 信用代码/纳税人识别号/组织机构代码：去掉连字符和空格
    if field in ('credit_code', 'taxpayer_id', 'org_code', 'registration_no'):
        s = s.replace("-", "").replace(" ", "")
    # 日期格式统一：2023/07/11 → 2023-07-11
    s = re.sub(r'(\d{4})/(\d{1,2})/(\d{1,2})', r'\1-\2-\3', s)
    # 去掉日期中的"年""月""日"（2023年07月11日 → 2023-07-11）
    s = re.sub(r'(\d{4})年(\d{1,2})月(\d{1,2})日?', r'\1-\2-\3', s)
    return s


@bp.route("/api/company/<int:company_id>/refresh", methods=["POST"])
def refresh_company_api(company_id):
    """详情页：调用 API 获取最新数据，返回与现有数据的对比 diff。

    不直接写库，用户在前端选择要更新的字段后调用 /apply 端点。
    消耗 1 次 API 配额。
    """
    if not is_provider_ready("jinghai"):
        return jsonify({"code": 2001, "message": "API 未配置或未启用，请到设置页面配置", "data": None})

    row = g.db.execute("SELECT id, name, credit_code FROM companies WHERE id = ?", [company_id]).fetchone()
    if not row:
        return jsonify({"code": 1002, "message": "企业不存在", "data": None})

    name = row["name"]
    credit_code = row["credit_code"] if row["credit_code"] else None

    # 1. 工商信息
    biz_result = enthub_api.fetch_company_info(company_name=name, credit_code=credit_code)
    if not biz_result["success"]:
        return jsonify({"code": 2001, "message": biz_result.get("error", "获取失败"), "data": None})

    mapped = dict(biz_result.get("mapped") or {})

    # 2. 逐字段对比现有数据库值
    SKIP_FIELDS = {"other_phone", "tags", "source_file", "shareholders"}
    diff = []
    same_count = 0

    for field, new_value in mapped.items():
        if field in SKIP_FIELDS:
            continue

        # phone 特殊处理：查 company_phones 表
        if field == "phone":
            existing_phones = g.db.execute(
                "SELECT phone FROM company_phones WHERE company_id = ?", [company_id]
            ).fetchall()
            existing_str = "; ".join(p["phone"] for p in existing_phones) if existing_phones else ""
            if not existing_str:
                diff.append({"field": field, "label": FIELD_LABELS.get(field, field),
                             "old": "", "new": new_value, "status": "new"})
            elif new_value not in existing_str:
                diff.append({"field": field, "label": FIELD_LABELS.get(field, field),
                             "old": existing_str, "new": new_value, "status": "changed"})
            else:
                same_count += 1
            continue

        # 其余字段查 companies 表
        try:
            current = g.db.execute(
                f"SELECT {field} FROM companies WHERE id = ?", [company_id]
            ).fetchone()
            old_value = current[0] if current else ""
        except Exception:
            old_value = ""

        old_str = str(old_value).strip() if old_value else ""
        new_str = str(new_value).strip() if new_value else ""

        # 使用归一化对比，避免格式差异导致假阳性变更
        old_norm = _normalize_for_compare(field, old_str)
        new_norm = _normalize_for_compare(field, new_str)

        if not old_norm or old_norm in ("", "-", "--"):
            diff.append({"field": field, "label": FIELD_LABELS.get(field, field),
                         "old": "", "new": new_str, "status": "new"})
        elif old_norm != new_norm:
            diff.append({"field": field, "label": FIELD_LABELS.get(field, field),
                         "old": old_str, "new": new_str, "status": "changed"})
        else:
            same_count += 1

    return jsonify({
        "code": 0,
        "message": f"{len(diff)} 个字段有变化，{same_count} 个字段一致",
        "data": {
            "diff": diff,
            "same_count": same_count,
            "mapped": mapped,
        },
    })


@bp.route("/api/company/<int:company_id>/apply", methods=["POST"])
def apply_refresh_api(company_id):
    """详情页：应用用户选中的字段更新到数据库（不调用 API，不消耗配额）。

    请求 JSON: {"mapped": {...}, "selected_fields": ["field1", "field2", ...]}
    """
    if not is_provider_ready("jinghai"):
        return jsonify({"code": 2001, "message": "API 未配置或未启用", "data": None})

    row = g.db.execute("SELECT id FROM companies WHERE id = ?", [company_id]).fetchone()
    if not row:
        return jsonify({"code": 1002, "message": "企业不存在", "data": None})

    body = request.get_json(silent=True) or {}
    mapped = body.get("mapped", {})
    selected_fields = body.get("selected_fields", [])

    updated_fields = []

    for field in selected_fields:
        if field not in mapped:
            continue
        value = mapped[field]

        # phone 特殊处理
        if field == "phone":
            from data_helpers import merge_phones
            merge_phones(g.db, company_id, value, "")
            updated_fields.append("phone")
            continue

        # shareholders 特殊处理
        if field == "shareholders":
            from data_helpers import merge_shareholders
            merge_shareholders(g.db, company_id, value)
            updated_fields.append("shareholders")
            continue

        # 常规字段
        try:
            g.db.execute(f"UPDATE companies SET {field} = ? WHERE id = ?", [value, company_id])
            updated_fields.append(field)
        except Exception:
            pass

    # 更新 normalized_* 辅助字段
    for field, norm_field in _NORMALIZED_FIELDS.items():
        if field in updated_fields:
            val = mapped.get(field, "")
            if field == "name":
                norm_val = normalize_name(val)
            elif field == "legal_person":
                norm_val = normalize_person_name(val)
            elif field == "email":
                norm_val = normalize_email(val)
            else:
                continue
            g.db.execute(f"UPDATE companies SET {norm_field} = ? WHERE id = ?", [norm_val, company_id])

    # 信用代码归一化
    if "credit_code" in updated_fields and mapped.get("credit_code"):
        g.db.execute("UPDATE companies SET credit_code = ? WHERE id = ?",
                     [normalize_credit_code(mapped["credit_code"]), company_id])

    # 更新时间戳
    g.db.execute("UPDATE companies SET updated_at = datetime('now', 'localtime') WHERE id = ?", [company_id])
    g.db.commit()

    return jsonify({
        "code": 0,
        "message": f"已更新 {len(updated_fields)} 个字段",
        "data": {"updated_fields": updated_fields},
    })
