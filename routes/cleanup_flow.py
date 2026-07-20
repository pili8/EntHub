"""数据清理流程：统计、异步清理、SSE 进度、停止。"""
import json
import sqlite3
import queue as queue_module

from flask import Blueprint, g, request, render_template, redirect, url_for, \
                   jsonify, Response

from db import DB_PATH
import tasks
from data_helpers import merge_phones, merge_shareholders

bp = Blueprint('cleanup_flow_bp', __name__)


# 与 import 流程一致的全量字段列表
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


# ── 清理页面 ────────────────────────────────────────────────────────────────

@bp.route("/cleanup")
def cleanup_page():
    """统计待清理数据量。"""
    stats_data = {}
    stats_data["nan_count"] = g.db.execute(
        "SELECT COUNT(*) FROM companies "
        "WHERE name IN ('nan','NaN','NAN') OR normalized_name IN ('nan','NaN','NAN')"
    ).fetchone()[0]

    stats_data["header_count"] = g.db.execute(
        "SELECT COUNT(*) FROM companies WHERE name IN ('公司名称','企业名称')"
    ).fetchone()[0]

    stats_data["cc_total"] = g.db.execute(
        "SELECT COUNT(*) FROM companies "
        "WHERE credit_code IS NOT NULL AND credit_code <> '' "
        "AND credit_code NOT IN ('nan','NaN','NAN')"
    ).fetchone()[0]
    stats_data["cc_unique"] = g.db.execute(
        "SELECT COUNT(DISTINCT credit_code) FROM companies "
        "WHERE credit_code IS NOT NULL AND credit_code <> '' "
        "AND credit_code NOT IN ('nan','NaN','NAN')"
    ).fetchone()[0]
    stats_data["cc_dup"] = stats_data["cc_total"] - stats_data["cc_unique"]

    stats_data["name_total"] = g.db.execute(
        "SELECT COUNT(*) FROM companies "
        "WHERE normalized_name IS NOT NULL AND normalized_name <> '' "
        "AND normalized_name <> 'nan'"
    ).fetchone()[0]
    stats_data["name_unique"] = g.db.execute(
        "SELECT COUNT(DISTINCT normalized_name) FROM companies "
        "WHERE normalized_name IS NOT NULL AND normalized_name <> '' "
        "AND normalized_name <> 'nan'"
    ).fetchone()[0]
    stats_data["name_dup"] = stats_data["name_total"] - stats_data["name_unique"]

    stats_data["total"] = g.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]

    stats_data["placeholder_count"] = g.db.execute("""
        SELECT COUNT(*) FROM companies WHERE
        business_scope LIKE '%暂不予显示%' OR business_scope LIKE '%企业信息暂不%'
        OR name LIKE '%暂不予显示%' OR address LIKE '%暂不予显示%'
    """).fetchone()[0]

    # 数据质量指标
    stats_data["with_phone"] = g.db.execute(
        """SELECT COUNT(DISTINCT company_id) FROM company_phones
           WHERE phone IS NOT NULL AND phone <> '' AND phone <> '-'"""
    ).fetchone()[0]
    stats_data["with_legal_person"] = g.db.execute(
        "SELECT COUNT(*) FROM companies "
        "WHERE legal_person IS NOT NULL AND legal_person <> '' AND legal_person <> '-'"
    ).fetchone()[0]
    stats_data["with_address"] = g.db.execute(
        "SELECT COUNT(*) FROM companies "
        "WHERE address IS NOT NULL AND address <> '' AND address <> '-'"
    ).fetchone()[0]

    stats_data["total_to_clean"] = (
        stats_data["nan_count"] + stats_data["header_count"] +
        stats_data["placeholder_count"] + stats_data["cc_dup"] +
        stats_data["name_dup"]
    )

    return render_template("cleanup.html", stats=stats_data)


# ── 启动异步清理 ────────────────────────────────────────────────────────────

@bp.route("/cleanup/execute", methods=["POST"])
def cleanup_execute():
    """启动异步清理。"""
    clean_nan = request.form.get("clean_nan", "0") == "1"
    clean_header = request.form.get("clean_header", "0") == "1"
    clean_placeholder = request.form.get("clean_placeholder", "0") == "1"
    clean_cc_dup = request.form.get("clean_cc_dup", "0") == "1"
    clean_name_dup = request.form.get("clean_name_dup", "0") == "1"

    task_queue, stop_event = tasks.create("cleanup")
    import threading
    t = threading.Thread(
        target=_cleanup_worker,
        args=(task_queue, stop_event, clean_nan, clean_header,
              clean_placeholder, clean_cc_dup, clean_name_dup),
        daemon=True,
    )
    t.start()

    return render_template("cleanup_progress.html")


# ── 后台清理线程 ────────────────────────────────────────────────────────────

def _cleanup_worker(task_queue, stop_event, clean_nan, clean_header,
                    clean_placeholder, clean_cc_dup, clean_name_dup):
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
            n = db.execute(
                "DELETE FROM companies "
                "WHERE name IN ('nan','NaN','NAN') "
                "OR normalized_name IN ('nan','NaN','NAN')"
            ).rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 2. 删除表头行
        if clean_header and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "清理表头行"})
            n = db.execute(
                "DELETE FROM companies WHERE name IN ('公司名称','企业名称')"
            ).rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 2.5 清理"暂不予显示"等占位文本
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

        # 4. 企业名称去重
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
            n = db.execute(
                "DELETE FROM company_phones "
                "WHERE company_id NOT IN (SELECT id FROM companies)"
            ).rowcount
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        db.commit()

        total_after = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        db.close()

        if stop_event.is_set():
            send("stopped", {"total_deleted": deleted_total, "total_after": total_after})
        else:
            send("done", {
                "total_before": total_before,
                "total_after": total_after,
                "total_deleted": deleted_total,
            })

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
        WHERE {field} IS NOT NULL AND {field} <> ''
          AND {field} NOT IN ('nan','NaN','NAN')
        GROUP BY {field}
        HAVING COUNT(*) > 1
    """).fetchall()

    deleted = 0

    for group in groups:
        ids = [int(x) for x in group["ids"].split(",")]
        if len(ids) <= 1:
            continue

        placeholders = ",".join(["?"] * len(ids))
        rows = db.execute(f"""
            SELECT c.id, c.name, c.credit_code, c.legal_person,
                   c.address, c.province, c.city, c.industry, c.business_status, c.email,
                   c.updated_at, c.registered_capital, c.paid_capital,
                   c.established_date, c.approved_date, c.business_term,
                   c.district, c.insured_count, c.company_type, c.former_name,
                   c.website, c.other_email, c.business_scope, c.enterprise_scale,
                   c.shareholders, c.mailing_address, c.english_name, c.tags,
                   c.annual_report_address, c.taxpayer_id, c.registration_no, c.org_code,
                   (SELECT COUNT(*) FROM company_phones WHERE company_id = c.id) AS phone_count,
                   (CASE WHEN c.name <> '' THEN 1 ELSE 0 END +
                    CASE WHEN (SELECT COUNT(*) FROM company_phones WHERE company_id = c.id) > 0 THEN 1 ELSE 0 END +
                    CASE WHEN c.credit_code <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.legal_person <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.address <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.province <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.city <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.industry <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.business_status <> '' THEN 1 ELSE 0 END +
                    CASE WHEN c.email <> '' THEN 1 ELSE 0 END) AS completeness
            FROM companies c WHERE c.id IN ({placeholders})
        """, ids).fetchall()

        # 排序：完整度降序 > updated_at 降序 > id 升序
        rows = list(rows)
        rows.sort(key=lambda r: (-r["completeness"], r["updated_at"] or "", r["id"]))

        keep = rows[0]
        keep_id = keep["id"]

        # 从被删记录中合并数据到保留记录
        updates = {}
        for r in rows[1:]:
            # 电话累加：从被删企业的 company_phones 表读取所有电话，合并到保留企业
            dup_phone_rows = db.execute(
                "SELECT phone FROM company_phones WHERE company_id = ?", [r["id"]]
            ).fetchall()
            if dup_phone_rows:
                dup_phones_str = ";".join([p["phone"] for p in dup_phone_rows])
                merge_phones(db, keep_id, dup_phones_str, "", "")

            # 股东累加
            dup_sh_rows = db.execute(
                "SELECT name FROM company_shareholders WHERE company_id = ?", [r["id"]]
            ).fetchall()
            if dup_sh_rows:
                dup_shs_str = ";".join([s["name"] for s in dup_sh_rows])
                merge_shareholders(db, keep_id, dup_shs_str)

            # 其他字段：保留记录为空时，从被删记录补全
            for f in IMPORT_FIELDS:
                if f in ("name", "phone", "other_phone", "shareholders",
                         "source_file", "tags"):
                    continue  # 这些字段特殊处理或不合并
                kept_val = updates.get(f) or keep[f]
                dup_val = r[f] if f in r.keys() else ""
                if (not kept_val or kept_val in ("", "-", "nan")) and \
                   dup_val and dup_val not in ("", "-", "nan"):
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

        # 删除被合并的记录（含关联表数据）
        delete_ids = [r["id"] for r in rows[1:]]
        del_placeholders = ",".join(["?"] * len(delete_ids))
        db.execute(f"DELETE FROM company_phones WHERE company_id IN ({del_placeholders})",
                   delete_ids)
        db.execute(f"DELETE FROM company_shareholders WHERE company_id IN ({del_placeholders})",
                   delete_ids)
        db.execute(f"DELETE FROM companies WHERE id IN ({del_placeholders})", delete_ids)
        deleted += len(delete_ids)

    return deleted


# ── SSE 进度推送 ────────────────────────────────────────────────────────────

@bp.route("/cleanup/stream")
def cleanup_stream():
    task = tasks.get("cleanup")
    if not task:
        return Response('event: error\ndata: {"message": "任务不存在"}\n\n',
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
                except queue_module.Empty:
                    yield 'event: heartbeat\ndata: {}\n\n'
        finally:
            tasks.pop("cleanup", None)

    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 停止清理 ────────────────────────────────────────────────────────────────

@bp.route("/cleanup/stop", methods=["POST"])
def cleanup_stop():
    if tasks.request_stop("cleanup"):
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404
