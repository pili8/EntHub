"""EntHub - 企业工商信息管理工具

应用入口：创建 Flask 实例、注册蓝图、启动检查。
具体路由分散在 routes/ 目录下的各蓝图模块。
"""
from flask import Flask, g

from db import get_db, init_db, DB_PATH
from routes import register_blueprints
from api import api_bp
import backup

app = Flask(__name__)
app.secret_key = "enthub-dev-key"


@app.before_request
def before_request():
    g.db = get_db()


@app.teardown_request
def teardown_request(exc):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


# 注册所有蓝图（Web 路由 + REST API）
register_blueprints(app)
app.register_blueprint(api_bp)


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
