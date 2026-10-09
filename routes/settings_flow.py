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
                     list_llm_models, upsert_llm_model, delete_llm_model,
                     set_active_llm_model, get_active_llm_model,
                     get_module_model_id, set_module_model,
                     set_access_password, is_password_enabled, get_access_password,
                     get_webhook_url, save_webhook_url,
                     get_preferences, save_preferences)

# 支持「单独指定模型」的功能位：内部名 -> 界面名
LLM_MODULE_LABELS = {
    "collector": "网页采集",
    "quick_extract": "快速提取",
}
import enthub_api
from feishu_service import get_feishu_config, save_feishu_config, mask_secret, test_feishu_connection

bp = Blueprint('settings_flow_bp', __name__)

MODULE_META = {
    "id": "settings_flow",
    "name": "设置",
    "icon": "settings",
    "url": "/settings",
    "group": "system",
    "order": 30,
    "core": True,
    "risk": "high",
    "impact": "设置页本身，也是模块开关面板的所在地。停用后等于把自己锁死。",
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
    llm_models, llm_active_id = list_llm_models()
    local_ips = get_local_ips()
    password_enabled = is_password_enabled()
    webhook_url = get_webhook_url()
    prefs = get_preferences()
    import bootstrap
    bs = bootstrap.get_bootstrap()

    # 模块管理数据：分「可管理」与「核心常驻」两组渲染（core 模块无开关、不可删除）
    from config import get_module_config
    import routes
    states = get_module_config()
    group_labels = {"data": "数据", "ops": "操作", "system": "系统"}
    manageable, core_modules = [], []
    for m in routes.ALL_MODULES:
        item = {k: v for k, v in m.items() if not k.startswith("_")}
        item["enabled"] = states.get(m["id"], True)
        item["core"] = m.get("core", False)
        item["external"] = m.get("external", False)
        item["group_label"] = group_labels.get(m.get("group"), m.get("group") or "")
        item["url"] = m.get("url", "")
        # 开关与已注册路由不一致 = 已改配置但尚未重启，界面上给出提示
        registered = m["id"] in routes.ENABLED_IDS
        item["pending"] = ""
        if not item["core"]:
            if item["enabled"] and not registered:
                item["pending"] = "待启用"
            elif not item["enabled"] and registered:
                item["pending"] = "待停用"
        (core_modules if item["core"] else manageable).append(item)
    manageable.sort(key=lambda x: (x["group"], x["order"]))
    core_modules.sort(key=lambda x: (x["group"], x["order"]))

    # 飞书公共服务不在 routes/ 下（是根目录服务层），手动注入核心模块列表
    feishu = get_feishu_config()
    from routes.collector import get_sync_to_feishu
    sync_feishu = get_sync_to_feishu()
    feishu_core = {
        "id": "feishu_service",
        "name": "飞书公共服务",
        "icon": "cloud",
        "url": "",
        "group": "system",
        "order": 25,
        "core": True,
        "external": False,
        "group_label": "系统",
        "enabled": True,
        "pending": "",
    }
    # 插入到 system 组前面（按 order 排，25 在 settings 的 30 之前）
    core_modules.insert(0, feishu_core)
    core_modules.sort(key=lambda x: (x["group"], x["order"]))

    return render_template("settings.html",
                           providers=providers, quota=quota, llm=llm,
                           llm_models=llm_models, llm_active_id=llm_active_id,
                           local_ips=local_ips,
                           password_enabled=password_enabled,
                           webhook_url=webhook_url,
                           db_path=bs["db_path"],
                           backup_dir=bs["backup_dir"],
                           manageable_modules=manageable,
                           core_modules=core_modules,
                           prefs=prefs,
                           feishu=feishu,
                           sync_feishu=sync_feishu,
                           mask_secret=mask_secret)


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


# ── 多模型管理 ──────────────────────────────────────────────────────────────

def _probe_llm(base_url, api_key, model, timeout=20):
    """真实发一次最小对话请求，验证这个模型到底能不能用。

    通路：`POST {base_url}/chat/completions`（OpenAI 兼容协议），带
    `Authorization: Bearer <key>`，body 为 `{model, messages:[ping], max_tokens:64}`。
    判据 = HTTP 200 且返回体能解析出 `choices`。一次请求即可覆盖：
      · 网络是否可达（连不上 → 网络错误）
      · 地址对不对（路径写错 → 404 或返回 HTML）
      · 密钥是否有效（401 / 403）
      · 账户有没有额度（402 余额不足 / 429 限流）
      · 模型名是否存在（404 model not found）
    返回 (ok, message, detail)，detail 带完整诊断供界面展示。
    """
    import json as _json
    import ssl
    import time as _time
    import urllib.error
    import urllib.request

    base = (base_url or "").rstrip("/")
    detail = {
        "url": (base + "/chat/completions") if base else "",
        "model": model, "status": None, "elapsed": None,
        "model_echo": None, "finish_reason": None, "sample": None,
    }

    if not base.startswith("http"):
        return False, "接口地址不合法，需以 http(s):// 开头", detail
    if not api_key:
        return False, "缺少 API Key", detail
    if not model:
        return False, "缺少模型名", detail

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        # 给足 token：推理型模型会先输出思考过程，max_tokens 太小会拿到空内容
        "max_tokens": 64,
    }
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(
        detail["url"], data=_json.dumps(payload).encode(), method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"})

    t0 = _time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            detail["status"] = resp.status
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail["status"] = e.code
        detail["elapsed"] = round(_time.time() - t0, 2)
        body = e.read().decode(errors="ignore")[:240]
        detail["sample"] = body
        low = body.lower()
        # 各服务商对「模型名不对」用的状态码不统一（404 / 400 / 422），认关键词更准
        model_missing = "model" in low and any(
            k in low for k in ("not exist", "does not exist", "invalid", "unknown",
                               "不存在", "未找到", "not found"))
        if e.code == 401:
            return False, "API Key 无效或被拒绝（401）", detail
        if e.code == 403:
            return False, "无权限或被拦截（403）—— 密钥权限不足或地区限制", detail
        if e.code == 402:
            return False, "账户余额不足（402）—— 需到服务商后台充值", detail
        if model_missing:
            return False, f"模型名不存在或该服务商不支持：{body[:130]}", detail
        if e.code == 404:
            return False, f"接口地址或模型不存在（404）：{body[:130]}", detail
        if e.code == 429:
            return False, "被限流（429）—— 免费额度用完或上游限流，稍后再试", detail
        if e.code >= 500:
            return False, f"对方服务端错误（{e.code}）—— 不是你的配置问题", detail
        return False, f"HTTP {e.code}：{body[:140]}", detail
    except Exception as e:
        detail["elapsed"] = round(_time.time() - t0, 2)
        return False, f"网络不通：{type(e).__name__}: {str(e)[:140]}", detail

    detail["elapsed"] = round(_time.time() - t0, 2)

    try:
        data = _json.loads(raw)
    except Exception:
        detail["sample"] = raw.decode(errors="ignore")[:160]
        return False, "返回的不是 JSON —— 地址可能填成了网页地址", detail

    choices = data.get("choices") or []
    if not choices:
        err = (data.get("error") or {})
        detail["sample"] = _json.dumps(data, ensure_ascii=False)[:200]
        return False, f"返回里没有 choices：{err.get('message') or detail['sample'][:110]}", detail

    ch = choices[0]
    msg = ch.get("message") or {}
    detail["model_echo"] = data.get("model")
    detail["finish_reason"] = ch.get("finish_reason")
    content = (msg.get("content") or "").strip()
    reasoning = (msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
    detail["sample"] = (content or reasoning or "(空内容)")[:120]

    return True, f"联通正常（{detail['model_echo'] or model}）", detail


@bp.route("/api/settings/llm/models", methods=["POST"])
def save_llm_model():
    """新增 / 更新一条模型配置（api_key 留空表示不改动原密钥）。"""
    body = request.get_json(silent=True) or {}
    model = (body.get("model") or "").strip()
    base_url = (body.get("base_url") or "").strip()
    if not model:
        return jsonify({"code": 2001, "message": "请填写模型名，例如 z-ai/glm-5.2", "data": None})
    if not base_url:
        return jsonify({"code": 2001, "message": "请填写接口地址（Base URL）", "data": None})

    item = upsert_llm_model({
        "name": (body.get("name") or "").strip() or model,
        "note": body.get("note", ""),
        "base_url": base_url,
        "api_key": body.get("api_key", ""),
        "model": model,
        "timeout": body.get("timeout", 30),
    }, model_id=(body.get("id") or None))

    models, active_id = list_llm_models()
    return jsonify({
        "code": 0,
        "message": f"已保存「{item['name']}」",
        "data": {"models": models, "active_id": active_id},
    })


@bp.route("/api/settings/llm/models/activate", methods=["POST"])
def activate_llm_model():
    """切换当前使用的模型。"""
    body = request.get_json(silent=True) or {}
    mid = (body.get("id") or "").strip()
    active = set_active_llm_model(mid)
    if active != mid:
        return jsonify({"code": 2001, "message": "模型不存在", "data": None})
    item = get_active_llm_model()
    return jsonify({
        "code": 0,
        "message": f"已把「{item['name'] if item else ''}」设为默认模型",
        "data": {"active_id": active},
    })


@bp.route("/api/settings/llm/models/delete", methods=["POST"])
def remove_llm_model():
    """删除一条模型配置。"""
    body = request.get_json(silent=True) or {}
    mid = (body.get("id") or "").strip()
    if not mid:
        return jsonify({"code": 2001, "message": "缺少模型 id", "data": None})
    models, active_id = delete_llm_model(mid)
    return jsonify({
        "code": 0, "message": "已删除",
        "data": {"models": models, "active_id": active_id},
    })


@bp.route("/api/settings/llm/enabled", methods=["POST"])
def toggle_llm_enabled():
    """启用 / 停用大模型（全局开关，与具体模型无关）。"""
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled"))
    save_llm_config(enabled=enabled)
    return jsonify({
        "code": 0,
        "message": "已启用大模型" if enabled else "已停用大模型",
        "data": {"enabled": enabled},
    })


@bp.route("/api/settings/llm/module-model", methods=["POST"])
def set_module_llm_model():
    """给某个功能位单独指定模型；传空 model_id 表示恢复「跟随默认」。"""
    body = request.get_json(silent=True) or {}
    module = (body.get("module") or "").strip()
    if module not in LLM_MODULE_LABELS:
        return jsonify({"code": 2001, "message": "未知的功能标识", "data": None})

    model_id = (body.get("model_id") or "").strip()
    if model_id:
        models, _ = list_llm_models()
        if not any(m["id"] == model_id for m in models):
            return jsonify({"code": 2001, "message": "模型不存在", "data": None})

    set_module_model(module, model_id)
    label = LLM_MODULE_LABELS.get(module, module)
    if model_id:
        models, _ = list_llm_models()
        name = next((m["name"] for m in models if m["id"] == model_id), model_id)
        msg = f"「{label}」将使用「{name}」"
    else:
        msg = f"「{label}」恢复为跟随默认模型"
    return jsonify({"code": 0, "message": msg, "data": {"module": module, "model_id": model_id}})


@bp.route("/api/settings/llm/models/test", methods=["POST"])
def test_llm_model():
    """测试某个模型的连通性（可测未保存的填写内容）。"""
    body = request.get_json(silent=True) or {}
    base_url = (body.get("base_url") or "").strip()
    api_key = (body.get("api_key") or "").strip()
    model = (body.get("model") or "").strip()
    timeout = body.get("timeout") or 15

    # 编辑已有条目时表单不回显密钥，用库里存的兜底
    if body.get("id"):
        saved = next((x for x in list_llm_models()[0] if x["id"] == body["id"]), None)
        if saved:
            base_url = base_url or saved["base_url"]
            api_key = api_key or saved["api_key"]
            model = model or saved["model"]
            timeout = timeout or saved.get("timeout", 15)

    ok, msg, detail = _probe_llm(base_url, api_key, model, timeout)
    return jsonify({
        "code": 0 if ok else 2001,
        "message": ("✅ " if ok else "❌ ") + msg,
        "data": detail,
    })


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


# ── 飞书配置 ─────────────────────────────────────────────────────────────────

@bp.route("/settings/feishu", methods=["POST"])
def save_feishu():
    """保存飞书配置。App Secret 留空不覆盖已有值。"""
    kwargs = {
        "app_id": request.form.get("feishu_app_id", "").strip(),
        "app_token": request.form.get("feishu_app_token", "").strip(),
        "collect_table_id": request.form.get("feishu_collect_table_id", "").strip(),
        "source_table_id": request.form.get("feishu_source_table_id", "").strip(),
    }
    # App Secret 留空 = 不修改，只有用户输入了内容才写入
    new_secret = request.form.get("feishu_app_secret", "").strip()
    if new_secret:
        kwargs["app_secret"] = new_secret
    save_feishu_config(**kwargs)
    flash("✅ 飞书配置已保存", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


@bp.route("/api/settings/feishu/test", methods=["POST"])
def test_feishu():
    """测试飞书连接：认证 + 采集表 + 收集表。"""
    results = test_feishu_connection()
    all_ok = all(r["status"] in ("ok", "skip") for r in results)
    return jsonify({
        "code": 0 if all_ok else 2001,
        "message": "\n".join(
            f"{r['name']}: {'✅ ' + (r.get('detail') or '连接正常') if r['status'] == 'ok' else '⏭️ ' + (r.get('error') or '跳过') if r['status'] == 'skip' else '❌ ' + (r.get('error') or '失败')}"
            for r in results
        ),
        "data": results,
    })


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


@bp.route("/settings/preferences", methods=["POST"])
def save_preferences_route():
    """保存通用设置（偏好设置）。"""
    auto_backup = request.form.get("auto_backup") == "on"
    backup_keep_count = max(1, min(100, int(request.form.get("backup_keep_count", 7) or 7)))
    show_phone_location = request.form.get("show_phone_location") == "on"
    recent_search_enabled = request.form.get("recent_search_enabled") == "on"
    recent_search_limit = max(10, min(500, int(request.form.get("recent_search_limit", 50) or 50)))
    auto_open_web = request.form.get("auto_open_web") == "on"
    auto_start = request.form.get("auto_start") == "on"

    # 记录旧值（用于判断是否需要安装/卸载 LaunchAgent）
    old_prefs = get_preferences()
    old_auto_start = old_prefs.get("auto_start", False)

    save_preferences(
        auto_backup=auto_backup,
        backup_keep_count=backup_keep_count,
        show_phone_location=show_phone_location,
        recent_search_enabled=recent_search_enabled,
        recent_search_limit=recent_search_limit,
        auto_open_web=auto_open_web,
        auto_start=auto_start,
    )

    # 开机自启动：安装/卸载 LaunchAgent（仅 macOS）
    import sys
    if sys.platform == "darwin":
        try:
            import menubar
            if auto_start and not old_auto_start:
                menubar._install_launch_agent()
            elif not auto_start and old_auto_start:
                menubar._uninstall_launch_agent()
        except Exception:
            pass  # menubar 依赖 rumps，非 macOS 环境会失败，静默跳过

    flash("✅ 通用设置已保存", "success")
    return redirect(url_for("settings_flow_bp.settings_page"))


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
