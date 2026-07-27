"""EntHub - 企业工商信息管理工具

应用入口：创建 Flask 实例、注册蓝图、启动检查。
具体路由分散在 routes/ 目录下的各蓝图模块。
"""
import threading
import time
import urllib.request

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


def _warmup_templates():
    """启动后预热模板编译，避免用户首次访问感知到 5-10 秒延迟。

    Flask debug 模式下首次访问每个模板都要现场编译（base.html + 子模板合计 700+ 行），
    编译完缓存到内存，二次访问毫秒级。这个后台线程在服务就绪后主动触发关键页面编译。

    start.sh 后台模式会监听 "[预热] 完成" 日志，等预热完成后再打开浏览器，
    确保用户首次访问已经预热。
    """
    time.sleep(1)  # 等 Flask 服务就绪（debug 模式启动约 1-2 秒）
    pages = [
        "http://127.0.0.1:5210/",
        "http://127.0.0.1:5210/browse",
        "http://127.0.0.1:5210/search",
        "http://127.0.0.1:5210/browse/data",
    ]
    print("[预热] 开始", flush=True)
    for url in pages:
        try:
            urllib.request.urlopen(url, timeout=30).read()
            print(f"[预热] {url}", flush=True)
        except Exception as e:
            print(f"[预热失败] {url}: {e}", flush=True)
    print("[预热] 完成", flush=True)


_startup_backup_check()


if __name__ == "__main__":
    init_db()
    # 后台预热模板（首次访问变快）
    threading.Thread(target=_warmup_templates, daemon=True).start()
    app.run(host="0.0.0.0", port=5210, debug=True)
