"""统一注册所有 Web 蓝图。

app.py 入口只需调用 register_blueprints(app)。
每个子模块内部以 `bp = Blueprint(__name__, ...)` 暴露自己的路由。
"""
from flask import Blueprint


def register_blueprints(app):
    """把 routes/ 下所有蓝图挂到 app 上。"""
    # 顺序不重要；蓝图通过 url_for 解析对方路由
    from . import pages, companies, tags
    from . import import_flow, cleanup_flow, backup_flow, mcp_flow, api_legacy
    from . import settings_flow, quick_import, phone_tags, phone_wechat

    app.register_blueprint(pages.bp)
    app.register_blueprint(companies.bp)
    app.register_blueprint(tags.bp)
    app.register_blueprint(import_flow.bp)
    app.register_blueprint(cleanup_flow.bp)
    app.register_blueprint(backup_flow.bp)
    app.register_blueprint(mcp_flow.bp)
    app.register_blueprint(api_legacy.bp)
    app.register_blueprint(settings_flow.bp)
    app.register_blueprint(quick_import.bp)
    app.register_blueprint(phone_tags.bp)
    app.register_blueprint(phone_wechat.bp)
