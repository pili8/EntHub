"""数据备份：页面、创建、下载、删除、压缩。"""
from flask import Blueprint, g, render_template, redirect, url_for, flash, \
                   abort, send_file

from db import DB_PATH
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

    # 碎片率统计
    db_stats = backup.get_db_stats(db_path)
    fragmentation = db_stats["fragmentation"]
    reclaimable_mb = round(db_stats["reclaimable_bytes"] / 1024 / 1024, 2)

    # 数据统计
    total_records = g.db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    total_phones = g.db.execute("SELECT COUNT(*) FROM company_phones").fetchone()[0]

    # 上次备份信息
    last_backup = backups[0] if backups else None

    return render_template("backup.html",
                           backups=backups,
                           backup_dir=str(backup_dir),
                           db_size_mb=db_size_mb,
                           total_records=total_records,
                           total_phones=total_phones,
                           last_backup=last_backup,
                           fragmentation=fragmentation,
                           reclaimable_mb=reclaimable_mb)


@bp.route("/backup/create", methods=["POST"])
def backup_create():
    result = backup.create_backup(DB_PATH, reason="手动备份")
    if result["success"]:
        backup.cleanup_old_backups(keep_count=7)
        flash(f"备份成功：{result['filename']}", "success")
    else:
        flash(f"备份失败：{result.get('error', '未知错误')}", "error")
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


@bp.route("/backup/vacuum", methods=["POST"])
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
    return redirect(url_for("backup_flow_bp.backup_page"))
