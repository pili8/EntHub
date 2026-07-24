"""设置页面：API 密钥管理 + 配额统计。"""
from flask import Blueprint, g, request, render_template, redirect, url_for, \
                   flash, jsonify

from config import (get_api_providers, update_provider, is_provider_ready,
                     get_quota, set_quota_remaining)
import enthub_api

bp = Blueprint('settings_flow_bp', __name__)


@bp.route("/settings")
def settings_page():
    """设置页面：API 密钥 + 配额统计。"""
    providers = get_api_providers()
    quota = get_quota()
    return render_template("settings.html", providers=providers, quota=quota)


@bp.route("/settings/api-provider/<provider_key>", methods=["POST"])
def update_api_provider(provider_key):
    """保存 API 提供商配置。"""
    app_id = request.form.get("app_id", "").strip()
    api_key = request.form.get("api_key", "").strip()
    enabled = request.form.get("enabled") == "on"

    update_provider(provider_key, app_id=app_id, api_key=api_key, enabled=enabled)

    providers = get_api_providers()
    provider_name = providers.get(provider_key, {}).get("name", provider_key)
    flash(f"已保存 {provider_name} 的 API 配置", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


@bp.route("/settings/quota", methods=["POST"])
def update_quota():
    """手动校对剩余配额次数。"""
    remaining = request.form.get("remaining", "0").strip()
    try:
        remaining = int(remaining)
    except ValueError:
        remaining = 0
    set_quota_remaining(remaining)
    flash(f"已校对剩余次数为 {remaining}", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


# ── REST API 端点 ────────────────────────────────────────────────────────────

@bp.route("/api/settings/test-connection", methods=["POST"])
def test_connection():
    """测试 API 连通性（不消耗配额）。"""
    body = request.get_json(silent=True) or {}
    provider_key = body.get("provider_key", "jinghai")
    result = enthub_api.test_connection(provider_key)
    return jsonify({
        "code": 0 if result["success"] else 2001,
        "message": result["message"],
        "data": result.get("details"),
    })
