"""EntHub - 企业工商信息管理工具"""
import math
import uuid
import os
import json
import sqlite3
import tempfile
import threading
import queue

import pandas as pd
from flask import (
    Flask, g, render_template, request, redirect,
    url_for, flash, jsonify, abort, send_file, Response
)

from db import get_db, init_db, DB_PATH
import backup
from utils import (
    normalize_name, normalize_phone, normalize_credit_code,
    normalize_person_name, normalize_email,
    map_columns, clean_val, is_industrial_park_file,
    extract_date_from_filename,
)

app = Flask(__name__)
app.secret_key = "enthub-dev-key"
PER_PAGE = 25

# 注册 API 蓝图
from api import api_bp
app.register_blueprint(api_bp)

# 导入任务跟踪（SSE 异步导入）
_import_tasks = {}  # batch_id -> {queue, stop_event, status}

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


def split_shareholders(shareholders_str):
    """Split shareholders string into list of (raw, normalized)."""
    from utils import normalize_person_name
    if not shareholders_str:
        return []
    parts = str(shareholders_str).replace("；", ";").replace(",", ";").replace("，", ";").split(";")
    result = []
    for p in parts:
        raw = p.strip()
        if raw and raw != "-":
            norm = normalize_person_name(raw)
            if norm:
                result.append((raw, norm))
    return result


def sync_shareholders(db, company_id, shareholders_str):
    """Full rebuild: delete all shareholders then insert (for edit/create)."""
    db.execute("DELETE FROM company_shareholders WHERE company_id = ?", [company_id])
    for raw, norm in split_shareholders(shareholders_str):
        if norm:
            db.execute(
                "INSERT INTO company_shareholders (company_id, name, normalized_name) VALUES (?, ?, ?)",
                [company_id, raw, norm]
            )


def merge_shareholders(db, company_id, shareholders_str):
    """Merge new shareholders into existing ones (accumulate, don't replace)."""
    from utils import normalize_person_name
    existing = db.execute(
        "SELECT normalized_name FROM company_shareholders WHERE company_id = ?",
        [company_id]
    ).fetchall()
    existing_norms = {row["normalized_name"] for row in existing}

    for raw, norm in split_shareholders(shareholders_str):
        if norm and norm not in existing_norms:
            db.execute(
                "INSERT INTO company_shareholders (company_id, name, normalized_name) VALUES (?, ?, ?)",
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
    recents = g.db.execute("""
        SELECT id, q, query_type, result_count, created_at
        FROM recent_searches
        ORDER BY id DESC
        LIMIT 8
    """).fetchall()
    return render_template("index.html", stats=stats, recents=recents)


# --------------------------------------------------------------------------- #
#  重启服务器
# --------------------------------------------------------------------------- #

@app.route("/restart")
def restart_server():
    """触发服务器重启（通过修改监控文件）"""
    from pathlib import Path
    import time
    # 修改一个临时文件触发Flask的auto-reload
    trigger_file = Path(__file__).parent / ".restart_trigger"
    trigger_file.write_text(str(time.time()))
    return render_template("restarting.html")


# --------------------------------------------------------------------------- #
#  数据清理
# --------------------------------------------------------------------------- #

@app.route("/cleanup")
def cleanup_page():
    """统计待清理数据量。"""
    stats = {}
    stats["nan_count"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE name IN ('nan','NaN','NAN') OR normalized_name IN ('nan','NaN','NAN')"
    ).fetchone()[0]

    stats["header_count"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE name IN ('公司名称','企业名称')"
    ).fetchone()[0]

    stats["cc_total"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE credit_code IS NOT NULL AND credit_code <> '' AND credit_code NOT IN ('nan','NaN','NAN')"
    ).fetchone()[0]
    stats["cc_unique"] = g.db.execute(
        "SELECT COUNT(DISTINCT credit_code) FROM companies WHERE credit_code IS NOT NULL AND credit_code <> '' AND credit_code NOT IN ('nan','NaN','NAN')"
    ).fetchone()[0]
    stats["cc_dup"] = stats["cc_total"] - stats["cc_unique"]

    stats["name_total"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE normalized_name IS NOT NULL AND normalized_name <> '' AND normalized_name <> 'nan'"
    ).fetchone()[0]
    stats["name_unique"] = g.db.execute(
        "SELECT COUNT(DISTINCT normalized_name) FROM companies WHERE normalized_name IS NOT NULL AND normalized_name <> '' AND normalized_name <> 'nan'"
    ).fetchone()[0]
    stats["name_dup"] = stats["name_total"] - stats["name_unique"]

    stats["total"] = g.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    stats["placeholder_count"] = g.db.execute("""
        SELECT COUNT(*) FROM companies WHERE 
        business_scope LIKE '%暂不予显示%' OR business_scope LIKE '%企业信息暂不%'
        OR name LIKE '%暂不予显示%' OR address LIKE '%暂不予显示%'
    """).fetchone()[0]

    # 新增：数据质量指标
    stats["with_phone"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE phone IS NOT NULL AND phone <> '' AND phone <> '-'"
    ).fetchone()[0]
    stats["with_legal_person"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE legal_person IS NOT NULL AND legal_person <> '' AND legal_person <> '-'"
    ).fetchone()[0]
    stats["with_address"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE address IS NOT NULL AND address <> '' AND address <> '-'"
    ).fetchone()[0]

    # 计算待清理总数
    stats["total_to_clean"] = (stats["nan_count"] + stats["header_count"] + 
                               stats["placeholder_count"] + stats["cc_dup"] + stats["name_dup"])

    return render_template("cleanup.html", stats=stats)


@app.route("/cleanup/execute", methods=["POST"])
def cleanup_execute():
    """启动异步清理。"""
    # 读取用户勾选的清理项
    clean_nan = request.form.get("clean_nan", "0") == "1"
    clean_header = request.form.get("clean_header", "0") == "1"
    clean_placeholder = request.form.get("clean_placeholder", "0") == "1"
    clean_cc_dup = request.form.get("clean_cc_dup", "0") == "1"
    clean_name_dup = request.form.get("clean_name_dup", "0") == "1"

    task_queue = queue.Queue()
    stop_event = threading.Event()
    _import_tasks["cleanup"] = {"queue": task_queue, "stop_event": stop_event, "status": "running"}

    t = threading.Thread(
        target=_cleanup_worker,
        args=(task_queue, stop_event, clean_nan, clean_header, clean_placeholder, clean_cc_dup, clean_name_dup),
        daemon=True,
    )
    t.start()

    return render_template("cleanup_progress.html")


def _cleanup_worker(task_queue, stop_event, clean_nan, clean_header, clean_placeholder, clean_cc_dup, clean_name_dup):
    def send(event, data=None):
        task_queue.put({"event": event, "data": data or {}})

    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")

        total_before = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        send("start", {"total_before": total_before})

        deleted_total = 0
        step = 0

        # 1. 删除 nan 记录
        if clean_nan and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "清理空值/无效数据"})
            n = db.execute("DELETE FROM companies WHERE name IN ('nan','NaN','NAN') OR normalized_name IN ('nan','NaN','NAN')").rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 2. 删除表头行
        if clean_header and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "清理表头行"})
            n = db.execute("DELETE FROM companies WHERE name IN ('公司名称','企业名称')").rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 2.5 清理"暂不予显示"等无效占位文本
        if clean_placeholder and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "清理无效占位文本"})
            n = db.execute("""
                DELETE FROM companies WHERE 
                business_scope LIKE '%暂不予显示%' OR business_scope LIKE '%企业信息暂不%'
                OR name LIKE '%暂不予显示%' OR address LIKE '%暂不予显示%'
            """).rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 3. 信用代码去重（保留字段最完整的记录）
        if clean_cc_dup and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "信用代码去重"})
            n = _dedup_by_field(db, "credit_code")
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 4. 企业名称去重（保留字段最完整的记录）
        if clean_name_dup and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "企业名称去重"})
            n = _dedup_by_field(db, "normalized_name")
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 5. 清理孤立电话
        if not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "清理孤立电话记录"})
            n = db.execute("DELETE FROM company_phones WHERE company_id NOT IN (SELECT id FROM companies)").rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        db.commit()

        total_after = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        db.close()

        if stop_event.is_set():
            send("stopped", {"total_deleted": deleted_total, "total_after": total_after})
        else:
            send("done", {"total_before": total_before, "total_after": total_after, "total_deleted": deleted_total})

    except Exception as e:
        send("error", {"message": str(e)})


def _dedup_by_field(db, field):
    """按字段去重，保留每组中「最好」的记录。
    
    逻辑与导入一致：
    - 保留字段最完整的记录（完整度 > updated_at > id）
    - 被删记录的电话累加到保留记录（merge_phones 自动去重）
    - 被删记录的非空字段补全保留记录的空字段
    """
    groups = db.execute(f"""
        SELECT {field}, GROUP_CONCAT(id) as ids
        FROM companies
        WHERE {field} IS NOT NULL AND {field} <> '' AND {field} NOT IN ('nan','NaN','NAN')
        GROUP BY {field}
        HAVING COUNT(*) > 1
    """).fetchall()

    deleted = 0

    for group in groups:
        ids = [int(x) for x in group["ids"].split(",")]
        if len(ids) <= 1:
            continue

        # 读取这些记录
        placeholders = ",".join(["?"] * len(ids))
        rows = db.execute(f"""
            SELECT id, name, phone, other_phone, credit_code, legal_person,
                   address, province, city, industry, business_status, email,
                   updated_at, registered_capital, paid_capital,
                   established_date, approved_date, business_term,
                   district, insured_count, company_type, former_name,
                   website, other_email, business_scope, enterprise_scale,
                   shareholders, mailing_address, english_name, tags,
                   annual_report_address, taxpayer_id, registration_no, org_code,
                   (CASE WHEN name <> '' THEN 1 ELSE 0 END +
                    CASE WHEN phone <> '' THEN 1 ELSE 0 END +
                    CASE WHEN credit_code <> '' THEN 1 ELSE 0 END +
                    CASE WHEN legal_person <> '' THEN 1 ELSE 0 END +
                    CASE WHEN address <> '' THEN 1 ELSE 0 END +
                    CASE WHEN province <> '' THEN 1 ELSE 0 END +
                    CASE WHEN city <> '' THEN 1 ELSE 0 END +
                    CASE WHEN industry <> '' THEN 1 ELSE 0 END +
                    CASE WHEN business_status <> '' THEN 1 ELSE 0 END +
                    CASE WHEN email <> '' THEN 1 ELSE 0 END) AS completeness
            FROM companies WHERE id IN ({placeholders})
        """, ids).fetchall()

        # 排序：完整度降序 > updated_at 降序 > id 升序
        rows = list(rows)
        rows.sort(key=lambda r: (-r["completeness"], r["updated_at"] or "", r["id"]))

        keep = rows[0]
        keep_id = keep["id"]

        # 从被删记录中合并数据到保留记录
        updates = {}
        for r in rows[1:]:
            # 电话累加（merge_phones 自动按 normalized_phone 去重）
            if r["phone"] or r["other_phone"]:
                merge_phones(db, keep_id, r["phone"] or "", r["other_phone"] or "")

            # 其他字段：保留记录为空时，从被删记录补全
            for f in IMPORT_FIELDS:
                if f in ("name", "phone", "other_phone", "source_file", "tags"):
                    continue  # 这些字段特殊处理或不合并
                kept_val = updates.get(f) or keep[f]
                dup_val = r[f] if f in r.keys() else ""
                if (not kept_val or kept_val in ("", "-", "nan")) and dup_val and dup_val not in ("", "-", "nan"):
                    updates[f] = dup_val

        # 应用字段补全
        if updates:
            set_parts = []
            params = []
            for k, v in updates.items():
                set_parts.append(f"{k} = ?")
                params.append(v)
            params.append(keep_id)
            db.execute(f"UPDATE companies SET {', '.join(set_parts)} WHERE id = ?", params)

        # 删除被合并的记录
        delete_ids = [r["id"] for r in rows[1:]]
        del_placeholders = ",".join(["?"] * len(delete_ids))
        db.execute(f"DELETE FROM companies WHERE id IN ({del_placeholders})", delete_ids)
        deleted += len(delete_ids)

    return deleted


@app.route("/cleanup/stream")
def cleanup_stream():
    task = _import_tasks.get("cleanup")
    if not task:
        return Response("event: error\ndata: {\"message\": \"任务不存在\"}\n\n",
                        content_type="text/event-stream")

    def generate():
        try:
            while True:
                try:
                    msg = task["queue"].get(timeout=30)
                    event = msg.get("event", "message")
                    data = json.dumps(msg.get("data", {}), ensure_ascii=False)
                    yield f"event: {event}\ndata: {data}\n\n"
                    if event in ("done", "error", "stopped"):
                        break
                except queue.Empty:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
        finally:
            _import_tasks.pop("cleanup", None)

    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/cleanup/stop", methods=["POST"])
def cleanup_stop():
    task = _import_tasks.get("cleanup")
    if task:
        task["stop_event"].set()
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404



# --------------------------------------------------------------------------- #
#  数据浏览
# --------------------------------------------------------------------------- #

@app.route("/browse")
def browse():
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", 25, type=int)))

    # 筛选
    filters = {}
    filter_clauses = []
    filter_params = []
    for f in ("city", "district", "business_status", "industry"):
        val = request.args.get(f, "").strip()
        if val:
            filters[f] = val
            filter_clauses.append(f"{f} = ?")
            filter_params.append(val)

    # 成立年份区间
    year_from = request.args.get("year_from", "").strip()
    year_to = request.args.get("year_to", "").strip()
    if year_from:
        filters["year_from"] = year_from
        filter_clauses.append("established_date >= ?")
        filter_params.append(f"{year_from}-01-01")
    if year_to:
        filters["year_to"] = year_to
        filter_clauses.append("established_date <= ?")
        filter_params.append(f"{year_to}-12-31")

    # 注册资本区间（万元）
    cap_from = request.args.get("cap_from", "").strip()
    cap_to = request.args.get("cap_to", "").strip()
    if cap_from:
        filters["cap_from"] = cap_from
        filter_clauses.append("CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL) >= ?")
        filter_params.append(float(cap_from))
    if cap_to:
        filters["cap_to"] = cap_to
        filter_clauses.append("CAST(REPLACE(REPLACE(registered_capital, '万元', ''), '万', '') AS REAL) <= ?")
        filter_params.append(float(cap_to))

    # 社保人数区间
    insured_from = request.args.get("insured_from", "").strip()
    insured_to = request.args.get("insured_to", "").strip()
    if insured_from:
        filters["insured_from"] = insured_from
        filter_clauses.append("CAST(insured_count AS INTEGER) >= ?")
        filter_params.append(int(insured_from))
    if insured_to:
        filters["insured_to"] = insured_to
        filter_clauses.append("CAST(insured_count AS INTEGER) <= ?")
        filter_params.append(int(insured_to))

    where = ""
    if filter_clauses:
        where = "WHERE " + " AND ".join(filter_clauses)

    # 排序
    sort = request.args.get("sort", "id")
    direction = request.args.get("dir", "desc")
    allowed_sorts = {
        "id": "id", "name": "normalized_name", "province": "province",
        "city": "city", "established_date": "established_date",
        "business_status": "business_status", "created_at": "created_at",
        "phone": "phone", "legal_person": "legal_person", 
        "registered_capital": "registered_capital"
    }
    sort_col = allowed_sorts.get(sort, "id")
    dir_sql = "ASC" if direction == "asc" else "DESC"

    # 总数
    total = g.db.execute(
        f"SELECT COUNT(*) FROM companies {where}", filter_params
    ).fetchone()[0]

    pages = max(1, math.ceil(total / per_page))
    offset = (page - 1) * per_page

    rows = g.db.execute(f"""
        SELECT id, name, phone, credit_code, legal_person, city, district,
               business_status, established_date, registered_capital, industry, enterprise_scale
        FROM companies {where}
        ORDER BY {sort_col} {dir_sql}
        LIMIT ? OFFSET ?
    """, filter_params + [per_page, offset]).fetchall()

    # 筛选器选项（从数据库取已有值）
    filter_options = {}
    for f in ("city", "district", "business_status", "industry"):
        vals = g.db.execute(f"""
            SELECT DISTINCT {f} FROM companies
            WHERE {f} IS NOT NULL AND {f} <> '' AND {f} <> '-'
            ORDER BY {f} LIMIT 50
        """).fetchall()
        filter_options[f] = [v[f] for v in vals]

    # 动态生成年份区间（递增：1,2,3,4,5,6,7...），只包含有数据的区间
    year_bounds = g.db.execute(
        """SELECT MIN(SUBSTR(established_date, 1, 4)) as min_y,
                  MAX(SUBSTR(established_date, 1, 4)) as max_y
           FROM companies
           WHERE established_date IS NOT NULL AND established_date <> ''
           AND established_date NOT IN ('nan','NaN','NAN')
           AND SUBSTR(established_date, 1, 4) GLOB '[0-9][0-9][0-9][0-9]'"""
    ).fetchone()
    max_year = int(year_bounds["max_y"]) if year_bounds and year_bounds["max_y"] else 2026
    min_year = int(year_bounds["min_y"]) if year_bounds and year_bounds["min_y"] else 1950
    year_ranges = []
    y = max_year
    span = 1
    while y >= min_year:
        start = max(min_year, y - span + 1)
        year_ranges.append((str(start), str(y)))
        y = start - 1
        span += 1

    return render_template("browse.html",
                           rows=rows, total=total, page=page, pages=pages,
                           per_page=per_page, sort=sort, direction=direction,
                           filters=filters, filter_options=filter_options,
                           year_ranges=year_ranges)


# --------------------------------------------------------------------------- #
#  关联发现（独立页面）
# --------------------------------------------------------------------------- #

@app.route("/relations")
def relation_discovery():
    """关联发现独立页面"""
    return render_template("relation_discovery.html")


# --------------------------------------------------------------------------- #
#  关联发现 - HTMX 局部加载
# --------------------------------------------------------------------------- #

@app.route("/browse/relation-groups")
def browse_relation_groups():
    """按关联类型分组展示企业（HTMX片段）"""
    dup_type = request.args.get("dup_type", "phone")
    sort = request.args.get("sort", "count_desc")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", 20, type=int)))

    offset = (page - 1) * per_page

    # 根据排序方式生成ORDER BY子句
    if sort == "count_asc":
        order_by = "ORDER BY cnt ASC"
    elif sort == "name_asc":
        order_by = "ORDER BY field_value ASC"
    else:  # count_desc (默认)
        order_by = "ORDER BY cnt DESC"

    if dup_type == "phone":
        # 电话关联分组
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT normalized_phone FROM company_phones
                GROUP BY normalized_phone HAVING COUNT(DISTINCT company_id) > 1
            )
        """).fetchone()[0]

        # 根据排序方式调整查询
        if sort == "name_asc":
            groups = g.db.execute(f"""
                SELECT
                    cp.normalized_phone as field_value,
                    COUNT(DISTINCT cp.company_id) as cnt,
                    GROUP_CONCAT(c.name, '、') as company_names,
                    GROUP_CONCAT(c.id, ',') as company_ids
                FROM company_phones cp
                JOIN companies c ON c.id = cp.company_id
                WHERE cp.normalized_phone IN (
                    SELECT normalized_phone FROM company_phones
                    GROUP BY normalized_phone HAVING COUNT(DISTINCT company_id) > 1
                )
                GROUP BY cp.normalized_phone
                {order_by}, cp.normalized_phone
                LIMIT ? OFFSET ?
            """, [per_page, offset]).fetchall()
        else:
            groups = g.db.execute(f"""
                SELECT
                    cp.normalized_phone as field_value,
                    COUNT(DISTINCT cp.company_id) as cnt,
                    GROUP_CONCAT(c.name, '、') as company_names,
                    GROUP_CONCAT(c.id, ',') as company_ids
                FROM company_phones cp
                JOIN companies c ON c.id = cp.company_id
                WHERE cp.normalized_phone IN (
                    SELECT normalized_phone FROM company_phones
                    GROUP BY normalized_phone HAVING COUNT(DISTINCT company_id) > 1
                )
                GROUP BY cp.normalized_phone
                {order_by}, cp.normalized_phone
                LIMIT ? OFFSET ?
            """, [per_page, offset]).fetchall()

        pages = max(1, math.ceil(total / per_page))

        return render_template("_relation_groups.html",
                             groups=groups, dup_type=dup_type, sort=sort,
                             page=page, pages=pages, total=total, per_page=per_page,
                             field_label="电话", field_icon="📞")

    elif dup_type == "email":
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT email FROM companies
                WHERE email IS NOT NULL AND email <> '' AND email <> '-'
                GROUP BY email HAVING COUNT(*) > 1
            )
        """).fetchone()[0]

        if sort == "name_asc":
            order_clause = f"{order_by}, email"
        else:
            order_clause = f"{order_by}, email"

        groups = g.db.execute(f"""
            SELECT
                email as field_value,
                COUNT(*) as cnt,
                GROUP_CONCAT(name, '、') as company_names,
                GROUP_CONCAT(id, ',') as company_ids
            FROM companies
            WHERE email IN (
                SELECT email FROM companies
                WHERE email IS NOT NULL AND email <> '' AND email <> '-'
                GROUP BY email HAVING COUNT(*) > 1
            )
            GROUP BY email
            {order_clause}
            LIMIT ? OFFSET ?
        """, [per_page, offset]).fetchall()

        pages = max(1, math.ceil(total / per_page))

        return render_template("_relation_groups.html",
                             groups=groups, dup_type=dup_type, sort=sort,
                             page=page, pages=pages, total=total, per_page=per_page,
                             field_label="邮箱", field_icon="📧")

    elif dup_type == "legal_person":
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT legal_person FROM companies
                WHERE legal_person IS NOT NULL AND legal_person <> '' AND legal_person <> '-'
                GROUP BY legal_person HAVING COUNT(*) > 1
            )
        """).fetchone()[0]

        if sort == "name_asc":
            order_clause = f"{order_by}, legal_person"
        else:
            order_clause = f"{order_by}, legal_person"

        groups = g.db.execute(f"""
            SELECT
                legal_person as field_value,
                COUNT(*) as cnt,
                GROUP_CONCAT(name, '、') as company_names,
                GROUP_CONCAT(id, ',') as company_ids
            FROM companies
            WHERE legal_person IN (
                SELECT legal_person FROM companies
                WHERE legal_person IS NOT NULL AND legal_person <> '' AND legal_person <> '-'
                GROUP BY legal_person HAVING COUNT(*) > 1
            )
            GROUP BY legal_person
            {order_clause}
            LIMIT ? OFFSET ?
        """, [per_page, offset]).fetchall()

        pages = max(1, math.ceil(total / per_page))

        return render_template("_relation_groups.html",
                             groups=groups, dup_type=dup_type, sort=sort,
                             page=page, pages=pages, total=total, per_page=per_page,
                             field_label="法人", field_icon="👤")

    elif dup_type == "shareholders":
        # 暂时无数据
        return render_template("_relation_groups.html",
                             groups=[], dup_type=dup_type,
                             page=1, pages=1, total=0,
                             field_label="股东", field_icon="🏢")

    elif dup_type == "tag":
        # 标签关联分组
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT tag_id FROM company_tags
                GROUP BY tag_id HAVING COUNT(DISTINCT company_id) > 0
            )
        """).fetchone()[0]

        if sort == "name_asc":
            order_clause = f"{order_by}, t.name"
        else:
            order_clause = f"{order_by}, t.name"

        groups = g.db.execute(f"""
            SELECT
                t.name as field_value,
                t.color as tag_color,
                COUNT(DISTINCT ct.company_id) as cnt,
                GROUP_CONCAT(c.name, '、') as company_names,
                GROUP_CONCAT(c.id, ',') as company_ids
            FROM tags t
            INNER JOIN company_tags ct ON t.id = ct.tag_id
            INNER JOIN companies c ON c.id = ct.company_id
            GROUP BY t.id
            {order_clause}
            LIMIT ? OFFSET ?
        """, [per_page, offset]).fetchall()

        pages = max(1, math.ceil(total / per_page))

        return render_template("_relation_groups.html",
                             groups=groups, dup_type=dup_type, sort=sort,
                             page=page, pages=pages, total=total, per_page=per_page,
                             field_label="标签", field_icon="🏷️")

    return "", 400


@app.route("/relation-group")
def relation_group_detail():
    """关联分组详情页面"""
    dup_type = request.args.get("dup_type")
    field_value = request.args.get("field_value")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", 25, type=int)))
    sort = request.args.get("sort", "name")
    direction = request.args.get("dir", "asc")
    
    offset = (page - 1) * per_page
    
    # 根据关联类型查询
    if dup_type == "phone":
        total = g.db.execute("""
            SELECT COUNT(DISTINCT company_id) FROM company_phones
            WHERE normalized_phone = ?
        """, (field_value,)).fetchone()[0]
        
        sort_col = "c.name" if sort == "name" else "c.id"
        dir_sql = "ASC" if direction == "asc" else "DESC"
        
        companies = g.db.execute(f"""
            SELECT c.id, c.name, c.legal_person, c.city, c.district, c.business_status
            FROM companies c
            INNER JOIN company_phones cp ON c.id = cp.company_id
            WHERE cp.normalized_phone = ?
            ORDER BY {sort_col} {dir_sql}
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
        field_label = "电话"
        field_icon = "📞"
        
    elif dup_type == "email":
        total = g.db.execute("""
            SELECT COUNT(*) FROM companies WHERE email = ?
        """, (field_value,)).fetchone()[0]
        
        sort_col = "name" if sort == "name" else "id"
        dir_sql = "ASC" if direction == "asc" else "DESC"
        
        companies = g.db.execute(f"""
            SELECT id, name, legal_person, city, district, business_status
            FROM companies
            WHERE email = ?
            ORDER BY {sort_col} {dir_sql}
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
        field_label = "邮箱"
        field_icon = "📧"
        
    elif dup_type == "legal_person":
        total = g.db.execute("""
            SELECT COUNT(*) FROM companies WHERE legal_person = ?
        """, (field_value,)).fetchone()[0]
        
        sort_col = "name" if sort == "name" else "id"
        dir_sql = "ASC" if direction == "asc" else "DESC"
        
        companies = g.db.execute(f"""
            SELECT id, name, legal_person, city, district, business_status
            FROM companies
            WHERE legal_person = ?
            ORDER BY {sort_col} {dir_sql}
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
        field_label = "法人"
        field_icon = "👤"
        
    elif dup_type == "tag":
        total = g.db.execute("""
            SELECT COUNT(DISTINCT ct.company_id)
            FROM company_tags ct
            INNER JOIN tags t ON ct.tag_id = t.id
            WHERE t.name = ?
        """, (field_value,)).fetchone()[0]
        
        sort_col = "c.name" if sort == "name" else "c.id"
        dir_sql = "ASC" if direction == "asc" else "DESC"
        
        companies = g.db.execute(f"""
            SELECT c.id, c.name, c.legal_person, c.city, c.district, c.business_status
            FROM companies c
            INNER JOIN company_tags ct ON c.id = ct.company_id
            INNER JOIN tags t ON ct.tag_id = t.id
            WHERE t.name = ?
            ORDER BY {sort_col} {dir_sql}
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
        field_label = "标签"
        field_icon = "🏷️"
    else:
        abort(404)
    
    pages = max(1, math.ceil(total / per_page))
    
    return render_template("relation_group.html",
                         companies=companies, dup_type=dup_type,
                         field_value=field_value, field_label=field_label,
                         field_icon=field_icon, page=page, pages=pages,
                         total=total, per_page=per_page, sort=sort,
                         direction=direction)


@app.route("/browse/relation-group-detail")
def browse_relation_group_detail():
    """获取单个分组的详细企业列表（HTMX片段）"""
    dup_type = request.args.get("dup_type")
    field_value = request.args.get("field_value")
    page = max(1, request.args.get("page", 1, type=int))
    per_page = min(100, max(10, request.args.get("per_page", 20, type=int)))
    
    offset = (page - 1) * per_page
    
    if dup_type == "phone":
        # 查询该电话号码关联的所有企业
        total = g.db.execute("""
            SELECT COUNT(DISTINCT company_id) FROM company_phones
            WHERE normalized_phone = ?
        """, (field_value,)).fetchone()[0]
        
        companies = g.db.execute("""
            SELECT c.id, c.name
            FROM companies c
            INNER JOIN company_phones cp ON c.id = cp.company_id
            WHERE cp.normalized_phone = ?
            ORDER BY c.name
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
    elif dup_type == "email":
        total = g.db.execute("""
            SELECT COUNT(*) FROM companies WHERE email = ?
        """, (field_value,)).fetchone()[0]
        
        companies = g.db.execute("""
            SELECT id, name FROM companies
            WHERE email = ?
            ORDER BY name
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
    elif dup_type == "legal_person":
        total = g.db.execute("""
            SELECT COUNT(*) FROM companies WHERE legal_person = ?
        """, (field_value,)).fetchone()[0]
        
        companies = g.db.execute("""
            SELECT id, name FROM companies
            WHERE legal_person = ?
            ORDER BY name
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
        
    elif dup_type == "tag":
        total = g.db.execute("""
            SELECT COUNT(DISTINCT ct.company_id)
            FROM company_tags ct
            INNER JOIN tags t ON ct.tag_id = t.id
            WHERE t.name = ?
        """, (field_value,)).fetchone()[0]
        
        companies = g.db.execute("""
            SELECT c.id, c.name
            FROM companies c
            INNER JOIN company_tags ct ON c.id = ct.company_id
            INNER JOIN tags t ON ct.tag_id = t.id
            WHERE t.name = ?
            ORDER BY c.name
            LIMIT ? OFFSET ?
        """, (field_value, per_page, offset)).fetchall()
    else:
        return "", 400
    
    pages = max(1, math.ceil(total / per_page))
    
    return render_template("_relation_group_detail.html",
                         companies=companies, dup_type=dup_type,
                         field_value=field_value, page=page, pages=pages,
                         total=total, per_page=per_page)


@app.route("/browse/relation-top")
def browse_relation_top():
    """关联企业最多的TOP 50（HTMX片段）- 简化版，只计算电话关联"""
    # 只计算电话关联，大大提高查询速度
    companies = g.db.execute("""
        SELECT 
            c.id,
            c.name,
            c.legal_person,
            c.city,
            c.district,
            COUNT(DISTINCT cp.normalized_phone) as phone_cnt,
            0 as email_cnt,
            0 as lp_cnt,
            COUNT(DISTINCT cp.normalized_phone) as relation_score
        FROM companies c
        INNER JOIN company_phones cp ON c.id = cp.company_id
        WHERE cp.normalized_phone IN (
            SELECT normalized_phone FROM company_phones
            GROUP BY normalized_phone HAVING COUNT(DISTINCT company_id) > 1
        )
        GROUP BY c.id
        HAVING phone_cnt > 0
        ORDER BY relation_score DESC
        LIMIT 50
    """).fetchall()

    return render_template("_relation_top.html", companies=companies)


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

    # 记录最近查询（仅首页查询，分页不记）
    if page == 1 and q:
        g.db.execute(
            "INSERT INTO recent_searches (q, query_type, result_count) VALUES (?, ?, ?)",
            [q, query_type, total]
        )
        # 超过 50 条清理一次
        g.db.execute(
            "DELETE FROM recent_searches WHERE id NOT IN (SELECT id FROM recent_searches ORDER BY id DESC LIMIT 50)"
        )
        g.db.commit()

    return render_template("search.html", q=q, query_type=query_type,
                           rows=rows, total=total, page=page, pages=pages)

# --------------------------------------------------------------------------- #
#  最近查询
# --------------------------------------------------------------------------- #

@app.route("/recent/clear", methods=["POST"])
def recent_clear():
    g.db.execute("DELETE FROM recent_searches")
    g.db.commit()
    return redirect(url_for("index"))


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
#  股东关联统计
# --------------------------------------------------------------------------- #

@app.route("/stats/shareholder")
def stats_shareholder():
    """股东关联统计 - 按股东分组，显示关联企业数"""
    page = max(1, request.args.get("page", 1, type=int))
    min_count = max(2, request.args.get("min", 2, type=int))

    total = g.db.execute("""
        SELECT COUNT(*) FROM (
            SELECT normalized_name FROM company_shareholders
            WHERE normalized_name IS NOT NULL AND normalized_name <> ''
            GROUP BY normalized_name
            HAVING COUNT(DISTINCT company_id) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages = max(1, math.ceil(total / PER_PAGE))
    offset = (page - 1) * PER_PAGE

    rows = g.db.execute("""
        SELECT cs.normalized_name,
               MIN(cs.name) AS display_name,
               COUNT(DISTINCT cs.company_id) AS cnt,
               GROUP_CONCAT(DISTINCT c.name, '; ') AS company_names
        FROM company_shareholders cs
        JOIN companies c ON cs.company_id = c.id
        WHERE cs.normalized_name IS NOT NULL AND cs.normalized_name <> ''
        GROUP BY cs.normalized_name
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ? OFFSET ?
    """, [min_count, PER_PAGE, offset]).fetchall()

    return render_template("stats_shareholder.html", rows=rows,
                           total=total, min_count=min_count,
                           page=page, pages=pages)


# --------------------------------------------------------------------------- #
#  通用字段统计（法人 / 股东 / 行业）
# --------------------------------------------------------------------------- #

def _stats_generic(field, title, subtitle, min_default=2, endpoint=None):
    """通用统计视图：按某字段分组，统计企业数量。"""
    ep = endpoint or f"stats_{field}"
    page = max(1, request.args.get("page", 1, type=int))
    min_count = max(min_default, request.args.get("min", min_default, type=int))

    total = g.db.execute(f"""
        SELECT COUNT(*) FROM (
            SELECT {field} FROM companies
            WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
            GROUP BY {field}
            HAVING COUNT(*) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages = max(1, math.ceil(total / PER_PAGE))
    offset = (page - 1) * PER_PAGE

    rows = g.db.execute(f"""
        SELECT {field} AS val,
               COUNT(*) AS cnt,
               GROUP_CONCAT(name) AS company_names
        FROM companies
        WHERE {field} IS NOT NULL AND {field} <> '' AND {field} <> '-'
        GROUP BY {field}
        HAVING cnt >= ?
        ORDER BY cnt DESC
        LIMIT ? OFFSET ?
    """, [min_count, PER_PAGE, offset]).fetchall()

    return render_template("stats_list.html",
                           field=field, endpoint=ep,
                           title=title, subtitle=subtitle,
                           rows=rows, total=total, min_count=min_count,
                           page=page, pages=pages)


@app.route("/stats/legal_person")
def stats_legal_person():
    return _stats_generic("legal_person", "法定代表人统计",
                          "担任多家企业法人的记录，按企业数量排序")


@app.route("/stats/industry")
def stats_industry():
    return _stats_generic("industry", "行业统计",
                          "各行业的企业数量分布")


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

    # Find related records by normalized legal person
    related_legal_person = []
    if row["normalized_legal_person"]:
        related_legal_person = g.db.execute("""
            SELECT id, name, city, business_status
            FROM companies
            WHERE normalized_legal_person = ? AND id != ?
            ORDER BY normalized_legal_person
            LIMIT 10
        """, [row["normalized_legal_person"], company_id]).fetchall()

    # Find related records via company_shareholders (同股东关联企业)
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

    # Find related records by normalized email
    related_email = []
    if row["normalized_email"]:
        related_email = g.db.execute("""
            SELECT id, name, city, business_status
            FROM companies
            WHERE normalized_email = ? AND id != ? AND normalized_email != ''
            ORDER BY normalized_email
            LIMIT 10
        """, [row["normalized_email"], company_id]).fetchall()

    # Find related records by industry
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
        "shareholders": None,  # handled separately via company_shareholders
        "industry": "industry",
        "email": "normalized_email",
    }
    for f, norm_f in norm_field_map.items():
        if norm_f is None:
            # shareholders count via company_shareholders table
            cnt = g.db.execute(
                "SELECT COUNT(DISTINCT company_id) FROM company_shareholders WHERE company_id = ?",
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
                           company_phones=company_phones, field_counts=field_counts)


@app.route("/company/<int:company_id>/edit", methods=["GET", "POST"])
def edit_company(company_id):
    row = g.db.execute("SELECT * FROM companies WHERE id = ?", [company_id]).fetchone()
    if not row:
        abort(404)

    if request.method == "POST":
        fields = {}
        for f in IMPORT_FIELDS:
            val = request.form.get(f, "").strip()
            fields[f] = val if val else ""

        # 更新 normalized 字段
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

        # 更新电话关联表
        sync_phones(g.db, company_id, phone_val, other_phone_val)
        # 更新股东关联表
        sync_shareholders(g.db, company_id, shareholders_val)
        g.db.commit()

        flash(f"已更新：{fields.get('name', '')}", "success")
        return redirect(url_for("company_detail", company_id=company_id))

    return render_template("edit.html", company=dict(row))


@app.route("/company/<int:company_id>/delete", methods=["POST"])
def delete_company(company_id):
    """删除企业记录"""
    # 删除电话关联
    g.db.execute("DELETE FROM company_phones WHERE company_id = ?", (company_id,))
    # 删除企业记录
    g.db.execute("DELETE FROM companies WHERE id = ?", (company_id,))
    g.db.commit()
    
    flash("企业已删除", "success")
    return redirect(url_for("browse"))


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
        fields["normalized_legal_person"] = normalize_person_name(fields.get("legal_person", ""))
        fields["normalized_email"] = normalize_email(fields.get("email", ""))
        if fields.get("credit_code"):
            fields["credit_code"] = normalize_credit_code(fields["credit_code"])

        fields["status"] = request.form.get("status", "active")
        fields["source"] = "manual"

        # 电话/股东字段仅存入关联表，不在 companies 表中
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
        return redirect(url_for("add_company"))

    return render_template("add.html", company={})


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
            if not record.get("source_file"):
                record["source_file"] = source_name

            # File date for updated_at
            record["_file_date"] = file_date

            if not record.get("name"):
                continue

            # 跳过包含无效占位文本的记录
            placeholder_texts = ["暂不予显示", "企业信息暂不"]
            has_placeholder = False
            for field in ["name", "business_scope", "address"]:
                val = record.get(field, "")
                if val and any(pt in val for pt in placeholder_texts):
                    has_placeholder = True
                    break
            if has_placeholder:
                continue

            record["normalized_name"] = normalize_name(record.get("name", ""))
            record["normalized_phone"] = normalize_phone(record.get("phone", ""))
            if record.get("credit_code"):
                record["credit_code"] = normalize_credit_code(record.get("credit_code"))

            # 预览阶段不做去重检查，确认时统一处理
            record["is_duplicate"] = 0
            record["duplicate_reason"] = ""
            record["will_update"] = 0
            all_cleaned.append(record)

    if not all_cleaned:
        for e in errors:
            flash(e, "error")
        return redirect(url_for("import_page"))

    # Insert sample into preview table for display
    for rec in all_cleaned[:50]:
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
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if not os.path.exists(temp_path):
        flash("导入会话已过期，请重新上传", "error")
        return redirect(url_for("import_page"))

    with open(temp_path) as f:
        data = json.load(f)

    if isinstance(data, dict):
        mappings = data.get("mappings", [])
        total = len(data.get("records", []))
    else:
        mappings = []
        total = len(data)

    sample = g.db.execute("""
        SELECT * FROM import_preview
        WHERE batch_id = ? ORDER BY row_num LIMIT 30
    """, [batch_id]).fetchall()

    return render_template("import_preview.html", batch_id=batch_id,
                           total=total, sample=sample, mappings=mappings)


@app.route("/import/confirm/<batch_id>", methods=["POST"])
def import_confirm(batch_id):
    skip_dup = request.form.get("skip_dup", "1") == "1"

    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if not os.path.exists(temp_path):
        flash("导入会话已过期，请重新上传", "error")
        return redirect(url_for("import_page"))

    # 导入前自动备份
    backup_result = backup.create_backup(DB_PATH, reason="导入前自动备份")
    backup_msg = ""
    if backup_result["success"]:
        backup.cleanup_old_backups(keep_count=7)
        backup_msg = f"已自动创建备份：{backup_result['filename']}"
    else:
        backup_msg = f"自动备份失败：{backup_result.get('error', '未知错误')}"

    # 创建任务跟踪
    task_queue = queue.Queue()
    stop_event = threading.Event()
    _import_tasks[batch_id] = {"queue": task_queue, "stop_event": stop_event, "status": "running"}

    # 启动后台线程
    t = threading.Thread(
        target=_import_worker,
        args=(batch_id, temp_path, skip_dup, task_queue, stop_event),
        daemon=True,
    )
    t.start()

    return render_template("import_progress.html",
                           batch_id=batch_id, backup_msg=backup_msg)


def _import_worker(batch_id, temp_path, skip_dup, task_queue, stop_event):
    """后台导入线程：逐条处理，发送进度事件。"""
    def send(event, data=None):
        task_queue.put({"event": event, "data": data or {}})

    try:
        with open(temp_path) as f:
            data = json.load(f)
            cleaned = data["records"] if isinstance(data, dict) else data

        total = len(cleaned)
        send("start", {"total": total})

        # 用独立连接操作数据库
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")

        # 预加载已有记录
        existing_by_code = {}
        existing_by_name = {}
        for row in db.execute("SELECT id, credit_code, normalized_name, updated_at FROM companies"):
            if row["credit_code"]:
                existing_by_code[row["credit_code"]] = (row["id"], row["updated_at"])
            if row["normalized_name"]:
                existing_by_name[row["normalized_name"]] = (row["id"], row["updated_at"])

        db.execute("BEGIN TRANSACTION")

        inserted = 0
        updated = 0
        phones_merged = 0
        skipped = 0
        report_every = max(1, total // 100)  # 每 1% 报告一次

        for i, rec in enumerate(cleaned):
            if stop_event.is_set():
                db.rollback()
                send("stopped", {
                    "processed": i, "total": total,
                    "inserted": inserted, "updated": updated,
                    "phones_merged": phones_merged, "skipped": skipped,
                })
                db.close()
                return

            file_date = rec.get("_file_date")

            # 去重检查
            existing_id = None
            existing_updated = None

            if rec.get("credit_code") and rec["credit_code"] in existing_by_code:
                existing_id, existing_updated = existing_by_code[rec["credit_code"]]
            elif rec.get("normalized_name") and rec["normalized_name"] in existing_by_name:
                existing_id, existing_updated = existing_by_name[rec["normalized_name"]]

            if existing_id:
                if skip_dup:
                    if file_date and existing_updated:
                        if file_date > existing_updated:
                            pass  # UPDATE
                        else:
                            merge_phones(db, existing_id,
                                rec.get("phone", ""), rec.get("other_phone", ""),
                                rec.get("_recommended_phone", ""))
                            merge_shareholders(db, existing_id,
                                rec.get("shareholders", ""))
                            phones_merged += 1
                            if (i + 1) % report_every == 0:
                                send("progress", {
                                    "processed": i + 1, "total": total,
                                    "inserted": inserted, "updated": updated,
                                    "phones_merged": phones_merged, "skipped": skipped,
                                })
                            continue
                    elif file_date:
                        pass  # UPDATE
                    else:
                        merge_phones(db, existing_id,
                            rec.get("phone", ""), rec.get("other_phone", ""),
                            rec.get("_recommended_phone", ""))
                        merge_shareholders(db, existing_id,
                            rec.get("shareholders", ""))
                        phones_merged += 1
                        if (i + 1) % report_every == 0:
                            send("progress", {
                                "processed": i + 1, "total": total,
                                "inserted": inserted, "updated": updated,
                                "phones_merged": phones_merged, "skipped": skipped,
                            })
                        continue

                # UPDATE
                fields = {}
                for f_name in IMPORT_FIELDS:
                    val = rec.get(f_name, "")
                    if val:
                        fields[f_name] = val
                if fields:
                    fields["normalized_name"] = rec.get("normalized_name", "")
                    # normalized 字段：法人、邮箱
                    lp = fields.get("legal_person", "")
                    fields["normalized_legal_person"] = normalize_person_name(lp) if lp else ""
                    em = fields.get("email", "")
                    fields["normalized_email"] = normalize_email(em) if em else ""
                    # 电话/股东/其他邮箱 不写入 companies 主表
                    phone_val = fields.pop("phone", "")
                    other_phone_val = fields.pop("other_phone", "")
                    shareholders_val = fields.pop("shareholders", "")
                    other_email_val = fields.pop("other_email", "")
                    if file_date:
                        fields["updated_at"] = file_date
                    set_clause = ", ".join([f"{k} = ?" for k in fields.keys()])
                    db.execute(
                        f"UPDATE companies SET {set_clause} WHERE id = ?",
                        list(fields.values()) + [existing_id]
                    )
                    merge_phones(db, existing_id,
                        phone_val, other_phone_val,
                        rec.get("_recommended_phone", ""))
                    merge_shareholders(db, existing_id, shareholders_val)
                    # 合并其他邮箱（分号去重）
                    if other_email_val:
                        existing_oe = db.execute(
                            "SELECT other_email FROM companies WHERE id = ?",
                            [existing_id]).fetchone()["other_email"] or ""
                        merged_oe = set(p.strip() for p in existing_oe.split(";") if p.strip())
                        merged_oe.update(p.strip() for p in other_email_val.replace(",", ";").split(";")
                                         if p.strip() and p.strip() != "-")
                        db.execute("UPDATE companies SET other_email = ? WHERE id = ?",
                                   ["; ".join(sorted(merged_oe)), existing_id])
                    updated += 1
            else:
                # INSERT
                fields = {}
                for f_name in IMPORT_FIELDS:
                    val = rec.get(f_name, "")
                    if val:
                        fields[f_name] = val
                fields["normalized_name"] = rec.get("normalized_name", "")
                # normalized 字段：法人、邮箱
                lp = fields.get("legal_person", "")
                fields["normalized_legal_person"] = normalize_person_name(lp) if lp else ""
                em = fields.get("email", "")
                fields["normalized_email"] = normalize_email(em) if em else ""
                # 电话/股东字段仅存入关联表，不在 companies 表中
                phone_val = fields.pop("phone", "")
                other_phone_val = fields.pop("other_phone", "")
                shareholders_val = fields.pop("shareholders", "")
                fields["status"] = "active"
                fields["source"] = "import"
                if file_date:
                    fields["updated_at"] = file_date
                cols = ", ".join(fields.keys())
                placeholders = ", ".join(["?"] * len(fields))
                cursor = db.execute(
                    f"INSERT INTO companies ({cols}) VALUES ({placeholders})",
                    list(fields.values())
                )
                sync_phones(db, cursor.lastrowid,
                    phone_val, other_phone_val,
                    rec.get("_recommended_phone", ""))
                sync_shareholders(db, cursor.lastrowid, shareholders_val)
                # 更新索引
                if rec.get("credit_code"):
                    existing_by_code[rec["credit_code"]] = (cursor.lastrowid, file_date)
                if rec.get("normalized_name"):
                    existing_by_name[rec["normalized_name"]] = (cursor.lastrowid, file_date)
                inserted += 1

            # 进度报告
            if (i + 1) % report_every == 0:
                send("progress", {
                    "processed": i + 1, "total": total,
                    "inserted": inserted, "updated": updated,
                    "phones_merged": phones_merged, "skipped": skipped,
                })

        db.execute("DELETE FROM import_preview WHERE batch_id = ?", [batch_id])
        db.commit()
        db.close()

        # 清理临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)

        send("done", {
            "total": total,
            "inserted": inserted, "updated": updated,
            "phones_merged": phones_merged, "skipped": skipped,
        })

    except Exception as e:
        send("error", {"message": str(e)})


@app.route("/import/confirm/<batch_id>/stream")
def import_stream(batch_id):
    """SSE 端点：向前端推送导入进度。"""
    task = _import_tasks.get(batch_id)
    if not task:
        return Response("event: error\ndata: {\"message\": \"任务不存在\"}\n\n",
                        content_type="text/event-stream")

    def generate():
        try:
            while True:
                try:
                    msg = task["queue"].get(timeout=30)
                    event = msg.get("event", "message")
                    data = json.dumps(msg.get("data", {}), ensure_ascii=False)
                    yield f"event: {event}\ndata: {data}\n\n"
                    if event in ("done", "error", "stopped"):
                        break
                except queue.Empty:
                    yield f"event: heartbeat\ndata: {{}}\n\n"
        finally:
            _import_tasks.pop(batch_id, None)

    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.route("/import/confirm/<batch_id>/stop", methods=["POST"])
def import_stop(batch_id):
    """停止导入任务。"""
    task = _import_tasks.get(batch_id)
    if task:
        task["stop_event"].set()
        return jsonify({"ok": True})
    return jsonify({"ok": False, "error": "任务不存在"}), 404


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
        return jsonify({"code": 0, "message": "ok", "data": {"query": "", "type": "text", "count": 0, "results": []}})

    query_type = _detect_query_type(q)

    if query_type == "phone":
        norm_q = normalize_phone(q)
        rows = g.db.execute(
            """SELECT c.id, c.name, c.phone, c.address, c.credit_code,
                      c.legal_person, c.city, '电话' AS matched_field
               FROM company_phones cp
               JOIN companies c ON cp.company_id = c.id
               WHERE cp.normalized_phone = ?
               ORDER BY c.name LIMIT ?""",
            [norm_q, limit]
        ).fetchall()
        return jsonify({"code": 0, "message": "ok", "data": {
            "query": q, "type": "phone",
            "count": len(rows), "results": [dict(r) for r in rows]
        }})
    elif query_type == "credit_code":
        norm_q = normalize_credit_code(q)
        rows = g.db.execute(
            """SELECT id, name, phone, address, credit_code,
                      legal_person, city, '信用代码' AS matched_field
               FROM companies WHERE credit_code = ?
               LIMIT ?""",
            [norm_q, limit]
        ).fetchall()
        return jsonify({"code": 0, "message": "ok", "data": {
            "query": q, "type": "credit_code",
            "count": len(rows), "results": [dict(r) for r in rows]
        }})
    else:
        norm_q_name = normalize_name(q)
        like_q = "%" + q + "%"
        like_name = "%" + norm_q_name + "%"
        rows = g.db.execute(
            """SELECT id, name, phone, address, credit_code,
                      legal_person, city, matched_field
               FROM (
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city,
                          '名称' AS matched_field, 1 AS priority
                   FROM companies WHERE normalized_name LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city, '曾用名', 2
                   FROM companies WHERE former_name LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city, '地址', 3
                   FROM companies WHERE address LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city, '法人', 4
                   FROM companies WHERE legal_person LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city, '股东', 5
                   FROM companies WHERE shareholders LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city, '邮箱', 6
                   FROM companies WHERE email LIKE ?
                   UNION ALL
                   SELECT id, name, phone, address, credit_code,
                          legal_person, city, '网站', 7
                   FROM companies WHERE website LIKE ?
               )
               GROUP BY id ORDER BY priority, name LIMIT ?""",
            [like_name, like_q, like_q, like_q, like_q, like_q, like_q, limit]
        ).fetchall()
        return jsonify({"code": 0, "message": "ok", "data": {
            "query": q, "type": "text",
            "count": len(rows), "results": [dict(r) for r in rows]
        }})


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
    return jsonify({"code": 0, "message": "ok",
                    "data": {"results": [dict(r) for r in rows]}})


# --------------------------------------------------------------------------- #
#  数据管理（备份 + 清理）
# --------------------------------------------------------------------------- #


# ── 数据备份 ──

@app.route("/backup")
def backup_page():
    backups = backup.list_backups()
    backup_dir = backup.get_backup_dir()
    
    # 数据库信息
    db_path = DB_PATH
    db_size = db_path.stat().st_size if db_path.exists() else 0
    db_size_mb = round(db_size / 1024 / 1024, 2)
    
    # 碎片率统计
    db_stats = backup.get_db_stats(db_path)
    fragmentation = db_stats["fragmentation"]
    reclaimable_mb = round(db_stats["reclaimable_bytes"] / 1024 / 1024, 2)
    
    # 数据统计
    total_records = g.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    total_phones = g.db.execute("SELECT COUNT(*) FROM company_phones").fetchone()[0]
    
    # 上次备份信息
    last_backup = None
    if backups:
        last_backup = backups[0]  # 假设按时间倒序排列
    
    return render_template("backup.html", 
                         backups=backups, 
                         backup_dir=str(backup_dir),
                         db_size_mb=db_size_mb,
                         total_records=total_records,
                         total_phones=total_phones,
                         last_backup=last_backup,
                         fragmentation=fragmentation,
                         reclaimable_mb=reclaimable_mb)


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


@app.route("/backup/vacuum", methods=["POST"])
def backup_vacuum():
    """压缩数据库，回收空闲页空间。"""
    result = backup.vacuum_database(DB_PATH)
    if result["success"]:
        before_mb = round(result["before_size"] / 1024 / 1024, 2)
        after_mb = round(result["after_size"] / 1024 / 1024, 2)
        freed_mb = round(result["freed"] / 1024 / 1024, 2)
        flash(
            f"压缩成功：{before_mb} MB → {after_mb} MB，释放 {freed_mb} MB"
            f"（已自动创建备份 {result['backup_filename']}）",
            "success"
        )
    else:
        flash(f"压缩失败：{result.get('error', '未知错误')}", "error")
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


# --------------------------------------------------------------------------- #
#  标签管理
# --------------------------------------------------------------------------- #

@app.route("/tags")
def tags_page():
    """标签管理页面"""
    tags = g.db.execute("""
        SELECT t.*, COUNT(ct.company_id) as company_count
        FROM tags t
        LEFT JOIN company_tags ct ON t.id = ct.tag_id
        GROUP BY t.id
        ORDER BY company_count DESC, t.name
    """).fetchall()
    return render_template("tags.html", tags=tags)


@app.route("/api/tags", methods=["POST"])
def create_tag():
    """创建标签"""
    name = request.json.get("name", "").strip()
    color = request.json.get("color", "#3b82f6")
    
    if not name:
        return jsonify({"code": 1001, "message": "标签名称不能为空", "data": None}), 400
    
    try:
        g.db.execute("INSERT INTO tags (name, color) VALUES (?, ?)", (name, color))
        g.db.commit()
        return jsonify({"code": 0, "message": "标签创建成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标签名称已存在", "data": None}), 400


@app.route("/api/tags", methods=["GET"])
def get_all_tags():
    """获取所有标签"""
    tags = g.db.execute("SELECT * FROM tags ORDER BY name").fetchall()
    return jsonify({"code": 0, "message": "ok",
                    "data": {"results": [dict(tag) for tag in tags]}})


@app.route("/api/tags/<int:tag_id>", methods=["PUT"])
def update_tag(tag_id):
    """更新标签"""
    name = request.json.get("name", "").strip()
    color = request.json.get("color", "#3b82f6")
    
    if not name:
        return jsonify({"code": 1001, "message": "标签名称不能为空", "data": None}), 400
    
    try:
        g.db.execute("UPDATE tags SET name=?, color=? WHERE id=?", (name, color, tag_id))
        g.db.commit()
        return jsonify({"code": 0, "message": "标签更新成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标签名称已存在", "data": None}), 400


@app.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def delete_tag(tag_id):
    """删除标签"""
    g.db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    g.db.commit()
    return jsonify({"code": 0, "message": "标签删除成功", "data": None})


@app.route("/api/companies/<int:company_id>/tags", methods=["GET"])
def get_company_tags(company_id):
    """获取企业标签"""
    tags = g.db.execute("""
        SELECT t.id, t.name, t.color
        FROM tags t
        JOIN company_tags ct ON t.id = ct.tag_id
        WHERE ct.company_id = ?
        ORDER BY t.name
    """, (company_id,)).fetchall()
    return jsonify({"code": 0, "message": "ok",
                    "data": {"results": [dict(tag) for tag in tags]}})


@app.route("/api/companies/<int:company_id>/tags", methods=["POST"])
def add_company_tag(company_id):
    """为企业添加标签"""
    tag_id = request.json.get("tag_id")
    if not tag_id:
        return jsonify({"code": 1001, "message": "标签ID不能为空", "data": None}), 400
    
    try:
        g.db.execute("INSERT INTO company_tags (company_id, tag_id) VALUES (?, ?)", 
                    (company_id, tag_id))
        g.db.commit()
        return jsonify({"code": 0, "message": "标签添加成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标签已存在", "data": None}), 400


@app.route("/api/companies/<int:company_id>/tags/<int:tag_id>", methods=["DELETE"])
def remove_company_tag(company_id, tag_id):
    """删除企业标签"""
    g.db.execute("DELETE FROM company_tags WHERE company_id=? AND tag_id=?", 
                (company_id, tag_id))
    g.db.commit()
    return jsonify({"code": 0, "message": "标签删除成功", "data": None})


@app.route("/api/companies/batch-delete", methods=["POST"])
def batch_delete_companies():
    """批量删除企业"""
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"code": 1001, "message": "未选择企业", "data": None}), 400
    
    # 限制一次最多删除1000家，防止性能问题
    if len(ids) > 1000:
        return jsonify({"code": 1001, "message": "一次最多删除1000家企业", "data": None}), 400
    
    try:
        # 删除企业标签关联
        g.db.execute(f"DELETE FROM company_tags WHERE company_id IN ({','.join('?' * len(ids))})", ids)
        # 删除企业电话关联
        g.db.execute(f"DELETE FROM company_phones WHERE company_id IN ({','.join('?' * len(ids))})", ids)
        # 删除企业
        g.db.execute(f"DELETE FROM companies WHERE id IN ({','.join('?' * len(ids))})", ids)
        g.db.commit()
        
        return jsonify({
            "code": 0,
            "message": "批量删除成功",
            "data": {"deleted": len(ids)}
        })
    except Exception as e:
        return jsonify({"code": 2001, "message": str(e), "data": None}), 500


@app.route("/api/companies/batch-add-tag", methods=["POST"])
def batch_add_tag():
    """批量添加标签"""
    ids = request.json.get("ids", [])
    tag_id = request.json.get("tag_id")
    
    if not ids:
        return jsonify({"code": 1001, "message": "未选择企业", "data": None}), 400
    if not tag_id:
        return jsonify({"code": 1001, "message": "未选择标签", "data": None}), 400
    
    # 限制一次最多操作1000家
    if len(ids) > 1000:
        return jsonify({"code": 1001, "message": "一次最多操作1000家企业", "data": None}), 400
    
    try:
        added = 0
        for company_id in ids:
            try:
                g.db.execute("INSERT INTO company_tags (company_id, tag_id) VALUES (?, ?)", 
                           (company_id, tag_id))
                added += 1
            except sqlite3.IntegrityError:
                # 标签已存在，跳过
                pass
        
        g.db.commit()
        
        return jsonify({
            "code": 0,
            "message": "批量添加标签成功",
            "data": {"updated": added}
        })
    except Exception as e:
        return jsonify({"code": 2001, "message": str(e), "data": None}), 500


if __name__ == "__main__":
    init_db()
    app.run(host="127.0.0.1", port=5210, debug=True)
