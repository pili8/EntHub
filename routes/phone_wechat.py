"""电话微信号管理 API。

每个号码一条微信信息（normalized_phone 为主键）。

API：
  GET    /api/phone/<normalized>/wechat    获取某号码的微信信息
  POST   /api/phone/<normalized>/wechat    设置微信信息（upsert）
  DELETE /api/phone/<normalized>/wechat    清除微信信息
"""
import sqlite3
from flask import Blueprint, g, request, jsonify

bp = Blueprint('phone_wechat_bp', __name__)


@bp.route("/api/phone/<normalized>/wechat", methods=["GET"])
def get_phone_wechat(normalized):
    """获取某号码的微信信息"""
    row = g.db.execute(
        "SELECT wechat_name, wechat_id, note FROM phone_wechat WHERE normalized_phone = ?",
        (normalized,)
    ).fetchone()
    if row:
        return jsonify({"code": 0, "message": "ok", "data": {"wechat": dict(row)}})
    return jsonify({"code": 0, "message": "ok", "data": {"wechat": None}})


@bp.route("/api/phone/<normalized>/wechat", methods=["POST"])
def set_phone_wechat(normalized):
    """设置微信信息（upsert：替换已有）"""
    data = request.json or {}
    wechat_name = (data.get("wechat_name") or "").strip()
    wechat_id = (data.get("wechat_id") or "").strip()
    note = (data.get("note") or "").strip()

    # 允许只保存备注（不填微信昵称）
    # 如果全部为空则删除记录
    if not wechat_name and not wechat_id and not note:
        g.db.execute("DELETE FROM phone_wechat WHERE normalized_phone = ?", (normalized,))
        g.db.commit()
        return jsonify({"code": 0, "message": "已清除", "data": None})

    try:
        g.db.execute(
            """INSERT OR REPLACE INTO phone_wechat
               (normalized_phone, wechat_name, wechat_id, note, updated_at)
               VALUES (?, ?, ?, ?, datetime('now', 'localtime'))""",
            (normalized, wechat_name, wechat_id or None, note or None)
        )
        g.db.commit()
        return jsonify({"code": 0, "message": "微信信息已保存", "data": None})
    except sqlite3.IntegrityError:
        return jsonify({"code": 1001, "message": "保存失败", "data": None}), 400


@bp.route("/api/phone/<normalized>/wechat", methods=["DELETE"])
def clear_phone_wechat(normalized):
    """清除微信信息"""
    g.db.execute(
        "DELETE FROM phone_wechat WHERE normalized_phone = ?",
        (normalized,)
    )
    g.db.commit()
    return jsonify({"code": 0, "message": "微信信息已清除", "data": None})
