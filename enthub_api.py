"""鲸海数据 API 客户端。

调用鲸海数据（kqdaas.com）的工商信息接口，获取企业工商照面数据，
并映射到 EntHub 数据库字段结构。

接口说明（来源: https://www.kqdaas.com/docs/getting-started）：
- 端点: /DataService/api/v3/company/detail/{company}?queryType=1|2
  - queryType=1: 按企业名称查询
  - queryType=2: 按统一社会信用代码查询
- 鉴权: X-Jinghai-App-Id + X-Jinghai-Api-Key (两个 Header)
- 计费: 0.05 元/次，免费额度 1,000 次

注意：API 总调用次数限制为 1,000 次，务必节省使用。
"""
import json
import ssl
import urllib.request
import urllib.error
from urllib.parse import quote
from config import get_provider, is_provider_ready, increment_quota_used

# macOS 上 Python 可能缺少 CA 证书，创建不验证证书的 SSL 上下文
# 注意：仅在本地工具中使用，不影响 Flask Web 服务的安全性
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ── API 网关配置 ────────────────────────────────────────────────────────────

# 鲸海数据网关地址（文档: JINGHAI_API_BASE 默认 https://www.kqdaas.com）
JINGHAI_BASE_URL = "https://www.kqdaas.com"

# 工商信息端点（文档: /DataService/api/v3/company/detail/{COMPANY}?queryType=1|2）
JINGHAI_DETAIL_ENDPOINT = "/DataService/api/v3/company/detail"


def _get_credentials(provider_key="jinghai"):
    """获取 API 凭证: (app_id, api_key)。"""
    provider = get_provider(provider_key)
    if not provider:
        return None, None
    creds = provider.get("credentials", {})
    return creds.get("app_id", ""), creds.get("api_key", "")


def fetch_company_info(company_name=None, credit_code=None,
                       provider_key="jinghai", timeout=15):
    """调用鲸海数据 API 获取企业工商信息。

    参数：
        company_name: 企业名称（与 credit_code 二选一）
        credit_code: 统一社会信用代码（优先使用）
        provider_key: API 提供商标识（默认 jinghai）
        timeout: 请求超时秒数

    返回：
        dict: {
            "success": bool,
            "raw": {...},           # API 原始返回
            "mapped": {...},        # 映射到 EntHub 字段后的数据
            "error": str or None,   # 错误信息
        }

    注意：每次调用消耗 1 次配额，请谨慎使用。
    """
    if not is_provider_ready(provider_key):
        return {
            "success": False,
            "raw": None,
            "mapped": None,
            "error": "API 未配置或未启用，请到设置页面配置",
        }

    if not company_name and not credit_code:
        return {
            "success": False,
            "raw": None,
            "mapped": None,
            "error": "请提供企业名称或统一社会信用代码",
        }

    app_id, api_key = _get_credentials(provider_key)

    # 构建请求 URL
    # 端点: /DataService/api/v3/company/detail/{COMPANY}?queryType=1|2
    if credit_code:
        company_param = credit_code
        query_type = "2"
    else:
        company_param = company_name
        query_type = "1"

    url = f"{JINGHAI_BASE_URL}{JINGHAI_DETAIL_ENDPOINT}/{quote(company_param)}?queryType={query_type}"

    # 构建请求
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/json")
    req.add_header("X-Jinghai-App-Id", app_id)
    req.add_header("X-Jinghai-Api-Key", api_key)

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
            body = resp.read().decode("utf-8")
            raw = json.loads(body)
    except urllib.error.HTTPError as e:
        error_body = ""
        try:
            error_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        # 尝试解析 JSON 错误
        err_msg = f"HTTP {e.code}"
        try:
            err_data = json.loads(error_body)
            err_msg = err_data.get("errmsg") or err_data.get("message") or err_msg
        except Exception:
            pass
        return {
            "success": False,
            "raw": None,
            "mapped": None,
            "error": f"{err_msg} (HTTP {e.code})",
        }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "raw": None,
            "mapped": None,
            "error": f"网络错误: {e.reason}",
        }
    except json.JSONDecodeError as e:
        return {
            "success": False,
            "raw": None,
            "mapped": None,
            "error": f"响应解析失败: {e}",
        }
    except Exception as e:
        return {
            "success": False,
            "raw": None,
            "mapped": None,
            "error": f"未知错误: {e}",
        }

    # 映射到 EntHub 数据库字段
    mapped = map_jinghai_to_enthub(raw)

    # 成功调用，扣减配额
    if mapped:
        increment_quota_used(1)

    return {
        "success": mapped is not None,
        "raw": raw,
        "mapped": mapped,
        "error": None if mapped else "API 返回数据格式异常或未找到企业",
    }


# ── 字段映射 ────────────────────────────────────────────────────────────────

# 鲸海数据 API 返回字段 → EntHub 数据库字段
# 基于 2026-07-23 真实 API 调用确认的字段名
JINGHAI_FIELD_MAP = {
    # 基本信息
    "companyName":       "name",                # 企业名称
    "creditNumber":      "credit_code",         # 统一社会信用代码
    "juridicalPerson":   "legal_person",        # 法定代表人
    "registeredCapital": "registered_capital",  # 注册资本（如 "1000.00万人民币"）
    "contributedCapital":"paid_capital",        # 实缴资本（如 "50.00万人民币"）
    "establishTime":     "established_date",    # 成立日期（如 "2017-05-08"）
    "approveDate":       "approved_date",       # 核准日期
    "businessPeriod":    "business_term",       # 营业期限（如 "2017-05-08 至 无固定期限"）
    "businessStatus":    "business_status",     # 经营状态（如 "存续"）
    "companyType":       "company_type",        # 公司类型
    "companyIndustry":   "industry",            # 所属行业（分类路径格式）
    "socialSecurityNum": "insured_count",      # 参保人数

    # 工商注册信息
    "orgCode":           "org_code",             # 组织机构代码
    "licenseNumber":     "registration_no",      # 注册号/执照编号

    # 地址信息
    "province":          "province",            # 省份
    "belongCity":        "city",                # 所属城市
    "district":          "district",            # 区县
    "regitAddress":      "address",             # 注册地址（API 拼写为 regitAddress）

    # 其他
    "businessScope":    "business_scope",       # 经营范围
}


def map_jinghai_to_enthub(raw_response):
    """将鲸海数据 API 返回映射到 EntHub 数据库字段。

    参数：
        raw_response: API 返回的 dict

    返回：
        dict: 映射后的字段（key 为 EntHub 字段名），或 None（解析失败）
    """
    if not raw_response or not isinstance(raw_response, dict):
        return None

    # 鲸海数据响应结构: {status: 200, message: "Success", data: {...}, success: true}
    data = raw_response

    # 尝试从 data 字段提取
    if "data" in raw_response and isinstance(raw_response["data"], dict):
        data = raw_response["data"]

    # 检查业务状态码
    success = raw_response.get("success")
    status = raw_response.get("status") or raw_response.get("code") or raw_response.get("errcode")
    if success is False or (status is not None and str(status) not in ("200", "0")):
        # 业务失败
        errmsg = raw_response.get("errmsg") or raw_response.get("message") or "未知错误"
        return None

    mapped = {}
    for jinghai_field, enthub_field in JINGHAI_FIELD_MAP.items():
        val = data.get(jinghai_field)
        if val is not None and val != "":
            # 清理 "-" 等空值标记
            if isinstance(val, str) and val.strip() in ("-", "--", "—"):
                continue
            # 数字类型转为字符串
            if isinstance(val, (int, float)):
                mapped[enthub_field] = str(int(val)) if val == int(val) else str(val)
            else:
                mapped[enthub_field] = val

    # 曾用名: API 返回 historyNames 列表，拼接为分号分隔的字符串
    history_names = data.get("historyNames")
    if isinstance(history_names, list) and history_names:
        mapped["former_name"] = "; ".join(str(n) for n in history_names)

    # API 返回但当前数据库无对应字段的（记录但不映射）:
    # - products: 产品/服务
    # - companyProfile: 公司简介
    # - issuingAuthority: 发证机关
    # - addressLastChangeDate: 地址变更日期
    # - companyIndustryCode: 行业代码 (L7251)
    # - addressCode: 地址编码
    # - revokeDate: 注销日期
    # - socialSecurityFrom: 社保来源（年报时间）
    # - annualTurnover: 年营业额
    # - reportTaxFrom / reportTaxTotal: 纳税信息

    # 注意: API 工商信息接口不返回以下字段:
    # - phone (电话) → 需单独调用联系方式接口
    # - email (邮箱) → 需单独调用联系方式接口
    # - website (网址) → 需单独调用网站信息接口
    # - shareholders (股东) → 需单独调用股东信息接口 (ID: 10004)
    # - annual_report_address (年报地址) → 需单独调用年报接口
    # - enterprise_scale (企业规模) → API 不提供
    # - english_name (英文名) → API 不提供
    # - mailing_address (通信地址) → API 不提供
    # - taxpayer_id (纳税人识别号) → 通常与信用代码一致，可自动生成

    # 纳税人识别号: 统一社会信用代码即纳税人识别号（三证合一后）
    if "credit_code" in mapped and mapped["credit_code"]:
        mapped["taxpayer_id"] = mapped["credit_code"]

    return mapped if mapped else None


def test_connection(provider_key="jinghai"):
    """测试 API 连通性（不消耗配额）。

    仅访问 API 网关根地址，检查网络是否通、密钥格式是否合理。
    不会调用任何业务接口（如工商信息），因此不消耗 API 配额。

    返回：
        dict: {
            "success": bool,       # 网络是否可达
            "message": str,        # 结果描述
            "details": {...},      # 详细信息
        }
    """
    provider = get_provider(provider_key)
    if not provider:
        return {"success": False, "message": "未找到提供商配置",
                "details": {"provider_key": provider_key}}

    app_id, api_key = _get_credentials(provider_key)
    enabled = provider.get("enabled", False)

    details = {
        "base_url": JINGHAI_BASE_URL,
        "app_id_prefix": app_id[:4] + "***" if len(app_id) > 4 else "",
        "api_key_prefix": api_key[:6] + "***" if len(api_key) > 6 else "",
        "enabled": enabled,
    }

    if not enabled:
        return {"success": False, "message": "平台未启用，请勾选「启用此平台」并保存",
                "details": details}

    if not api_key:
        return {"success": False, "message": "API Key 为空，请填写后保存",
                "details": details}

    if not app_id:
        return {"success": False, "message": "App ID 为空，请填写后保存",
                "details": details}

    # 尝试访问 API 网关（不调用业务接口，不消耗配额）
    try:
        req = urllib.request.Request(JINGHAI_BASE_URL)
        req.add_header("Accept", "application/json")
        req.add_header("X-Jinghai-App-Id", app_id)
        req.add_header("X-Jinghai-Api-Key", api_key)

        with urllib.request.urlopen(req, timeout=8, context=_SSL_CTX) as resp:
            status = resp.status
            try:
                body = resp.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                body = ""

            return {
                "success": True,
                "message": f"✅ 网络连通（HTTP {status}）",
                "details": {**details, "http_status": status, "response_snippet": body[:200]},
            }
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            err_body = ""

        if e.code in (401, 403):
            return {
                "success": True,
                "message": f"✅ 网络连通（HTTP {e.code}，鉴权待验证）",
                "details": {**details, "http_status": e.code, "response_snippet": err_body[:200]},
            }
        elif e.code == 404:
            return {
                "success": True,
                "message": f"✅ 网络连通（HTTP 404，网关地址可达）",
                "details": {**details, "http_status": 404, "response_snippet": err_body[:200]},
            }
        else:
            return {
                "success": False,
                "message": f"⚠️ HTTP {e.code}: {e.reason}",
                "details": {**details, "http_status": e.code, "response_snippet": err_body[:200]},
            }
    except urllib.error.URLError as e:
        return {
            "success": False,
            "message": f"❌ 网络不通: {e.reason}",
            "details": {**details, "error_type": str(type(e).__name__)},
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"❌ 连接失败: {e}",
            "details": {**details, "error_type": str(type(e).__name__)},
        }
