"""EntHub - 企业工商信息管理工具"""
import math
import uuid
import os

import pandas as pd
from flask import (
    Flask, g, render_template, request, redirect,
    url_for, flash, jsonify, abort, send_file
)

from db import get_db, init_db, DB_PATH
import backup
from utils import (
    normalize_name, normalize_phone, normalize_credit_code,
    map_columns, clean_val, is_industrial_park_file,
    extract_date_from_filename,
)

app = Flask(__name__)
app.secret_key = "enthub-dev-key"
PER_PAGE = 25

# Full field list for import
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


@app.before_request
def before_request():
    g.db = get_db()


@app.teardown_request
def teardown_request(exc):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


def split_phones(phone_str, other_phone_str):
    """Extract all unique phone numbers from phone and other_phone fields.
    Returns list of (raw_phone, normalized_phone) tuples. First entry is primary.
    """
    from utils import normalize_phone
    result = []
    seen = set()

    # Primary phone
    if phone_str and phone_str.strip():
        norm = normalize_phone(phone_str.strip())
        if norm and norm not in seen:
            result.append((phone_str.strip(), norm))
            seen.add(norm)

    # Other phones - split by ; ；,
    if other_phone_str and other_phone_str.strip():
        parts = other_phone_str.replace("；", ";").replace(",", ";").split(";")
        for p in parts:
            p = p.strip()
            if not p:
                continue
            norm = normalize_phone(p)
            if norm and norm not in seen:
                result.append((p, norm))
                seen.add(norm)

    return result


def sync_phones(db, company_id, phone_str, other_phone_str,
                recommended_str=""):
    """Populate company_phones table for a given company."""
    db.execute("DELETE FROM company_phones WHERE company_id = ?", [company_id])
    phones = split_phones(phone_str, other_phone_str)
    for i, (raw, norm) in enumerate(phones):
        db.execute(
            "INSERT INTO company_phones (company_id, phone, normalized_phone, is_primary) VALUES (?, ?, ?, ?)",
            [company_id, raw, norm, 1 if i == 0 else 0]
        )

    # Recommended phones — marked separately, displayed first after primary
    if recommended_str and recommended_str.strip():
        from utils import normalize_phone
        parts = recommended_str.replace("；", ";").replace(",", ";").split(";")
        for p in parts:
            raw = p.strip()
            if raw:
                norm = normalize_phone(raw)
                if norm:
                    db.execute(
                        "INSERT INTO company_phones (company_id, phone, normalized_phone, is_primary, is_recommended) VALUES (?, ?, ?, 0, 1)",
                        [company_id, raw, norm]
                    )


def merge_phones(db, company_id, phone_str, other_phone_str,
                 recommended_str=""):
    """Merge new phones into existing ones (accumulate, don't replace)."""
    from utils import normalize_phone
    
    # Get existing normalized phones
    existing = db.execute(
        "SELECT normalized_phone FROM company_phones WHERE company_id = ?",
        [company_id]
    ).fetchall()
    existing_norms = {row["normalized_phone"] for row in existing}
    
    # Check if there's already a primary phone
    has_primary = db.execute(
        "SELECT COUNT(*) as count FROM company_phones WHERE company_id = ? AND is_primary = 1",
        [company_id]
    ).fetchone()["count"] > 0
    
    # Add new phones that don't exist
    phones = split_phones(phone_str, other_phone_str)
    for raw, norm in phones:
        if norm and norm not in existing_norms:
            # If no primary phone exists, mark this as primary
            is_primary = 1 if not has_primary else 0
            db.execute(
                "INSERT INTO company_phones (company_id, phone, normalized_phone, is_primary) VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, is_primary]
            )
            existing_norms.add(norm)
            if is_primary:
                has_primary = True
    
    # Add recommended phones
    if recommended_str and recommended_str.strip():
        parts = recommended_str.replace("；", ";").replace(",", ";").split(";")
        for p in parts:
            raw = p.strip()
            if raw:
                norm = normalize_phone(raw)
                if norm and norm not in existing_norms:
                    db.execute(
                        "INSERT INTO company_phones (company_id, phone, normalized_phone, is_primary, is_recommended) VALUES (?, ?, ?, 0, 1)",
                        [company_id, raw, norm]
                    )
                    existing_norms.add(norm)


# --------------------------------------------------------------------------- #
#  首页
# --------------------------------------------------------------------------- #

@app.route("/")
def index():
    stats = g.db.execute("""
        SELECT
            COUNT(*)                                       AS total,
            COUNT(DISTINCT normalized_name)                AS unique_names,
            (SELECT COUNT(DISTINCT normalized_phone) FROM company_phones) AS unique_phones,
            (SELECT COUNT(*) FROM company_phones) AS rows_with_phone
        FROM companies
    """).fetchone()
    return render_template("index.html", stats=stats)


# --------------------------------------------------------------------------- #
#  统一搜索
# --------------------------------------------------------------------------- #

def _detect_query_type(q):
    """Auto-detect query type: 'phone', 'credit_code', or 'text'."""
    stripped = q.replace(" ", "").replace("-", "").replace("+", "")
    # Pure digits → phone
    if stripped.isdigit():
        return "phone"
    # 18-char alphanumeric → credit code
    norm_cc = normalize_credit_code(q)
    if len(norm_cc) == 18 and norm_cc.isalnum():
        return "credit_code"
    return "text"


@app.route("/search")
def search():
    q = request.args.get("q", "").strip()
    page = max(1, request.args.get("page", 1, type=int))

    if not q:
        return render_template("search.html", q="", query_type="",
                               rows=[], total=0, page=1, pages=1)

    query_type = _detect_query_type(q)

    if query_type == "phone":
        norm_q = normalize_phone(q)
        if not norm_q:
            return render_template("search.html", q=q, query_type="phone",
                                   rows=[], total=0, page=1, pages=1)
        total = g.db.execute(
            """SELECT COUNT(*) FROM company_phones WHERE normalized_phone = ?""",
            [norm_q]
        ).fetchone()[0]
        pages = max(1, math.ceil(total / PER_PAGE))
        offset = (page - 1) * PER_PAGE
        rows = g.db.execute(
            """SELECT c.id, c.name, c.phone, c.address, c.credit_code,
                      c.legal_person, c.business_status, c.province, c.city,
                      '电话' AS matched_field
               FROM company_phones cp
               JOIN companies c ON cp.company_id = c.id
               WHERE cp.normalized_phone = ?
               ORDER BY c.name LIMIT ? OFFSET ?""",
            [norm_q, PER_PAGE, offset]
        ).fetchall()

    elif query_type == "credit_code":
        norm_q = normalize_credit_code(q)
        total = g.db.execute(
            """SELECT COUNT(*) FROM companies WHERE credit_code = ?""",
            [norm_q]
        ).fetchone()[0]
        pages = max(1, math.ceil(total / PER_PAGE))
        offset = (page - 1) * PER_PAGE
        rows = g.db.execute(
            """SELECT id, name, phone, address, credit_code,
                      legal_person, business_status, province, city,
                      '信用代码' AS matched_field
               FROM companies WHERE credit_code = ?
               LIMIT ? OFFSET ?""",
            [norm_q, PER_PAGE, offset]
        ).fetchall()

    else:
        # Text search across multiple fields, ranked by priority
        norm_q_name = normalize_name(q)
        like_q = "%" + q + "%"
        like_name = "%" + norm_q_name + "%"

        # Count total unique results
        total_sql = """
            SELECT COUNT(*) FROM (
                SELECT id FROM companies WHERE normalized_name LIKE ?
                UNION
                SELECT id FROM companies WHERE former_name LIKE ?
                UNION
                SELECT id FROM companies WHERE address LIKE ?
                UNION
                SELECT id FROM companies WHERE legal_person LIKE ?
                UNION
                SELECT id FROM companies WHERE shareholders LIKE ?
                UNION
                SELECT id FROM companies WHERE email LIKE ?
                UNION
                SELECT id FROM companies WHERE website LIKE ?
            )
        """
        total = g.db.execute(total_sql, [
            like_name, like_q, like_q, like_q, like_q, like_q, like_q
        ]).fetchone()[0]
        pages = max(1, math.ceil(total / PER_PAGE))
        offset = (page - 1) * PER_PAGE

        # Ranked query using UNION ALL with priority ordering
        ranked_sql = """
            SELECT id, name, phone, address, credit_code,
                   legal_person, business_status, province, city, matched_field
            FROM (
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '名称' AS matched_field, 1 AS priority
                FROM companies WHERE normalized_name LIKE ?
                UNION ALL
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '曾用名', 2
                FROM companies WHERE former_name LIKE ?
                UNION ALL
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '地址', 3
                FROM companies WHERE address LIKE ?
                UNION ALL
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '法人', 4
                FROM companies WHERE legal_person LIKE ?
                UNION ALL
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '股东', 5
                FROM companies WHERE shareholders LIKE ?
                UNION ALL
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '邮箱', 6
                FROM companies WHERE email LIKE ?
                UNION ALL
                SELECT id, name, phone, address, credit_code,
                       legal_person, business_status, province, city,
                       '网站', 7
                FROM companies WHERE website LIKE ?
            )
            GROUP BY id
            ORDER BY priority, name
            LIMIT ? OFFSET ?
        """
        rows = g.db.execute(ranked_sql, [
            like_name, like_q, like_q, like_q, like_q, like_q, like_q,
            PER_PAGE, offset
        ]).fetchall()

    return render_template("search.html", q=q, query_type=query_type,
                           rows=rows, total=total, page=page, pages=pages)


# --------------------------------------------------------------------------- #
#  电话重复统计
# --------------------------------------------------------------------------- #

@app.route("/stats/phone")
def stats_phone():
    page = max(1, request.args.get("page", 1, type=int))
    min_count = max(2, request.args.get("min", 2, type=int))

    # Query from company_phones for full coverage
    total_dup_phones = g.db.execute("""
        SELECT COUNT(*) FROM (
            SELECT normalized_phone FROM company_phones
            WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
            GROUP BY normalized_phone
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages = max(1, math.ceil(total_dup_phones / PER_PAGE))
    offset = (page - 1) * PER_PAGE

    rows = g.db.execute("""
        SELECT
            cp.normalized_phone,
            MIN(cp.phone)     AS display_phone,
            COUNT(*)          AS cnt,
            GROUP_CONCAT(DISTINCT c.name) AS company_names
        FROM company_phones cp
        JOIN companies c ON cp.company_id = c.id
        WHERE cp.normalized_phone IS NOT NULL AND cp.normalized_phone <> ''
        GROUP BY cp.normalized_phone
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ? OFFSET ?
    """, [min_count, PER_PAGE, offset]).fetchall()

    return render_template("stats_phone.html", rows=rows,
                           total=total_dup_phones, min_count=min_count,
                           page=page, pages=pages)


# --------------------------------------------------------------------------- #
#  企业详情
# --------------------------------------------------------------------------- #

@app.route("/company/<int:company_id>")
def company_detail(company_id):
    row = g.db.execute(
        "SELECT * FROM companies WHERE id = ?", [company_id]
    ).fetchone()
    if not row:
        abort(404)

    # Get all phones for this company from company_phones
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

    # Find related records via company_phones
    phone_norms = [r["normalized_phone"] for r in company_phones] if company_phones else [row["normalized_phone"]]
    phone_placeholders = ",".join(["?"] * len(phone_norms)) if phone_norms else "''"

    related = g.db.execute(f"""
        SELECT DISTINCT c.id, c.name, c.phone, c.address, c.credit_code
        FROM companies c
        JOIN company_phones cp ON cp.company_id = c.id
        WHERE c.id <> ? AND cp.normalized_phone IN ({phone_placeholders})
        AND cp.normalized_phone <> ''
        ORDER BY c.name LIMIT 20
    """, [company_id] + phone_norms).fetchall()

    return render_template("company_detail.html", company=row, related=related, company_phones=company_phones)


# --------------------------------------------------------------------------- #
#  手动录入
# --------------------------------------------------------------------------- #

@app.route("/add", methods=["GET", "POST"])
def add_company():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("请填写企业名称", "error")
            return redirect(url_for("add_company"))

        fields = {}
        for f in IMPORT_FIELDS:
            val = request.form.get(f, "").strip()
            if val:
                fields[f] = val

        fields["normalized_name"] = normalize_name(name)
        if fields.get("phone"):
            fields["normalized_phone"] = normalize_phone(fields["phone"])
        else:
            fields["normalized_phone"] = ""
        if fields.get("credit_code"):
            fields["credit_code"] = normalize_credit_code(fields["credit_code"])

        fields["status"] = request.form.get("status", "active")
        fields["source"] = "manual"

        cols = ", ".join(fields.keys())
        placeholders = ", ".join(["?"] * len(fields))
        cursor = g.db.execute(
            f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
            list(fields.values())
        )
        sync_phones(g.db, cursor.lastrowid,
                    fields.get("phone", ""), fields.get("other_phone", ""))
        g.db.commit()
        flash(f"已录入：{name}", "success")
        return redirect(url_for("add_company"))

    return render_template("add.html")


# --------------------------------------------------------------------------- #
#  Excel 导入
# --------------------------------------------------------------------------- #

@app.route("/import", methods=["GET"])
def import_page():
    return render_template("import.html")


def detect_header_row(df_raw):
    """Detect which row contains the real column headers.
    Tianyancha exports have 2 junk rows before the real header.
    """
    header_keys = {"公司名称", "企业名称", "统一社会信用代码", "法定代表人",
                   "登记状态", "经营状态", "联系电话", "注册资本"}
    for i in range(min(5, len(df_raw))):
        row_vals = [str(v).strip() for v in df_raw.iloc[i].values]
        if any(v in header_keys for v in row_vals):
            return i
    return None


@app.route("/import/upload", methods=["POST"])
def import_upload():
    files = request.files.getlist("file")
    valid_files = [f for f in files if f.filename]

    if not valid_files:
        flash("未选择文件", "error")
        return redirect(url_for("import_page"))

    batch_id = uuid.uuid4().hex[:12]
    all_cleaned = []
    errors = []
    file_mappings = []  # Store field mapping info per file

    for file in valid_files:
        try:
            df_raw = pd.read_excel(file, header=None)
        except Exception as e:
            errors.append(f"{file.filename}: 读取失败 ({e})")
            continue

        if df_raw.empty:
            errors.append(f"{file.filename}: 文件为空")
            continue

        # Auto-detect header row
        header_idx = detect_header_row(df_raw)
        if header_idx is not None and header_idx > 0:
            file.seek(0)
            df = pd.read_excel(file, header=header_idx)
        else:
            file.seek(0)
            df = pd.read_excel(file)

        if df.empty:
            errors.append(f"{file.filename}: 无有效数据")
            continue

        # Detect industrial park / sales tracker files
        if is_industrial_park_file(df.columns):
            errors.append(f"{file.filename}: 检测到非工商数据（工业园区/销售跟踪表），已跳过")
            continue

        col_map, sec_phones, rec_phones, unmatched = map_columns(df.columns)
        if "name" not in col_map.values():
            errors.append(f"{file.filename}: 未找到企业名称列")
            continue

        if unmatched:
            errors.append(f"{file.filename}: 以下列无法匹配，已忽略: {', '.join(str(c) for c in unmatched[:5])}{'...' if len(unmatched) > 5 else ''}")

        # Store field mapping info for preview display
        file_mappings.append({
            "file": file.filename,
            "total_cols": len(df.columns),
            "matched": {str(k): v for k, v in col_map.items()},
            "secondary": [str(c) for c in sec_phones],
            "recommended": [str(c) for c in rec_phones],
            "unmatched": [str(c) for c in unmatched],
        })

        # Extract date from filename for updated_at
        # For web uploads, we can't get file modification time, so use current time as fallback
        from datetime import datetime
        file_date = extract_date_from_filename(file.filename)
        if not file_date:
            file_date = datetime.now().strftime('%Y-%m-%d')

        # Source filename for traceability
        source_name = file.filename

        row_offset = len(all_cleaned)
        for idx, row in df.iterrows():
            record = {"row_num": row_offset + idx + 2, "batch_id": batch_id}
            for orig_col, field in col_map.items():
                record[field] = clean_val(row[orig_col])

            # Merge secondary phone columns (联系电话2~10) into other_phone
            sec_parts = []
            for sc in sec_phones:
                v = clean_val(row[sc])
                if v:
                    sec_parts.append(v)
            if sec_parts:
                existing_other = record.get("other_phone", "")
                merged = ";".join(p for p in [existing_other] + sec_parts if p)
                record["other_phone"] = merged

            # Store recommended phone separately for sync_phones
            rec_parts = []
            for rc in rec_phones:
                v = clean_val(row[rc])
                if v:
                    rec_parts.append(v)
            record["_recommended_phone"] = ";".join(rec_parts)

            # Source filename
            # If the data has a 文件名 column, use that; otherwise use the actual filename
            if not record.get("source_file"):
                record["source_file"] = source_name

            # File date for updated_at
            record["_file_date"] = file_date

            if not record.get("name"):
                continue

            record["normalized_name"] = normalize_name(record.get("name", ""))
            record["normalized_phone"] = normalize_phone(record.get("phone", ""))
            if record.get("credit_code"):
                record["credit_code"] = normalize_credit_code(record.get("credit_code"))

            # Dedup check against existing data
            is_dup = 0
            dup_reason = ""
            will_update = 0  # 1 = will update, 0 = will skip, -1 = new record

            if record.get("credit_code"):
                existing = g.db.execute(
                    "SELECT id, updated_at FROM companies WHERE credit_code = ? LIMIT 1",
                    [record["credit_code"]]
                ).fetchone()
                if existing:
                    is_dup = 1
                    dup_reason = "credit_code"
                    # Check if new data is newer
                    if file_date and existing["updated_at"]:
                        will_update = 1 if file_date > existing["updated_at"] else 0
                    elif file_date:
                        will_update = 1  # No existing date, new has date

            if not is_dup and record["normalized_name"]:
                existing = g.db.execute(
                    "SELECT id, updated_at FROM companies WHERE normalized_name = ? LIMIT 1",
                    [record["normalized_name"]]
                ).fetchone()
                if existing:
                    is_dup = 1
                    dup_reason = "name"
                    # Check if new data is newer
                    if file_date and existing["updated_at"]:
                        will_update = 1 if file_date > existing["updated_at"] else 0
                    elif file_date:
                        will_update = 1

            record["is_duplicate"] = is_dup
            record["duplicate_reason"] = dup_reason
            record["will_update"] = will_update
            all_cleaned.append(record)

    if not all_cleaned:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("import_page"))

    # Intra-batch dedup (across all files)
    seen_names = {}
    seen_codes = {}
    for rec in all_cleaned:
        if rec.get("credit_code") and rec["credit_code"] in seen_codes:
            rec["is_duplicate"] = 1
            if not rec["duplicate_reason"]:
                rec["duplicate_reason"] = "batch:credit_code"
        elif rec.get("credit_code"):
            seen_codes[rec["credit_code"]] = rec["row_num"]

        if rec["normalized_name"] and rec["normalized_name"] in seen_names:
            if not rec["is_duplicate"]:
                rec["is_duplicate"] = 1
                rec["duplicate_reason"] = "batch:name"
        elif rec["normalized_name"]:
            seen_names[rec["normalized_name"]] = rec["row_num"]

    # Insert into preview
    for rec in all_cleaned:
        g.db.execute("""
            INSERT INTO import_preview
                (batch_id, row_num, name, normalized_name, phone, normalized_phone,
                 address, credit_code, legal_person, is_duplicate, duplicate_reason, will_update)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            rec["batch_id"], rec["row_num"], rec.get("name", ""),
            rec.get("normalized_name", ""), rec.get("phone", ""),
            rec.get("normalized_phone", ""), rec.get("address", ""),
            rec.get("credit_code", ""), rec.get("legal_person", ""),
            rec.get("is_duplicate", 0), rec.get("duplicate_reason", ""),
            rec.get("will_update", 0)
        ])
    g.db.commit()

    # Store full cleaned data + field mappings
    import json, tempfile, os
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    with open(temp_path, "w") as f:
        json.dump({"records": all_cleaned, "mappings": file_mappings}, f, ensure_ascii=False)

    if errors:
        for e in errors:
            flash(e, "warning")

    flash(f"已分析 {len(valid_files)} 个文件，共 {len(all_cleaned)} 条记录", "success")
    return redirect(url_for("import_preview", batch_id=batch_id))


@app.route("/import/preview/<batch_id>")
def import_preview(batch_id):
    stats = g.db.execute("""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN is_duplicate = 0 THEN 1 ELSE 0 END), 0) AS new_count,
            COALESCE(SUM(CASE WHEN is_duplicate = 1 AND will_update = 1 THEN 1 ELSE 0 END), 0) AS update_count,
            COALESCE(SUM(CASE WHEN is_duplicate = 1 AND will_update = 0 THEN 1 ELSE 0 END), 0) AS skip_count
        FROM import_preview WHERE batch_id = ?
    """, [batch_id]).fetchone()

    sample = g.db.execute("""
        SELECT * FROM import_preview
        WHERE batch_id = ? ORDER BY row_num LIMIT 30
    """, [batch_id]).fetchall()

    # Load field mapping info
    import json, tempfile, os
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    mappings = []
    if os.path.exists(temp_path):
        with open(temp_path) as f:
            data = json.load(f)
            if isinstance(data, dict):
                mappings = data.get("mappings", [])

    return render_template("import_preview.html", batch_id=batch_id,
                           stats=stats, sample=sample, mappings=mappings)


@app.route("/import/confirm/<batch_id>", methods=["POST"])
def import_confirm(batch_id):
    import json, tempfile, os, sqlite3
    skip_dup = request.form.get("skip_dup", "1") == "1"

    # 导入前自动备份
    backup_result = backup.create_backup(DB_PATH, reason="导入前自动备份")
    if backup_result["success"]:
        backup.cleanup_old_backups(keep_count=7)
        flash(f"已自动创建备份：{backup_result['filename']}", "info")
    else:
        flash(f"自动备份失败：{backup_result.get('error', '未知错误')}", "warning")

    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if not os.path.exists(temp_path):
        flash("导入会话已过期，请重新上传", "error")
        return redirect(url_for("import_page"))

    with open(temp_path) as f:
        data = json.load(f)
        cleaned = data["records"] if isinstance(data, dict) else data  # backward compat

    # Pre-load existing records for faster duplicate checking
    existing_by_code = {}
    existing_by_name = {}
    for row in g.db.execute("SELECT id, credit_code, normalized_name, updated_at FROM companies"):
        if row["credit_code"]:
            existing_by_code[row["credit_code"]] = (row["id"], row["updated_at"])
        if row["normalized_name"]:
            existing_by_name[row["normalized_name"]] = (row["id"], row["updated_at"])

    # Start transaction
    g.db.execute("BEGIN TRANSACTION")
    
    inserted = 0
    updated = 0
    phones_merged = 0
    try:
        for rec in cleaned:
            file_date = rec.get("_file_date")
            
            # Check if this is a duplicate
            existing_id = None
            existing_updated = None
            
            if rec.get("credit_code") and rec["credit_code"] in existing_by_code:
                existing_id, existing_updated = existing_by_code[rec["credit_code"]]
            elif rec.get("normalized_name") and rec["normalized_name"] in existing_by_name:
                existing_id, existing_updated = existing_by_name[rec["normalized_name"]]
        
            # Decide: insert, update, or skip
            if existing_id:
                # Duplicate found
                if skip_dup:
                    # Check if new data is newer
                    if file_date and existing_updated:
                        if file_date > existing_updated:
                            # New data is newer → UPDATE
                            pass  # Continue to update logic below
                        else:
                            # New data is older → SKIP field updates, but MERGE phones
                            merge_phones(
                                g.db, existing_id,
                                rec.get("phone", ""),
                                rec.get("other_phone", ""),
                                rec.get("_recommended_phone", "")
                            )
                            phones_merged += 1
                            continue
                    elif file_date:
                        # No existing date, but new has date → UPDATE
                        pass
                    else:
                        # No date comparison possible → SKIP field updates, but MERGE phones
                        merge_phones(
                            g.db, existing_id,
                            rec.get("phone", ""),
                            rec.get("other_phone", ""),
                            rec.get("_recommended_phone", "")
                        )
                        phones_merged += 1
                        continue
                # If skip_dup is False, we'll update anyway (upsert mode)
                
                # UPDATE existing record
                fields = {}
                for f_name in IMPORT_FIELDS:
                    val = rec.get(f_name, "")
                    if val:  # Only update non-empty fields
                        fields[f_name] = val
                
                if fields:
                    fields["normalized_name"] = rec.get("normalized_name", "")
                    fields["normalized_phone"] = rec.get("normalized_phone", "")
                    if file_date:
                        fields["updated_at"] = file_date
                    
                    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
                    g.db.execute(
                        f"UPDATE companies SET {set_clause} WHERE id = ?",
                        list(fields.values()) + [existing_id]
                    )
                    
                    # Update phones (merge, don't replace)
                    merge_phones(
                        g.db, existing_id,
                        rec.get("phone", ""),
                        rec.get("other_phone", ""),
                        rec.get("_recommended_phone", "")
                    )
                    updated += 1
            else:
                # No duplicate → INSERT
                fields = {}
                for f_name in IMPORT_FIELDS:
                    val = rec.get(f_name, "")
                    if val:
                        fields[f_name] = val

                fields["normalized_name"] = rec.get("normalized_name", "")
                fields["normalized_phone"] = rec.get("normalized_phone", "")
                fields["status"] = "active"
                fields["source"] = "import"

                if file_date:
                    fields["updated_at"] = file_date

                cols = ", ".join(fields.keys())
                placeholders = ", ".join(["?"] * len(fields))
                cursor = g.db.execute(
                    f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
                    list(fields.values())
                )
                sync_phones(
                    g.db, cursor.lastrowid,
                    rec.get("phone", ""),
                    rec.get("other_phone", ""),
                    rec.get("_recommended_phone", "")
                )
                inserted += 1

        g.db.execute("DELETE FROM import_preview WHERE batch_id = ?", [batch_id])
        g.db.commit()
        
        # Clean up temp file
        if os.path.exists(temp_path):
            os.remove(temp_path)
    except Exception as e:
        g.db.rollback()
        flash(f"导入失败：{str(e)}", "error")
        return redirect(url_for("import_page"))
    
    msg = f"已导入 {inserted} 条新记录"
    if updated > 0:
        msg += f"，更新 {updated} 条已有记录"
    if phones_merged > 0:
        msg += f"，累加 {phones_merged} 条记录的电话号码"
    flash(msg, "success")
    return redirect(url_for("index"))


@app.route("/import/cancel/<batch_id>", methods=["POST"])
def import_cancel(batch_id):
    import os, tempfile
    g.db.execute("DELETE FROM import_preview WHERE batch_id = ?", [batch_id])
    g.db.commit()
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if os.path.exists(temp_path):
        os.remove(temp_path)
    flash("已取消导入", "info")
    return redirect(url_for("import_page"))


# --------------------------------------------------------------------------- #
#  JSON API
# --------------------------------------------------------------------------- #

@app.route("/api/search")
def api_search():
    q = request.args.get("q", "").strip()
    limit = min(50, request.args.get("limit", 20, type=int))

    if not q:
        return jsonify({"results": []})

    query_type = _detect_query_type(q)

    if query_type == "phone":
        norm_q = normalize_phone(q)
        rows = g.db.execute(
            """SELECT c.id, c.name, c.phone, c.address, c.credit_code, '电话' AS matched_field
               FROM company_phones cp
               JOIN companies c ON cp.company_id = c.id
               WHERE cp.normalized_phone = ?
               ORDER BY c.name LIMIT ?""",
            [norm_q, limit]
        ).fetchall()
        return jsonify({
            "query": q, "type": "phone",
            "count": len(rows), "results": [dict(r) for r in rows]
        })
    elif query_type == "credit_code":
        norm_q = normalize_credit_code(q)
        rows = g.db.execute(
            """SELECT id, name, phone, address, credit_code, '信用代码' AS matched_field
               FROM companies WHERE credit_code = ?
               LIMIT ?""",
            [norm_q, limit]
        ).fetchall()
        return jsonify({
            "query": q, "type": "credit_code",
            "count": len(rows), "results": [dict(r) for r in rows]
        })
    else:
        norm_q_name = normalize_name(q)
        like_q = "%" + q + "%"
        like_name = "%" + norm_q_name + "%"
        rows = g.db.execute(
            """SELECT id, name, phone, address, credit_code, matched_field
               FROM (
                   SELECT id, name, phone, address, credit_code,
                          '名称' AS matched_field, 1 AS priority
                   FROM companies WHERE normalized_name LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code, '曾用名', 2
                   FROM companies WHERE former_name LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code, '地址', 3
                   FROM companies WHERE address LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code, '法人', 4
                   FROM companies WHERE legal_person LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code, '股东', 5
                   FROM companies WHERE shareholders LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code, '邮箱', 6
                   FROM companies WHERE email LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code, '网站', 7
                   FROM companies WHERE website LIKE ?
               )
               GROUP BY id ORDER BY priority, name LIMIT ?""",
            [like_name, like_q, like_q, like_q, like_q, like_q, like_q, limit]
        ).fetchall()
        return jsonify({
            "query": q, "type": "text",
            "count": len(rows), "results": [dict(r) for r in rows]
        })


@app.route("/api/phone_stats")
def api_phone_stats():
    limit = min(100, request.args.get("limit", 20, type=int))
    rows = g.db.execute("""
        SELECT
            cp.normalized_phone,
            MIN(cp.phone) AS display_phone,
            COUNT(*)   AS cnt,
            GROUP_CONCAT(DISTINCT c.name) AS company_names
        FROM company_phones cp
        JOIN companies c ON cp.company_id = c.id
        WHERE cp.normalized_phone IS NOT NULL AND cp.normalized_phone <> ''
        GROUP BY cp.normalized_phone
        HAVING cnt >= 2
        ORDER BY cnt DESC
        LIMIT ?
    """, [limit]).fetchall()
    return jsonify({"results": [dict(r) for r in rows]})


# ── 数据备份 ──

@app.route("/backup")
def backup_page():
    backups = backup.list_backups()
    backup_dir = backup.get_backup_dir()
    return render_template("backup.html", backups=backups, backup_dir=str(backup_dir))


@app.route("/backup/create", methods=["POST"])
def backup_create():
    result = backup.create_backup(DB_PATH, reason="手动备份")
    if result["success"]:
        backup.cleanup_old_backups(keep_count=7)
        flash(f"备份成功：{result['filename']}", "success")
    else:
        flash(f"备份失败：{result.get('error', '未知错误')}", "error")
    return redirect(url_for("backup_page"))


@app.route("/backup/download/<filename>")
def backup_download(filename):
    backup_dir = backup.get_backup_dir()
    filepath = backup_dir / filename
    # 安全检查
    if not filepath.resolve().is_relative_to(backup_dir.resolve()):
        abort(403)
    if not filepath.exists():
        flash("文件不存在", "error")
        return redirect(url_for("backup_page"))
    return send_file(str(filepath), as_attachment=True, download_name=filename)


@app.route("/backup/delete/<filename>", methods=["POST"])
def backup_delete(filename):
    result = backup.delete_backup(filename)
    if result["success"]:
        flash(f"已删除备份：{filename}", "success")
    else:
        flash(f"删除失败：{result.get('error', '未知错误')}", "error")
    return redirect(url_for("backup_page"))


# ── 启动时检查定时备份 ──

def _startup_backup_check():
    """应用启动时检查是否需要每日备份"""
    result = backup.check_daily_backup(DB_PATH)
    if result.get("success") and not result.get("skipped"):
        print(f"[备份] 启动时自动备份：{result.get('filename')}")
    elif result.get("skipped"):
        print(f"[备份] 今日已备份，跳过")


_startup_backup_check()


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5210, debug=True)
