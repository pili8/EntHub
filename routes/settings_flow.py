"""设置页面：API 密钥管理 + 配额统计 + 访问密码 + 网络地址。"""
import socket
from flask import Blueprint, g, request, render_template, redirect, url_for, \
                   flash, jsonify, Response

from config import (get_api_providers, update_provider, is_provider_ready,
                     get_quota, set_quota_remaining, get_llm_config, save_llm_config,
                     set_access_password, is_password_enabled, get_access_password,
                     get_webhook_url, save_webhook_url)
import enthub_api

bp = Blueprint('settings_flow_bp', __name__)


def get_local_ips():
    """获取本机局域网 IP 地址列表。"""
    ips = []
    try:
        # 方法1: 通过连接外部地址获取本机IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.1)
        s.connect(("10.254.254.254", 1))
        local_ip = s.getsockname()[0]
        s.close()
        if local_ip and not local_ip.startswith("127."):
            ips.append(local_ip)
    except Exception:
        pass

    # 方法2: 遍历网络接口
    try:
        import subprocess
        result = subprocess.run(
            ["ifconfig", "-l"],
            capture_output=True, text=True, timeout=2
        )
        ifaces = result.stdout.strip().split()
        for iface in ifaces:
            if iface == "lo0":
                continue
            try:
                r = subprocess.run(
                    ["ifconfig", iface],
                    capture_output=True, text=True, timeout=2
                )
                for line in r.stdout.split("\n"):
                    line = line.strip()
                    if line.startswith("inet ") and "127.0.0.1" not in line:
                        ip = line.split()[1]
                        if ip not in ips:
                            ips.append(ip)
            except Exception:
                continue
    except Exception:
        pass

    return ips


@bp.route("/settings")
def settings_page():
    """设置页面：API 密钥 + 配额统计 + LLM 配置 + 访问密码 + 网络地址 + 金山多维表。"""
    providers = get_api_providers()
    quota = get_quota()
    llm = get_llm_config()
    local_ips = get_local_ips()
    password_enabled = is_password_enabled()
    webhook_url = get_webhook_url()
    return render_template("settings.html",
                           providers=providers, quota=quota, llm=llm,
                           local_ips=local_ips,
                           password_enabled=password_enabled,
                           webhook_url=webhook_url)


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

@bp.route("/settings/llm", methods=["POST"])
def update_llm():
    """保存 LLM API 配置。"""
    base_url = request.form.get("llm_base_url", "").strip()
    api_key = request.form.get("llm_api_key", "").strip()
    model = request.form.get("llm_model", "").strip()
    timeout = request.form.get("llm_timeout", "30").strip()
    enabled = request.form.get("llm_enabled") == "on"

    try:
        timeout = int(timeout)
    except ValueError:
        timeout = 30

    save_llm_config(
        base_url=base_url or "https://api.openai.com/v1",
        api_key=api_key,
        model=model or "gpt-4o-mini",
        timeout=timeout,
        enabled=enabled,
    )
    flash("已保存大模型 API 配置", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


@bp.route("/settings/llm/test", methods=["POST"])
def test_llm():
    """测试 LLM API 连通性。"""
    from extract_service import extract_by_llm
    result = extract_by_llm("测试企业名称：示例有限公司", timeout=10)
    if "error" in result:
        return jsonify({"code": 2001, "message": result["error"], "data": None})
    return jsonify({"code": 0, "message": f"✅ 连通成功，提取到 {len(result)} 个字段", "data": result})


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


# ── 访问密码设置 ────────────────────────────────────────────────────────────

@bp.route("/settings/password", methods=["POST"])
def update_password():
    """设置/修改访问密码。"""
    current_password = request.form.get("current_password", "").strip()
    new_password = request.form.get("new_password", "").strip()
    confirm_password = request.form.get("confirm_password", "").strip()

    # 如果已有密码，需要验证当前密码
    if is_password_enabled():
        from config import verify_access_password
        if not verify_access_password(current_password):
            flash("当前密码不正确", "error")
            return redirect(url_for("settings_flow_bp.settings_page"))

    if not new_password:
        flash("密码不能为空", "error")
        return redirect(url_for("settings_flow_bp.settings_page"))

    if len(new_password) < 4:
        flash("密码长度不能少于 4 位", "error")
        return redirect(url_for("settings_flow_bp.settings_page"))

    if new_password != confirm_password:
        flash("两次输入的密码不一致", "error")
        return redirect(url_for("settings_flow_bp.settings_page"))

    set_access_password(new_password)
    flash("✅ 访问密码已设置成功", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


@bp.route("/settings/password/disable", methods=["POST"])
def disable_password():
    """关闭密码保护。"""
    from config import save_config, load_config
    config = load_config()
    config.pop("access_password", None)
    save_config(config)
    flash("✅ 已关闭密码保护", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


@bp.route("/api/settings/password-status")
def password_status():
    """返回密码保护状态（JSON）。"""
    return jsonify({
        "enabled": is_password_enabled(),
    })


# ── 二维码生成 ──────────────────────────────────────────────────────────────

@bp.route("/api/settings/qrcode")
def generate_qrcode():
    """生成二维码图片，直接返回 PNG 字节流。"""
    url = request.args.get("url", "")
    if not url:
        return jsonify({"error": "缺少 url 参数"}), 400
    try:
        import qrcode
        from io import BytesIO
        img = qrcode.make(url)
        buf = BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return Response(buf.getvalue(), mimetype="image/png")
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── 金山多维表 Webhook ──────────────────────────────────────────────────────

@bp.route("/settings/kinboard-webhook", methods=["POST"])
def save_kinboard_webhook():
    """保存金山多维表 Webhook URL。"""
    url = request.form.get("webhook_url", "").strip()
    save_webhook_url(url)
    flash("✅ 金山多维表 Webhook 已保存", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))
