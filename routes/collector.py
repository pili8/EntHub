"""网页采集模块：从网页链接或文字中提取公司信息，写入飞书多维表格。

功能保留 collector 原有设计：
- 多源输入：支持粘贴网页链接或直接粘贴文字，混合输入自动识别
- 收集表采集：通过飞书表单收集链接或文字，一键批量采集处理
- 自动采集：按设定间隔自动从收集表采集未处理的内容，支持暂停/恢复
- 智能提取：大模型自动识别文章中的公司名称、联系方式、地址、招聘岗位等结构化信息
- 飞书写入：提取结果自动写入飞书多维表格，支持去重（链接去重 + 公司名电话组合去重）
- 实时反馈：采集各阶段进度通过 SSE 实时推送到页面

LLM 配置复用 EntHub 的 settings 表配置（base_url / api_key / model）。
飞书配置使用 feishu_service（下沉到核心层的公共服务）。
采集提示词保留 collector 原有的编辑功能。
"""
import json
import re
import time
import html
import datetime
import threading
import urllib.request
from collections import Counter

from flask import render_template, request, jsonify, Response, stream_with_context

from feishu_service import (
    get_feishu_config, is_feishu_ready,
    get_token, get_existing_records, add_record,
    get_pending_entries, mark_url_collected, create_table_fields,
    test_feishu_connection,
)
from config import (get_llm_config, load_config, save_config,
                    list_llm_models, get_module_model_id)
from utils import (normalize_name, normalize_phone, validate_phone,
                   extract_phones, extract_contacts, build_phone_note)
from data_helpers import split_phones
from db import get_db
from ._base import make_bp

bp = make_bp("collector")

MODULE_META = {
    "id": "collector",
    "name": "网页采集",
    "name_short": "采集",
    "icon": "globe",
    "url": "/collector",
    "group": "ops",
    "order": 30,
    "desktop_order": 80,
    "core": False,
    "risk": "warn",
    "impact": "从网页链接或文字提取公司信息并写入飞书和本地线索库。停用后采集入口、动态线索、电话招聘标记全部消失，数据保留。",
    "show_in": ["drawer", "desktop"],
    # 采集执行与采集记录同属一个页面，用页面内视图切换（?view=records）区分，
    # 不再各占一个导航入口（/collector/leads 保留为兼容入口）。
    "tables": [
        """CREATE TABLE IF NOT EXISTS collector_leads (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            source_url      TEXT,
            source_author   TEXT,
            source_date     TEXT,
            article_title   TEXT,
            article_summary TEXT,
            company_name    TEXT,
            normalized_name TEXT,
            company_id      INTEGER,
            lead_type       TEXT,
            contact_person  TEXT,
            phone           TEXT,
            normalized_phone TEXT,
            address         TEXT,
            summary         TEXT,
            extract_status  TEXT DEFAULT 'ok',
            error_msg       TEXT,
            collected_at    TEXT DEFAULT (datetime('now', 'localtime'))
        )""",
        "CREATE INDEX IF NOT EXISTS idx_cl_company_id ON collector_leads(company_id)",
        "CREATE INDEX IF NOT EXISTS idx_cl_norm_name ON collector_leads(normalized_name)",
        "CREATE INDEX IF NOT EXISTS idx_cl_collected_at ON collector_leads(collected_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_cl_extract_status ON collector_leads(extract_status)",
    ],
}


# ──────────────────────────────────────────────
# 自动采集状态
# ──────────────────────────────────────────────
_auto_lock = threading.Lock()
_auto_scheduler_thread = None
_auto_state = {
    "enabled": False,
    "interval": 30,
    "last_run": None,
    "last_count": 0,
    "last_errors": [],
    "running": False,
    "progress_current": 0,
    "progress_total": 0,
    "progress_msg": "",
}


# ──────────────────────────────────────────────
# 默认提示词
# ──────────────────────────────────────────────

SYSTEM_PROMPT = "你是结构化信息提取助手，只输出合法JSON数组，不要输出其他任何内容。"

EXTRACT_PROMPT = """你是一个信息提取助手。从以下文本中提取所有公司信息。

规则：
1. 文中提到的每家公司都要提取，不管有没有联系方式
2. 每家公司对应一条记录，字段：类型、公司名、联系人、电话、地址、摘要
3. 类型：判断文章类型，只能是以下之一：招聘、新闻、公司宣传、政策、其他
4. 同一家公司的多个联系人/电话合并到一条记录中，用空格分隔
5. 摘要：
   - 如果是招聘类：以「招聘」开头，然后列出该公司招聘的所有岗位名称，用顿号分隔。财务、会计、出纳、ERP相关岗位放在最前面。例如：「招聘财务、会计、出纳、销售」
   - 如果是非招聘类：30字以内，提取该公司的关键信息，重点关注：业务类型、数字化/信息化程度、生产规模、ERP需求相关的内容
6. 如果某个字段找不到，填空字符串
7. 严格只返回 JSON 数组，不要有其他文字

返回格式：
[{{"类型": "xxx", "公司名": "xxx", "联系人": "xxx", "电话": "xxx", "地址": "xxx", "摘要": "xxx"}}, ...]

文本内容：
{text}"""


# ──────────────────────────────────────────────
# 采集提示词配置（存 settings 表 config.collector_prompts）
# ──────────────────────────────────────────────

def get_collector_prompts():
    """获取采集模块的提示词配置。"""
    config = load_config()
    prompts = config.get("collector_prompts", {})
    return {
        "system_prompt": prompts.get("system_prompt", SYSTEM_PROMPT),
        "user_prompt": prompts.get("user_prompt", EXTRACT_PROMPT),
    }


def save_collector_prompts(system_prompt, user_prompt):
    """保存采集模块的提示词配置。"""
    config = load_config()
    config["collector_prompts"] = {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
    }
    save_config(config)


def get_collector_auto_config():
    """获取自动采集配置。"""
    config = load_config()
    ac = config.get("collector_auto", {})
    return {
        "enabled": ac.get("enabled", False),
        "interval": ac.get("interval", 30),
    }


def save_collector_auto_config(enabled=None, interval=None):
    """保存自动采集配置。"""
    config = load_config()
    if "collector_auto" not in config:
        config["collector_auto"] = {}
    if enabled is not None:
        config["collector_auto"]["enabled"] = bool(enabled)
    if interval is not None:
        config["collector_auto"]["interval"] = max(5, min(1440, int(interval)))
    save_config(config)
    return config["collector_auto"]


def get_sync_to_feishu():
    """是否同步到飞书（默认 True，保持向后兼容）。"""
    config = load_config()
    return config.get("collector_sync_feishu", True)


def save_sync_to_feishu(enabled):
    """保存飞书同步开关。"""
    config = load_config()
    config["collector_sync_feishu"] = bool(enabled)
    save_config(config)


# ──────────────────────────────────────────────
# 文章抓取
# ──────────────────────────────────────────────

import ssl as _ssl

# 某些网络环境（公司防火墙、代理网关）使用自签证书，
# 与 LLM 调用和飞书 API 保持一致：跳过证书验证。
_ssl_ctx = _ssl.create_default_context()
_ssl_ctx.check_hostname = False
_ssl_ctx.verify_mode = _ssl.CERT_NONE


def fetch_article(url, max_retries=2):
    """抓取网页 HTML，带重试。"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }
    last_error = None
    for attempt in range(max_retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=15, context=_ssl_ctx) as resp:
                return resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            last_error = e
            print(f"[抓取] 失败（第{attempt+1}次）: {url} - {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
    raise last_error


def is_wechat_url(url):
    """判断是否微信公众号链接。"""
    return "mp.weixin.qq.com" in url


def is_trafilatura_available():
    """检查 trafilatura 是否已安装。"""
    try:
        import trafilatura  # noqa: F401
        return True
    except ImportError:
        return False


def parse_generic_article(url, raw_html):
    """通用网页正文提取（用 trafilatura）。"""
    title = ""
    for pattern in [
        r'property="og:title"\s+content="([^"]+)"',
        r'name="title"\s+content="([^"]+)"',
        r'<title>([^<]+)</title>',
    ]:
        m = re.search(pattern, raw_html)
        if m and m.group(1).strip():
            title = html.unescape(m.group(1).strip())
            break

    body = ""
    try:
        import trafilatura
        body = trafilatura.extract(raw_html, include_comments=False, include_tables=True) or ""
    except ImportError:
        # 缺库时 body 为空，调用方会报 "无法获取文章内容"
        # 但不会崩，微信公众号文章走 parse_article 不受影响
        pass

    return {"title": title, "author": "", "date": "", "body": body}


def parse_article(raw_html):
    """微信公众号文章专用解析。"""
    # 标题
    title = ""
    for pattern in [
        r'msg_title\s*=\s*["\x27](.*?)["\x27]',
        r'property="og:title"\s+content="([^"]+)"',
        r'id="activity-name"[^>]*>([^<]+)',
    ]:
        m = re.search(pattern, raw_html)
        if m and m.group(1).strip():
            title = html.unescape(m.group(1).strip())
            break

    # 公众号名称
    author = ""
    for pattern in [
        r'id="js_name"[^>]*>([^<]+)',
        r'nickname\s*=\s*["\x27](.*?)["\x27]',
    ]:
        m = re.search(pattern, raw_html)
        if m and m.group(1).strip():
            author = html.unescape(m.group(1).strip())
            break

    # 发布时间
    date_m = re.search(r'var\s+ct\s*=\s*"?(\d+)"?', raw_html)
    date_str = ""
    if date_m:
        date_str = datetime.datetime.fromtimestamp(int(date_m.group(1))).strftime("%Y-%m-%d %H:%M")

    # 正文
    body = ""
    body_m = re.search(r'id="js_content"[^>]*>(.*?)</div>\s*<script', raw_html, re.DOTALL)
    if body_m:
        body = body_m.group(1)
        body = re.sub(r'<[^>]+>', ' ', body)
        body = re.sub(r'&nbsp;', ' ', body)
        body = re.sub(r'\s+', ' ', body).strip()
        body = html.unescape(body)

    return {"title": title, "author": author, "date": date_str, "body": body}


# ──────────────────────────────────────────────
# 信息提取（LLM）
# ──────────────────────────────────────────────

def _get_llm_config():
    """读取采集功能使用的大模型连接信息。

    传 "collector" 让采集可以单独指定模型（在采集页选），没选就跟随全局默认。
    """
    llm = get_llm_config("collector")
    return {
        "base_url": llm.get("base_url", "").rstrip("/"),
        "api_key": llm.get("api_key", ""),
        "model": llm.get("model", "gpt-4o-mini"),
        "timeout": llm.get("timeout", 120),
        "enabled": llm.get("enabled", False),
    }


def should_skip_llm(title, text):
    """预筛：判断文章是否值得调用 LLM 提取公司信息。"""
    full = (title or "") + (text or "")
    company_keywords = ["公司", "有限", "集团", "企业", "工厂", "厂商"]
    if not any(kw in full for kw in company_keywords):
        return True
    return False


def extract_info(title, text, max_retries=2):
    """用 LLM 从文本中提取公司信息列表，失败自动重试。

    使用 collector 自己的提示词（从 settings 表读取）。
    """
    llm = _get_llm_config()
    if not llm["enabled"] or not llm["api_key"] or not llm["base_url"]:
        raise Exception("LLM API 未配置或未启用，请到设置页面配置大模型 API")

    full_text = f"标题。{title}\n\n{text}" if title else text

    prompts = get_collector_prompts()
    system_prompt = prompts["system_prompt"]
    user_prompt = prompts["user_prompt"]

    timeout = llm["timeout"] or 120
    url = f"{llm['base_url']}/chat/completions"
    payload = json.dumps({
        "model": llm["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt.format(text=full_text)}
        ],
        "temperature": 0.1,
        "max_tokens": 2048,
    }, ensure_ascii=False).encode("utf-8")

    import ssl
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    last_error = None
    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Authorization", f"Bearer {llm['api_key']}")

            with urllib.request.urlopen(req, timeout=timeout, context=ssl_ctx) as resp:
                body = json.loads(resp.read().decode("utf-8"))

            content = body["choices"][0]["message"]["content"].strip()
            records = parse_llm_json(content)
            return [r for r in records if r.get("公司名", "").strip()]
        except urllib.error.HTTPError as e:
            err_body = ""
            try:
                err_body = e.read().decode("utf-8", errors="replace")[:500]
            except Exception:
                pass
            last_error = Exception(f"LLM API 错误: HTTP {e.code} {err_body[:200]}")
            print(f"[LLM] API错误（第{attempt+1}次）: {e.code}")
            if e.code >= 500 and attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise last_error
        except Exception as e:
            last_error = e
            print(f"[LLM] 错误（第{attempt+1}次）: {e}")
            if attempt < max_retries - 1:
                time.sleep(3 * (attempt + 1))
                continue

    raise Exception(f"LLM 提取失败（已重试{max_retries}次）: {last_error}")


def parse_llm_json(content):
    """从 LLM 返回中提取 JSON 数组，兼容各种不规范格式。"""
    # 去掉 markdown 代码块
    content = re.sub(r'```(?:json)?\s*', '', content)
    content = re.sub(r'```', '', content)
    content = content.strip()

    # 尝试提取 JSON 数组
    json_match = re.search(r'\[.*\]', content, re.DOTALL)
    if not json_match:
        raise Exception(f"LLM 返回格式异常: {content}")

    raw = json_match.group()

    # 第一次尝试：直接解析
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # 第二次尝试：清理常见问题后解析
    cleaned = raw
    cleaned = cleaned.replace("'", '"')
    cleaned = re.sub(r',\s*([\]}])', r'\1', cleaned)
    cleaned = re.sub(r'//.*?\n', '\n', cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise Exception(f"LLM 返回 JSON 解析失败: {e}\n原始内容: {raw[:500]}")


# ──────────────────────────────────────────────
# 记录写入（本地优先 + 可选飞书同步）
# ──────────────────────────────────────────────

def save_records(records, source_url, token, fc, existing_links, existing_company_phone,
                source_author="", source_date="", article_title="", article_summary="",
                sync_feishu=True):
    """先写本地 collector_leads，再写飞书（可选）。

    飞书失败不影响本地写入。返回 (saved_count, skipped_count, error_list)。
    """
    saved = 0
    skipped = 0
    errors = []

    # 本地数据库连接（独立于 Flask g.db，SSE 场景需自己管理）
    local_db = None
    try:
        local_db = get_db()
    except Exception as e:
        print(f"[本地线索] 数据库连接失败: {e}")

    try:
        for rec in records:
            rec = dict(rec)
            rec["来源链接"] = source_url
            rec["采集时间"] = int(time.time()) * 1000
            rec["来源"] = source_author or rec.get("来源", "")
            rec["发布日期"] = source_date or rec.get("发布日期", "")

            company = rec.get("公司名", "").strip()
            phone = rec.get("电话", "").strip()
            lead_type = rec.get("类型", "").strip()
            contact_person = rec.get("联系人", "").strip()
            address = rec.get("地址", "").strip()
            summary = rec.get("摘要", "").strip()

            if company and (company, phone) in existing_company_phone:
                skipped += 1
                continue

            # ── 写本地 collector_leads（主） ──
            lead_id = None
            if local_db:
                try:
                    norm_name = normalize_name(company) if company else ""
                    # 尝试匹配本地公司
                    company_id = None
                    if norm_name:
                        row = local_db.execute(
                            "SELECT id FROM companies WHERE normalized_name = ? LIMIT 1",
                            [norm_name]
                        ).fetchone()
                        if row:
                            company_id = row["id"]

                    # 单值归一化：多号码时取第一个，保证 normalized_phone 列仍可被 SQL 匹配
                    norm_phone = normalize_phone(phone) if phone else ""
                    if not norm_phone and phone:
                        _first = extract_phones(phone)
                        norm_phone = normalize_phone(_first[0]) if _first else ""

                    cur = local_db.execute("""
                        INSERT INTO collector_leads
                            (source_url, source_author, source_date,
                             article_title, article_summary,
                             company_name, normalized_name, company_id,
                             lead_type, contact_person, phone, normalized_phone,
                             address, summary, extract_status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok')
                    """, [source_url, source_author, source_date,
                           article_title or "", article_summary or "",
                           company, norm_name, company_id,
                           lead_type, contact_person, phone, norm_phone,
                           address, summary])
                    lead_id = cur.lastrowid

                    # 只要有电话且匹配到本地公司，所有号码都入电话表（不再限招聘类）
                    if company_id and phone:
                        _attach_lead_phones(local_db, company_id, phone,
                                            contact_person, lead_id, lead_type)

                    local_db.commit()
                    print(f"[本地线索] 写入: {company or '?'} -> lead #{lead_id}")
                    # 回写 company_id 到 rec，供前端跳转详情
                    if company_id:
                        rec["_company_id"] = company_id
                        rec["_lead_id"] = lead_id
                except Exception as e:
                    print(f"[本地线索] 写入失败: {e}")
                    try:
                        local_db.rollback()
                    except Exception:
                        pass

            # ── 写飞书（可选备份） ──
            if sync_feishu and token and fc and fc.get("app_token") and fc.get("collect_table_id"):
                fields = {}
                for k, v in rec.items():
                    if k == "来源链接":
                        fields[k] = {"link": v, "text": v}
                    else:
                        fields[k] = v
                try:
                    print(f"[同步飞书] {company or '?'} -> {json.dumps(fields, ensure_ascii=False)[:200]}")
                    add_record(token, fc["app_token"], fc["collect_table_id"], fields)
                    print(f"[同步成功] {company or '?'}")
                except Exception as e:
                    print(f"[同步失败] {company or '?'}: {e}")
                    errors.append(f"飞书同步失败({company or '?'}): {e}")

            # 本地写入成功就算成功
            if company:
                existing_company_phone.add((company, phone))
            existing_links.add(source_url)
            saved += 1
    finally:
        if local_db:
            try:
                local_db.close()
            except Exception:
                pass

    return saved, skipped, errors


def _merge_phone_note(db, norm_phone, contact, lead_type):
    """把「联系人（来源类型）」并入号码备注：追加而非覆盖，已含则不重复写。

    备注落在 phone_wechat.note（详情页电话行显示的那条说明）。
    采集**只写备注**：微信名一律留「无微信」占位，不把联系人当成微信号填进去。
    """
    token = build_phone_note(contact, lead_type)
    if not norm_phone or not token:
        return False

    row = db.execute(
        "SELECT note FROM phone_wechat WHERE normalized_phone = ?", [norm_phone]
    ).fetchone()

    if row is None:
        db.execute(
            "INSERT INTO phone_wechat (normalized_phone, wechat_name, note) VALUES (?, ?, ?)",
            [norm_phone, "无微信", token])
        return True

    note = (row["note"] or "").strip()
    if token in note:
        return False
    merged = f"{note} {token}".strip() if note else token
    db.execute(
        "UPDATE phone_wechat SET note = ?, updated_at = datetime('now', 'localtime') "
        "WHERE normalized_phone = ?", [merged, norm_phone])
    return True


def _ensure_phone_in_company(db, company_id, phone, norm_phone, lead_id,
                             contact="", lead_type=""):
    """把采集到的号码并入公司电话表，并补齐来源标记与备注（不覆盖已有内容）。

    返回 'added'（新号码）/ 'linked'（补上了来源标记）/ 'exists'（已齐全）
        / None（号码无效）
    """
    is_valid, _, _ = validate_phone(norm_phone)
    if not is_valid:
        return None

    existing = db.execute(
        "SELECT id, collector_lead_id FROM company_phones "
        "WHERE company_id = ? AND normalized_phone = ?",
        [company_id, norm_phone]
    ).fetchone()

    if existing:
        action = "exists"
        # 已有号码：只补空的来源标记，不动其它字段
        if not existing["collector_lead_id"]:
            db.execute(
                "UPDATE company_phones SET collector_lead_id = ? WHERE id = ?",
                [lead_id, existing["id"]])
            action = "linked"
    else:
        db.execute(
            "INSERT INTO company_phones (company_id, phone, normalized_phone, is_primary, collector_lead_id) "
            "VALUES (?, ?, ?, 0, ?)",
            [company_id, phone, norm_phone, lead_id])
        action = "added"

    _merge_phone_note(db, norm_phone, contact, lead_type)
    return action


def _split_phone_field(phone_text):
    """把采集的「电话」字段拆成 [(显示号, 归一化号), ...]，支持一条里多个号码。

    先用 data_helpers.split_phones（按标点/空格拆，也能识别「139 8485 2931」
    这类分段写法的单个号码）；拆不出来时再用正则从混合文本里扫描兜底。
    显示号会剥掉人名等非号码字符（「薛女士 19508335677」→「19508335677」）。
    """
    if not phone_text:
        return []

    pairs = split_phones(phone_text)
    if not pairs:
        pairs = [(p, normalize_phone(p)) for p in extract_phones(phone_text)]

    out, seen = [], set()
    for raw, norm in pairs:
        if not norm or norm in seen:
            continue
        seen.add(norm)
        clean = re.sub(r'[^\d\-]', '', str(raw)) or norm
        out.append((clean, norm))
    return out


def _attach_lead_phones(db, company_id, phone_text, contact_text, lead_id,
                        lead_type, stats=None):
    """把一个 lead 的电话字段（可能含多个号码）拆开逐个并入公司电话表。

    联系人按顺序与号码配对；只有一个联系人时套用到所有号码。
    """
    phones = _split_phone_field(phone_text)
    if not phones:
        return
    contacts = extract_contacts(contact_text)

    for idx, (raw, norm) in enumerate(phones):
        if idx < len(contacts):
            contact = contacts[idx]
        elif contacts:
            contact = contacts[0]
        else:
            contact = ""
        action = _ensure_phone_in_company(
            db, company_id, raw, norm, lead_id,
            contact=contact, lead_type=lead_type)
        if stats is not None and action:
            stats[action] = stats.get(action, 0) + 1


def save_failed_lead(source_url, source_author="", source_date="",
                      article_title="", article_body="", error_msg=""):
    """LLM 提取失败时，把原始内容存入 collector_leads，标记提取失败。

    这样大模型恢复后可以重跑这些记录。
    """
    # 截取正文前 500 字做摘要，太长没意义
    body_snippet = (article_body or "")[:500]
    try:
        db = get_db()
        try:
            db.execute("""
                INSERT INTO collector_leads
                    (source_url, source_author, source_date,
                     article_title, article_summary,
                     extract_status, error_msg)
                VALUES (?, ?, ?, ?, ?, 'failed', ?)
            """, [source_url, source_author, source_date,
                   article_title or "", body_snippet,
                   error_msg[:500]])
            db.commit()
            print(f"[本地线索] 保存失败记录: {article_title or source_url} -> {error_msg[:100]}")
        finally:
            db.close()
    except Exception as e:
        print(f"[本地线索] 保存失败记录也失败了: {e}")


# ──────────────────────────────────────────────
# 输入分类
# ──────────────────────────────────────────────

def classify_input(lines):
    """将输入分行，判断每行是网址还是文字。"""
    url_pattern = re.compile(
        r'^(https?://)?'
        r'[a-zA-Z0-9]'
        r'[a-zA-Z0-9.-]*\.'
        r'[a-zA-Z]{2,}'
        r'(/.*)?$'
    )
    urls = []
    texts = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if url_pattern.match(line):
            if not line.startswith("http"):
                line = "https://" + line
            urls.append(line)
        else:
            texts.append(line)
    return urls, "\n".join(texts)


# ──────────────────────────────────────────────
# 页面路由
# ──────────────────────────────────────────────

def _records_summary():
    """记录视图角标：文章组数 + 全局失败记录数。"""
    db = get_db()
    try:
        total = db.execute(
            "SELECT COUNT(DISTINCT source_url) FROM collector_leads"
        ).fetchone()[0]
        failed = db.execute(
            "SELECT COUNT(*) FROM collector_leads WHERE extract_status = 'failed'"
        ).fetchone()[0]
    finally:
        db.close()
    return total, failed


def _records_context(page, lead_type, matched, status):
    """采集记录视图上下文：按来源文章分组 + 分页 + 成功/失败计数。"""
    per_page = 20
    offset = (page - 1) * per_page

    # 筛选
    lead_type = request.args.get("type", "")
    matched = request.args.get("matched", "")
    status = request.args.get("status", "")

    where_clauses = []
    params = []
    if lead_type:
        where_clauses.append("lead_type = ?")
        params.append(lead_type)
    if matched == "yes":
        where_clauses.append("company_id IS NOT NULL")
    elif matched == "no":
        where_clauses.append("company_id IS NULL")
    if status == "ok":
        where_clauses.append("extract_status = 'ok'")
    elif status == "failed":
        where_clauses.append("extract_status = 'failed'")

    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    db = get_db()
    try:
        # 清掉指向已删除企业的悬空 company_id（否则记录页会链到 404）
        db.execute("""
            UPDATE collector_leads
            SET company_id = NULL
            WHERE company_id IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM companies c WHERE c.id = collector_leads.company_id)
        """)

        # 方案 C：每次打开页面，自动补关联——对 company_id 为空但 normalized_name 匹配上本地公司的记录回写 company_id
        db.execute("""
            UPDATE collector_leads
            SET company_id = (
                SELECT c.id FROM companies c
                WHERE c.normalized_name = collector_leads.normalized_name
                LIMIT 1
            )
            WHERE company_id IS NULL
              AND normalized_name IS NOT NULL
              AND normalized_name != ''
        """)
        db.commit()

        # 先查分页后的文章组（按 source_url 分组）
        # 状态不再用 MIN(extract_status) 一刀切：同组可能既有成功也有失败，
        # 分别计数交给模板展示，避免"有一条失败整组看起来都失败"。
        group_sql = f"""
            SELECT source_url, MIN(article_title) AS article_title,
                   MIN(source_author) AS source_author, MIN(source_date) AS source_date,
                   MIN(collected_at) AS first_collected_at,
                   MAX(collected_at) AS last_collected_at,
                   COUNT(*) AS lead_count,
                   SUM(CASE WHEN extract_status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
                   SUM(CASE WHEN extract_status = 'failed' THEN 0 ELSE 1 END) AS ok_count
            FROM collector_leads{where_sql}
            GROUP BY source_url
            ORDER BY last_collected_at DESC
            LIMIT ? OFFSET ?
        """
        groups = db.execute(group_sql, params + [per_page, offset]).fetchall()

        # 再查每组下的具体 leads
        total = db.execute(
            f"SELECT COUNT(DISTINCT source_url) FROM collector_leads{where_sql}", params
        ).fetchone()[0]

        leads_by_group = []
        for g in groups:
            group_where = where_clauses[:] if where_clauses else []
            group_params = params[:] + [g["source_url"]]
            group_where.append("source_url = ?")
            group_where_sql = (" WHERE " + " AND ".join(group_where)) if group_where else ""
            group_leads = db.execute(
                f"""SELECT cl.*, c.name AS matched_company_name
                    FROM collector_leads cl
                    LEFT JOIN companies c ON cl.company_id = c.id
                    {group_where_sql}
                    ORDER BY cl.id ASC""",
                group_params
            ).fetchall()

            failed_count = g["failed_count"] or 0
            ok_count = g["ok_count"] or 0
            if failed_count and ok_count:
                state = "partial"
            elif failed_count:
                state = "failed"
            else:
                state = "ok"

            # 组内主类型取众数（招聘类文章里也可能夹带新闻条目）
            type_counter = Counter(
                (l["lead_type"] or "").strip() for l in group_leads
                if (l["lead_type"] or "").strip()
            )
            main_type = type_counter.most_common(1)[0][0] if type_counter else ""

            # 失败提示取该组最后一条失败原因
            error_hint = ""
            if failed_count:
                for l in reversed(group_leads):
                    if (l["extract_status"] or "") == "failed" and l["error_msg"]:
                        error_hint = l["error_msg"]
                        break

            leads_by_group.append({
                "source_url": g["source_url"],
                "article_title": g["article_title"],
                "source_author": g["source_author"],
                "source_date": g["source_date"],
                "state": state,
                "main_type": main_type,
                "first_collected_at": g["first_collected_at"],
                "last_collected_at": g["last_collected_at"],
                "lead_count": g["lead_count"],
                "ok_count": ok_count,
                "failed_count": failed_count,
                "error_hint": error_hint,
                "leads": group_leads,
            })

        # 全局失败记录数（不受当前筛选影响）——「清理全部失败」按钮的计数与显隐
        failed_total = db.execute(
            "SELECT COUNT(*) FROM collector_leads WHERE extract_status = 'failed'"
        ).fetchone()[0]
    finally:
        db.close()

    total_pages = (total + per_page - 1) // per_page

    return dict(
        leads_by_group=leads_by_group,
        page=page,
        total_pages=total_pages,
        total=total,
        failed_total=failed_total,
        current_type=lead_type,
        current_matched=matched,
        current_status=status,
    )


def _render_collector(view="collect"):
    """渲染采集页：view=collect 为采集执行区，view=records 为采集记录区。"""
    auto_config = get_collector_auto_config()
    feishu_ready = is_feishu_ready()
    llm = get_llm_config()
    prompts = get_collector_prompts()
    fc = get_feishu_config()
    trafilatura_ok = is_trafilatura_available()
    sync_feishu = get_sync_to_feishu()
    records_total, records_failed = _records_summary()
    # 采集页的模型选择：默认跟随全局激活模型，也可以在这里单独指定
    llm_models, llm_active_id = list_llm_models()
    llm_module_model = get_module_model_id("collector")

    ctx = dict(
        view=view,
        llm_models=llm_models,
        llm_active_id=llm_active_id,
        llm_module_model=llm_module_model,
        auto_config=auto_config,
        feishu_ready=feishu_ready,
        llm=llm,
        prompts=prompts,
        feishu_config=fc,
        trafilatura_ok=trafilatura_ok,
        sync_feishu=sync_feishu,
        records_total=records_total,
        records_failed=records_failed,
    )
    if view == "records":
        ctx.update(_records_context(
            request.args.get("page", 1, type=int),
            request.args.get("type", ""),
            request.args.get("matched", ""),
            request.args.get("status", ""),
        ))
    return render_template("collector.html", **ctx)


@bp.route("/collector")
def collector_page():
    """采集主页面；view=records 时切到采集记录视图。"""
    view = request.args.get("view", "collect")
    if view not in ("collect", "records"):
        view = "collect"
    return _render_collector(view)


@bp.route("/collector/leads")
def leads_page():
    """采集记录（兼容入口：旧链接与 #lead-<id> 锚点跳转仍指向这里）。"""
    return _render_collector("records")


# ──────────────────────────────────────────────
# 采集记录管理 API（删除 / 重试）
# ──────────────────────────────────────────────

@bp.route("/api/collector/leads/<int:lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    """删除单条采集记录。

    前端只在提取失败的记录上暴露删除入口，接口本身不限定状态
    （清理历史脏数据时也可能需要删掉成功记录）。
    """
    db = get_db()
    try:
        row = db.execute(
            "SELECT id FROM collector_leads WHERE id = ?", [lead_id]
        ).fetchone()
        if not row:
            return jsonify({"success": False, "error": "记录不存在或已被删除"}), 404
        db.execute("DELETE FROM collector_leads WHERE id = ?", [lead_id])
        db.commit()
        return jsonify({"success": True, "deleted": lead_id})
    finally:
        db.close()


@bp.route("/api/collector/leads/purge-failed", methods=["POST"])
def purge_failed_leads():
    """批量删除提取失败的采集记录：带 source_url 清该来源，不带则清全部。"""
    data = request.get_json(silent=True) or {}
    source_url = (data.get("source_url") or "").strip()
    db = get_db()
    try:
        if source_url:
            cur = db.execute(
                "DELETE FROM collector_leads WHERE source_url = ? "
                "AND extract_status = 'failed'", [source_url])
        else:
            cur = db.execute(
                "DELETE FROM collector_leads WHERE extract_status = 'failed'")
        db.commit()
        return jsonify({"success": True, "deleted": cur.rowcount})
    finally:
        db.close()


def _resolve_company_id(db, lead_row):
    """找出该采集记录对应的企业 id，并处理指向已删除企业的脏数据。

    company_id 可能指向已被删除的企业：这种情况先清空，再按公司名重新匹配一次；
    仍匹配不到就返回 None（调用方记为「未匹配企业」并跳过）。
    """
    cid = lead_row["company_id"]
    if cid:
        if db.execute("SELECT 1 FROM companies WHERE id = ?", [cid]).fetchone():
            return cid
        # 企业已被删除：清掉悬空引用，避免外键约束报错
        db.execute("UPDATE collector_leads SET company_id = NULL WHERE id = ?",
                   [lead_row["id"]])

    norm = lead_row["normalized_name"]
    if norm:
        hit = db.execute(
            "SELECT id FROM companies WHERE normalized_name = ? LIMIT 1", [norm]
        ).fetchone()
        if hit:
            db.execute("UPDATE collector_leads SET company_id = ? WHERE id = ?",
                       [hit["id"], lead_row["id"]])
            return hit["id"]
    return None


@bp.route("/api/collector/backfill-phones", methods=["POST"])
def backfill_phones():
    """把历史采集记录里的电话补进企业电话表（含一条记录里的多个号码）。

    用于修复早期版本「多号码串在一起 / 非招聘类」没能入表的情况。
    只做补充：已有号码只补空的来源标记，备注按追加合并，不覆盖用户已有内容。
    """
    db = get_db()
    try:
        rows = db.execute("""
            SELECT id, phone, contact_person, lead_type,
                   company_id, normalized_name
            FROM collector_leads
            WHERE extract_status = 'ok'
              AND phone IS NOT NULL AND phone != ''
            ORDER BY id
        """).fetchall()

        stats = {"added": 0, "linked": 0, "exists": 0}
        scanned = 0
        no_company = 0
        unparsable = 0
        touched = set()

        for r in rows:
            scanned += 1

            company_id = _resolve_company_id(db, r)
            if not company_id:
                no_company += 1
                continue
            if not _split_phone_field(r["phone"]):
                unparsable += 1
                continue

            before = dict(stats)
            _attach_lead_phones(db, company_id, r["phone"], r["contact_person"],
                                r["id"], r["lead_type"], stats=stats)
            if any(stats[k] > before[k] for k in stats):
                touched.add(company_id)

        # 修正历史遗留：早期版本把联系人名写进了「微信名」，采集只该补备注。
        # 只在备注里确实带着「X（」这种采集痕迹时才改，避免误伤手工录入的微信名。
        fixed_wechat = 0
        stale = db.execute("""
            SELECT normalized_phone, wechat_name, note FROM phone_wechat
            WHERE wechat_name IS NOT NULL AND wechat_name NOT IN ('', '无微信')
              AND note IS NOT NULL AND note != ''
        """).fetchall()
        for w in stale:
            if (w["wechat_name"] + "（") in (w["note"] or ""):
                db.execute(
                    "UPDATE phone_wechat SET wechat_name = '无微信', "
                    "updated_at = datetime('now', 'localtime') "
                    "WHERE normalized_phone = ?", [w["normalized_phone"]])
                fixed_wechat += 1

        db.commit()
    finally:
        db.close()

    return jsonify({
        "success": True,
        "scanned": scanned,
        "added": stats["added"],
        "linked": stats["linked"],
        "already": stats["exists"],
        "no_company": no_company,
        "unparsable": unparsable,
        "companies": len(touched),
        "fixed_wechat": fixed_wechat,
    })


@bp.route("/api/collector/leads-by-phone")
def leads_by_phone():
    """查某个号码关联的全部采集记录。

    一个号码可能出现在多条招聘信息里（不同公众号、不同批次），
    详情页点击号码上的来源标记时用它列出全部，避免只能看到其中一条。
    """
    raw = (request.args.get("phone") or "").strip()
    if not raw:
        return jsonify({"success": False, "error": "缺少号码"}), 400
    norm = normalize_phone(raw) or raw

    db = get_db()
    try:
        # SQL 粗筛（一条记录里可能存了多个号码，normalized_phone 只存第一个）
        candidates = db.execute("""
            SELECT id, company_name, normalized_name, company_id,
                   lead_type, contact_person, phone,
                   article_title, source_author, source_url,
                   collected_at, extract_status
            FROM collector_leads
            WHERE extract_status = 'ok'
              AND (normalized_phone = ? OR (phone IS NOT NULL AND phone LIKE '%' || ? || '%'))
            ORDER BY collected_at DESC, id DESC
        """, [norm, norm]).fetchall()
    finally:
        db.close()

    # 精筛：确认该号码确实在这条记录的电话字段里
    leads = []
    for r in candidates:
        if any(pn == norm for _, pn in _split_phone_field(r["phone"])):
            leads.append(dict(r))

    return jsonify({
        "success": True,
        "phone": raw,
        "normalized_phone": norm,
        "count": len(leads),
        "leads": leads,
    })


def _retry_collect_once(source_url, hist):
    """重新抓取并提取一篇文章，返回 (records, article, error_msg)。"""
    try:
        raw = fetch_article(source_url)
    except Exception as e:
        return None, None, f"抓取失败: {e}"

    if is_wechat_url(source_url):
        article = parse_article(raw)
    else:
        article = parse_generic_article(source_url, raw)

    if not article.get("body"):
        return None, article, "无法获取文章正文，请检查链接是否有效"

    # 失败记录里存过公众号/日期，抓取不到时回填，保证历史信息不丢
    if hist:
        article["author"] = article.get("author") or hist["source_author"] or ""
        article["date"] = article.get("date") or hist["source_date"] or ""
        article["title"] = article.get("title") or hist["article_title"] or ""

    try:
        records = extract_info(article.get("title", ""), article.get("body", ""))
    except Exception as e:
        return None, article, f"AI 提取失败: {e}"

    if not records:
        return None, article, "文章中未找到公司信息"
    return records, article, None


@bp.route("/api/collector/retry", methods=["POST"])
def retry_collect():
    """重试采集某来源：重新抓取 → AI 提取 → 入库，成功后清掉该来源的失败记录。"""
    data = request.get_json(silent=True) or {}
    source_url = (data.get("source_url") or "").strip()
    if not source_url:
        return jsonify({"success": False, "error": "缺少来源链接"}), 400
    if source_url == "文字输入":
        return jsonify({"success": False, "error": "文字输入记录无法自动重试，请重新粘贴内容采集"}), 400

    # 同组历史信息（公众号 / 日期 / 标题）
    db = get_db()
    try:
        hist = db.execute(
            "SELECT source_author, source_date, article_title FROM collector_leads "
            "WHERE source_url = ? ORDER BY id DESC LIMIT 1", [source_url]).fetchone()
    finally:
        db.close()

    if not _auto_lock.acquire(blocking=False):
        return jsonify({"success": False, "error": "另一个采集任务正在运行，请稍后再试"}), 409

    try:
        sync_feishu = get_sync_to_feishu()
        fc = get_feishu_config()
        token = None
        if sync_feishu:
            if not fc.get("app_token") or not fc.get("collect_table_id"):
                return jsonify({"success": False, "error": "飞书同步已开启但未配置完整，请先到设置页处理"}), 400
            try:
                token = get_token()
            except Exception as e:
                return jsonify({"success": False, "error": f"飞书认证失败: {e}"}), 500

        records, article, err = _retry_collect_once(source_url, hist)
        if err:
            # 失败原因落库，便于用户回看（同来源失败记录合并为一条即可）
            if article is not None:
                save_failed_lead(source_url,
                                 article.get("author", ""), article.get("date", ""),
                                 article.get("title", ""), article.get("body", ""),
                                 err)
            return jsonify({"success": False, "error": err}), 200

        # 去重基线：库里已成功的「公司名 + 电话」组合
        db = get_db()
        try:
            rows = db.execute(
                "SELECT company_name, phone FROM collector_leads WHERE extract_status = 'ok'"
            ).fetchall()
            existing_company_phone = {
                ((r["company_name"] or "").strip(), (r["phone"] or "").strip()) for r in rows
            }
        finally:
            db.close()

        saved, skipped, errs = save_records(
            records, source_url, token, fc, set(), existing_company_phone,
            source_author=article.get("author", ""),
            source_date=article.get("date", ""),
            article_title=article.get("title", ""),
            sync_feishu=sync_feishu)

        # 成功后清掉该来源的失败记录，避免记录页长期挂着已解决的失败
        db = get_db()
        try:
            cur = db.execute(
                "DELETE FROM collector_leads WHERE source_url = ? "
                "AND extract_status = 'failed'", [source_url])
            removed = cur.rowcount
            db.commit()
        finally:
            db.close()

        return jsonify({
            "success": True,
            "count": len(records),
            "saved": saved,
            "skipped": skipped,
            "removed_failed": removed,
            "title": article.get("title", ""),
            "errors": errs,
        })
    except Exception as e:
        return jsonify({"success": False, "error": f"重试失败: {e}"}), 500
    finally:
        _auto_lock.release()


# ──────────────────────────────────────────────
# 采集 API
# ──────────────────────────────────────────────

@bp.route("/api/collector/collect", methods=["POST"])
def collect():
    """手动采集：粘贴链接或文字，SSE 流式返回进度。"""
    data = request.get_json()
    raw_input = data.get("urls", "")
    lines = [l.strip() for l in raw_input.split("\n") if l.strip()]
    if not lines:
        return jsonify({"error": "请输入内容"}), 400

    urls, text_content = classify_input(lines)

    sync_feishu = get_sync_to_feishu()
    fc = get_feishu_config()
    token = None

    if sync_feishu:
        if not fc.get("app_token") or not fc.get("collect_table_id"):
            return jsonify({"error": "请先在设置页面配置飞书 App Token 和采集表 ID，或关闭飞书同步"}), 400
        try:
            token = get_token()
        except Exception as e:
            return jsonify({"error": f"飞书认证失败: {e}"}), 500

    # 去重数据：飞书同步时从飞书拉，不同步时用空集合
    existing_links = set()
    existing_company_phone = set()
    if sync_feishu and token:
        try:
            existing_links, existing_company_phone = get_existing_records(
                token, fc["app_token"], fc["collect_table_id"])
        except Exception as e:
            return jsonify({"error": f"读取飞书数据失败: {e}"}), 500

    if not _auto_lock.acquire(blocking=False):
        return jsonify({"error": "另一个采集任务正在运行，请稍后再试"}), 409

    total = len(urls) + (1 if text_content else 0)

    def generate():
        all_results = []
        errors = []
        articles_info = []
        skipped_links = 0
        skipped_records = 0
        current = 0
        try:
            def emit_progress(msg):
                yield json.dumps({"type": "progress", "current": current, "total": total, "msg": msg}, ensure_ascii=False) + "\n"

            def process_article(url, source_author="", source_date=""):
                nonlocal skipped_records
                yield from emit_progress("正在抓取网页...")
                raw = fetch_article(url)
                yield from emit_progress("抓取成功，正在解析...")
                if is_wechat_url(url):
                    article = parse_article(raw)
                else:
                    article = parse_generic_article(url, raw)

                if not article["body"]:
                    errors.append(f"{url} — 无法获取文章内容，请检查链接是否正确")
                    return

                if should_skip_llm(article["title"], article["body"]):
                    errors.append(f"{url} — 文章未发现公司相关信息，跳过（标题：{article['title'] or '未知'}）")
                    return

                articles_info.append({
                    "title": article["title"],
                    "author": article.get("author", source_author),
                    "date": article.get("date", source_date),
                })

                yield from emit_progress("正在调用大模型分析...")
                try:
                    records = extract_info(article["title"], article["body"])
                except Exception as e:
                    errors.append(f"AI提取失败 {url}: {e}")
                    save_failed_lead(url, article.get("author", source_author),
                                     article.get("date", source_date),
                                     article.get("title", ""),
                                     article.get("body", ""), str(e))
                    yield from emit_progress("AI提取失败，原始内容已保存")
                    return

                if not records:
                    errors.append(f"{url} — 文章中未找到公司信息（标题：{article['title'] or '未知'}）")
                    return

                yield from emit_progress("AI分析完成，正在写入...")
                saved, skipped, errs = save_records(
                    records, url, token, fc, existing_links, existing_company_phone,
                    source_author=article.get("author", source_author),
                    source_date=article.get("date", source_date),
                    article_title=article.get("title", ""),
                    sync_feishu=sync_feishu)
                skipped_records += skipped
                errors.extend(errs)
                all_results.extend(records)
                if skipped:
                    yield from emit_progress(f"完成，提取 {len(records)} 条，写入 {saved} 条，跳过 {skipped} 条重复")
                else:
                    yield from emit_progress(f"完成，提取 {len(records)} 条")

            # 处理链接
            for url in urls:
                current += 1
                if url in existing_links:
                    skipped_links += 1
                    errors.append(f"{url} — 该链接已采集过，跳过")
                    yield from emit_progress("跳过已采集链接")
                    continue

                try:
                    yield from process_article(url)
                except Exception as e:
                    errors.append(f"抓取失败 {url}: {e}")

            # 处理文字
            if text_content:
                current += 1
                try:
                    yield from emit_progress("正在调用大模型分析...")
                    records = extract_info("", text_content)
                    if not records:
                        errors.append("文字内容中未找到公司信息")
                    else:
                        articles_info.append({"title": "文字输入", "author": "手动输入", "date": ""})
                        yield from emit_progress("AI分析完成，正在写入...")
                        saved, skipped, errs = save_records(
                            records, "文字输入", token, fc, existing_links, existing_company_phone,
                            source_author="手动输入", article_title="文字输入",
                            sync_feishu=sync_feishu)
                        skipped_records += skipped
                        errors.extend(errs)
                        all_results.extend(records)
                        if skipped:
                            yield from emit_progress(f"完成，提取 {len(records)} 条，写入 {saved} 条，跳过 {skipped} 条重复")
                        else:
                            yield from emit_progress(f"完成，提取 {len(records)} 条")
                except Exception as e:
                    errors.append(f"文字提取失败: {e}")
                    save_failed_lead("文字输入", "手动输入", "", "文字输入", text_content, str(e))
                    yield from emit_progress("AI提取失败，原始内容已保存")

            # 最终结果
            summary = []
            if skipped_links:
                summary.append(f"跳过已采集链接 {skipped_links} 个")
            if skipped_records:
                summary.append(f"跳过重复记录 {skipped_records} 条")

            yield json.dumps({
                "type": "result",
                "success": True,
                "count": len(all_results),
                "records": all_results,
                "errors": errors,
                "articles": articles_info,
                "summary": summary,
            }, ensure_ascii=False) + "\n"
        finally:
            _auto_lock.release()

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@bp.route("/api/collector/collect-from-table", methods=["POST"])
def collect_from_table():
    """从收集表读取内容并自动采集（支持 URL 和文字混合输入）。

    收集表是飞书的输入入口，必须连飞书；但写入是否同步飞书可选。
    """
    fc = get_feishu_config()
    if not fc.get("source_table_id"):
        return jsonify({"error": "请先配置收集表 ID（source_table_id）"}), 400
    if not fc.get("app_token") or not fc.get("collect_table_id"):
        return jsonify({"error": "请先配置 app_token 和 collect_table_id"}), 400

    try:
        token = get_token()
    except Exception as e:
        return jsonify({"error": f"飞书认证失败: {e}"}), 500

    try:
        pending = get_pending_entries(token, fc["app_token"], fc["source_table_id"])
    except Exception as e:
        return jsonify({"error": f"读取收集表失败: {e}"}), 500

    if not pending:
        return jsonify({"error": "收集表中没有未处理的内容"}), 400

    sync_feishu = get_sync_to_feishu()
    # 去重数据：同步时从飞书拉，不同步用空集合
    existing_links = set()
    existing_company_phone = set()
    if sync_feishu:
        try:
            existing_links, existing_company_phone = get_existing_records(
                token, fc["app_token"], fc["collect_table_id"])
        except Exception as e:
            return jsonify({"error": f"读取结果表失败: {e}"}), 500

    if not _auto_lock.acquire(blocking=False):
        return jsonify({"error": "另一个采集任务正在运行，请稍后再试"}), 409

    total = len(pending)

    def generate():
        all_results = []
        errors = []
        articles_info = []
        skipped_links = 0
        skipped_records = 0
        current = 0
        try:
            yield json.dumps({"type": "count", "total": total}, ensure_ascii=False) + "\n"

            def emit_progress(msg):
                yield json.dumps({"type": "progress", "current": current, "total": total, "msg": msg}, ensure_ascii=False) + "\n"

            for record_id, content in pending:
                current += 1

                # 判断是 URL 还是文字
                _, text_part = classify_input([content])
                is_url = not text_part

                if is_url:
                    if content in existing_links:
                        skipped_links += 1
                        errors.append(f"{content} — 该链接已采集过，跳过")
                        try:
                            mark_url_collected(token, fc["app_token"], fc["source_table_id"], record_id, "已存在，跳过")
                        except Exception:
                            pass
                        yield from emit_progress("跳过已采集链接")
                        continue

                    status_msg = ""
                    article_title = ""
                    try:
                        yield from emit_progress("正在抓取网页...")
                        raw = fetch_article(content)
                        yield from emit_progress("抓取成功，正在解析...")
                        if is_wechat_url(content):
                            article = parse_article(raw)
                        else:
                            article = parse_generic_article(content, raw)
                        article_title = article.get("title", "")

                        if not article["body"]:
                            status_msg = "无法获取文章内容"
                            errors.append(f"{content} — {status_msg}")
                        elif should_skip_llm(article["title"], article["body"]):
                            status_msg = f"未发现公司信息（{article['title'] or '未知'}）"
                            errors.append(f"{content} — {status_msg}")
                        else:
                            articles_info.append({
                                "title": article["title"],
                                "author": article["author"],
                                "date": article["date"],
                            })
                            yield from emit_progress("正在调用大模型分析...")
                            try:
                                records = extract_info(article["title"], article["body"])
                            except Exception as e:
                                status_msg = f"AI提取失败: {e}"
                                errors.append(f"{content} — {status_msg}")
                                save_failed_lead(content, article.get("author", ""),
                                                 article.get("date", ""),
                                                 article.get("title", ""),
                                                 article.get("body", ""), str(e))
                                yield from emit_progress("AI提取失败，原始内容已保存")
                            else:
                                if not records:
                                    status_msg = f"AI未提取到公司信息（{article['title'] or '未知'}）"
                                    errors.append(f"{content} — {status_msg}")
                                else:
                                    yield from emit_progress("AI分析完成，正在写入...")
                                    saved, skipped, errs = save_records(
                                        records, content, token, fc, existing_links, existing_company_phone,
                                        source_author=article["author"],
                                        source_date=article["date"],
                                        article_title=article.get("title", ""),
                                        sync_feishu=sync_feishu)
                                    skipped_records += skipped
                                    errors.extend(errs)
                                    all_results.extend(records)
                                    if skipped:
                                        status_msg = f"提取 {len(records)} 条，写入 {saved} 条，跳过 {skipped} 条重复"
                                    else:
                                        status_msg = f"成功，提取 {saved} 条公司信息"
                                    yield from emit_progress(f"完成，{status_msg}")
                    except Exception as e:
                        status_msg = f"采集失败：{e}"
                        errors.append(f"抓取失败 {content}: {e}")
                else:
                    # 文字流程
                    status_msg = ""
                    article_title = ""
                    try:
                        yield from emit_progress("正在调用大模型分析...")
                        records = extract_info("", content)
                        if not records:
                            status_msg = "文字内容中未找到公司信息"
                            errors.append(f"文字提取失败 — {status_msg}")
                        else:
                            articles_info.append({"title": "文字输入", "author": "收集表", "date": ""})
                            yield from emit_progress("AI分析完成，正在写入...")
                            saved, skipped, errs = save_records(
                                records, "文字输入", token, fc, existing_links, existing_company_phone,
                                source_author="收集表", article_title="文字输入",
                                sync_feishu=sync_feishu)
                            skipped_records += skipped
                            errors.extend(errs)
                            all_results.extend(records)
                            if skipped:
                                status_msg = f"提取 {len(records)} 条，写入 {saved} 条，跳过 {skipped} 条重复"
                            else:
                                status_msg = f"成功，提取 {saved} 条公司信息"
                            yield from emit_progress(f"完成，{status_msg}")
                    except Exception as e:
                        status_msg = f"文字提取失败：{e}"
                        errors.append(f"文字提取失败: {e}")
                        save_failed_lead("文字输入", "收集表", "", "文字输入", content, str(e))
                        yield from emit_progress("AI提取失败，原始内容已保存")

                try:
                    mark_url_collected(token, fc["app_token"], fc["source_table_id"], record_id, status_msg, article_title)
                except Exception:
                    pass

                if current < total:
                    time.sleep(1)

            # 最终结果
            summary = []
            if skipped_links:
                summary.append(f"跳过已采集链接 {skipped_links} 个")
            if skipped_records:
                summary.append(f"跳过重复记录 {skipped_records} 条")

            yield json.dumps({
                "type": "result",
                "success": True,
                "count": len(all_results),
                "records": all_results,
                "errors": errors,
                "articles": articles_info,
                "summary": summary,
            }, ensure_ascii=False) + "\n"
        finally:
            _auto_lock.release()

    return Response(stream_with_context(generate()),
                    mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ──────────────────────────────────────────────
# 自动采集
# ──────────────────────────────────────────────

def auto_collect_once():
    """非流式版本的收集表采集，供自动调度调用。"""
    if not _auto_lock.acquire(blocking=False):
        print("[自动采集] 跳过本轮：其他采集中")
        return None

    try:
        _auto_state["running"] = True
        fc = get_feishu_config()

        if not fc.get("source_table_id"):
            raise Exception("未配置 source_table_id")
        if not fc.get("app_token") or not fc.get("collect_table_id"):
            raise Exception("未配置 app_token 或 collect_table_id")

        token = get_token()
        pending = get_pending_entries(token, fc["app_token"], fc["source_table_id"])

        if not pending:
            return {"count": 0, "errors": [], "articles": [], "summary": [], "skipped_links": 0, "skipped_records": 0}

        sync_feishu = get_sync_to_feishu()
        existing_links = set()
        existing_company_phone = set()
        if sync_feishu:
            existing_links, existing_company_phone = get_existing_records(
                token, fc["app_token"], fc["collect_table_id"])

        total = len(pending)
        all_results = []
        errors = []
        articles_info = []
        skipped_links = 0
        skipped_records = 0
        current = 0

        for record_id, content in pending:
            current += 1
            _auto_state["progress_current"] = current
            _auto_state["progress_total"] = total

            _, text_part = classify_input([content])
            is_url = not text_part

            if is_url:
                if content in existing_links:
                    skipped_links += 1
                    errors.append(f"{content} — 该链接已采集过，跳过")
                    _auto_state["progress_msg"] = f"{current}/{total} 跳过已采集链接"
                    try:
                        mark_url_collected(token, fc["app_token"], fc["source_table_id"], record_id, "已存在，跳过")
                    except Exception:
                        pass
                    continue

                status_msg = ""
                article_title = ""
                try:
                    _auto_state["progress_msg"] = f"{current}/{total} 正在抓取网页..."
                    raw = fetch_article(content)
                    _auto_state["progress_msg"] = f"{current}/{total} 正在解析内容..."
                    if is_wechat_url(content):
                        article = parse_article(raw)
                    else:
                        article = parse_generic_article(content, raw)
                    article_title = article.get("title", "")

                    if not article["body"]:
                        status_msg = "无法获取文章内容"
                        errors.append(f"{content} — {status_msg}")
                    elif should_skip_llm(article["title"], article["body"]):
                        status_msg = f"未发现公司信息（{article['title'] or '未知'}）"
                        errors.append(f"{content} — {status_msg}")
                    else:
                        articles_info.append({"title": article["title"], "author": article["author"], "date": article["date"]})
                        _auto_state["progress_msg"] = f"{current}/{total} AI分析中..."
                        try:
                            records = extract_info(article["title"], article["body"])
                        except Exception as e:
                            status_msg = f"AI提取失败: {e}"
                            errors.append(f"{content} — {status_msg}")
                            save_failed_lead(content, article.get("author", ""),
                                             article.get("date", ""),
                                             article.get("title", ""),
                                             article.get("body", ""), str(e))
                            _auto_state["progress_msg"] = f"{current}/{total} AI提取失败"
                        else:
                            if not records:
                                status_msg = f"AI未提取到公司信息（{article['title'] or '未知'}）"
                                errors.append(f"{content} — {status_msg}")
                            else:
                                _auto_state["progress_msg"] = f"{current}/{total} 写入..."
                                saved, skipped, errs = save_records(
                                    records, content, token, fc, existing_links, existing_company_phone,
                                    source_author=article["author"], source_date=article["date"],
                                    article_title=article.get("title", ""),
                                    sync_feishu=sync_feishu)
                                skipped_records += skipped
                                errors.extend(errs)
                                all_results.extend(records)
                                status_msg = f"提取 {len(records)} 条，写入 {saved} 条" + (f"，跳过 {skipped} 条重复" if skipped else "")
                except Exception as e:
                    status_msg = f"采集失败：{e}"
                    errors.append(f"抓取失败 {content}: {e}")
            else:
                # 文字流程
                status_msg = ""
                article_title = ""
                try:
                    _auto_state["progress_msg"] = f"{current}/{total} AI分析中..."
                    try:
                        records = extract_info("", content)
                    except Exception as e:
                        status_msg = f"文字提取失败: {e}"
                        errors.append(f"文字提取失败: {e}")
                        save_failed_lead("文字输入", "收集表", "", "文字输入", content, str(e))
                        _auto_state["progress_msg"] = f"{current}/{total} AI提取失败"
                    else:
                        if not records:
                            status_msg = "文字内容中未找到公司信息"
                            errors.append(f"文字提取失败 — {status_msg}")
                        else:
                            articles_info.append({"title": "文字输入", "author": "收集表", "date": ""})
                            _auto_state["progress_msg"] = f"{current}/{total} 写入..."
                            saved, skipped, errs = save_records(
                                records, "文字输入", token, fc, existing_links, existing_company_phone,
                                source_author="收集表", article_title="文字输入",
                                sync_feishu=sync_feishu)
                            skipped_records += skipped
                            errors.extend(errs)
                            all_results.extend(records)
                            status_msg = f"提取 {len(records)} 条，写入 {saved} 条" + (f"，跳过 {skipped} 条重复" if skipped else "")
                except Exception as e:
                    status_msg = f"文字提取失败：{e}"
                    errors.append(f"文字提取失败: {e}")
                    save_failed_lead("文字输入", "收集表", "", "文字输入", content, str(e))
                    _auto_state["progress_msg"] = f"{current}/{total} AI提取失败"

            try:
                mark_url_collected(token, fc["app_token"], fc["source_table_id"], record_id, status_msg, article_title)
            except Exception:
                pass

            if current < total:
                time.sleep(1)

        summary = []
        if skipped_links:
            summary.append(f"跳过已采集链接 {skipped_links} 个")
        if skipped_records:
            summary.append(f"跳过重复记录 {skipped_records} 条")

        return {
            "count": len(all_results),
            "errors": errors,
            "articles": articles_info,
            "summary": summary,
            "skipped_links": skipped_links,
            "skipped_records": skipped_records,
        }
    finally:
        _auto_state["running"] = False
        _auto_lock.release()


def _auto_scheduler_loop():
    """后台调度线程：每 60 秒检查一次是否该执行自动采集。"""
    print("[自动采集] 调度器已启动")
    while True:
        time.sleep(60)
        auto_config = get_collector_auto_config()
        if not auto_config.get("enabled"):
            continue

        interval = auto_config.get("interval", 30)
        last_run = _auto_state.get("last_run")
        if last_run:
            try:
                last_dt = datetime.datetime.fromisoformat(last_run)
                if (datetime.datetime.now() - last_dt).total_seconds() / 60 < interval:
                    continue
            except (ValueError, TypeError):
                pass

        print(f"[自动采集] 触发采集（间隔 {interval} 分钟）")
        try:
            result = auto_collect_once()
        except Exception as e:
            print(f"[自动采集] 意外错误: {e}")
            _auto_state["last_errors"] = [f"意外错误: {e}"]
            continue

        if result is not None:
            _auto_state["last_run"] = datetime.datetime.now().isoformat()
            _auto_state["last_count"] = result["count"]
            _auto_state["last_errors"] = result["errors"][-10:]
            print(f"[自动采集] 完成：{result['count']} 条，错误 {len(result['errors'])} 条")


def _ensure_scheduler():
    """启动调度器线程（如未运行）。"""
    global _auto_scheduler_thread
    if _auto_scheduler_thread is None or not _auto_scheduler_thread.is_alive():
        _auto_scheduler_thread = threading.Thread(target=_auto_scheduler_loop, daemon=True)
        _auto_scheduler_thread.start()


def init_module():
    """模块初始化：如果自动采集已开启，启动调度器。"""
    auto_config = get_collector_auto_config()
    if auto_config.get("enabled"):
        _ensure_scheduler()


def on_disable():
    """模块停用时：确保自动采集调度器不再启动新任务。"""
    global _auto_scheduler_thread
    # 调度器是 daemon 线程，不需要强杀；只需把 enabled 关掉
    save_collector_auto_config(enabled=False)


# ──────────────────────────────────────────────
# 自动采集 API
# ──────────────────────────────────────────────

@bp.route("/api/collector/auto-status", methods=["GET"])
def auto_status():
    """获取自动采集状态。"""
    auto_config = get_collector_auto_config()
    return jsonify({
        "enabled": auto_config.get("enabled", False),
        "interval": auto_config.get("interval", 30),
        "last_run": _auto_state.get("last_run"),
        "last_count": _auto_state.get("last_count", 0),
        "last_errors": _auto_state.get("last_errors", []),
        "running": _auto_state.get("running", False),
        "progress_current": _auto_state.get("progress_current", 0),
        "progress_total": _auto_state.get("progress_total", 0),
        "progress_msg": _auto_state.get("progress_msg", ""),
    })


@bp.route("/api/collector/auto-toggle", methods=["POST"])
def auto_toggle():
    """开启/关闭自动采集。"""
    data = request.get_json()
    enabled = data.get("enabled", None)
    interval = data.get("interval", None)

    save_collector_auto_config(enabled=enabled, interval=interval)
    auto_config = get_collector_auto_config()

    if auto_config.get("enabled"):
        _ensure_scheduler()

    _auto_state["enabled"] = auto_config["enabled"]
    _auto_state["interval"] = auto_config["interval"]

    return jsonify({
        "success": True,
        "enabled": auto_config["enabled"],
        "interval": auto_config["interval"],
        "message": "自动采集已开启" if auto_config["enabled"] else "自动采集已关闭",
    })


@bp.route("/api/collector/init-fields", methods=["POST"])
def init_fields():
    """在飞书采集表中初始化字段。"""
    fc = get_feishu_config()
    if not fc.get("app_token") or not fc.get("collect_table_id"):
        return jsonify({"error": "请先配置 app_token 和 collect_table_id"}), 400
    try:
        token = get_token()
        create_table_fields(token, fc["app_token"], fc["collect_table_id"])
        return jsonify({"success": True, "message": "字段创建完成"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ──────────────────────────────────────────────
# 提示词 API
# ──────────────────────────────────────────────

@bp.route("/api/collector/prompts", methods=["GET"])
def get_prompts():
    """获取采集提示词。"""
    return jsonify(get_collector_prompts())


@bp.route("/api/collector/prompts", methods=["POST"])
def update_prompts():
    """保存采集提示词。"""
    data = request.get_json()
    system_prompt = data.get("system_prompt", "")
    user_prompt = data.get("user_prompt", "")
    save_collector_prompts(system_prompt, user_prompt)
    return jsonify({"success": True, "message": "提示词已保存"})


# ──────────────────────────────────────────────
# 飞书同步开关 API
# ──────────────────────────────────────────────

@bp.route("/api/collector/sync-feishu", methods=["GET"])
def get_sync_feishu():
    """获取飞书同步状态。"""
    return jsonify({"sync_feishu": get_sync_to_feishu()})


@bp.route("/api/collector/sync-feishu", methods=["POST"])
def toggle_sync_feishu():
    """开启/关闭飞书同步。"""
    data = request.get_json()
    enabled = data.get("enabled", None)
    if enabled is not None:
        save_sync_to_feishu(bool(enabled))
    return jsonify({
        "success": True,
        "sync_feishu": get_sync_to_feishu(),
        "message": "飞书同步已开启" if get_sync_to_feishu() else "飞书同步已关闭",
    })


# ──────────────────────────────────────────────
# 连通性测试 API
# ──────────────────────────────────────────────

@bp.route("/api/collector/test-connectivity", methods=["POST"])
def test_connectivity():
    """检测飞书和大模型的连通性。"""
    results = []

    # 1. 测试大模型
    llm_item = {"name": "大模型", "status": "ok", "error": ""}
    try:
        llm = _get_llm_config()
        if not llm["enabled"]:
            llm_item["status"] = "skip"
            llm_item["error"] = "未启用"
        elif not llm["api_key"] or not llm["base_url"]:
            llm_item["status"] = "skip"
            llm_item["error"] = "未配置"
        else:
            # 发送测试请求
            url = f"{llm['base_url']}/chat/completions"
            payload = json.dumps({
                "model": llm["model"],
                "messages": [{"role": "user", "content": "Hi"}],
                "max_tokens": 5,
            }, ensure_ascii=False).encode("utf-8")
            import ssl
            ssl_ctx = ssl.create_default_context()
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            req = urllib.request.Request(url, data=payload, method="POST")
            req.add_header("Content-Type", "application/json; charset=utf-8")
            req.add_header("Authorization", f"Bearer {llm['api_key']}")
            with urllib.request.urlopen(req, timeout=15, context=ssl_ctx) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            llm_item["detail"] = f"模型: {llm['model']}"
    except Exception as e:
        llm_item["status"] = "fail"
        llm_item["error"] = str(e)
    results.append(llm_item)

    # 2. 测试飞书（认证 + 采集表 + 收集表）
    results.extend(test_feishu_connection())

    all_ok = all(r["status"] in ("ok", "skip") for r in results)
    return jsonify({"success": all_ok, "results": results})
