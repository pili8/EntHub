"""Excel 导入流程：上传、预览、异步导入、SSE 进度、停止/取消。"""
import os
import json
import uuid
import sqlite3
import tempfile
import threading
import queue as queue_module

import pandas as pd
from flask import Blueprint, g, request, render_template, redirect, url_for, \
                   flash, jsonify, Response

from db import DB_PATH
import backup
import tasks
from data_helpers import sync_phones, merge_phones, sync_shareholders, merge_shareholders
from utils import (
    map_columns, clean_val, is_industrial_park_file,
    extract_date_from_filename,
    normalize_name, normalize_phone, normalize_credit_code,
    normalize_person_name, normalize_email,
)

bp = Blueprint('import_flow_bp', __name__)


# 全量字段列表（与 companies 模块共用，但 import 流程独立维护一份避免循环依赖）
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


# ── 入口 ────────────────────────────────────────────────────────────────────

@bp.route("/import", methods=["GET"])
def import_page():
    return render_template("import.html")


# ── 表头识别 ────────────────────────────────────────────────────────────────

def detect_header_row(df_raw):
    """检测真正的表头所在行（天眼查导出通常前两行是垃圾数据）。"""
    header_keys = {"公司名称", "企业名称", "统一社会信用代码", "法定代表人",
                   "登记状态", "经营状态", "联系电话", "注册资本"}
    for i in range(min(5, len(df_raw))):
        row_vals = [str(v).strip() for v in df_raw.iloc[i].values]
        if any(v in header_keys for v in row_vals):
            return i
    return None


# ── 上传 ────────────────────────────────────────────────────────────────────

@bp.route("/import/upload", methods=["POST"])
def import_upload():
    files = request.files.getlist("file")
    valid_files = [f for f in files if f.filename]

    if not valid_files:
        flash("未选择文件", "error")
        return redirect(url_for("import_flow_bp.import_page"))

    batch_id = uuid.uuid4().hex[:12]
    all_cleaned = []
    errors = []
    file_mappings = []

    for file in valid_files:
        try:
            df_raw = pd.read_excel(file, header=None)
        except Exception as e:
            errors.append(f"{file.filename}: 读取失败 ({e})")
            continue

        if df_raw.empty:
            errors.append(f"{file.filename}: 文件为空")
            continue

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

        if is_industrial_park_file(df.columns):
            errors.append(f"{file.filename}: 检测到非工商数据（工业园区/销售跟踪表），已跳过")
            continue

        col_map, sec_phones, rec_phones, unmatched = map_columns(df.columns)
        if "name" not in col_map.values():
            errors.append(f"{file.filename}: 未找到企业名称列")
            continue

        if unmatched:
            errors.append(
                f"{file.filename}: 以下列无法匹配，已忽略: "
                f"{', '.join(str(c) for c in unmatched[:5])}"
                f"{'...' if len(unmatched) > 5 else ''}"
            )

        file_mappings.append({
            "file": file.filename,
            "total_cols": len(df.columns),
            "matched": {str(k): v for k, v in col_map.items()},
            "secondary": [str(c) for c in sec_phones],
            "recommended": [str(c) for c in rec_phones],
            "unmatched": [str(c) for c in unmatched],
        })

        # 文件名中提取日期，用作 updated_at
        from datetime import datetime
        file_date = extract_date_from_filename(file.filename)
        if not file_date:
            file_date = datetime.now().strftime('%Y-%m-%d')

        source_name = file.filename

        row_offset = len(all_cleaned)
        for idx, row in df.iterrows():
            record = {"row_num": row_offset + idx + 2, "batch_id": batch_id}
            for orig_col, field in col_map.items():
                record[field] = clean_val(row[orig_col])

            # 副电话列（联系电话2~10）合并到 other_phone
            sec_parts = []
            for sc in sec_phones:
                v = clean_val(row[sc])
                if v:
                    sec_parts.append(v)
            if sec_parts:
                existing_other = record.get("other_phone", "")
                merged = ";".join(p for p in [existing_other] + sec_parts if p)
                record["other_phone"] = merged

            # 推荐电话单独存储
            rec_parts = []
            for rc in rec_phones:
                v = clean_val(row[rc])
                if v:
                    rec_parts.append(v)
            record["_recommended_phone"] = ";".join(rec_parts)

            if not record.get("source_file"):
                record["source_file"] = source_name

            record["_file_date"] = file_date

            if not record.get("name"):
                continue

            # 跳过包含无效占位文本的记录
            placeholder_texts = ["暂不予显示", "企业信息暂不"]
            has_placeholder = False
            for fld in ["name", "business_scope", "address"]:
                val = record.get(fld, "")
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
        return redirect(url_for("import_flow_bp.import_page"))

    # 预览表只存前 50 行样本
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

    # 完整数据 + 字段映射存临时文件
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    with open(temp_path, "w") as f:
        json.dump({"records": all_cleaned, "mappings": file_mappings}, f, ensure_ascii=False)

    if errors:
        for e in errors:
            flash(e, "warning")

    flash(f"已分析 {len(valid_files)} 个文件，共 {len(all_cleaned)} 条记录", "success")
    return redirect(url_for("import_flow_bp.import_preview", batch_id=batch_id))


# ── 预览 ────────────────────────────────────────────────────────────────────

@bp.route("/import/preview/<batch_id>")
def import_preview(batch_id):
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if not os.path.exists(temp_path):
        flash("导入会话已过期，请重新上传", "error")
        return redirect(url_for("import_flow_bp.import_page"))

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


# ── 确认导入 ────────────────────────────────────────────────────────────────

@bp.route("/import/confirm/<batch_id>", methods=["POST"])
def import_confirm(batch_id):
    skip_dup = request.form.get("skip_dup", "1") == "1"

    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if not os.path.exists(temp_path):
        flash("导入会话已过期，请重新上传", "error")
        return redirect(url_for("import_flow_bp.import_page"))

    # 导入前自动备份
    backup_result = backup.create_backup(DB_PATH, reason="导入前自动备份")
    if backup_result["success"]:
        backup.cleanup_old_backups(keep_count=7)
        backup_msg = f"已自动创建备份：{backup_result['filename']}"
    else:
        backup_msg = f"自动备份失败：{backup_result.get('error', '未知错误')}"

    # 启动后台线程
    task_queue, stop_event = tasks.create(batch_id)
    t = threading.Thread(
        target=_import_worker,
        args=(batch_id, temp_path, skip_dup, task_queue, stop_event),
        daemon=True,
    )
    t.start()

    return render_template("import_progress.html",
                           batch_id=batch_id, backup_msg=backup_msg)


# ── 后台导入线程 ────────────────────────────────────────────────────────────

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

        # 独立连接操作数据库
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")

        # 预加载已有记录
        existing_by_code = {}
        existing_by_name = {}
        for row in db.execute(
            "SELECT id, credit_code, normalized_name, updated_at FROM companies"
        ):
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
                    lp = fields.get("legal_person", "")
                    fields["normalized_legal_person"] = normalize_person_name(lp) if lp else ""
                    em = fields.get("email", "")
                    fields["normalized_email"] = normalize_email(em) if em else ""
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
                lp = fields.get("legal_person", "")
                fields["normalized_legal_person"] = normalize_person_name(lp) if lp else ""
                em = fields.get("email", "")
                fields["normalized_email"] = normalize_email(em) if em else ""
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
                # 更新内存索引
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


# ── SSE 进度推送 ────────────────────────────────────────────────────────────

@bp.route("/import/confirm/<batch_id>/stream")
def import_stream(batch_id):
    """SSE 端点：向前端推送导入进度。"""
    task = tasks.get(batch_id)
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
            tasks.pop(batch_id, None)

    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 停止 / 取消 ─────────────────────────────────────────────────────────────

@bp.route("/import/confirm/<batch_id>/stop", methods=["POST"])
def import_stop(batch_id):
    if tasks.request_stop(batch_id):
        return jsonify({"ok": True})
    return jsonify({"ok": False}), 404


@bp.route("/import/cancel/<batch_id>", methods=["POST"])
def import_cancel(batch_id):
    temp_path = os.path.join(tempfile.gettempdir(), f"enthub_import_{batch_id}.json")
    if os.path.exists(temp_path):
        os.remove(temp_path)
    flash("已取消导入", "info")
    return redirect(url_for("import_flow_bp.import_page"))
