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

from flask import render_template, request, jsonify, Response, stream_with_context

from feishu_service import (
    get_feishu_config, is_feishu_ready,
    get_token, get_existing_records, add_record,
    get_pending_entries, mark_url_collected, create_table_fields,
    test_feishu_connection,
)
from config import get_llm_config, load_config, save_config
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
    "impact": "从网页链接或文字提取公司信息并写入飞书。停用后采集入口消失，飞书数据不受影响。",
    "show_in": ["drawer", "desktop"],
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
    """从 EntHub 的 LLM 配置中读取连接信息。"""
    llm = get_llm_config()
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
# 记录写入（去重 + 写飞书）
# ──────────────────────────────────────────────

def save_records(records, source_url, token, fc, existing_links, existing_company_phone,
                source_author="", source_date=""):
    """写入飞书，返回 (saved_count, skipped_count, error_list)。"""
    saved = 0
    skipped = 0
    errors = []

    for rec in records:
        rec = dict(rec)
        rec["来源链接"] = source_url
        rec["采集时间"] = int(time.time()) * 1000
        rec["来源"] = source_author or rec.get("来源", "")
        rec["发布日期"] = source_date or rec.get("发布日期", "")

        company = rec.get("公司名", "").strip()
        phone = rec.get("电话", "").strip()
        if company and (company, phone) in existing_company_phone:
            skipped += 1
            continue

        fields = {}
        for k, v in rec.items():
            if k == "来源链接":
                fields[k] = {"link": v, "text": v}
            else:
                fields[k] = v

        try:
            print(f"[写入飞书] {company or '?'} -> {json.dumps(fields, ensure_ascii=False)[:200]}")
            add_record(token, fc["app_token"], fc["collect_table_id"], fields)
            print(f"[写入成功] {company or '?'}")
            if company:
                existing_company_phone.add((company, phone))
            existing_links.add(source_url)
            saved += 1
        except Exception as e:
            print(f"[写入失败] {company or '?'}: {e}")
            errors.append(f"写入飞书失败({company or '?'}): {e}")

    return saved, skipped, errors


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

@bp.route("/collector")
def collector_page():
    """采集主页面。"""
    auto_config = get_collector_auto_config()
    feishu_ready = is_feishu_ready()
    llm = get_llm_config()
    prompts = get_collector_prompts()
    fc = get_feishu_config()
    trafilatura_ok = is_trafilatura_available()

    return render_template("collector.html",
                           auto_config=auto_config,
                           feishu_ready=feishu_ready,
                           llm=llm,
                           prompts=prompts,
                           feishu_config=fc,
                           trafilatura_ok=trafilatura_ok)


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

    fc = get_feishu_config()
    if not fc.get("app_token") or not fc.get("collect_table_id"):
        return jsonify({"error": "请先在设置页面配置飞书 App Token 和采集表 ID"}), 400

    try:
        token = get_token()
    except Exception as e:
        return jsonify({"error": f"飞书认证失败: {e}"}), 500

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
                records = extract_info(article["title"], article["body"])
                if not records:
                    errors.append(f"{url} — 文章中未找到公司信息（标题：{article['title'] or '未知'}）")
                    return

                yield from emit_progress("AI分析完成，正在写入飞书...")
                saved, skipped, errs = save_records(
                    records, url, token, fc, existing_links, existing_company_phone,
                    source_author=article.get("author", source_author),
                    source_date=article.get("date", source_date))
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
                        yield from emit_progress("AI分析完成，正在写入飞书...")
                        saved, skipped, errs = save_records(
                            records, "文字输入", token, fc, existing_links, existing_company_phone,
                            source_author="手动输入")
                        skipped_records += skipped
                        errors.extend(errs)
                        all_results.extend(records)
                        if skipped:
                            yield from emit_progress(f"完成，提取 {len(records)} 条，写入 {saved} 条，跳过 {skipped} 条重复")
                        else:
                            yield from emit_progress(f"完成，提取 {len(records)} 条")
                except Exception as e:
                    errors.append(f"文字提取失败: {e}")

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
    """从收集表读取内容并自动采集（支持 URL 和文字混合输入）。"""
    fc = get_feishu_config()
    if not fc.get("source_table_id"):
        return jsonify({"error": "请先配置收集表 ID（source_table_id）"}), 400
    if not fc.get("app_token") or not fc.get("collect_table_id"):
        return jsonify({"error": "请先配置采集表（结果表）的 app_token 和 collect_table_id"}), 400

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
                            records = extract_info(article["title"], article["body"])
                            if not records:
                                status_msg = f"AI未提取到公司信息（{article['title'] or '未知'}）"
                                errors.append(f"{content} — {status_msg}")
                            else:
                                yield from emit_progress("AI分析完成，正在写入飞书...")
                                saved, skipped, errs = save_records(
                                    records, content, token, fc, existing_links, existing_company_phone,
                                    source_author=article["author"],
                                    source_date=article["date"])
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
                            yield from emit_progress("AI分析完成，正在写入飞书...")
                            saved, skipped, errs = save_records(
                                records, "文字输入", token, fc, existing_links, existing_company_phone,
                                source_author="收集表")
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
                        records = extract_info(article["title"], article["body"])
                        if not records:
                            status_msg = f"AI未提取到公司信息（{article['title'] or '未知'}）"
                            errors.append(f"{content} — {status_msg}")
                        else:
                            _auto_state["progress_msg"] = f"{current}/{total} 写入飞书..."
                            saved, skipped, errs = save_records(
                                records, content, token, fc, existing_links, existing_company_phone,
                                source_author=article["author"], source_date=article["date"])
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
                    records = extract_info("", content)
                    if not records:
                        status_msg = "文字内容中未找到公司信息"
                        errors.append(f"文字提取失败 — {status_msg}")
                    else:
                        articles_info.append({"title": "文字输入", "author": "收集表", "date": ""})
                        _auto_state["progress_msg"] = f"{current}/{total} 写入飞书..."
                        saved, skipped, errs = save_records(
                            records, "文字输入", token, fc, existing_links, existing_company_phone,
                            source_author="收集表")
                        skipped_records += skipped
                        errors.extend(errs)
                        all_results.extend(records)
                        status_msg = f"提取 {len(records)} 条，写入 {saved} 条" + (f"，跳过 {skipped} 条重复" if skipped else "")
                except Exception as e:
                    status_msg = f"文字提取失败：{e}"
                    errors.append(f"文字提取失败: {e}")

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
