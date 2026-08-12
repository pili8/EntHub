"""引导配置：告诉应用数据在哪里。

引导文件位置：~/Library/Application Support/EntHub/bootstrap.json
内容只有两个键：
  - db_path:   数据库文件路径
  - backup_dir: 备份目录路径

其余所有业务配置（API 密钥、webhook URL 等）存在数据库的 settings 表中。
首次升级时自动从旧架构（config.json + data/enthub.db）迁移。
"""
import json
import shutil
from pathlib import Path

# macOS 标准应用支持目录
APP_SUPPORT_DIR = Path.home() / "Library" / "Application Support" / "EntHub"
BOOTSTRAP_PATH = APP_SUPPORT_DIR / "bootstrap.json"

# 默认路径
DEFAULT_DB_PATH = APP_SUPPORT_DIR / "enthub.db"
DEFAULT_BACKUP_DIR = Path.home() / ".enthub" / "backups"


def get_bootstrap() -> dict:
    """读取引导配置。如果文件不存在，返回默认值。"""
    if BOOTSTRAP_PATH.exists():
        try:
            with open(BOOTSTRAP_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return {
                    "db_path": data.get("db_path", str(DEFAULT_DB_PATH)),
                    "backup_dir": data.get("backup_dir", str(DEFAULT_BACKUP_DIR)),
                }
        except (json.JSONDecodeError, IOError):
            pass
    return {
        "db_path": str(DEFAULT_DB_PATH),
        "backup_dir": str(DEFAULT_BACKUP_DIR),
    }


def save_bootstrap(db_path: str = None, backup_dir: str = None) -> dict:
    """保存引导配置。只更新传入的字段，其余保留原值。"""
    current = get_bootstrap()
    if db_path is not None:
        current["db_path"] = db_path
    if backup_dir is not None:
        current["backup_dir"] = backup_dir

    APP_SUPPORT_DIR.mkdir(parents=True, exist_ok=True)
    with open(BOOTSTRAP_PATH, "w", encoding="utf-8") as f:
        json.dump(current, f, ensure_ascii=False, indent=2)
    return current


def get_db_path() -> Path:
    """获取数据库文件路径。"""
    return Path(get_bootstrap()["db_path"])


def get_backup_dir() -> Path:
    """获取备份目录路径。"""
    return Path(get_bootstrap()["backup_dir"])


def ensure_bootstrap():
    """确保 bootstrap.json 存在。首次升级时从旧架构迁移文件位置。

    迁移逻辑：
    1. 如果 bootstrap.json 已存在，说明已初始化，直接返回
    2. 检查旧 config.json，提取 backup_dir
    3. 检查旧 data/enthub.db，复制到新默认位置
    4. 创建 bootstrap.json
    """
    if BOOTSTRAP_PATH.exists():
        return

    project_dir = Path(__file__).parent
    old_config = project_dir / "config.json"
    old_db = project_dir / "data" / "enthub.db"

    backup_dir = str(DEFAULT_BACKUP_DIR)

    # 从旧 config.json 提取 backup_dir
    if old_config.exists():
        try:
            with open(old_config, "r", encoding="utf-8") as f:
                old_data = json.load(f)
                if old_data.get("backup_dir"):
                    backup_dir = old_data["backup_dir"]
        except (json.JSONDecodeError, IOError):
            pass

    # 如果旧 DB 存在且新默认位置没有 DB，复制过去
    if old_db.exists() and not DEFAULT_DB_PATH.exists():
        DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(old_db), str(DEFAULT_DB_PATH))
        # 同时复制 WAL/SHM 文件（如果存在）
        for suffix in ("-wal", "-shm"):
            old_sidecar = old_db.parent / f"{old_db.name}{suffix}"
            if old_sidecar.exists():
                shutil.copy2(
                    str(old_sidecar),
                    str(DEFAULT_DB_PATH.parent / f"{DEFAULT_DB_PATH.name}{suffix}"),
                )

    save_bootstrap(db_path=str(DEFAULT_DB_PATH), backup_dir=backup_dir)
