"""配置管理：从数据库 settings 表读写配置（替代 config.json）。

配置存储在 settings 表中，key = 'config'，value = JSON 字符串。
引导配置（db_path / backup_dir）在 bootstrap.json 中，不在此处。
"""
import json
import sqlite3
import uuid
from pathlib import Path
from werkzeug.security import generate_password_hash, check_password_hash

APP_PORT = 5210

# 应用版本号（发版时与 git tag / CHANGELOG 保持一致）
APP_VERSION = "1.1.1"

# 旧路径（仅用于迁移检测，不再用于读写）
CONFIG_PATH = Path(__file__).parent / "config.json"

# 默认配额
DEFAULT_QUOTA = {"total": 1000, "used": 0}


# API 提供商默认配置（首次使用时自动写入 config.json）
DEFAULT_PROVIDERS = {
    "jinghai": {
        "name": "鲸海数据",
        "base_url": "https://www.kqdaas.com",
        "enabled": False,
        "credentials": {
            "app_id": "",   # X-Jinghai-App-Id
            "api_key": "",  # X-Jinghai-Api-Key
        },
    },
    # 预留：后续可添加更多平台
    # "tianyancha": { ... },
    # "qixinbao": { ... },
}


def _get_conn():
    """获取 DB 连接：优先使用 Flask g.db，否则创建独立连接。

    返回 (conn, should_close) 元组。

    注意：SSE 流式响应中 Flask 的 teardown_request 可能在 generator 执行期间
    就关闭了 g.db（连接对象还在但内部状态已 closed），需要检测并创建新连接。
    """
    try:
        from flask import g
        if hasattr(g, "db") and g.db is not None:
            try:
                # 验证连接是否仍然可用（可能已被 teardown_request 关闭）
                g.db.execute("SELECT 1")
                return g.db, False
            except sqlite3.ProgrammingError:
                # 连接已关闭，创建新的独立连接
                pass
    except (RuntimeError, ImportError):
        pass
    from db import DB_PATH
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn, True


def load_config():
    """读取配置（从数据库 settings 表）。"""
    from db import DB_PATH
    if not DB_PATH.exists():
        return {}
    conn, should_close = _get_conn()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = 'config'").fetchone()
        if row:
            return json.loads(row["value"])
        return {}
    except sqlite3.OperationalError:
        # settings 表可能还不存在（init_db 未执行）
        return {}
    finally:
        if should_close:
            conn.close()


def save_config(config):
    """写入配置（到数据库 settings 表）。"""
    conn, should_close = _get_conn()
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = datetime('now', 'localtime')",
            ["config", json.dumps(config, ensure_ascii=False)],
        )
        conn.commit()
    finally:
        if should_close:
            conn.close()


def get_api_providers():
    """获取所有 API 提供商配置（确保默认值存在）。"""
    config = load_config()
    if "api_providers" not in config:
        config["api_providers"] = {}
    # 补全缺失的默认提供商
    for key, defaults in DEFAULT_PROVIDERS.items():
        if key not in config["api_providers"]:
            config["api_providers"][key] = defaults
        else:
            # 确保每个提供商有完整的字段结构
            provider = config["api_providers"][key]
            for k, v in defaults.items():
                if k not in provider:
                    provider[k] = v
    return config["api_providers"]


def get_provider(provider_key):
    """获取单个提供商配置。"""
    providers = get_api_providers()
    return providers.get(provider_key)


def update_provider(provider_key, **kwargs):
    """更新提供商配置（如 api_key、enabled 等）。

    用法：
        update_provider("jinghai", enabled=True, api_key="xxx")
        update_provider("jinghai", api_secret="yyy")
    """
    config = load_config()
    if "api_providers" not in config:
        config["api_providers"] = DEFAULT_PROVIDERS.copy()

    if provider_key not in config["api_providers"]:
        return None

    provider = config["api_providers"][provider_key]

    # 支持直接传 credentials 子字段（app_id / api_key）
    if "app_id" in kwargs:
        provider.setdefault("credentials", {})["app_id"] = kwargs.pop("app_id")
    if "api_key" in kwargs:
        provider.setdefault("credentials", {})["api_key"] = kwargs.pop("api_key")

    # 其余字段直接更新到 provider 层级
    for k, v in kwargs.items():
        provider[k] = v

    save_config(config)
    return provider


def is_provider_ready(provider_key):
    """检查提供商是否已配置好（已启用且有 API Key）。"""
    provider = get_provider(provider_key)
    if not provider:
        return False
    if not provider.get("enabled"):
        return False
    creds = provider.get("credentials", {})
    return bool(creds.get("app_id")) and bool(creds.get("api_key"))


# ── API 配额管理 ─────────────────────────────────────────────────────────────

def get_quota():
    """获取当前配额统计 {total, used, remaining}。"""
    config = load_config()
    quota = config.get("api_quota", DEFAULT_QUOTA)
    total = quota.get("total", 1000)
    used = quota.get("used", 0)
    return {"total": total, "used": used, "remaining": max(0, total - used)}


def increment_quota_used(count=1):
    """增加已使用次数（每次成功 API 调用后调用）。"""
    config = load_config()
    if "api_quota" not in config:
        config["api_quota"] = dict(DEFAULT_QUOTA)
    config["api_quota"]["used"] = config["api_quota"].get("used", 0) + count
    save_config(config)
    return get_quota()


def set_quota_total(total):
    """手动校对配额总数。"""
    config = load_config()
    if "api_quota" not in config:
        config["api_quota"] = dict(DEFAULT_QUOTA)
    config["api_quota"]["total"] = int(total)
    save_config(config)
    return get_quota()


def set_quota_used(used):
    """手动校对已使用次数。"""
    config = load_config()
    if "api_quota" not in config:
        config["api_quota"] = dict(DEFAULT_QUOTA)
    config["api_quota"]["used"] = int(used)
    save_config(config)
    return get_quota()


def set_quota_remaining(remaining):
    """手动校对剩余次数（反算 used = total - remaining）。"""
    config = load_config()
    if "api_quota" not in config:
        config["api_quota"] = dict(DEFAULT_QUOTA)
    total = config["api_quota"].get("total", 1000)
    remaining = int(remaining)
    config["api_quota"]["used"] = max(0, total - remaining)
    save_config(config)
    return get_quota()


# ── 模块启停配置 ─────────────────────────────────────────────────────────────

def get_module_config():
    """获取模块启用开关表 {module_id: bool}。未记录的模块视为启用。"""
    config = load_config()
    return config.get("module_config", {})


def set_module_enabled(module_id: str, enabled: bool) -> None:
    """设置单个模块的启用开关（写入 settings 表，重启服务后生效）。"""
    config = load_config()
    mc = config.setdefault("module_config", {})
    mc[module_id] = bool(enabled)
    save_config(config)


def remove_module_config(module_id: str) -> None:
    """模块删除后清理其开关记录。"""
    config = load_config()
    mc = config.get("module_config", {})
    if module_id in mc:
        mc.pop(module_id)
        save_config(config)


# ── 金山多维表 Webhook ──────────────────────────────────────────────────────

def get_webhook_url():
    """获取金山多维表 Webhook URL。"""
    config = load_config()
    return config.get("webhook_url", "")


def save_webhook_url(url):
    """保存金山多维表 Webhook URL。"""
    config = load_config()
    config["webhook_url"] = url.strip()
    save_config(config)


# ── LLM API 配置 ─────────────────────────────────────────────────────────────

DEFAULT_LLM = {
    "enabled": False,
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "timeout": 30,
}

# ── 多模型支持 ────────────────────────────────────────────────────────────────
# config["llm_api"]["models"] 是模型条目列表，每条：
#   {id, name(备注名), note(说明), base_url, api_key, model, timeout}
# config["llm_api"]["active_id"] 指向当前使用的那条。
# 顶层 base_url/api_key/model/timeout 保留为「激活条目的镜像」，由 get_llm_config()
# 读取时刷新 —— 这样 collector / extract_service 等既有调用点完全不用改。
LLM_MODEL_FIELDS = ("base_url", "api_key", "model", "timeout")


def _new_model_id():
    return "m_" + uuid.uuid4().hex[:10]


def _normalize_model(item, existing_id=None):
    """把一条模型配置补全成规范结构。"""
    item = dict(item or {})
    name = (item.get("name") or "").strip()
    model = (item.get("model") or "").strip()
    try:
        timeout = int(item.get("timeout") or 30)
    except (TypeError, ValueError):
        timeout = 30
    return {
        "id": item.get("id") or existing_id or _new_model_id(),
        "name": name or model or "未命名模型",
        "note": (item.get("note") or "").strip(),
        "base_url": (item.get("base_url") or "").strip(),
        "api_key": (item.get("api_key") or "").strip(),
        "model": model,
        "timeout": timeout,
    }


def _ensure_llm_models(config):
    """确保 models / active_id 就绪，返回 (llm, changed)。

    旧版单模型配置（只有顶层 base_url/api_key/model）自动迁移成第一条，
    备注名写「默认模型」，避免升级后模型凭空消失。
    """
    changed = False
    llm = config.setdefault("llm_api", dict(DEFAULT_LLM))

    if not isinstance(llm.get("models"), list):
        llm["models"] = []
        changed = True

    if not llm["models"] and (llm.get("api_key") or llm.get("model") or llm.get("base_url")):
        llm["models"] = [_normalize_model({
            "name": "默认模型",
            "note": "由旧版单模型配置自动迁移",
            "base_url": llm.get("base_url", ""),
            "api_key": llm.get("api_key", ""),
            "model": llm.get("model", ""),
            "timeout": llm.get("timeout", 30),
        }, existing_id="m_default")]
        changed = True

    ids = [m["id"] for m in llm["models"]]
    if llm.get("active_id") not in ids:
        llm["active_id"] = ids[0] if ids else ""
        changed = True

    return llm, changed


def list_llm_models():
    """列出全部模型条目与当前激活 id（必要时完成旧配置迁移并写回）。"""
    config = load_config()
    llm, changed = _ensure_llm_models(config)
    if changed:
        save_config(config)
    return llm["models"], llm.get("active_id", "")


def get_active_llm_model():
    """返回当前激活的模型条目；没有配置则 None。"""
    models, active_id = list_llm_models()
    for m in models:
        if m["id"] == active_id:
            return m
    return models[0] if models else None


def upsert_llm_model(item, model_id=None):
    """新增或更新一条模型配置（首次添加会自动设为激活），返回该条目。

    api_key 传空表示「不改动原密钥」—— 表单里不回显密钥，避免编辑时误清空。
    """
    config = load_config()
    llm, _ = _ensure_llm_models(config)
    norm = _normalize_model(item, existing_id=model_id)

    replaced = False
    for i, m in enumerate(llm["models"]):
        if m["id"] == norm["id"]:
            if not norm["api_key"]:
                norm["api_key"] = m.get("api_key", "")
            llm["models"][i] = norm
            replaced = True
            break
    if not replaced:
        llm["models"].append(norm)

    if llm.get("active_id") not in [m["id"] for m in llm["models"]]:
        llm["active_id"] = norm["id"]
    save_config(config)
    return norm


def delete_llm_model(model_id):
    """删除一条模型配置；若删的是当前激活的，自动切到第一条。"""
    config = load_config()
    llm, _ = _ensure_llm_models(config)
    llm["models"] = [m for m in llm["models"] if m["id"] != model_id]
    if llm.get("active_id") == model_id:
        llm["active_id"] = llm["models"][0]["id"] if llm["models"] else ""
    save_config(config)
    return llm["models"], llm.get("active_id", "")


def set_active_llm_model(model_id):
    """切换当前使用的模型。"""
    config = load_config()
    llm, _ = _ensure_llm_models(config)
    if any(m["id"] == model_id for m in llm["models"]):
        llm["active_id"] = model_id
        save_config(config)
    return llm.get("active_id", "")


def get_module_model_id(module):
    """某个功能单独指定的模型 id；没有则返回空串（表示跟随全局默认）。"""
    if not module:
        return ""
    config = load_config()
    return (config.get("llm_api", {}).get("module_models") or {}).get(module, "") or ""


def set_module_model(module, model_id):
    """给某个功能单独指定模型；传空串表示恢复成「跟随默认」。"""
    config = load_config()
    llm, _ = _ensure_llm_models(config)
    mm = llm.setdefault("module_models", {})
    mm[module] = (model_id or "").strip()
    save_config(config)
    return mm


def get_llm_config(module=None):
    """获取当前生效的 LLM 配置。

    多模型时取激活条目并把它的字段镜像到顶层，保证既有调用点（collector /
    extract_service）拿到的仍是 {base_url, api_key, model, timeout}。
    另外附带 active_id / active_name / module_models 供界面展示。

    module 指定功能名（如 "collector" / "quick_extract"）时：若该功能单独指定了
    模型就用它，否则依旧用全局激活模型 —— 也就是「默认 + 按需覆盖」。
    """
    config = load_config()
    llm = dict(config.get("llm_api", {}))
    for k, v in DEFAULT_LLM.items():
        llm.setdefault(k, v)

    models = llm.get("models") or []
    module_models = llm.get("module_models") or {}
    llm["module_models"] = module_models

    active_id = llm.get("active_id")
    if module:
        override = module_models.get(module) or ""
        if override and any(m.get("id") == override for m in models):
            active_id = override

    active = next((m for m in models if m.get("id") == active_id), None)
    if active is None and models:
        active = models[0]

    if active:
        for k in LLM_MODEL_FIELDS:
            if active.get(k) not in (None, ""):
                llm[k] = active[k]
        llm["active_id"] = active.get("id", "")
        llm["active_name"] = active.get("name") or active.get("model") or ""

    return llm


def save_llm_config(**kwargs):
    """保存 LLM 配置（兼容旧调用）。

    模型字段写进当前激活条目；没有条目时自动建一条「默认模型」。
    enabled 是全局开关，写在顶层。
    """
    config = load_config()
    llm, _ = _ensure_llm_models(config)

    if not llm["models"]:
        llm["models"] = [_normalize_model({"name": "默认模型"})]
        llm["active_id"] = llm["models"][0]["id"]

    idx = next((i for i, m in enumerate(llm["models"]) if m["id"] == llm["active_id"]), 0)
    target = llm["models"][idx]
    for k, v in kwargs.items():
        if k in DEFAULT_LLM:
            if k in LLM_MODEL_FIELDS:
                target[k] = v
            else:
                llm[k] = v
    save_config(config)
    return get_llm_config()


def is_llm_ready():
    """检查 LLM API 是否已配置好。"""
    llm = get_llm_config()
    return bool(llm.get("enabled") and llm.get("api_key") and llm.get("base_url"))


# ── 访问密码管理 ─────────────────────────────────────────────────────────────

def get_access_password() -> str:
    """获取已设置的访问密码哈希，未设置返回空字符串。"""
    config = load_config()
    return config.get("access_password", "")


def set_access_password(plain_password: str) -> None:
    """设置访问密码（存储哈希值，不存明文）。"""
    if not plain_password:
        return
    config = load_config()
    config["access_password"] = generate_password_hash(plain_password)
    save_config(config)


def verify_access_password(plain_password: str) -> bool:
    """验证密码是否匹配。如果未设置密码，返回 True（不设密码就是全开放）。"""
    password_hash = get_access_password()
    if not password_hash:
        return True  # 未设置密码，默认放行
    return check_password_hash(password_hash, plain_password)


def is_password_enabled() -> bool:
    """检查是否启用了密码保护。"""
    return bool(get_access_password())


# ── 通用设置（偏好设置）──────────────────────────────────────────────────────

DEFAULT_PREFERENCES = {
    "auto_backup": True,           # 启动时自动备份
    "backup_keep_count": 7,        # 备份保留份数
    "show_phone_location": True,   # 电话归属地显示
    "recent_search_enabled": True, # 最近查询记录
    "recent_search_limit": 50,    # 最近查询保留条数
    "auto_open_web": False,        # 启动时自动打开浏览器（与菜单栏同步）
    "auto_start": False,           # 开机自启动（与菜单栏同步）
}


def get_preferences():
    """获取通用设置，补全默认值。"""
    config = load_config()
    prefs = config.get("preferences", {})
    for k, v in DEFAULT_PREFERENCES.items():
        if k not in prefs:
            prefs[k] = v
    return prefs


def save_preferences(**kwargs):
    """保存通用设置（只更新传入的字段）。

    auto_open_web / auto_start 同时写入根级别，兼容 menubar.py 和 start.sh 的读取方式。
    """
    config = load_config()
    if "preferences" not in config:
        config["preferences"] = dict(DEFAULT_PREFERENCES)
    for k, v in kwargs.items():
        if k in DEFAULT_PREFERENCES:
            config["preferences"][k] = v
            # 同步到根级别（兼容 menubar.py / start.sh 的旧读取方式）
            if k in ("auto_open_web", "auto_start"):
                config[k] = v
    save_config(config)
    return config["preferences"]
