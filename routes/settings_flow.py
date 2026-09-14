"""设置页面：API 密钥管理 + 配额统计 + 访问密码 + 网络地址 + 模块管理。"""
import re
import socket
import sqlite3
import sys
from pathlib import Path
from flask import Blueprint, g, request, render_template, redirect, url_for, \
                   flash, jsonify, Response

from config import (get_api_providers, update_provider, is_provider_ready,
                     get_quota, set_quota_remaining, get_llm_config, save_llm_config,
                     set_access_password, is_password_enabled, get_access_password,
                     get_webhook_url, save_webhook_url)
import enthub_api

bp = Blueprint('settings_flow_bp', __name__)

MODULE_META = {
    "id": "settings_flow",
    "name": "设置",
    "icon": "settings",
    "url": "/settings",
    "group": "system",
    "order": 30,
    "core": True,
}


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
    """设置页面：API 密钥 + 配额统计 + LLM 配置 + 访问密码 + 网络地址 + 模块管理。"""
    providers = get_api_providers()
    quota = get_quota()
    llm = get_llm_config()
    local_ips = get_local_ips()
    password_enabled = is_password_enabled()
    webhook_url = get_webhook_url()
    import bootstrap
    bs = bootstrap.get_bootstrap()

    # 模块管理数据：全部模块 + 当前开关状态（core 模块禁用开关、不可删除）
    from config import get_module_config
    import routes
    states = get_module_config()
    manage_modules = []
    for m in routes.ALL_MODULES:
        item = {k: v for k, v in m.items() if not k.startswith("_")}
        item["enabled"] = states.get(m["id"], True)
        item["core"] = m.get("core", False)
        manage_modules.append(item)
    manage_modules.sort(key=lambda x: (not x["core"], x["group"], x["order"]))

    return render_template("settings.html",
                           providers=providers, quota=quota, llm=llm,
                           local_ips=local_ips,
                           password_enabled=password_enabled,
                           webhook_url=webhook_url,
                           db_path=bs["db_path"],
                           backup_dir=bs["backup_dir"],
                           manage_modules=manage_modules)


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


# ── 模块管理（启用 / 停用 / 删除）────────────────────────────────────────────

def _drop_module_tables(meta):
    """清空模块数据：优先模块自带 drop_module_tables()，否则按 tables SQL 解析表名 DROP。"""
    mod = sys.modules.get(f"routes.{meta['id']}")
    fn = getattr(mod, "drop_module_tables", None) if mod else None
    if fn:
        fn()
        return True
    names = []
    for sql in meta.get("tables") or []:
        m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`]?(\w+)", sql, re.I)
        if m:
            names.append(m.group(1))
    if not names:
        return False
    from db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    try:
        for n in names:
            conn.execute(f'DROP TABLE IF EXISTS "{n}"')
        conn.commit()
    finally:
        conn.close()
    return True


@bp.route("/settings/modules/toggle", methods=["POST"])
def toggle_module():
    """启用/停用一个可选模块（写入 settings，重启服务后生效）。"""
    from config import set_module_enabled
    module_id = request.form.get("module_id", "").strip()
    action = request.form.get("action", "")
    import routes
    meta = next((m for m in routes.ALL_MODULES if m["id"] == module_id), None)
    if not meta or meta.get("core"):
        flash("该模块不可关闭", "error")
        return redirect(url_for("settings_flow_bp.settings_page"))
    set_module_enabled(module_id, action == "on")
    state = "启用" if action == "on" else "停用"
    flash(f"✅ 模块「{meta['name']}」已{state}，重启服务后生效", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


@bp.route("/settings/modules/delete", methods=["POST"])
def delete_module():
    """删除一个模块：移除代码文件；purge 勾选时同时清空其数据表。"""
    from config import remove_module_config
    module_id = request.form.get("module_id", "").strip()
    purge = request.form.get("purge") == "on"
    import routes
    meta = next((m for m in routes.ALL_MODULES if m["id"] == module_id), None)
    if not meta or meta.get("core"):
        flash("核心模块不可删除", "error")
        return redirect(url_for("settings_flow_bp.settings_page"))

    file_path = Path(__file__).parent / meta.get("_file", f"{module_id}.py")
    if not file_path.exists():
        flash(f"❌ 模块文件不存在：{file_path}", "error")
        return redirect(url_for("settings_flow_bp.settings_page"))

    file_path.unlink()  # 移除模块代码文件，重启后路由/菜单自动消失

    purged = False
    if purge:
        try:
            purged = _drop_module_tables(meta)
        except Exception as e:
            flash(f"⚠️ 数据清空失败：{e}", "error")

    remove_module_config(module_id)
    tail = "，数据已清空" if (purge and purged) else "，数据已保留"
    flash(f"✅ 模块「{meta['name']}」代码已删除{tail}，重启服务后生效", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


# ── 数据存储路径配置 ──────────────────────────────────────────────────────────

@bp.route("/settings/storage", methods=["POST"])
def save_storage_paths():
    """保存数据存储路径（数据库路径 + 备份目录）。"""
    import bootstrap
    new_db_path = request.form.get("db_path", "").strip()
    new_backup_dir = request.form.get("backup_dir", "").strip()

    if new_backup_dir:
        bootstrap.save_bootstrap(backup_dir=new_backup_dir)
        flash(f"✅ 备份目录已更新为 {new_backup_dir}", "success")

    if new_db_path:
        from pathlib import Path
        p = Path(new_db_path)
        if not p.exists():
            flash(f"❌ 数据库文件不存在：{new_db_path}", "error")
        elif p.suffix != ".db":
            flash("❌ 请选择 .db 格式的数据库文件", "error")
        else:
            bootstrap.save_bootstrap(db_path=new_db_path)
            flash(f"✅ 数据库路径已更新，重启后生效", "success")

    return redirect(url_for("settings_flow_bp.settings_page"))
