"""EntHub - 企业工商信息管理工具

应用入口：创建 Flask 实例、注册蓝图、启动检查。
具体路由分散在 routes/ 目录下的各蓝图模块。
"""
import threading
import time
import urllib.request

from flask import Flask, g, session, redirect, url_for, request, jsonify

from db import get_db, init_db, DB_PATH
from routes import register_blueprints
from api import api_bp
from config import is_password_enabled, verify_access_password, APP_PORT
import backup

app = Flask(__name__)
app.secret_key = "enthub-dev-key-2024"


# ── 全局模板变量 ────────────────────────────────────────────────────────────

@app.context_processor
def inject_globals():
    return {"APP_PORT": APP_PORT}


# ── 访问密码保护 ────────────────────────────────────────────────────────────

# 不需要密码验证的路径白名单
_PUBLIC_PATHS = {
    "/login",
    "/static/",
    "/api/settings/check-password",
    "/api/settings/login",
    "/api/settings/qrcode",
    "/favicon",
}


@app.before_request
def before_request():
    g.db = get_db()

    # 密码保护检查
    if not is_password_enabled():
        return  # 未设置密码，全部放行

    path = request.path

    # 白名单路径放行
    for p in _PUBLIC_PATHS:
        if path.startswith(p):
            return

    # 已登录放行
    if session.get("authenticated"):
        return

    # API 路径：支持 token 密码直通（?token= 或 Authorization: Bearer）
    if path.startswith("/api/"):
        token = request.args.get("token") or request.headers.get("Authorization", "")
        if token.startswith("Bearer "):
            token = token[7:]
        if token and verify_access_password(token):
            return
        # 未通过，返回 JSON 401 而非 302 重定向
        return jsonify({"code": 401, "message": "需要认证，请在 URL 加 ?token=密码 或 Authorization: Bearer 密码", "data": None}), 401

    # 未登录，重定向到登录页
    return redirect(url_for("login_page"))


@app.teardown_request
def teardown_request(exc):
    db = getattr(g, "db", None)
    if db is not None:
        db.close()


# ── 登录页面 ────────────────────────────────────────────────────────────────


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """访问密码登录页。"""
    if request.method == "POST":
        password = request.form.get("password", "")
        if verify_access_password(password):
            session["authenticated"] = True
            session.permanent = True
            next_url = request.args.get("next") or url_for("pages_bp.index")
            return redirect(next_url)
        from flask import flash
        flash("密码错误，请重试", "error")
        return redirect(url_for("login_page"))

    # 已登录则直接跳转首页
    if session.get("authenticated"):
        return redirect(url_for("pages_bp.index"))

    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>EntHub · 访问验证</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif;
            background: #FBFAF7;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            color: #1F1B17;
        }
        .login-card {
            background: white;
            border-radius: 14px;
            padding: 40px 36px 36px;
            width: 380px;
            max-width: 90vw;
            box-shadow: 0 4px 24px rgba(0,0,0,0.06);
            border: 1px solid #ECE7DF;
            text-align: center;
        }
        .login-icon {
            width: 52px;
            height: 52px;
            background: #FBE7DF;
            border-radius: 14px;
            display: flex;
            align-items: center;
            justify-content: center;
            margin: 0 auto 16px;
        }
        .login-icon svg { width: 26px; height: 26px; color: #D97757; }
        .login-title { font-size: 20px; font-weight: 600; margin-bottom: 4px; }
        .login-subtitle { font-size: 13px; color: #A39E96; margin-bottom: 28px; }
        .login-input {
            width: 100%;
            padding: 12px 16px;
            border: 1px solid #ECE7DF;
            border-radius: 8px;
            font-size: 15px;
            outline: none;
            transition: border-color 0.2s;
            background: #FBFAF7;
        }
        .login-input:focus { border-color: #D97757; }
        .login-btn {
            width: 100%;
            padding: 12px;
            background: #D97757;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 15px;
            font-weight: 500;
            cursor: pointer;
            margin-top: 16px;
            transition: background 0.2s;
        }
        .login-btn:hover { background: #C9663F; }
        .login-error {
            background: #FEF1EB;
            color: #C2410C;
            padding: 10px 14px;
            border-radius: 8px;
            font-size: 13px;
            margin-bottom: 16px;
        }
        .login-footer {
            margin-top: 20px;
            font-size: 12px;
            color: #A39E96;
        }
    </style>
</head>
<body>
    <div class="login-card">
        <div class="login-icon">
            <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
        </div>
        <div class="login-title">EntHub</div>
        <div class="login-subtitle">请输入访问密码</div>
        """ + ("""
        <div class="login-error">密码错误，请重试</div>
        """ if request.args.get("error") else "") + """
        <form method="post">
            <input type="password" name="password" class="login-input" placeholder="输入密码" autofocus>
            <button type="submit" class="login-btn">进入控制台</button>
        </form>
        <div class="login-footer">EntHub · 企业工商信息管理工具</div>
    </div>
</body>
</html>"""


@app.route("/logout")
def logout_page():
    """退出登录。"""
    session.pop("authenticated", None)
    return redirect(url_for("login_page"))


# ═════════════════════════════════════════════════════════════════════════════

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
    app.run(host="0.0.0.0", port=APP_PORT, debug=True)
