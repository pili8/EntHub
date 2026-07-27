"""Normalization and cleaning utilities for company data."""
import re
import os
from datetime import datetime

# Sentinel value used by Tianyancha exports for empty fields
NULL_MARKERS = {"-", "——", "--", "无", "N/A", "n/a", "null", "NULL", "nan", "NaN", "NAN", ""}


def is_null_value(val):
    """Check if a value should be treated as empty."""
    if val is None:
        return True
    return str(val).strip() in NULL_MARKERS


def clean_val(val):
    """Convert NaN/None/'-' to empty string."""
    if val is None:
        return ""
    s = str(val).strip()
    if s in NULL_MARKERS:
        return ""
    return s


def normalize_name(name):
    """Normalize a company name for dedup matching."""
    if not name:
        return ""
    name = str(name).strip()
    name = name.replace("(", "\uff08").replace(")", "\uff09")
    name = re.sub(r"[\s\u3000]+", "", name)
    return name.lower()


def normalize_phone(phone):
    """Normalize a phone number.
    
    Rules:
    - Mobile: strip +86/86 prefix
    - Landline with 0817 area code (Nanchong): remove 0817
    - Extension: keep format XXXXXXX-XXX or XXXXXXXX-XXX (dash preserved)
    - Other area codes (0571, 010, etc.): keep full, strip internal dashes
    - Non-area-code: keep as-is (7-8 digit landline)
    
    Examples:
        +86 13800138000  →  13800138000
        0817-3350888     →  3350888
        0817-3350888-356 →  3350888-356
        3350888          →  3350888
        3350888-356      →  3350888-356
        0571-88889999    →  057188889999
        010-12345678     →  01012345678
    """
    if not phone:
        return ""

    s = str(phone).strip()
    # 含分隔符的输入直接拒绝（应该在外层拆分）
    if any(ch in s for ch in [';', '；', ',', '，']):
        return ""
    
    # Detect extension: XXXXXXX-XXX or XXXXXXXX-XXX format
    # Matches: 7-8 digit main number followed by dash and 1-6 digit extension
    ext_match = re.search(r'(\d{7,8})-(\d{1,6})$', s)
    extension = ""
    if ext_match:
        main_part = s[:ext_match.start()] + ext_match.group(1)
        extension = "-" + ext_match.group(2)
    else:
        main_part = s
    
    # Extract digits from main part only
    digits = re.sub(r"\D", "", main_part)
    
    # Mobile: strip 86 prefix (when length > 11)
    if digits.startswith("86") and len(digits) > 11:
        digits = digits[2:]
    
    # Nanchong landline: strip 0817 area code
    if digits.startswith("0817"):
        digits = digits[4:]

    # 防御：归一化后超过 15 位（手机+分机极限）视为无效
    if len(digits) > 15:
        return ""

    return digits + extension


def normalize_person_name(name):
    """Normalize a person name: trim, full-width to half-width."""
    import unicodedata
    if not name:
        return ""
    s = str(name).strip()
    # 全角转半角（NFKC 同时规范化 Unicode 兼容字符）
    s = unicodedata.normalize('NFKC', s)
    s = s.replace("\u3000", " ").strip()  # 全角空格处理
    return s


def normalize_email(email):
    """Normalize email: lowercase and strip."""
    if not email:
        return ""
    return str(email).strip().lower()


def normalize_credit_code(code):
    """Normalize credit code: uppercase, strip whitespace."""
    if not code:
        return ""
    return str(code).strip().upper()


# ── Layer 1: exact-match aliases ──────────────────────────────────────────────
COLUMN_ALIASES = {
    # 公司名称
    "公司名称": "name", "公司": "name",
    "企业名称": "name", "企业": "name",
    "单位名称": "name", "单位": "name",
    "name": "name", "company": "name", "company_name": "name",
    "enterprise_name": "name", "enterprise": "name",
    # 电话
    "电话": "phone", "联系电话": "phone",
    "手机": "phone", "联系人电话": "phone", "座机": "phone",
    "phone": "phone", "tel": "phone", "telephone": "phone",
    "mobile": "phone", "contact": "phone",
    # 其他电话
    "其他电话": "other_phone", "other_phone": "other_phone",
    # 地址
    "地址": "address", "住址": "address",
    "公司地址": "address", "企业地址": "address",
    "注册地址": "address", "单位地址": "address",
    "address": "address", "addr": "address", "location": "address",
    # 最新年报地址
    "最新年报地址": "annual_report_address",
    "annual_report_address": "annual_report_address",
    # 通信地址
    "通信地址": "mailing_address",
    # 统一社会信用代码
    "统一社会信用代码": "credit_code", "信用代码": "credit_code",
    "统一信用代码": "credit_code",
    "credit_code": "credit_code", "creditcode": "credit_code",
    "usci": "credit_code",
    # 纳税人识别号
    "纳税人识别号": "taxpayer_id", "taxpayer_id": "taxpayer_id",
    # 注册号
    "注册号": "registration_no", "registration_no": "registration_no",
    # 组织机构代码
    "组织机构代码": "org_code", "org_code": "org_code",
    # 法定代表人
    "法定代表人": "legal_person", "legal_person": "legal_person",
    # 注册资本
    "注册资本": "registered_capital", "注册资金": "registered_capital",
    "registered_capital": "registered_capital",
    # 实缴资本
    "实缴资本": "paid_capital", "paid_capital": "paid_capital",
    # 成立日期
    "成立日期": "established_date",
    "established_date": "established_date",
    # 核准日期
    "核准日期": "approved_date", "approved_date": "approved_date",
    # 营业期限
    "营业期限": "business_term", "business_term": "business_term",
    # 省份
    "所属省份": "province", "省份": "province", "province": "province",
    # 城市
    "所属城市": "city", "市": "city", "city": "city",
    # 区县
    "所属区县": "district", "区县": "district", "district": "district",
    # 参保人数
    "参保人数": "insured_count", "社保人数": "insured_count",
    "insured_count": "insured_count",
    # 公司类型
    "公司类型": "company_type", "企业类型": "company_type",
    "企业(机构)类型": "company_type", "company_type": "company_type",
    # 所属行业
    "所属行业": "industry", "industry": "industry",
    # 曾用名
    "曾用名": "former_name", "former_name": "former_name",
    # 网址
    "网址": "website", "website": "website", "url": "website",
    # 邮箱
    "邮箱": "email", "email": "email",
    # 其他邮箱
    "其他邮箱": "other_email", "other_email": "other_email",
    # 经营范围
    "经营范围": "business_scope", "business_scope": "business_scope",
    # 登记状态 / 经营状态
    "登记状态": "business_status", "经营状态": "business_status",
    "存续状态": "business_status", "business_status": "business_status",
    # 企业规模 (新增)
    "企业规模": "enterprise_scale",
    # 股东 (新增)
    "股东": "shareholders",
    # 英文名 (新增)
    "英文名": "english_name",
    # 标签 (新增)
    "标签": "tags",
}

# ── Layer 2: contains-match for long / decorated column names ─────────────────
# Order matters: first match wins. More specific patterns come first.
CONTAINS_ALIASES = [
    # 公司名称类（模糊匹配）
    ("公司名", "name"),
    ("企业名", "name"),
    ("单位名", "name"),
    # 电话类（长名称先匹配）
    ("联系电话（工商信息", "phone"),
    ("联系电话", "phone"),
    # 地址类
    ("地址（最新地址）", "annual_report_address"),
    ("地址（工商信息）", "address"),
    ("最新年报地址", "annual_report_address"),
    ("最新地址", "annual_report_address"),
    # 邮箱类
    ("邮箱（工商信息）", "email"),
    ("邮箱（企业认证", "other_email"),
]

# ── Secondary phone columns (启信宝: 联系电话2~10) ────────────────────────────
SECONDARY_PHONE_PATTERN = re.compile(r"^联系电话\d+$")

# ── Recommended phone column ──────────────────────────────────────────────────
RECOMMENDED_PHONE_KEYS = {"推荐电话"}


def map_columns(df_columns):
    """Three-layer column matching.

    Returns:
        mapping:   {df_col: db_field}    — standard matched columns
        secondary: [df_col, ...]          — secondary phone columns
        recommended: [df_col, ...]        — recommended phone columns
        unmatched: [df_col, ...]          — could not match
    """
    mapping = {}
    secondary = []
    recommended = []
    unmatched = []

    for col in df_columns:
        col_str = str(col).strip()
        col_lower = col_str.lower()

        # Layer 1: exact match
        if col_lower in COLUMN_ALIASES:
            mapping[col] = COLUMN_ALIASES[col_lower]
            continue

        # Layer 2: secondary phone check (before contains, to prevent "联系电话2" matching "联系电话")
        if SECONDARY_PHONE_PATTERN.match(col_str):
            secondary.append(col)
            continue

        # Layer 3: recommended phone
        if col_str in RECOMMENDED_PHONE_KEYS:
            recommended.append(col)
            continue

        # Layer 4: contains match
        matched = False
        for keyword, field in CONTAINS_ALIASES:
            if keyword in col_str:
                mapping[col] = field
                matched = True
                break
        if matched:
            continue

        unmatched.append(col)

    return mapping, secondary, recommended, unmatched


# ── Industrial park file detection ────────────────────────────────────────────
_INDUSTRIAL_KEYWORDS = {"工业园", "服务商", "使用的板块", "客户情况描述", "合作客户"}
_COMPANY_KEYWORDS = {"统一社会信用代码", "法定代表人", "注册资本", "登记状态", "经营状态",
                     "参保人数", "社保人数", "信用代码"}


def is_industrial_park_file(headers):
    """Return True if headers look like an industrial-park / sales tracker file."""
    header_set = set(str(h).strip() for h in headers)
    has_industrial = bool(header_set & _INDUSTRIAL_KEYWORDS)
    has_company = bool(header_set & _COMPANY_KEYWORDS)
    return has_industrial and not has_company


# ── Date extraction from filename ─────────────────────────────────────────────
# Patterns: 20250103, 202412, 2025-01-03
_RE_DATE_FULL = re.compile(r"(20\d{2})(\d{2})(\d{2})")
_RE_DATE_YM = re.compile(r"(20\d{2})(\d{2})")
_RE_DATE_DASH = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")


def extract_date_from_filename(filename):
    """Try to extract a date from filename. Returns 'YYYY-MM-DD' or None."""
    base = os.path.splitext(os.path.basename(filename))[0]

    # 2025-01-03
    m = _RE_DATE_DASH.search(base)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 20250103
    m = _RE_DATE_FULL.search(base)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # 202412 (year-month only, day defaults to 01)
    m = _RE_DATE_YM.search(base)
    if m:
        return f"{m.group(1)}-{m.group(2)}-01"

    return None


def get_file_date(filepath):
    """Get file date: try filename first, then file modification time.
    Returns 'YYYY-MM-DD' or None.
    """
    # Try filename first
    date_str = extract_date_from_filename(filepath)
    if date_str:
        return date_str
    
    # Fallback to file modification time
    try:
        mtime = os.path.getmtime(filepath)
        return datetime.fromtimestamp(mtime).strftime('%Y-%m-%d')
    except:
        return None


# ── 手机号归属地查询 ──────────────────────────────────────────────────────────

# 延迟初始化 Phone 实例（避免 import 时加载 4MB dat）
_phone_lookup = None


def _get_phone_lookup():
    global _phone_lookup
    if _phone_lookup is None:
        try:
            from phone import Phone
            _phone_lookup = Phone()
        except ImportError:
            _phone_lookup = False  # 标记为不可用
    return _phone_lookup


def phone_location(phone_str):
    """查询手机号归属地。

    返回 dict: {province, city, carrier, area_code} 或 None（查不到/非手机号）。

    仅支持 11 位手机号（13x~19x）。座机/400/800 返回 None。
    """
    if not phone_str:
        return None

    digits = re.sub(r'\D', '', str(phone_str))

    # 去掉 +86 / 86 前缀
    if digits.startswith('86') and len(digits) > 11:
        digits = digits[2:]

    # 仅 11 位手机号能查
    if len(digits) != 11 or not digits.startswith('1'):
        return None

    lookup = _get_phone_lookup()
    if not lookup:
        return None

    try:
        result = lookup.find(digits[:7])
        if result:
            return {
                "province": result.get("province", ""),
                "city": result.get("city", ""),
                "carrier": result.get("phone_type", ""),
                "area_code": result.get("area_code", ""),
            }
    except Exception:
        pass
    return None


def phone_location_str(phone_str):
    """返回归属地简短字符串，如 '四川 南充 移动'。查不到返回空串。"""
    loc = phone_location(phone_str)
    if not loc:
        return ""
    parts = []
    if loc["province"]:
        parts.append(loc["province"])
    if loc["city"] and loc["city"] != loc["province"]:
        parts.append(loc["city"])
    if loc["carrier"]:
        parts.append(loc["carrier"])
    return " ".join(parts)


# ── 电话标记查询辅助 ──────────────────────────────────────────────────────────

def get_phone_tags(db, normalized_phone):
    """查询某个号码的电话标记（单标签模式）。

    返回 {id, name, color} 或 None。
    """
    if not normalized_phone:
        return None
    row = db.execute("""
        SELECT t.id, t.name, t.color
        FROM phone_tags t
        JOIN phone_tag_map m ON m.tag_id = t.id
        WHERE m.normalized_phone = ?
    """, [normalized_phone]).fetchone()
    return dict(row) if row else None


def get_phone_tags_batch(db, normalized_phones):
    """批量查询多个号码的电话标记（单标签模式）。

    返回 {normalized_phone: {id, name, color}, ...}
    没有标签的号码不在 dict 中。
    """
    if not normalized_phones:
        return {}
    # 去重 + 过滤空
    unique_norms = list(set(n for n in normalized_phones if n))
    if not unique_norms:
        return {}

    placeholders = ",".join(["?"] * len(unique_norms))
    rows = db.execute(f"""
        SELECT m.normalized_phone, t.id, t.name, t.color
        FROM phone_tag_map m
        JOIN phone_tags t ON m.tag_id = t.id
        WHERE m.normalized_phone IN ({placeholders})
    """, unique_norms).fetchall()

    return {r["normalized_phone"]: {"id": r["id"], "name": r["name"], "color": r["color"]} for r in rows}
