"""统计页面：电话重复、股东、法人、行业。"""
from flask import Blueprint, g, request, render_template

from queries import (
    DEFAULT_PER_PAGE, sanitize_page, sanitize_min_count,
    stats_grouped, phone_stats_grouped, paginate,
)

bp = Blueprint('stats_bp', __name__)

PER_PAGE = DEFAULT_PER_PAGE


# ── 电话重复统计 ────────────────────────────────────────────────────────────

@bp.route("/stats/phone")
def stats_phone():
    page = sanitize_page(request.args)
    min_count = sanitize_min_count(request.args)

    total, pages, rows = phone_stats_grouped(g.db, page, PER_PAGE, min_count)

    return render_template("stats_phone.html", rows=rows,
                           total=total, min_count=min_count,
                           page=page, pages=pages)


# ── 股东关联统计 ────────────────────────────────────────────────────────────

@bp.route("/stats/shareholder")
def stats_shareholder():
    """股东关联统计 - 按股东分组，显示关联企业数"""
    page = sanitize_page(request.args)
    min_count = sanitize_min_count(request.args)

    total = g.db.execute("""
        SELECT COUNT(*) FROM (
            SELECT normalized_name FROM company_shareholders
            WHERE normalized_name IS NOT NULL AND normalized_name <> ''
            GROUP BY normalized_name
            HAVING COUNT(DISTINCT company_id) >= ?
        )
    """, [min_count]).fetchone()[0]

    pages, offset = paginate(total, page, PER_PAGE)

    # 子查询 LIMIT 10 截断：避免大组 GROUP_CONCAT 拖慢，同时绕过
    # SQLite 3.51+ 不支持 GROUP_CONCAT(DISTINCT col, sep) 的限制
    rows = g.db.execute("""
        SELECT t.normalized_name,
               t.display_name,
               t.cnt,
               (SELECT GROUP_CONCAT(cname, '; ') FROM (
                   SELECT c.name AS cname
                   FROM company_shareholders cs2
                   JOIN companies c ON c.id = cs2.company_id
                   WHERE cs2.normalized_name = t.normalized_name
                   LIMIT 10
               )) AS company_names
        FROM (
            SELECT cs.normalized_name,
                   MIN(cs.name) AS display_name,
                   COUNT(DISTINCT cs.company_id) AS cnt
            FROM company_shareholders cs
            WHERE cs.normalized_name IS NOT NULL AND cs.normalized_name <> ''
            GROUP BY cs.normalized_name
            HAVING cnt >= ?
            ORDER BY cnt DESC
            LIMIT ? OFFSET ?
        ) t
    """, [min_count, PER_PAGE, offset]).fetchall()

    return render_template("stats_shareholder.html", rows=rows,
                           total=total, min_count=min_count,
                           page=page, pages=pages)


# ── 通用字段统计（法人 / 行业） ─────────────────────────────────────────────

def _stats_generic(field, title, subtitle, endpoint):
    """通用统计视图：按某字段分组，统计企业数量。"""
    page = sanitize_page(request.args)
    min_count = sanitize_min_count(request.args)

    total, pages, rows = stats_grouped(g.db, field, page, PER_PAGE, min_count)

    return render_template("stats_list.html",
                           field=field, endpoint=endpoint,
                           title=title, subtitle=subtitle,
                           rows=rows, total=total, min_count=min_count,
                           page=page, pages=pages)


@bp.route("/stats/legal_person")
def stats_legal_person():
    return _stats_generic("legal_person", "法定代表人统计",
                          "担任多家企业法人的记录，按企业数量排序",
                          "stats_bp.stats_legal_person")


@bp.route("/stats/industry")
def stats_industry():
    return _stats_generic("industry", "行业统计",
                          "各行业的企业数量分布",
                          "stats_bp.stats_industry")
