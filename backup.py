"""数据备份模块"""
import sqlite3
import shutil
from pathlib import Path
from datetime import datetime
import json

# 默认备份目录
DEFAULT_BACKUP_DIR = Path.home() / ".enthub" / "backups"


def get_backup_dir():
    """获取备份目录，优先从配置文件读取"""
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
            backup_dir = config.get("backup_dir")
            if backup_dir:
                return Path(backup_dir)
    return DEFAULT_BACKUP_DIR


def create_backup(db_path: Path, reason: str = "手动备份") -> dict:
    """
    创建数据库备份
    
    Args:
        db_path: 数据库文件路径
        reason: 备份原因（手动/导入前/定时）
    
    Returns:
        dict: 备份信息 {success, filename, filepath, size, timestamp, reason}
    """
    backup_dir = get_backup_dir()
    backup_dir.mkdir(parents=True, exist_ok=True)
    
    # 生成备份文件名
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    backup_filename = f"enthub_{timestamp}.db"
    backup_path = backup_dir / backup_filename
    
    try:
        # 使用 VACUUM INTO 保证备份一致性
        conn = sqlite3.connect(str(db_path))
        conn.execute(f"VACUUM INTO '{backup_path}'")
        conn.close()
        
        # 获取文件大小
        size = backup_path.stat().st_size
        
        return {
            "success": True,
            "filename": backup_filename,
            "filepath": str(backup_path),
            "size": size,
            "timestamp": timestamp,
            "reason": reason,
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "filename": None,
        }


def list_backups() -> list:
    """列出所有备份文件"""
    backup_dir = get_backup_dir()
    if not backup_dir.exists():
        return []
    
    backups = []
    for f in backup_dir.glob("enthub_*.db"):
        stat = f.stat()
        # 从文件名解析时间
        try:
            timestamp = f.stem.replace("enthub_", "")
            dt = datetime.strptime(timestamp, "%Y-%m-%d_%H-%M-%S")
            time_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        except:
            time_str = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        
        backups.append({
            "filename": f.name,
            "filepath": str(f),
            "size": stat.st_size,
            "time": time_str,
        })
    
    # 按时间倒序
    backups.sort(key=lambda x: x["time"], reverse=True)
    return backups


def delete_backup(filename: str) -> dict:
    """删除指定备份"""
    backup_dir = get_backup_dir()
    backup_path = backup_dir / filename
    
    # 安全检查：确保文件在备份目录内
    if not backup_path.resolve().is_relative_to(backup_dir.resolve()):
        return {"success": False, "error": "非法路径"}
    
    if not backup_path.exists():
        return {"success": False, "error": "文件不存在"}
    
    try:
        backup_path.unlink()
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}


def cleanup_old_backups(keep_count: int = 7):
    """清理旧备份，保留指定数量"""
    backups = list_backups()
    if len(backups) <= keep_count:
        return {"deleted": 0, "kept": len(backups)}
    
    deleted = 0
    for backup in backups[keep_count:]:
        result = delete_backup(backup["filename"])
        if result["success"]:
            deleted += 1
    
    return {"deleted": deleted, "kept": keep_count}


def get_db_stats(db_path: Path) -> dict:
    """获取数据库统计信息：大小、总页数、空闲页数、碎片率、可回收空间。"""
    if not db_path.exists():
        return {
            "size": 0,
            "page_count": 0,
            "freelist_count": 0,
            "page_size": 0,
            "fragmentation": 0.0,
            "reclaimable_bytes": 0,
        }

    size = db_path.stat().st_size
    conn = sqlite3.connect(str(db_path))
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist_count = conn.execute("PRAGMA freelist_count").fetchone()[0]
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    conn.close()

    fragmentation = round(freelist_count * 100.0 / page_count, 2) if page_count else 0.0
    reclaimable_bytes = freelist_count * page_size

    return {
        "size": size,
        "page_count": page_count,
        "freelist_count": freelist_count,
        "page_size": page_size,
        "fragmentation": fragmentation,
        "reclaimable_bytes": reclaimable_bytes,
    }


def vacuum_database(db_path: Path) -> dict:
    """压缩数据库，回收空闲页空间。

    步骤：
      1. 自动创建一份备份（保险，失败可恢复）
      2. VACUUM INTO 到临时文件（不锁原库，不影响读）
      3. 校验临时文件记录数与原库一致
      4. os.replace 原子替换原文件 + 清理旧的 -wal/-shm
    """
    import os

    if not db_path.exists():
        return {"success": False, "error": "数据库文件不存在"}

    before_size = db_path.stat().st_size

    # 步骤 1: 创建备份
    backup_result = create_backup(db_path, reason="压缩前自动备份")
    if not backup_result["success"]:
        return {
            "success": False,
            "error": f"压缩前备份失败：{backup_result.get('error')}",
        }

    # 步骤 2: VACUUM INTO 到临时文件
    temp_path = db_path.parent / f"{db_path.stem}_vacuuming.db"
    if temp_path.exists():
        temp_path.unlink()

    try:
        # 用独立连接执行 VACUUM INTO（不影响 Flask 的 g.db）
        src_conn = sqlite3.connect(str(db_path), timeout=30.0)
        src_conn.execute(f"VACUUM INTO '{temp_path}'")
        src_conn.close()

        # 步骤 3: 校验临时文件记录数与原库一致
        orig_conn = sqlite3.connect(str(db_path))
        orig_count = orig_conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        orig_conn.close()

        verify_conn = sqlite3.connect(str(temp_path))
        new_count = verify_conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        integrity = verify_conn.execute("PRAGMA integrity_check").fetchone()[0]
        verify_conn.close()

        if orig_count != new_count:
            temp_path.unlink()
            return {
                "success": False,
                "error": f"校验失败：记录数不一致（原库 {orig_count} / 新库 {new_count}）",
            }
        if integrity != "ok":
            temp_path.unlink()
            return {
                "success": False,
                "error": f"完整性检查未通过：{integrity}",
            }

        # 步骤 4: 原子替换原文件 + 清理 WAL/SHM
        wal_path = db_path.parent / f"{db_path.name}-wal"
        shm_path = db_path.parent / f"{db_path.name}-shm"

        os.replace(str(temp_path), str(db_path))

        for p in [wal_path, shm_path]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

        after_size = db_path.stat().st_size
        freed = before_size - after_size

        # 走 7 份保留策略，避免备份无限堆积
        cleanup_old_backups(keep_count=7)

        return {
            "success": True,
            "before_size": before_size,
            "after_size": after_size,
            "freed": freed,
            "backup_filename": backup_result["filename"],
        }
    except Exception as e:
        # 清理临时文件
        if temp_path.exists():
            try:
                temp_path.unlink()
            except Exception:
                pass
        return {"success": False, "error": str(e)}


def check_daily_backup(db_path: Path) -> dict:
    """检查是否需要每日备份（距离上次超过24小时）"""
    backups = list_backups()
    if not backups:
        # 没有备份，创建一个
        return create_backup(db_path, reason="首次备份")
    
    # 检查最新备份时间
    latest_time = backups[0]["time"]
    latest_dt = datetime.strptime(latest_time, "%Y-%m-%d %H:%M:%S")
    hours_since = (datetime.now() - latest_dt).total_seconds() / 3600
    
    if hours_since >= 24:
        result = create_backup(db_path, reason="定时备份")
        if result["success"]:
            cleanup_old_backups(keep_count=7)
        return result
    
    return {"success": False, "skipped": True, "reason": "今日已备份"}
