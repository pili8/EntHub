"""数据备份：页面、创建、恢复、下载、删除。"""
import subprocess
import sqlite3
import shutil
import tempfile
from pathlib import Path
from flask import Blueprint, g, render_template, redirect, url_for, flash, \
                   abort, send_file

from db import DB_PATH, init_db
import backup

bp = Blueprint('backup_flow_bp', __name__)


@bp.route("/backup")
def backup_page():
    backups = backup.list_backups()
    backup_dir = backup.get_backup_dir()

    # 数据库信息
    db_path = DB_PATH
    db_size = db_path.stat().st_size if db_path.exists() else 0
    db_size_mb = round(db_size / 1024 / 1024, 2)

    # 数据统计
    total_records = g.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    total_phones = g.db.execute("SELECT COUNT(*) FROM company_phones").fetchone()[0]
    total_emails = g.db.execute("SELECT COUNT(*) FROM company_emails").fetchone()[0]

    # 上次备份信息
    last_backup = backups[0] if backups else None

    return render_template("backup.html",
                           backups=backups,
                           backup_dir=str(backup_dir),
                           db_size_mb=db_size_mb,
                           total_records=total_records,
                           total_phones=total_phones,
                           total_emails=total_emails,
                           last_backup=last_backup)


@bp.route("/backup/create", methods=["POST"])
def backup_create():
    result = backup.create_backup(DB_PATH, reason="手动备份")
    if result["success"]:
        backup.cleanup_old_backups(keep_count=7)
        flash(f"备份成功：{result['filename']}", "success")
    else:
        flash(f"备份失败：{result.get('error', '未知错误')}", "error")
    return redirect(url_for("backup_flow_bp.backup_page"))


@bp.route("/backup/restore/<filename>", methods=["POST"])
def backup_restore(filename):
    """从备份文件恢复数据库。"""
    result = backup.restore_backup(filename, DB_PATH)
    if result["success"]:
        # 恢复后重新初始化数据库（确保表结构、FTS 索引等完整）
        init_db()
        flash(f"✅ 恢复成功！{result['message']}", "success")
        if result.get("backup_filename"):
            flash(f"💡 当前数据已自动备份为 {result['backup_filename']}，如需回退可恢复此备份", "info")
    else:
        flash(f"❌ 恢复失败：{result.get('error', '未知错误')}", "error")
    return redirect(url_for("backup_flow_bp.backup_page"))


@bp.route("/backup/restore-select", methods=["POST"])
def backup_restore_select():
    """从下拉菜单选择备份文件并恢复。"""
    filename = request.form.get("filename", "").strip()
    if not filename:
        flash("请选择要恢复的备份文件", "error")
        return redirect(url_for("backup_flow_bp.backup_page"))
    return redirect(url_for("backup_flow_bp.backup_restore", filename=filename))


@bp.route("/backup/restore-upload", methods=["POST"])
def backup_restore_upload():
    """上传备份文件并恢复。"""
    import tempfile
    file = request.files.get("file")
    if not file or not file.filename:
        flash("请选择要恢复的备份文件", "error")
        return redirect(url_for("backup_flow_bp.backup_page"))

    # 保存到临时目录
    tmp_dir = Path(tempfile.mkdtemp(prefix="enthub_restore_"))
    tmp_path = tmp_dir / file.filename
    file.save(str(tmp_path))

    # 校验是否为合法 SQLite 数据库
    try:
        test_conn = sqlite3.connect(str(tmp_path))
        test_conn.execute("SELECT COUNT(*) FROM companies").fetchone()
        test_conn.close()
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        flash("所选文件不是有效的数据库备份文件", "error")
        return redirect(url_for("backup_flow_bp.backup_page"))

    # 执行恢复
    from db import DB_PATH as target_db
    result = backup.restore_backup_from_file(tmp_path, target_db)
    shutil.rmtree(tmp_dir, ignore_errors=True)

    if result["success"]:
        init_db()
        flash(f"✅ 恢复成功！{result['message']}", "success")
        if result.get("backup_filename"):
            flash(f"💡 当前数据已自动备份为 {result['backup_filename']}，如需回退可恢复此备份", "info")
    else:
        flash(f"❌ 恢复失败：{result.get('error', '未知错误')}", "error")
    return redirect(url_for("backup_flow_bp.backup_page"))


@bp.route("/backup/download/<filename>")
def backup_download(filename):
    backup_dir = backup.get_backup_dir()
    filepath = backup_dir / filename
    # 安全检查：确保文件在备份目录内
    if not filepath.resolve().is_relative_to(backup_dir.resolve()):
        abort(403)
    if not filepath.exists():
        flash("文件不存在", "error")
        return redirect(url_for("backup_flow_bp.backup_page"))
    return send_file(str(filepath), as_attachment=True, download_name=filename)


@bp.route("/backup/delete/<filename>", methods=["POST"])
def backup_delete(filename):
    result = backup.delete_backup(filename)
    if result["success"]:
        flash(f"已删除备份：{filename}", "success")
    else:
        flash(f"删除失败：{result.get('error', '未知错误')}", "error")
    return redirect(url_for("backup_flow_bp.backup_page"))


@bp.route("/backup/open-dir")
def backup_open_dir():
    """在 Finder 中打开备份目录。"""
    backup_dir = backup.get_backup_dir()
    try:
        subprocess.Popen(["open", str(backup_dir)])
        flash(f"已在 Finder 中打开：{backup_dir}", "success")
    except Exception as e:
        flash(f"打开失败：{e}", "error")
    return redirect(url_for("backup_flow_bp.backup_page"))


@bp.route("/db/open-dir")
def db_open_dir():
    """在 Finder 中打开主数据库所在目录（项目 data/）。"""
    db_dir = DB_PATH.parent
    if not db_dir.exists():
        flash(f"目录不存在：{db_dir}", "error")
        return redirect(url_for("backup_flow_bp.backup_page"))
    try:
        subprocess.Popen(["open", str(db_dir)])
        flash(f"已在 Finder 中打开：{db_dir}", "success")
    except Exception as e:
        flash(f"打开失败：{e}", "error")
    return redirect(url_for("backup_flow_bp.backup_page"))
