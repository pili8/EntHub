"""标签管理：页面 + REST API。

页面：/tags
API：/api/tags（GET/POST）、/api/tags/<id>（PUT/DELETE）、
     /api/companies/<id>/tags（GET/POST）、/api/companies/<id>/tags/<tid>（DELETE）
"""
import sqlite3
from flask import Blueprint, g, request, render_template, jsonify

bp = Blueprint('tags_bp', __name__)


# ── 标签管理页面 ────────────────────────────────────────────────────────────

@bp.route("/tags")
def tags_page():
    """标签管理页面"""
    tags = g.db.execute("""
        SELECT t.*, COUNT(ct.company_id) as company_count
        FROM tags t
        LEFT JOIN company_tags ct ON t.id = ct.tag_id
        GROUP BY t.id
        ORDER BY company_count DESC, t.name
    """).fetchall()
    return render_template("tags.html", tags=tags)


# ── 标签 CRUD API ───────────────────────────────────────────────────────────

@bp.route("/api/tags", methods=["POST"])
def create_tag():
    """创建标签"""
    name = request.json.get("name", "").strip()
    color = request.json.get("color", "#3b82f6")

    if not name:
        return jsonify({"code": 1001, "message": "标签名称不能为空", "data": None}), 400

    try:
        g.db.execute("INSERT INTO tags (name, color) VALUES (?, ?)", (name, color))
        g.db.commit()
        return jsonify({"code": 0, "message": "标签创建成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标签名称已存在", "data": None}), 400


@bp.route("/api/tags", methods=["GET"])
def get_all_tags():
    """获取所有标签"""
    tags = g.db.execute("SELECT * FROM tags ORDER BY name").fetchall()
    return jsonify({"code": 0, "message": "ok",
                    "data": {"results": [dict(tag) for tag in tags]}})


@bp.route("/api/tags/<int:tag_id>", methods=["PUT"])
def update_tag(tag_id):
    """更新标签"""
    name = request.json.get("name", "").strip()
    color = request.json.get("color", "#3b82f6")

    if not name:
        return jsonify({"code": 1001, "message": "标签名称不能为空", "data": None}), 400

    try:
        g.db.execute("UPDATE tags SET name=?, color=? WHERE id=?", (name, color, tag_id))
        g.db.commit()
        return jsonify({"code": 0, "message": "标签更新成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标签名称已存在", "data": None}), 400


@bp.route("/api/tags/<int:tag_id>", methods=["DELETE"])
def delete_tag(tag_id):
    """删除标签"""
    g.db.execute("DELETE FROM tags WHERE id=?", (tag_id,))
    g.db.commit()
    return jsonify({"code": 0, "message": "标签删除成功", "data": None})


# ── 企业-标签关联 API ───────────────────────────────────────────────────────

@bp.route("/api/companies/<int:company_id>/tags", methods=["GET"])
def get_company_tags(company_id):
    """获取企业标签"""
    tags = g.db.execute("""
        SELECT t.id, t.name, t.color
        FROM tags t
        JOIN company_tags ct ON t.id = ct.tag_id
        WHERE ct.company_id = ?
        ORDER BY t.name
    """, (company_id,)).fetchall()
    return jsonify({"code": 0, "message": "ok",
                    "data": {"results": [dict(tag) for tag in tags]}})


@bp.route("/api/companies/<int:company_id>/tags", methods=["POST"])
def add_company_tag(company_id):
    """为企业添加标签"""
    tag_id = request.json.get("tag_id")
    if not tag_id:
        return jsonify({"code": 1001, "message": "标签ID不能为空", "data": None}), 400

    try:
        g.db.execute(
            "INSERT INTO company_tags (company_id, tag_id) VALUES (?, ?)",
            (company_id, tag_id)
        )
        g.db.commit()
        return jsonify({"code": 0, "message": "标签添加成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标签已存在", "data": None}), 400


@bp.route("/api/companies/<int:company_id>/tags/<int:tag_id>", methods=["DELETE"])
def remove_company_tag(company_id, tag_id):
    """删除企业标签"""
    g.db.execute(
        "DELETE FROM company_tags WHERE company_id=? AND tag_id=?",
        (company_id, tag_id)
    )
    g.db.commit()
    return jsonify({"code": 0, "message": "标签删除成功", "data": None})
