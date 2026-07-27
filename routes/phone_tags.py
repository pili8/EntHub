"""电话标记管理 API。

单标签模式：每个号码只有一个标记。
标记按 normalized_phone 关联，不跟企业走，所有企业共享。

API：
  GET    /api/phone_tags                 获取所有电话标记定义
  POST   /api/phone_tags                 创建电话标记
  PUT    /api/phone_tags/<id>            更新电话标记
  DELETE /api/phone_tags/<id>            删除电话标记

  GET    /api/phone/<normalized>/tags    获取某号码的标记（单个）
  POST   /api/phone/<normalized>/tags    设置号码标记（upsert，替换已有）
  DELETE /api/phone/<normalized>/tags    清除号码标记
"""
import sqlite3
from flask import Blueprint, g, request, jsonify

bp = Blueprint('phone_tags_bp', __name__)


# ── 电话标记定义 CRUD ─────────────────────────────────────────────────────────

@bp.route("/api/phone_tags", methods=["GET"])
def get_all_phone_tags():
    """获取所有电话标记定义"""
    tags = g.db.execute(
        "SELECT * FROM phone_tags ORDER BY sort_order, name"
    ).fetchall()
    return jsonify({"code": 0, "message": "ok",
                    "data": {"results": [dict(t) for t in tags]}})


@bp.route("/api/phone_tags", methods=["POST"])
def create_phone_tag():
    """创建电话标记"""
    name = (request.json.get("name") or "").strip()
    color = request.json.get("color", "#3b82f6")
    sort_order = request.json.get("sort_order", 0)

    if not name:
        return jsonify({"code": 1001, "message": "标记名称不能为空", "data": None}), 400

    try:
        g.db.execute(
            "INSERT INTO phone_tags (name, color, sort_order) VALUES (?, ?, ?)",
            (name, color, sort_order)
        )
        g.db.commit()
        return jsonify({"code": 0, "message": "标记创建成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标记名称已存在", "data": None}), 400


@bp.route("/api/phone_tags/<int:tag_id>", methods=["PUT"])
def update_phone_tag(tag_id):
    """更新电话标记"""
    name = (request.json.get("name") or "").strip()
    color = request.json.get("color", "#3b82f6")

    if not name:
        return jsonify({"code": 1001, "message": "标记名称不能为空", "data": None}), 400

    try:
        g.db.execute(
            "UPDATE phone_tags SET name=?, color=? WHERE id=?",
            (name, color, tag_id)
        )
        g.db.commit()
        return jsonify({"code": 0, "message": "标记更新成功", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "标记名称已存在", "data": None}), 400


@bp.route("/api/phone_tags/<int:tag_id>", methods=["DELETE"])
def delete_phone_tag(tag_id):
    """删除电话标记（同时清除所有号码上的该标记）"""
    g.db.execute("DELETE FROM phone_tags WHERE id=?", (tag_id,))
    g.db.commit()
    return jsonify({"code": 0, "message": "标记删除成功", "data": None})


# ── 号码-标记关联（单标签模式）──────────────────────────────────────────────

@bp.route("/api/phone/<normalized>/tags", methods=["GET"])
def get_phone_tags(normalized):
    """获取某号码的电话标记（单个）"""
    row = g.db.execute("""
        SELECT t.id, t.name, t.color
        FROM phone_tags t
        JOIN phone_tag_map m ON m.tag_id = t.id
        WHERE m.normalized_phone = ?
    """, (normalized,)).fetchone()
    if row:
        return jsonify({"code": 0, "message": "ok", "data": {"tag": dict(row)}})
    return jsonify({"code": 0, "message": "ok", "data": {"tag": None}})


@bp.route("/api/phone/<normalized>/tags", methods=["POST"])
def set_phone_tag(normalized):
    """设置号码标记（upsert：替换已有标记）"""
    tag_id = request.json.get("tag_id")
    if not tag_id:
        return jsonify({"code": 1001, "message": "标记ID不能为空", "data": None}), 400

    # INSERT OR REPLACE：单主键 normalized_phone，自动覆盖旧标记
    g.db.execute(
        "INSERT OR REPLACE INTO phone_tag_map (normalized_phone, tag_id) VALUES (?, ?)",
        (normalized, tag_id)
    )
    g.db.commit()
    return jsonify({"code": 0, "message": "标记已设置", "data": None})


@bp.route("/api/phone/<normalized>/tags", methods=["DELETE"])
def clear_phone_tag(normalized):
    """清除号码的标记"""
    g.db.execute(
        "DELETE FROM phone_tag_map WHERE normalized_phone=?",
        (normalized,)
    )
    g.db.commit()
    return jsonify({"code": 0, "message": "标记已清除", "data": None})
