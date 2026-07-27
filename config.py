"""配置管理：读写 config.json，支持 API 密钥和配额统计。"""
import json
from pathlib import Path

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


def load_config():
    """读取 config.json，如果不存在则创建默认配置。"""
    if not CONFIG_PATH.exists():
        return {}
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def save_config(config):
    """写入 config.json。"""
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


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


# ── LLM API 配置 ─────────────────────────────────────────────────────────────

DEFAULT_LLM = {
    "enabled": False,
    "base_url": "https://api.openai.com/v1",
    "api_key": "",
    "model": "gpt-4o-mini",
    "timeout": 30,
}


def get_llm_config():
    """获取 LLM API 配置。"""
    config = load_config()
    llm = config.get("llm_api", {})
    # 补全默认值
    for k, v in DEFAULT_LLM.items():
        if k not in llm:
            llm[k] = v
    return llm


def save_llm_config(**kwargs):
    """保存 LLM API 配置。"""
    config = load_config()
    if "llm_api" not in config:
        config["llm_api"] = dict(DEFAULT_LLM)
    llm = config["llm_api"]
    for k, v in kwargs.items():
        if k in DEFAULT_LLM:
            llm[k] = v
    save_config(config)
    return llm


def is_llm_ready():
    """检查 LLM API 是否已配置好。"""
    llm = get_llm_config()
    return bool(llm.get("enabled") and llm.get("api_key") and llm.get("base_url"))
