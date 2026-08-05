"""数据清理流程：统计、异步清理、SSE 进度、停止。"""
import json
import sqlite3
import queue as queue_module

from flask import Blueprint, g, request, render_template, redirect, url_for, \
                   flash, jsonify, Response

from db import DB_PATH
import backup
import tasks
from queries import invalidate_cache
from data_helpers import merge_phones, merge_emails, merge_shareholders

bp = Blueprint('cleanup_flow_bp', __name__)


# 与 import 流程一致的全量字段列表
IMPORT_FIELDS = [
    "name", "phone", "address", "annual_report_address",
    "credit_code", "taxpayer_id", "registration_no", "org_code",
    "legal_person", "registered_capital", "paid_capital",
    "established_date", "approved_date", "business_term",
    "province", "city", "district", "insured_count",
    "company_type", "industry", "former_name", "website",
    "email", "business_scope", "business_status",
    "enterprise_scale", "shareholders", "mailing_address",
    "english_name", "source_file",
]


# ── 清理页面 ────────────────────────────────────────────────────────────────

@bp.route("/cleanup")
def cleanup_page():
    """渲染清理页骨架（不查数据库），统计数据通过 SSE 异步推送。

    避免用户点击后白屏 10+ 秒（去重 SQL 耗时较长）。
    """
    return render_template("cleanup.html", stats={})


# ── 统计数据 SSE 流式推送 ──────────────────────────────────────────────────────
#
# 分批推送策略（按查询耗时由快到慢）：
#   批次1（毫秒级）：总数 / nan / 表头 / 占位 / 法人 / 地址
#   批次2（秒级）  ：信用代码重复 / 名称重复 / 电话去重
#   批次3（约 11s）：待清理总数（SQL 去重，最慢）
#
# 每查完一个立即推送，前端即可显示，避免长等待。


@bp.route("/cleanup/stats_stream")
def cleanup_stats_stream():
    """流式推送清理页统计数据。"""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    def generate():
        def emit(key, value):
            payload = json.dumps({"key": key, "value": value}, ensure_ascii=False)
            return f"event: stat\ndata: {payload}\n\n"

        try:
            # ── 批次 1：毫秒级查询 ──
            total = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
            yield emit("total", total)

            # 数据库碎片率统计（用于压缩功能）
            db_stats = backup.get_db_stats(DB_PATH)
            yield emit("fragmentation", db_stats["fragmentation"])
            yield emit("reclaimable_mb", round(db_stats["reclaimable_bytes"] / 1024 / 1024, 2))
            db_size_mb = round(DB_PATH.stat().st_size / 1024 / 1024, 2) if DB_PATH.exists() else 0
            yield emit("db_size_mb", db_size_mb)

            nan_count = db.execute(
                "SELECT COUNT(*) FROM companies "
                "WHERE name IN ('nan','NaN','NAN') "
                "OR normalized_name IN ('nan','NaN','NAN')"
            ).fetchone()[0]
            yield emit("nan_count", nan_count)

            header_count = db.execute(
                "SELECT COUNT(*) FROM companies WHERE name IN ('公司名称','企业名称')"
            ).fetchone()[0]
            yield emit("header_count", header_count)

            placeholder_count = db.execute("""
                SELECT COUNT(*) FROM companies WHERE
                business_scope LIKE '%暂不予显示%' OR business_scope LIKE '%企业信息暂不%'
                OR name LIKE '%暂不予显示%' OR address LIKE '%暂不予显示%'
            """).fetchone()[0]
            yield emit("placeholder_count", placeholder_count)

            with_legal_person = db.execute(
                "SELECT COUNT(*) FROM companies "
                "WHERE legal_person IS NOT NULL AND legal_person <> '' "
                "AND legal_person <> '-'"
            ).fetchone()[0]
            yield emit("with_legal_person", with_legal_person)

            with_address = db.execute(
                "SELECT COUNT(*) FROM companies "
                "WHERE address IS NOT NULL AND address <> '' AND address <> '-'"
            ).fetchone()[0]
            yield emit("with_address", with_address)

            # ── 批次 2：秒级查询（DISTINCT 扫描） ──
            cc_total = db.execute(
                "SELECT COUNT(*) FROM companies "
                "WHERE credit_code IS NOT NULL AND credit_code <> '' "
                "AND credit_code NOT IN ('nan','NaN','NAN')"
            ).fetchone()[0]
            cc_unique = db.execute(
                "SELECT COUNT(DISTINCT credit_code) FROM companies "
                "WHERE credit_code IS NOT NULL AND credit_code <> '' "
                "AND credit_code NOT IN ('nan','NaN','NAN')"
            ).fetchone()[0]
            yield emit("cc_total", cc_total)
            yield emit("cc_unique", cc_unique)
            yield emit("cc_dup", cc_total - cc_unique)

            name_total = db.execute(
                "SELECT COUNT(*) FROM companies "
                "WHERE normalized_name IS NOT NULL AND normalized_name <> '' "
                "AND normalized_name <> 'nan'"
            ).fetchone()[0]
            name_unique = db.execute(
                "SELECT COUNT(DISTINCT normalized_name) FROM companies "
                "WHERE normalized_name IS NOT NULL AND normalized_name <> '' "
                "AND normalized_name <> 'nan'"
            ).fetchone()[0]
            yield emit("name_total", name_total)
            yield emit("name_unique", name_unique)
            yield emit("name_dup", name_total - name_unique)

            with_phone = db.execute(
                """SELECT COUNT(DISTINCT company_id) FROM company_phones
                   WHERE phone IS NOT NULL AND phone <> '' AND phone <> '-'"""
            ).fetchone()[0]
            yield emit("with_phone", with_phone)

            # ── 批次 3：慢查询（约 11s）──
            # 准确的待清理记录数（SQL 去重）：
            # 同一条记录可能同时命中多个规则（如既在信用代码重复里，也在企业名称重复里），
            # 不能简单相加，否则会出现"待清理数 > 总记录数"的悖论。
            #
            # 规则定义（任一命中即视为待清理）：
            #   1. nan/NaN/NAN 脏数据
            #   2. 表头行（公司名称/企业名称）
            #   3. "暂不予显示"等占位文本
            #   4. 信用代码重复（每个重复组只保留 MIN(id) 的那条）
            #   5. 企业名称重复（每个重复组只保留 MIN(id) 的那条）
            total_to_clean = db.execute(
                """
                SELECT COUNT(*) FROM companies
                WHERE
                    name IN ('nan','NaN','NAN')
                    OR normalized_name IN ('nan','NaN','NAN')
                    OR name IN ('公司名称','企业名称')
                    OR business_scope LIKE '%暂不予显示%'
                    OR business_scope LIKE '%企业信息暂不%'
                    OR name LIKE '%暂不予显示%'
                    OR address LIKE '%暂不予显示%'
                    OR (
                        credit_code IS NOT NULL AND credit_code <> ''
                        AND credit_code NOT IN ('nan','NaN','NAN')
                        AND id NOT IN (SELECT MIN(id) FROM companies
                                       WHERE credit_code IS NOT NULL AND credit_code <> ''
                                       AND credit_code NOT IN ('nan','NaN','NAN')
                                       GROUP BY credit_code)
                    )
                    OR (
                        normalized_name IS NOT NULL AND normalized_name <> ''
                        AND normalized_name <> 'nan'
                        AND id NOT IN (SELECT MIN(id) FROM companies
                                       WHERE normalized_name IS NOT NULL AND normalized_name <> ''
                                       AND normalized_name <> 'nan'
                                       GROUP BY normalized_name)
                    )
                """
            ).fetchone()[0]
            yield emit("total_to_clean", total_to_clean)

            yield "event: done\ndata: {}\n\n"
        except Exception as e:
            err = json.dumps({"message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"
        finally:
            db.close()

    return Response(
        generate(),
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
            n = _dedup_by_field(db, "credit_code", task_queue, stop_event, send)
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 4. 企业名称去重
        if clean_name_dup and not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "企业名称去重"})
            n = _dedup_by_field(db, "normalized_name", task_queue, stop_event, send)
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        # 5. 清理孤立电话和邮箱
        if not stop_event.is_set():
            step += 1
            send("step", {"step": step, "label": "清理孤立关联记录"})
            n1 = db.execute(
                "DELETE FROM company_phones "
                "WHERE company_id NOT IN (SELECT id FROM companies)"
            ).rowcount
            n2 = db.execute(
                "DELETE FROM company_emails "
                "WHERE company_id NOT IN (SELECT id FROM companies)"
            ).rowcount
            n3 = db.execute(
                "DELETE FROM company_shareholders "
                "WHERE company_id NOT IN (SELECT id FROM companies)"
            ).rowcount
            n = n1 + n2 + n3
            deleted_total += n
            send("progress", {"step": step, "deleted": n, "total_deleted": deleted_total})

        db.commit()

        total_after = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        db.close()

        # 数据已变更，清空筛选器缓存
        invalidate_cache()

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


def _dedup_by_field(db, field, task_queue=None, stop_event=None, send=None):
    """按字段去重，保留每组中「最好」的记录。

    逻辑与导入一致：
    - 保留字段最完整的记录（完整度 > updated_at > id）
    - 被删记录的电话累加到保留记录（merge_phones 自动去重）
    - 被删记录的非空字段补全保留记录的空字段
    - source_file 字段会累加合并（保留所有来源文件的溯源信息）

    支持实时进度推送和中途停止（需要传入 task_queue/stop_event/send）。
    """
    groups = db.execute(f"""
        SELECT {field}, GROUP_CONCAT(id) as ids
        FROM companies
        WHERE {field} IS NOT NULL AND {field} <> ''
          AND {field} NOT IN ('nan','NaN','NAN')
        GROUP BY {field}
        HAVING COUNT(*) > 1
    """).fetchall()

    total_groups = len(groups)
    # 进度推送频率：大任务按 1% 推送，小任务每 10 组推送
    report_every = max(1, total_groups // 100) if total_groups > 100 else 10

    deleted = 0
    processed = 0
    last_commit = 0  # 每 1000 组提交一次，避免长事务

    for group in groups:
        # 中途停止检查
        if stop_event and stop_event.is_set():
            if send:
                send("sub_progress", {
                    "processed": processed, "total": total_groups,
                    "deleted": deleted, "stopped": True,
                })
            db.commit()  # 提交已完成的清理
            return deleted

        processed += 1

        # 实时进度推送
        if send and (processed % report_every == 0 or processed == total_groups):
            send("sub_progress", {
                "processed": processed,
                "total": total_groups,
                "deleted": deleted,
                "percent": round(processed / total_groups * 100, 1) if total_groups else 0,
            })

        ids = [int(x) for x in group["ids"].split(",")]
        if len(ids) <= 1:
            continue

        placeholders = ",".join(["?"] * len(ids))
        rows = db.execute(f"""
            SELECT c.id, c.name, c.credit_code, c.legal_person,
                   c.address, c.province, c.city, c.industry, c.business_status,
                   c.updated_at, c.registered_capital, c.paid_capital,
                   c.established_date, c.approved_date, c.business_term,
                   c.district, c.insured_count, c.company_type, c.former_name,
                   c.website, c.business_scope, c.enterprise_scale,
                   c.shareholders, c.mailing_address, c.english_name,
                   (SELECT GROUP_CONCAT(t.name, '; ')
                      FROM company_tags ct JOIN tags t ON ct.tag_id = t.id
                      WHERE ct.company_id = c.id) AS tags,
                   c.annual_report_address, c.taxpayer_id, c.registration_no, c.org_code,
                   c.source_file,
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
                    CASE WHEN (SELECT COUNT(*) FROM company_emails WHERE company_id = c.id) > 0 THEN 1 ELSE 0 END) AS completeness
            FROM companies c WHERE c.id IN ({placeholders})
        """, ids).fetchall()

        # 排序：完整度降序 > updated_at 降序 > id 升序
        rows = list(rows)
        rows.sort(key=lambda r: (-r["completeness"], r["updated_at"] or "", r["id"]))

        keep = rows[0]
        keep_id = keep["id"]

        # 从被删记录中合并数据到保留记录
        # 累计 source_file（保留记录 + 被删记录），分号拼接 + 去重
        merged_sources = []
        if keep["source_file"]:
            merged_sources.append(keep["source_file"])

        updates = {}
        for r in rows[1:]:
            # 电话累加：从被删企业的 company_phones 表读取所有电话，合并到保留企业
            dup_phone_rows = db.execute(
                "SELECT phone FROM company_phones WHERE company_id = ?", [r["id"]]
            ).fetchall()
            if dup_phone_rows:
                phones_list = [p["phone"] for p in dup_phone_rows if p["phone"]]
                if phones_list:
                    merge_phones(db, keep_id, ";".join(phones_list), "")

            # 邮箱累加
            dup_email_rows = db.execute(
                "SELECT email FROM company_emails WHERE company_id = ?", [r["id"]]
            ).fetchall()
            if dup_email_rows:
                emails_str = ";".join([e["email"] for e in dup_email_rows if e["email"]])
                merge_emails(db, keep_id, emails_str)

            # 股东累加
            dup_sh_rows = db.execute(
                "SELECT name FROM company_shareholders WHERE company_id = ?", [r["id"]]
            ).fetchall()
            if dup_sh_rows:
                dup_shs_str = ";".join([s["name"] for s in dup_sh_rows])
                merge_shareholders(db, keep_id, dup_shs_str)

            # source_file 累加去重（企业可能来自多个 Excel 文件，保留完整溯源）
            dup_source = r["source_file"] if r["source_file"] else ""
            if dup_source:
                for part in dup_source.split(";"):
                    part = part.strip()
                    if part and part not in merged_sources:
                        merged_sources.append(part)

            # 其他字段：保留记录为空时，从被删记录补全
            for f in IMPORT_FIELDS:
                if f in ("name", "phone", "email", "shareholders",
                         "source_file"):
                    continue  # 这些字段特殊处理或不合并
                kept_val = updates.get(f) or keep[f]
                dup_val = r[f] if f in r.keys() else ""
                if (not kept_val or kept_val in ("", "-", "nan")) and \
                   dup_val and dup_val not in ("", "-", "nan"):
                    updates[f] = dup_val

        # 合并后的 source_file
        if merged_sources:
            updates["source_file"] = ";".join(merged_sources)

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
        db.execute(f"DELETE FROM company_emails WHERE company_id IN ({del_placeholders})",
                   delete_ids)
        db.execute(f"DELETE FROM company_shareholders WHERE company_id IN ({del_placeholders})",
                   delete_ids)
        db.execute(f"DELETE FROM companies WHERE id IN ({del_placeholders})", delete_ids)
        deleted += len(delete_ids)

        # 定期提交，避免长事务占用资源
        last_commit += 1
        if last_commit >= 1000:
            db.commit()
            last_commit = 0

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


# ── 数据库压缩 ──────────────────────────────────────────────────────────────

@bp.route("/cleanup/vacuum", methods=["POST"])
def cleanup_vacuum():
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
    return redirect(url_for("cleanup_flow_bp.cleanup_page"))
