"""文本提取服务：从非结构化文本中提取工商信息字段。

支持两种提取方式：
1. 正则提取（3a）：适用于天眼查/企查查等规整格式的文本
2. 大模型提取（3b）：适用于任意文本，调用 LLM API 提取

统一入口：extract_company_info(text, method="auto")
  - auto: 先尝试正则，字段不足时 fallback 到 LLM
  - regex: 仅正则
  - llm: 仅 LLM
"""
import json
import re
import ssl
import urllib.request
import urllib.error
from utils import clean_val

# ── 正则提取（3a）────────────────────────────────────────────────────────────

# 字段正则：每项 (field, pattern, group_index)
# pattern 使用 re.DOTALL 使 . 匹配换行
_REGEX_PATTERNS = [
    # 企业名称（多种格式）
    ("name", re.compile(
        r'(?:企业名称|公司名称|单位名称|名称)[：:\s]*([^\n]{2,80})', re.UNICODE
    )),
    # 统一社会信用代码（18位字母数字）
    ("credit_code", re.compile(
        r'(?:统一社会信用代码|信用代码|统一信用代码|USCI)[：:\s]*([0-9A-Za-z]{18})'
    )),
    # 法定代表人
    ("legal_person", re.compile(
        r'(?:法定代表人|法人代表|法人|负责人)[：:\s]*([^\n,，;；]{2,20})', re.UNICODE
    )),
    # 注册资本（数字+万/元，支持 "1000万人民币" "500万元" "1,000.00万人民币元" 等）
    ("registered_capital", re.compile(
        r'(?:注册资本|注册资金)[：:\s]*([\d,.]+\s*万[元人民币]*)'
    )),
    # 实缴资本
    ("paid_capital", re.compile(
        r'(?:实缴资本|实缴资金)[：:\s]*([\d,.]+\s*万[元人民币]?)'
    )),
    # 成立日期（YYYY-MM-DD 或 YYYY年MM月DD日）
    ("established_date", re.compile(
        r'(?:成立日期|注册日期|成立时间)[：:\s]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)'
    )),
    # 核准日期
    ("approved_date", re.compile(
        r'(?:核准日期|核准时间)[：:\s]*(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}日?)'
    )),
    # 营业期限
    ("business_term", re.compile(
        r'(?:营业期限|经营期限)[：:\s]*([^\n]{2,60})', re.UNICODE
    )),
    # 经营状态
    ("business_status", re.compile(
        r'(?:经营状态|登记状态|存续状态|企业状态)[：:\s]*(存续|在业|吊销|注销|迁出|停业|清算|活跃|正常经营)'
    )),
    # 公司类型
    ("company_type", re.compile(
        r'(?:公司类型|企业类型|企业\(机构\)类型)[：:\s]*([^\n]{2,40})', re.UNICODE
    )),
    # 所属行业
    ("industry", re.compile(
        r'(?:所属行业|行业分类|行业)[：:\s]*([^\n]{2,40})', re.UNICODE
    )),
    # 参保人数
    ("insured_count", re.compile(
        r'(?:参保人数|社保人数|参加社保人数)[：:\s]*(\d+)'
    )),
    # 省份（匹配 "XX省" 格式，避免误匹配城市名）
    ("province", re.compile(
        r'(?:省份|所属省份)[：:\s]*([\u4e00-\u9fa5]{2,6}省)', re.UNICODE
    )),
    # 城市（匹配 "XX市" 格式）
    ("city", re.compile(
        r'(?:城市|所属城市)[：:\s]*([\u4e00-\u9fa5]{2,6}市)', re.UNICODE
    )),
    # 区县（匹配 "XX区" / "XX县" / "XX市" 格式）
    ("district", re.compile(
        r'(?:区县|所属区县)[：:\s]*([\u4e00-\u9fa5]{2,8}[区县市])', re.UNICODE
    )),
    # 注册地址
    ("address", re.compile(
        r'(?:注册地址|企业地址|公司地址|地址)[：:\s]*([^\n]{4,120})', re.UNICODE
    )),
    # 经营范围（可能多行）
    ("business_scope", re.compile(
        r'(?:经营范围)[：:\s]*([^\n]{4,500})', re.UNICODE | re.DOTALL
    )),
    # 曾用名
    ("former_name", re.compile(
        r'(?:曾用名|历史名称|前身)[：:\s]*([^\n]{2,80})', re.UNICODE
    )),
    # 网址
    ("website", re.compile(
        r'(?:网址|网站|官网)[：:\s]*(https?://[^\s\n]+|www\.[^\s\n]+)'
    )),
    # 邮箱
    ("email", re.compile(
        r'(?:邮箱|电子邮箱|电子邮件)[：:\s]*([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
    )),
    # 电话
    ("phone", re.compile(
        r'(?:电话|联系电话|联系方式|手机)[：:\s]*([\d\-\s+]+)'
    )),
    # 组织机构代码
    ("org_code", re.compile(
        r'(?:组织机构代码)[：:\s]*([0-9A-Za-z]{8,10})'
    )),
    # 注册号
    ("registration_no", re.compile(
        r'(?:注册号|执照编号)[：:\s]*([0-9]{15})'
    )),
    # 股东
    ("shareholders", re.compile(
        r'(?:股东|股东信息|股东名称)[：:\s]*([^\n]{2,200})', re.UNICODE
    )),
]


def _normalize_date(date_str):
    """将日期归一化为 YYYY-MM-DD 格式。"""
    if not date_str:
        return ""
    s = date_str.strip()
    # 2023年07月11日 → 2023-07-11
    m = re.match(r'(\d{4})年(\d{1,2})月(\d{1,2})日?', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    # 2023/07/11 → 2023-07-11
    s = s.replace("/", "-")
    # 2023-7-11 → 2023-07-11
    m = re.match(r'(\d{4})-(\d{1,2})-(\d{1,2})', s)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    return s


# ── 省市区从地址解析 ─────────────────────────────────────────────────────────

# 省级行政区列表
_PROVINCES = [
    "北京", "天津", "上海", "重庆",
    "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东",
    "河南", "湖北", "湖南", "广东", "海南",
    "四川", "贵州", "云南", "陕西", "甘肃", "青海",
    "台湾", "内蒙古", "广西", "西藏", "宁夏", "新疆",
    "香港", "澳门",
]

def _fill_province_city_district_from_address(result):
    """从地址字段中解析省/市/区，仅补充未提取到的字段。"""
    addr = result.get("address", "")
    if not addr:
        return

    # 省
    if "province" not in result:
        for p in _PROVINCES:
            if p in addr:
                # 直辖市用“市”，其他用“省”
                if p in ("北京", "天津", "上海", "重庆"):
                    result["province"] = p + "市"
                else:
                    result["province"] = p + "省"
                break

    # 市（匹配 "XX市"，从地址中找）
    if "city" not in result:
        # 用省名做锚点：先找 "省XX市" 或 "市XX市" 格式
        m = re.search(r'省([\u4e00-\u9fa5]{2,4}市)', addr)
        if m:
            result["city"] = m.group(1)
        else:
            # 直辖市或无省名的情况
            m = re.search(r'([\u4e00-\u9fa5]{2,4}市)', addr)
            if m:
                result["city"] = m.group(1)

    # 区（匹配地址中的区/县级地名）
    if "district" not in result:
        # 优先匹配 "市X区" / "市X县" 格式，避免跨级匹配
        m = re.search(r'市([\u4e00-\u9fa5]{2,4}[区县])', addr)
        if m:
            result["district"] = m.group(1)
        else:
            # fallback: 匹配最后出现的 "XX区/县"
            matches = re.findall(r'[\u4e00-\u9fa5]{2,4}[区县]', addr)
            if matches:
                result["district"] = matches[-1]


def extract_by_regex(text):
    """用正则从文本中提取工商信息字段。

    返回 dict，key 为 EntHub 字段名，value 为提取到的值。
    """
    if not text or not text.strip():
        return {}

    result = {}
    for field, pattern, *_ in _REGEX_PATTERNS:
        m = pattern.search(text)
        if m:
            val = clean_val(m.group(1))
            if val:
                # 日期字段归一化
                if field in ("established_date", "approved_date"):
                    val = _normalize_date(val)
                # 经营范围截断（避免贪婪匹配吃掉太多）
                if field == "business_scope" and len(val) > 300:
                    val = val[:300]
                result[field] = val

    # 自动补充纳税人识别号（三证合一后等于信用代码）
    if "credit_code" in result and "taxpayer_id" not in result:
        result["taxpayer_id"] = result["credit_code"]

    # 从地址中补充省/市/区（如果未从标签中提取到）
    _fill_province_city_district_from_address(result)

    # 清洗校验
    result = clean_extracted_fields(result)

    return result


# ── 数据清洗校验 ─────────────────────────────────────────────────────────────

# 已知经营状态
_KNOWN_STATUS = {
    "存续", "在业", "在营", "正常", "正常经营",
    "吊销", "吊销未注销", "注销", "迁出", "停业",
    "清算", "活跃",
}

# 已知省份后缀
_PROVINCE_SUFFIX = ("省", "市", "自治区", "特别行政区")


def _is_noise(val, field=None):
    """判断一个值是否是噪音/无效值。只过滤明显的垃圾，宁可放行不可误杀。"""
    if not val:
        return True
    val = val.strip()
    if len(val) == 0:
        return True

    # 单字
    if len(val) == 1:
        return True

    # 纯数字（中文数字也算）
    if re.match(r'^[\d一二三四五六七八九十百千万亿]+$', val):
        return True

    # 含有明显非字段的标记词
    noise_keywords = ['历史股东', '股权变更', '变更历程', '认缴出资', '实缴出资',
                      '持股比例', '主要成员', '主要人员', '高管信息',
                      '股东信息', '股东出资', '变更记录', '工商变更']
    if any(kw in val for kw in noise_keywords):
        return True

    return False


def clean_extracted_fields(fields):
    """清洗和校验提取到的字段，移除明显无效值。

    策略：只过滤明确的垃圾，宁可放行不可误杀。
    """
    cleaned = {}

    for field, val in fields.items():
        val = str(val).strip()
        if not val:
            continue

        # 跳过自动补充的字段
        if field == "taxpayer_id":
            cleaned[field] = val
            continue

        # ── 字段级校验（宽松，只拦截明显错误） ──

        if field == "legal_person":
            # 法人：2-10 个汉字，不能含数字
            if re.match(r'^[\u4e00-\u9fa5]{2,10}$', val) and not re.search(r'\d', val):
                cleaned[field] = val
            continue

        if field == "credit_code":
            # 信用代码：15-18 位字母数字
            norm = val.upper().replace(' ', '').replace('-', '')
            if 15 <= len(norm) <= 18 and norm.isalnum():
                cleaned[field] = norm
            continue

        if field == "insured_count":
            # 参保人数：纯数字
            digits = re.sub(r'\D', '', val)
            if digits:
                cleaned[field] = digits
            continue

        if field in ("established_date", "approved_date"):
            # 日期：能解析为 YYYY-MM-DD 即可
            norm = _normalize_date(val)
            if re.match(r'^\d{4}-\d{2}-\d{2}$', norm):
                cleaned[field] = norm
            continue

        if field == "registered_capital":
            # 注册资本：包含数字即可
            if re.search(r'\d', val):
                cleaned[field] = val
            continue

        if field == "phone":
            # 电话：包含 7 位以上数字
            digits = re.sub(r'\D', '', val)
            if len(digits) >= 7:
                cleaned[field] = val
            continue

        if field == "email":
            if '@' in val:
                cleaned[field] = val
            continue

        # 其他字段：通过噪音检测即可
        if not _is_noise(val, field):
            cleaned[field] = val

    return cleaned


def count_extracted_fields(result):
    """统计提取到的有效字段数（排除 phone 和自动补充的 taxpayer_id）。"""
    auto_fields = {"taxpayer_id"}
    return len([k for k in result if k not in auto_fields])


# ── LLM 提取（3b）────────────────────────────────────────────────────────────

# LLM 提取 prompt
_LLM_EXTRACT_PROMPT = """你是一个工商信息提取助手。请从以下文本中提取企业工商信息，返回 JSON 格式。

要求：
1. 只提取文本中明确存在的信息，不要推测或编造
2. 日期格式统一为 YYYY-MM-DD
3. 注册资本保留原文格式（如 "1000万人民币"）
4. 如果某个字段在文本中找不到，不要包含该字段
5. 返回纯 JSON，不要包含 markdown 代码块标记

可提取的字段：
- name: 企业名称
- credit_code: 统一社会信用代码（18位）
- legal_person: 法定代表人
- registered_capital: 注册资本
- paid_capital: 实缴资本
- established_date: 成立日期
- approved_date: 核准日期
- business_term: 营业期限
- business_status: 经营状态（存续/在业/吊销/注销等）
- company_type: 公司类型
- industry: 所属行业
- insured_count: 参保人数（纯数字）
- province: 省份
- city: 城市
- district: 区县
- address: 注册地址
- business_scope: 经营范围
- former_name: 曾用名
- website: 网址
- email: 邮箱
- phone: 电话
- org_code: 组织机构代码
- registration_no: 注册号
- shareholders: 股东（多个用分号分隔）

文本内容：
{text}

请返回提取到的 JSON："""


def _get_llm_config():
    """从配置文件读取 LLM API 配置。

    配置路径：config.json → llm_api
    {
        "llm_api": {
            "enabled": true,
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-xxx",
            "model": "gpt-4o-mini",
            "timeout": 30
        }
    }
    """
    from config import load_config
    config = load_config()
    return config.get("llm_api", {})


def extract_by_llm(text, timeout=None):
    """调用 LLM API 从文本中提取工商信息字段。

    返回 dict，key 为 EntHub 字段名。
    """
    if not text or not text.strip():
        return {"error": "文本为空"}

    llm_config = _get_llm_config()
    if not llm_config.get("enabled"):
        return {"error": "LLM API 未配置或未启用，请到设置页面配置"}

    base_url = llm_config.get("base_url", "").rstrip("/")
    api_key = llm_config.get("api_key", "")
    model = llm_config.get("model", "gpt-4o-mini")
    timeout = timeout or llm_config.get("timeout", 30)

    if not base_url or not api_key:
        return {"error": "LLM API 地址或密钥未配置"}

    # 截断过长文本（避免 token 超限）
    max_chars = 8000
    truncated = text[:max_chars] if len(text) > max_chars else text

    prompt = _LLM_EXTRACT_PROMPT.format(text=truncated)

    # 构建 OpenAI 兼容格式的请求
    url = f"{base_url}/chat/completions"
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个工商信息提取助手，只返回 JSON 格式的数据。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 2000,
    }, ensure_ascii=False).encode("utf-8")

    # SSL 上下文（与 enthub_api.py 保持一致）
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json; charset=utf-8")
    req.add_header("Authorization", f"Bearer {api_key}")

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = ""
        try:
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {"error": f"LLM API 请求失败 (HTTP {e.code}): {err_body[:200]}"}
    except urllib.error.URLError as e:
        return {"error": f"LLM API 网络错误: {e.reason}"}
    except Exception as e:
        return {"error": f"LLM API 调用异常: {e}"}

    # 解析 LLM 返回
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError):
        return {"error": "LLM 返回格式异常"}

    # 清理可能的 markdown 代码块标记
    content = content.strip()
    if content.startswith("```"):
        # 去掉 ```json 和 ```
        content = re.sub(r'^```(?:json)?\s*\n?', '', content)
        content = re.sub(r'\n?```\s*$', '', content)

    try:
        extracted = json.loads(content)
    except json.JSONDecodeError:
        # 尝试提取 JSON 子串
        m = re.search(r'\{[^{}]*\}', content, re.DOTALL)
        if m:
            try:
                extracted = json.loads(m.group())
            except json.JSONDecodeError:
                return {"error": f"LLM 返回的 JSON 无法解析: {content[:200]}"}
        else:
            return {"error": f"LLM 未返回有效 JSON: {content[:200]}"}

    if not isinstance(extracted, dict):
        return {"error": "LLM 返回的不是 JSON 对象"}

    # 过滤：只保留允许的字段名
    ALLOWED_FIELDS = {
        "name", "credit_code", "legal_person", "registered_capital",
        "paid_capital", "established_date", "approved_date", "business_term",
        "business_status", "company_type", "industry", "insured_count",
        "province", "city", "district", "address", "business_scope",
        "former_name", "website", "email", "phone", "org_code",
        "registration_no", "shareholders",
    }
    result = {}
    for k, v in extracted.items():
        k = k.strip().lower()
        if k in ALLOWED_FIELDS and v is not None:
            val = str(v).strip()
            if val and val not in ("-", "--", "无", "N/A", "null", "None"):
                # 日期归一化
                if k in ("established_date", "approved_date"):
                    val = _normalize_date(val)
                result[k] = val

    # 自动补充纳税人识别号
    if "credit_code" in result and "taxpayer_id" not in result:
        result["taxpayer_id"] = result["credit_code"]

    # 清洗校验
    result = clean_extracted_fields(result)

    return result


# ── 统一入口 ─────────────────────────────────────────────────────────────────

def extract_company_info(text, method="auto"):
    """从文本中提取企业工商信息。

    参数：
        text: 输入文本
        method: 提取方式
          - "auto": 先正则，字段不足 fallback 到 LLM
          - "regex": 仅正则
          - "llm": 仅 LLM

    返回：
        dict: {
            "method_used": "regex" | "llm" | "regex+llm",
            "fields": {...},           # 提取到的字段
            "field_count": int,        # 有效字段数
            "regex_fields": {...},     # 正则提取的字段（auto 模式）
            "llm_fields": {...},       # LLM 提取的字段（auto 模式）
            "error": str or None,      # 错误信息
        }
    """
    if not text or not text.strip():
        return {
            "method_used": None,
            "fields": {},
            "field_count": 0,
            "error": "文本为空",
        }

    text = text.strip()

    if method == "regex":
        fields = extract_by_regex(text)
        return {
            "method_used": "regex",
            "fields": fields,
            "field_count": count_extracted_fields(fields),
            "error": None,
        }

    if method == "llm":
        fields = extract_by_llm(text)
        if "error" in fields:
            return {
                "method_used": "llm",
                "fields": {},
                "field_count": 0,
                "error": fields["error"],
            }
        return {
            "method_used": "llm",
            "fields": fields,
            "field_count": count_extracted_fields(fields),
            "error": None,
        }

    # auto 模式：先正则，字段不足 fallback 到 LLM
    MIN_FIELDS = 3  # 至少提取到 3 个有效字段才算成功

    regex_fields = extract_by_regex(text)
    regex_count = count_extracted_fields(regex_fields)

    if regex_count >= MIN_FIELDS:
        return {
            "method_used": "regex",
            "fields": regex_fields,
            "field_count": regex_count,
            "regex_fields": regex_fields,
            "error": None,
        }

    # 正则不够，尝试 LLM
    llm_config = _get_llm_config()
    if not llm_config.get("enabled"):
        # LLM 不可用，返回正则结果（即使字段少）
        return {
            "method_used": "regex",
            "fields": regex_fields,
            "field_count": regex_count,
            "regex_fields": regex_fields,
            "error": "正则提取字段不足，LLM API 未启用" if regex_count > 0 else "未提取到任何字段",
        }

    llm_fields = extract_by_llm(text)
    if "error" in llm_fields:
        # LLM 失败，返回正则结果
        return {
            "method_used": "regex",
            "fields": regex_fields,
            "field_count": regex_count,
            "regex_fields": regex_fields,
            "error": f"正则提取字段不足，LLM 提取也失败: {llm_fields['error']}",
        }

    # 合并：LLM 结果为基础，正则结果覆盖（正则更精确）
    merged = dict(llm_fields)
    for k, v in regex_fields.items():
        if v and k != "taxpayer_id":  # taxpayer_id 是自动补充的，不覆盖
            merged[k] = v

    # 确保 taxpayer_id 存在
    if "credit_code" in merged and "taxpayer_id" not in merged:
        merged["taxpayer_id"] = merged["credit_code"]

    return {
        "method_used": "regex+llm",
        "fields": merged,
        "field_count": count_extracted_fields(merged),
        "regex_fields": regex_fields,
        "llm_fields": llm_fields,
        "error": None,
    }
