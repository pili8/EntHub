"""核心页面路由：首页、浏览、搜索、关联发现、电话页。"""
import json
import math
import sqlite3
from flask import Blueprint, g, request, render_template, redirect, url_for, flash, \
                   Response, jsonify

from db import DB_PATH
from queries import (
    DEFAULT_PER_PAGE, ALLOWED_SORTS, COMPANY_LIST_COLUMNS,
    build_filter_clause, build_sort_clause, where_sql,
    query_company_list, get_filter_options, get_year_bounds, build_year_ranges,
    detect_query_type, text_search, search_by_phone, search_by_credit_code,
    sanitize_page, sanitize_per_page, sanitize_min_count, paginate,
    phone_stats_grouped,
)
from utils import normalize_phone

bp = Blueprint('pages_bp', __name__)

PER_PAGE = DEFAULT_PER_PAGE


# ── 首页 ────────────────────────────────────────────────────────────────────

@bp.route("/")
def index():
    stats = g.db.execute("""
        SELECT
            COUNT(*)                                       AS total,
            COUNT(DISTINCT normalized_name)                AS unique_names,
            (SELECT COUNT(DISTINCT normalized_phone) FROM company_phones) AS unique_phones,
            (SELECT COUNT(*) FROM company_phones) AS rows_with_phone
        FROM companies
    """).fetchone()
    recents = g.db.execute("""
        SELECT id, q, query_type, result_count, created_at
        FROM recent_searches
        ORDER BY id DESC
        LIMIT 8
    """).fetchall()
    return render_template("index.html", stats=stats, recents=recents)


# ── 重启 ────────────────────────────────────────────────────────────────────

@bp.route("/restart")
def restart_server():
    """触发服务器重启（通过修改监控文件）"""
    from pathlib import Path
    import time
    trigger_file = Path(__file__).parent.parent / ".restart_trigger"
    trigger_file.write_text(str(time.time()))
    return render_template("restarting.html")


# ── 数据浏览 ────────────────────────────────────────────────────────────────

@bp.route("/browse")
def browse():
    """浏览页骨架：只渲染筛选器（带缓存），数据列表由 HTMX 异步加载。

    这样首次进入浏览页时跳转响应很快（< 100ms），用户立即看到骨架，
    数据填充由独立的 /browse/data 异步完成，避免"干等 3-5 秒"。
    """
    # 当前选中的筛选值（用于模板回填 + 透传给数据端点）
    filters = {}
    for key in ("city", "district", "business_status", "industry", "company_type",
               "year_from", "year_to", "cap_from", "cap_to",
               "insured_from", "insured_to", "created_at"):
       val = (request.args.get(key) or "").strip()
       if val:
           filters[key] = val

    # 筛选器选项（5 分钟缓存，命中后毫秒级）
    filter_options = get_filter_options(g.db)
    min_year, max_year = get_year_bounds(g.db)
    year_ranges = build_year_ranges(min_year, max_year)

    return render_template("browse.html",
                           # 透传参数（给 HTMX 用）
                           page=sanitize_page(request.args),
                           per_page=sanitize_per_page(request.args),
                           sort=request.args.get("sort", "id"),
                           direction=request.args.get("dir", "desc"),
                           filters=filters, filter_options=filter_options,
                           year_ranges=year_ranges,
                           # 数据区初始为空，由 HTMX 异步加载
                           rows=None, total=None, pages=None)


@bp.route("/browse/data")
def browse_data():
    """浏览页数据片段：企业列表 + 分页。由 HTMX 异步请求。"""
    page = sanitize_page(request.args)
    per_page = sanitize_per_page(request.args)

    clauses, params = build_filter_clause(request.args)
    where_clause = where_sql(clauses)
    sort_col, dir_sql = build_sort_clause(request.args)

    total = g.db.execute(
        f"SELECT COUNT(*) FROM companies {where_clause}", params
    ).fetchone()[0]
    pages, offset = paginate(total, page, per_page)

    rows = query_company_list(
        g.db, where_clause, params, sort_col, dir_sql, per_page, offset
    )

    filters = {}
    for key in ("city", "district", "business_status", "industry", "company_type",
               "year_from", "year_to", "cap_from", "cap_to",
               "insured_from", "insured_to", "created_at"):
       val = (request.args.get(key) or "").strip()
       if val:
           filters[key] = val

    return render_template("_browse_data.html",
                           rows=rows, total=total, page=page, pages=pages,
                           per_page=per_page,
                           sort=request.args.get("sort", "id"),
                           direction=request.args.get("dir", "desc"),
                           filters=filters)


# ── 流式浏览数据（SSE） ──────────────────────────────────────────────────────


@bp.route("/browse/data.json")
def browse_data_json():
    """浏览页数据（JSON）：一次性返回当前页行 + 总数 + 分页。

    替代 SSE 流式接口，规避 EventSource 在大数据量下自动重连导致列表
    不渲染的问题。查询本身很快（20w 行 COUNT 仅几十毫秒）。
    """
    page = sanitize_page(request.args)
    per_page = sanitize_per_page(request.args)

    clauses, params = build_filter_clause(request.args)
    where_clause = where_sql(clauses)
    sort_col, dir_sql = build_sort_clause(request.args)

    total = g.db.execute(
        f"SELECT COUNT(*) FROM companies {where_clause}", params
    ).fetchone()[0]
    pages = max(1, math.ceil(total / per_page)) if total else 1
    offset = (page - 1) * per_page

    rows = query_company_list(
        g.db, where_clause, params, sort_col, dir_sql, per_page, offset
    )

    def _val(v):
        return v.isoformat() if hasattr(v, "isoformat") else v

    return jsonify({
        "rows": [{k: _val(r[k]) for k in r.keys()} for r in rows],
        "total": total,
        "pages": pages,
        "page": page,
        "per_page": per_page,
        "sort": request.args.get("sort", "id"),
        "dir": request.args.get("dir", "desc"),
    })


@bp.route("/browse/stream")
def browse_stream():
    """SSE：流式推送浏览页数据。

    推送顺序：
      1. start  — 查询开始（前端显示骨架屏 + 计时器）
      2. batch  — 分批行数据（每批 10 行，前端追加到表格）
      3. count  — 总数 + 总页数（前端更新分页栏）
      4. done   — 完成

    用户能立即看到数据出现，而不是等整个查询（尤其是 COUNT）完成。
    """
    page = sanitize_page(request.args)
    per_page = sanitize_per_page(request.args)

    clauses, params = build_filter_clause(request.args)
    where_clause = where_sql(clauses)
    sort_col, dir_sql = build_sort_clause(request.args)
    offset = (page - 1) * per_page
    sort_arg = request.args.get("sort", "id")
    dir_arg = request.args.get("dir", "desc")

    def generate():
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")

        try:
            # start 事件
            start_data = json.dumps(
                {"page": page, "per_page": per_page,
                 "sort": sort_arg, "dir": dir_arg},
                ensure_ascii=False,
            )
            yield f"event: start\ndata: {start_data}\n\n"

            # 流式查询行数据
            batch_size = 10
            loaded = 0

            cursor = db.execute(f"""
                SELECT {COMPANY_LIST_COLUMNS}
                FROM companies c
                {where_clause}
                ORDER BY {sort_col} {dir_sql}
                LIMIT ? OFFSET ?
            """, params + [per_page, offset])

            while loaded < per_page:
                batch = cursor.fetchmany(batch_size)
                if not batch:
                    break

                rows_data = [dict(r) for r in batch]
                payload = json.dumps(
                    {"rows": rows_data, "loaded": loaded + len(batch)},
                    ensure_ascii=False, default=str,
                )
                yield f"event: batch\ndata: {payload}\n\n"
                loaded += len(batch)

            # 总数查询（可能较慢，放在最后）
            total = db.execute(
                f"SELECT COUNT(*) FROM companies {where_clause}", params
            ).fetchone()[0]
            pages = max(1, math.ceil(total / per_page))

            count_data = json.dumps(
                {"total": total, "pages": pages, "page": page},
                ensure_ascii=False,
            )
            yield f"event: count\ndata: {count_data}\n\n"

            yield "event: done\ndata: {}\n\n"

        except Exception as e:
            err = json.dumps({"message": str(e)}, ensure_ascii=False)
            yield f"event: error\ndata: {err}\n\n"
        finally:
            db.close()

    return Response(generate(), content_type="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── 统一搜索 ────────────────────────────────────────────────────────────────

@bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    page = sanitize_page(request.args)

    if not q:
        return render_template("search.html", q="", query_type="",
                               rows=[], total=0, page=1, pages=1)

    query_type = detect_query_type(q)
    pages, offset = paginate(0, page, PER_PAGE)  # 占位，每个分支会重算

    if query_type == "phone":
        norm_q = normalize_phone(q)
        if not norm_q:
            return render_template("search.html", q=q, query_type="phone",
                                   rows=[], total=0, page=1, pages=1)
        total, rows = search_by_phone(g.db, norm_q, PER_PAGE, offset)
        pages, _ = paginate(total, page, PER_PAGE)

    elif query_type == "credit_code":
        from utils import normalize_credit_code
        norm_q = normalize_credit_code(q)
        total, rows = search_by_credit_code(g.db, norm_q, PER_PAGE, offset)
        pages, _ = paginate(total, page, PER_PAGE)

    else:
        total, rows = text_search(g.db, q, PER_PAGE, offset)
        pages, _ = paginate(total, page, PER_PAGE)

    # 记录最近查询（仅首页查询，分页不记）
    if page == 1 and q:
        g.db.execute(
            "INSERT INTO recent_searches (q, query_type, result_count) VALUES (?, ?, ?)",
            [q, query_type, total]
        )
        # 超过 50 条清理一次
        g.db.execute(
            "DELETE FROM recent_searches WHERE id NOT IN "
            "(SELECT id FROM recent_searches ORDER BY id DESC LIMIT 50)"
        )
        g.db.commit()

    return render_template("search.html", q=q, query_type=query_type,
                           rows=rows, total=total, page=page, pages=pages)


# ── 最近查询 ────────────────────────────────────────────────────────────────

@bp.route("/recent/clear", methods=["POST"])
def recent_clear():
    g.db.execute("DELETE FROM recent_searches")
    g.db.commit()
    return redirect(url_for("pages_bp.index"))


# ── 关联发现页面 ────────────────────────────────────────────────────────────

@bp.route("/relations")
def relation_discovery():
    """关联发现独立页面"""
    return render_template("relation_discovery.html")


# ── 关联发现 - HTMX 局部加载 ────────────────────────────────────────────────

@bp.route("/browse/relation-groups")
def browse_relation_groups():
    """按关联类型列出分组（电话/邮箱/法人）"""
    dup_type = (request.args.get("dup_type") or "phone").strip()
    page = sanitize_page(request.args)
    per_page = sanitize_per_page(request.args, default=PER_PAGE)
    sort_by = (request.args.get("sort") or "cnt").strip()
    min_count = sanitize_min_count(request.args)

    # 排序映射（白名单）
    sort_map = {
        "cnt": "cnt DESC",
        "val": "val ASC",
    }
    order_sql = sort_map.get(sort_by, "cnt DESC")
    offset = (page - 1) * per_page

    if dup_type == "phone":
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT normalized_phone FROM company_phones
                WHERE normalized_phone IS NOT NULL AND normalized_phone <> ''
                GROUP BY normalized_phone HAVING COUNT(DISTINCT company_id) >= ?
            )
        """, [min_count]).fetchone()[0]
        # 子查询 LIMIT 10 截断：避免大组（如某号关联 532 家）GROUP_CONCAT 拖慢，
        # 同时绕过 SQLite 3.51+ 不支持 GROUP_CONCAT(DISTINCT col, sep) 的限制
        rows = g.db.execute(f"""
            SELECT t.normalized_phone AS val,
                   t.display_val,
                   t.cnt,
                   (SELECT GROUP_CONCAT(cname, '; ') FROM (
                       SELECT c.name AS cname
                       FROM company_phones cp2
                       JOIN companies c ON c.id = cp2.company_id
                       WHERE cp2.normalized_phone = t.normalized_phone
                       LIMIT 10
                   )) AS company_names
            FROM (
                SELECT cp.normalized_phone,
                       MIN(cp.phone) AS display_val,
                       COUNT(DISTINCT cp.company_id) AS cnt
                FROM company_phones cp
                WHERE cp.normalized_phone IS NOT NULL AND cp.normalized_phone <> ''
                GROUP BY cp.normalized_phone
                HAVING cnt >= ?
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            ) t
        """, [min_count, per_page, offset]).fetchall()

    elif dup_type == "email":
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT normalized_email FROM companies
                WHERE normalized_email IS NOT NULL AND normalized_email <> ''
                GROUP BY normalized_email HAVING COUNT(*) >= ?
            )
        """, [min_count]).fetchone()[0]
        rows = g.db.execute(f"""
            SELECT t.val, t.display_val, t.cnt,
                   (SELECT GROUP_CONCAT(cname, '; ') FROM (
                       SELECT name AS cname FROM companies
                       WHERE normalized_email = t.val LIMIT 10
                   )) AS company_names
            FROM (
                SELECT normalized_email AS val,
                       MIN(email)        AS display_val,
                       COUNT(*)          AS cnt
                FROM companies
                WHERE normalized_email IS NOT NULL AND normalized_email <> ''
                GROUP BY normalized_email
                HAVING cnt >= ?
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            ) t
        """, [min_count, per_page, offset]).fetchall()

    elif dup_type == "legal_person":
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT normalized_legal_person FROM companies
                WHERE normalized_legal_person IS NOT NULL AND normalized_legal_person <> ''
                  AND normalized_legal_person <> '-'
                GROUP BY normalized_legal_person HAVING COUNT(*) >= ?
            )
        """, [min_count]).fetchone()[0]
        rows = g.db.execute(f"""
            SELECT t.val, t.display_val, t.cnt,
                   (SELECT GROUP_CONCAT(cname, '; ') FROM (
                       SELECT name AS cname FROM companies
                       WHERE normalized_legal_person = t.val LIMIT 10
                   )) AS company_names
            FROM (
                SELECT normalized_legal_person AS val,
                       MIN(legal_person)       AS display_val,
                       COUNT(*)                AS cnt
                FROM companies
                WHERE normalized_legal_person IS NOT NULL AND normalized_legal_person <> ''
                  AND normalized_legal_person <> '-'
                GROUP BY normalized_legal_person
                HAVING cnt >= ?
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            ) t
        """, [min_count, per_page, offset]).fetchall()

    elif dup_type == "shareholder":
        # 股东关联：通过 company_shareholders 表反查多家公司共用的股东
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT normalized_name FROM company_shareholders
                WHERE normalized_name IS NOT NULL AND normalized_name <> ''
                GROUP BY normalized_name HAVING COUNT(DISTINCT company_id) >= ?
            )
        """, [min_count]).fetchone()[0]
        rows = g.db.execute(f"""
            SELECT t.val, t.display_val, t.cnt,
                   (SELECT GROUP_CONCAT(cname, '; ') FROM (
                       SELECT c.name AS cname
                       FROM company_shareholders cs2
                       JOIN companies c ON c.id = cs2.company_id
                       WHERE cs2.normalized_name = t.val LIMIT 10
                   )) AS company_names
            FROM (
                SELECT cs.normalized_name AS val,
                       MIN(cs.name)       AS display_val,
                       COUNT(DISTINCT cs.company_id) AS cnt
                FROM company_shareholders cs
                WHERE cs.normalized_name IS NOT NULL AND cs.normalized_name <> ''
                GROUP BY cs.normalized_name
                HAVING cnt >= ?
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            ) t
        """, [min_count, per_page, offset]).fetchall()

    elif dup_type == "industry":
        # 行业关联：同行业的公司分组
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT industry FROM companies
                WHERE industry IS NOT NULL AND industry <> '' AND industry <> '-'
                GROUP BY industry HAVING COUNT(*) >= ?
            )
        """, [min_count]).fetchone()[0]
        rows = g.db.execute(f"""
            SELECT t.val, t.display_val, t.cnt,
                   (SELECT GROUP_CONCAT(cname, '; ') FROM (
                       SELECT name AS cname FROM companies
                       WHERE industry = t.val LIMIT 10
                   )) AS company_names
            FROM (
                SELECT industry AS val,
                       MIN(industry) AS display_val,
                       COUNT(*) AS cnt
                FROM companies
                WHERE industry IS NOT NULL AND industry <> '' AND industry <> '-'
                GROUP BY industry
                HAVING cnt >= ?
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            ) t
        """, [min_count, per_page, offset]).fetchall()

    elif dup_type == "tag":
        # 标签关联：通过 company_tags 表反查同一标签下的多家公司
        total = g.db.execute("""
            SELECT COUNT(*) FROM (
                SELECT tag_id FROM company_tags
                GROUP BY tag_id HAVING COUNT(*) >= ?
            )
        """, [min_count]).fetchone()[0]
        rows = g.db.execute(f"""
            SELECT t.val, t.display_val, t.cnt, t.tag_color,
                   (SELECT GROUP_CONCAT(cname, '; ') FROM (
                       SELECT c.name AS cname
                       FROM company_tags ct2
                       JOIN companies c ON c.id = ct2.company_id
                       WHERE ct2.tag_id = t.val LIMIT 10
                   )) AS company_names
            FROM (
                SELECT ct.tag_id AS val,
                       tg.name  AS display_val,
                       tg.color AS tag_color,
                       COUNT(*) AS cnt
                FROM company_tags ct
                JOIN tags tg ON ct.tag_id = tg.id
                GROUP BY ct.tag_id
                HAVING cnt >= ?
                ORDER BY {order_sql}
                LIMIT ? OFFSET ?
            ) t
        """, [min_count, per_page, offset]).fetchall()

    else:
        total = 0
        rows = []

    pages = max(1, math.ceil(total / per_page)) if total else 1

    return render_template("_relation_groups.html",
                           dup_type=dup_type, rows=rows, total=total,
                           page=page, pages=pages, per_page=per_page,
                           sort_by=sort_by, min_count=min_count)


@bp.route("/relation-group")
def relation_group_detail():
    """单个关联分组的详情：列出该分组下的所有企业"""
    dup_type = (request.args.get("dup_type") or "phone").strip()
    val = (request.args.get("val") or "").strip()
    page = sanitize_page(request.args)
    per_page = sanitize_per_page(request.args, default=PER_PAGE)
    offset = (page - 1) * per_page

    # 标签/图标映射（用于完整页面显示）
    field_labels = {
        "phone": ("电话", "📞"),
        "email": ("邮箱", "📧"),
        "legal_person": ("法人", "👤"),
        "tag": ("标签", "🏷️"),
        "shareholder": ("股东", "💼"),
        "industry": ("行业", "🏭"),
    }
    field_label, field_icon = field_labels.get(dup_type, ("关联", "🔗"))

    if not val:
        companies = []
        total = 0
    else:
        from queries import COMPANY_LIST_PHONE_SUBQUERY

        if dup_type == "phone":
            total = g.db.execute(
                "SELECT COUNT(DISTINCT company_id) FROM company_phones "
                "WHERE normalized_phone = ?",
                [val]
            ).fetchone()[0]
            companies = g.db.execute(f"""
                SELECT c.id, c.name, c.district, c.legal_person, c.business_status,
                       {COMPANY_LIST_PHONE_SUBQUERY}
                FROM companies c
                JOIN company_phones cp ON cp.company_id = c.id
                WHERE cp.normalized_phone = ?
                ORDER BY c.name LIMIT ? OFFSET ?
            """, [val, per_page, offset]).fetchall()

        elif dup_type == "email":
            total = g.db.execute(
                "SELECT COUNT(*) FROM companies WHERE normalized_email = ?",
                [val]
            ).fetchone()[0]
            companies = g.db.execute(f"""
                SELECT c.id, c.name, c.district, c.legal_person, c.business_status,
                       {COMPANY_LIST_PHONE_SUBQUERY}
                FROM companies c
                WHERE c.normalized_email = ?
                ORDER BY c.name LIMIT ? OFFSET ?
            """, [val, per_page, offset]).fetchall()

        elif dup_type == "legal_person":
            total = g.db.execute(
                "SELECT COUNT(*) FROM companies WHERE normalized_legal_person = ?",
                [val]
            ).fetchone()[0]
            companies = g.db.execute(f"""
                SELECT c.id, c.name, c.district, c.legal_person, c.business_status,
                       {COMPANY_LIST_PHONE_SUBQUERY}
                FROM companies c
                WHERE c.normalized_legal_person = ?
                ORDER BY c.name LIMIT ? OFFSET ?
            """, [val, per_page, offset]).fetchall()

        else:
            total = 0
            companies = []

    pages = max(1, math.ceil(total / per_page)) if total else 1

    ctx = dict(dup_type=dup_type, val=val, companies=companies,
               total=total, page=page, pages=pages, per_page=per_page,
               field_label=field_label, field_icon=field_icon)

    # HTMX 请求返回片段，整页请求返回完整页面
    if request.headers.get("HX-Request"):
        return render_template("_relation_group_detail.html", **ctx)
    return render_template("relation_discovery.html", **ctx)


@bp.route("/browse/relation-group-detail")
def browse_relation_group_detail():
    """兼容旧路径"""
    return relation_group_detail()


@bp.route("/browse/relation-top")
def browse_relation_top():
    """TOP 50 关联企业排行"""
    companies = g.db.execute("""
        SELECT
            c.id, c.name,
            COUNT(DISTINCT cp.normalized_phone) AS phone_count,
            COUNT(DISTINCT cs.normalized_name) AS shareholder_count,
            (SELECT COUNT(*) FROM companies c2
             WHERE c2.normalized_legal_person = c.normalized_legal_person
               AND c2.normalized_legal_person IS NOT NULL
               AND c2.normalized_legal_person <> '') AS legal_person_count,
            (SELECT COUNT(*) FROM companies c2
             WHERE c2.normalized_email = c.normalized_email
               AND c2.normalized_email IS NOT NULL
               AND c2.normalized_email <> '') AS email_count
        FROM companies c
        LEFT JOIN company_phones cp ON cp.company_id = c.id
        LEFT JOIN company_shareholders cs ON cs.company_id = c.id
        GROUP BY c.id
        ORDER BY (phone_count + shareholder_count + legal_person_count + email_count) DESC
        LIMIT 50
    """).fetchall()

    return render_template("_relation_top.html", companies=companies)


# ── 电话查询页 ──────────────────────────────────────────────────────────────

@bp.route("/phones")
def phones_page():
    """电话查询页面"""
    return render_template("phones.html")
