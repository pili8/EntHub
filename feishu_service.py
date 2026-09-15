"""飞书公共服务：飞书 Open API 封装，供 collector 及其他模块复用。

配置存储在 EntHub 的 settings 表（config → feishu_config），结构：
{
    "app_id": "cli_xxx",
    "app_secret": "xxx",
    "app_token": "xxx",        // 多维表格 token
    "collect_table_id": "tblxxx",  // 采集结果表
    "source_table_id": "tblyyy",   // 收集表（待采集 URL）
}

功能：
- 获取飞书 tenant_access_token
- 通用飞书 API 调用（带限频重试）
- 读取已有记录（用于去重）
- 写入采集记录
- 从收集表读取未处理条目
- 标记收集表条目已处理
- 初始化采集表字段
"""
import json
import time
import urllib.request
from config import load_config, save_config


# ── 配置管理 ────────────────────────────────────────────────────────────────

DEFAULT_FEISHU_CONFIG = {
    "app_id": "",
    "app_secret": "",
    "app_token": "",
    "collect_table_id": "",
    "source_table_id": "",
}


def get_feishu_config():
    """获取飞书配置，补全默认值。"""
    config = load_config()
    fc = config.get("feishu_config", {})
    for k, v in DEFAULT_FEISHU_CONFIG.items():
        if k not in fc:
            fc[k] = v
    return fc


def save_feishu_config(**kwargs):
    """保存飞书配置（只更新传入的字段）。"""
    config = load_config()
    if "feishu_config" not in config:
        config["feishu_config"] = dict(DEFAULT_FEISHU_CONFIG)
    fc = config["feishu_config"]
    for k, v in kwargs.items():
        if k in DEFAULT_FEISHU_CONFIG:
            fc[k] = v
    save_config(config)
    return fc


def is_feishu_ready():
    """检查飞书是否已配置好（至少有凭证和表 ID）。"""
    fc = get_feishu_config()
    return bool(fc.get("app_id") and fc.get("app_secret")
                and fc.get("app_token") and fc.get("collect_table_id"))


def mask_secret(val):
    """脱敏显示密钥。"""
    if not val or len(val) < 10:
        return "未设置"
    return val[:6] + "****" + val[-4:]


# ── 飞书 API ─────────────────────────────────────────────────────────────────

import ssl as _ssl

# 某些网络环境（公司防火墙、代理网关）使用自签证书，
# 裸 urllib 默认严格验证会报 CERTIFICATE_VERIFY_FAILED。
# 与 LLM 调用保持一致：跳过证书验证。
_ssl_ctx = _ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = _ssl.CERT_NONE


def get_tenant_token(app_id, app_secret):
    """获取飞书 tenant_access_token。"""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    data = json.dumps({"app_id": app_id, "app_secret": app_secret}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10, context=_ssl_ctx) as resp:
        result = json.loads(resp.read())
    if result.get("code") != 0:
        raise Exception(f"获取飞书token失败: {result}")
    return result["tenant_access_token"]


def feishu_request(method, path, token, body=None, max_retries=3):
    """通用飞书 API 调用，带限频重试。"""
    url = f"https://open.feishu.cn/open-apis{path}"
    data = json.dumps(body).encode() if body else None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        })
        try:
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as resp:
                result = json.loads(resp.read())
            # 飞书限频错误码：99991400、99991429
            if result.get("code") in (99991400, 99991429) and attempt < max_retries - 1:
                time.sleep(2 * (attempt + 1))
                continue
            return result
        except Exception:
            if attempt < max_retries - 1:
                time.sleep(1)
                continue
            raise


def get_token():
    """便捷方法：从配置读取凭证并获取 token。"""
    fc = get_feishu_config()
    return get_tenant_token(fc["app_id"], fc["app_secret"])


# ── 采集表操作 ──────────────────────────────────────────────────────────────

def create_table_fields(token, app_token, table_id):
    """在飞书表中创建采集字段（幂等，已存在则跳过）。"""
    fields = [
        {"field_name": "类型", "type": 3, "property": {"options": [
            {"name": "招聘"}, {"name": "新闻"}, {"name": "公司宣传"}, {"name": "政策"}, {"name": "其他"}
        ]}},
        {"field_name": "公司名", "type": 1},
        {"field_name": "联系人", "type": 1},
        {"field_name": "电话", "type": 1},
        {"field_name": "地址", "type": 1},
        {"field_name": "摘要", "type": 1},
        {"field_name": "来源", "type": 1},
        {"field_name": "发布日期", "type": 1},
        {"field_name": "来源链接", "type": 15},
        {"field_name": "采集时间", "type": 5},
    ]
    for field in fields:
        try:
            feishu_request("POST",
                f"/bitable/v1/apps/{app_token}/tables/{table_id}/fields",
                token, field)
        except Exception as e:
            if "FieldNameExist" not in str(e):
                raise


def add_record(token, app_token, table_id, record):
    """写入一条飞书记录。"""
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records"
    result = feishu_request("POST", path, token, {"fields": record})
    print(f"[飞书响应] code={result.get('code')} msg={result.get('msg', 'ok')}")
    if result.get("code") != 0:
        raise Exception(f"飞书返回错误: {result}")
    return result


def get_existing_records(token, app_token, table_id):
    """读取飞书表已有记录，返回去重用的数据集合。

    返回 (existing_links, existing_company_phone)
        existing_links: 已采集的来源链接 set
        existing_company_phone: 公司名+电话 组合 set（无电话用""）
    """
    existing_links = set()
    existing_company_phone = set()

    page_token = None
    while True:
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=500"
        if page_token:
            path += f"&page_token={page_token}"
        result = feishu_request("GET", path, token)

        for item in result.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            # 来源链接
            link_field = fields.get("来源链接")
            if isinstance(link_field, dict):
                link = link_field.get("link", "")
            else:
                link = str(link_field) if link_field else ""
            if link:
                existing_links.add(link)
            # 公司名+电话
            company = str(fields.get("公司名", "")).strip()
            phone = str(fields.get("电话", "")).strip()
            if company:
                existing_company_phone.add((company, phone))

        if not result.get("data", {}).get("has_more"):
            break
        page_token = result["data"].get("page_token")

    return existing_links, existing_company_phone


def get_pending_entries(token, app_token, table_id):
    """从收集表读取未处理的条目，返回 [(record_id, content), ...]。"""
    pending = []
    page_token = None
    while True:
        path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records?page_size=100"
        if page_token:
            path += f"&page_token={page_token}"
        result = feishu_request("GET", path, token)

        for item in result.get("data", {}).get("items", []):
            fields = item.get("fields", {})
            collected = fields.get("是否已采集", False)
            if collected:
                continue
            url_field = fields.get("URL") or fields.get("url") or ""
            if isinstance(url_field, dict):
                content = url_field.get("link", "") or url_field.get("text", "")
            else:
                content = str(url_field).strip()
            if content:
                pending.append((item["record_id"], content))

        if not result.get("data", {}).get("has_more"):
            break
        page_token = result["data"].get("page_token")

    return pending


def mark_url_collected(token, app_token, table_id, record_id, status="", title=""):
    """标记收集表中的记录为已采集，可附带状态说明和网页标题。"""
    fields = {"是否已采集": True}
    if status:
        fields["采集状态"] = status
    if title:
        fields["网页标题"] = title
    path = f"/bitable/v1/apps/{app_token}/tables/{table_id}/records/{record_id}"
    feishu_request("PUT", path, token, {"fields": fields})


def test_feishu_connection():
    """测试飞书连接：认证 + 采集表/收集表可读写。

    返回 list[dict]，每项含 name/status/error/detail。
    """
    fc = get_feishu_config()
    results = []

    # 1. 测试飞书认证
    auth_item = {"name": "飞书认证", "status": "ok", "error": ""}
    token = None
    try:
        token = get_tenant_token(fc["app_id"], fc["app_secret"])
        auth_item["detail"] = "App ID: " + (fc.get("app_id") or "未配置")
    except Exception as e:
        auth_item["status"] = "fail"
        auth_item["error"] = str(e)
    results.append(auth_item)

    # 2. 测试采集表
    collect_item = {"name": "采集表", "status": "ok", "error": ""}
    if token and fc.get("collect_table_id"):
        try:
            path = f"/bitable/v1/apps/{fc['app_token']}/tables/{fc['collect_table_id']}/records?page_size=1"
            result = feishu_request("GET", path, token)
            if result.get("code") != 0:
                collect_item["status"] = "fail"
                collect_item["error"] = f"code={result.get('code')}: {result.get('msg', '')}"
            else:
                total = result.get("data", {}).get("total", 0)
                collect_item["detail"] = f"共 {total} 条记录"
        except Exception as e:
            collect_item["status"] = "fail"
            collect_item["error"] = str(e)
    else:
        collect_item["status"] = "skip"
        collect_item["error"] = "未配置" if not token else "未配置 collect_table_id"
    results.append(collect_item)

    # 3. 测试收集表
    source_item = {"name": "收集表", "status": "ok", "error": ""}
    if token and fc.get("source_table_id") and fc.get("app_token"):
        try:
            path = f"/bitable/v1/apps/{fc['app_token']}/tables/{fc['source_table_id']}/records?page_size=1"
            result = feishu_request("GET", path, token)
            if result.get("code") != 0:
                source_item["status"] = "fail"
                source_item["error"] = f"code={result.get('code')}: {result.get('msg', '')}"
            else:
                total = result.get("data", {}).get("total", 0)
                source_item["detail"] = f"共 {total} 条记录"
        except Exception as e:
            source_item["status"] = "fail"
            source_item["error"] = str(e)
    else:
        source_item["status"] = "skip"
        source_item["error"] = "未配置" if not token else "未配置 source_table_id"
    results.append(source_item)

    return results
