"""遗留 API：旧版搜索、电话统计、批量操作。

注意：这些端点功能上与 api.py 中的 /api/companies 等有重叠，
后续会逐步迁移到 api.py，迁移完成前保持向后兼容。
"""
from flask import Blueprint, g, request, jsonify

from queries import (
    detect_query_type, text_search, search_by_phone, search_by_credit_code,
    phone_stats_grouped, COMPANY_LIST_COLUMNS,
)
from utils import normalize_phone, normalize_credit_code

bp = Blueprint('api_legacy_bp', __name__)


@bp.route("/api/search")
def api_search():
    """旧版搜索 API（建议改用 /api/companies?q=...）"""
    q = (request.args.get("q") or "").strip()
    limit = min(50, request.args.get("limit", 20, type=int))

    if not q:
        return jsonify({
            "code": 0, "message": "ok",
            "data": {"query": "", "type": "text", "count": 0, "results": []}
        })

    query_type = detect_query_type(q)

    if query_type == "phone":
        norm_q = normalize_phone(q)
        total, rows = search_by_phone(g.db, norm_q, limit, 0)
        return jsonify({"code": 0, "message": "ok", "data": {
            "query": q, "type": "phone",
            "count": total, "results": [dict(r) for r in rows]
        }})
    elif query_type == "credit_code":
        norm_q = normalize_credit_code(q)
        total, rows = search_by_credit_code(g.db, norm_q, limit, 0)
        return jsonify({"code": 0, "message": "ok", "data": {
            "query": q, "type": "credit_code",
            "count": total, "results": [dict(r) for r in rows]
        }})
    else:
        total, rows = text_search(g.db, q, limit, 0)
        return jsonify({"code": 0, "message": "ok", "data": {
            "query": q, "type": "text",
            "count": total, "results": [dict(r) for r in rows]
        }})


@bp.route("/api/phone_stats")
def api_phone_stats():
    """重复号码排行（默认 TOP 20，最多 100）"""
    limit = min(100, request.args.get("limit", 20, type=int))
    # phone_stats_grouped 需要 page/per_page/min_count，这里直接取第 1 页
    total, _, rows = phone_stats_grouped(g.db, 1, limit, 2)
    return jsonify({
        "code": 0, "message": "ok",
        "data": {"results": [dict(r) for r in rows]}
    })


# ── 批量操作 ────────────────────────────────────────────────────────────────

@bp.route("/api/companies/batch-delete", methods=["POST"])
def batch_delete_companies():
    """批量删除企业"""
    ids = request.json.get("ids", [])
    if not ids:
        return jsonify({"code": 1001, "message": "未选择企业", "data": None}), 400

    if len(ids) > 1000:
        return jsonify({"code": 1001, "message": "一次最多删除1000家企业", "data": None}), 400

    try:
        placeholders = ",".join("?" * len(ids))
        g.db.execute(f"DELETE FROM company_tags WHERE company_id IN ({placeholders})", ids)
        g.db.execute(f"DELETE FROM company_phones WHERE company_id IN ({placeholders})", ids)
        g.db.execute(f"DELETE FROM companies WHERE id IN ({placeholders})", ids)
        g.db.commit()
        return jsonify({
            "code": 0,
            "message": "批量删除成功",
            "data": {"deleted": len(ids)}
        })
    except Exception as e:
        return jsonify({"code": 2001, "message": str(e), "data": None}), 500


@bp.route("/api/companies/batch-add-tag", methods=["POST"])
def batch_add_tag():
    """批量添加标签"""
    ids = request.json.get("ids", [])
    tag_id = request.json.get("tag_id")

    if not ids:
        return jsonify({"code": 1001, "message": "未选择企业", "data": None}), 400
    if not tag_id:
        return jsonify({"code": 1001, "message": "未选择标签", "data": None}), 400
    if len(ids) > 1000:
        return jsonify({"code": 1001, "message": "一次最多操作1000家企业", "data": None}), 400

    import sqlite3
    try:
        added = 0
        for company_id in ids:
            try:
                g.db.execute(
                    "INSERT INTO company_tags (company_id, tag_id) VALUES (?, ?)",
                    (company_id, tag_id)
                )
                added += 1
            except sqlite3.IntegrityError:
                pass  # 标签已存在，跳过
        g.db.commit()
        return jsonify({
            "code": 0,
            "message": "批量添加标签成功",
            "data": {"updated": added}
        })
    except Exception as e:
        return jsonify({"code": 2001, "message": str(e), "data": None}), 500
