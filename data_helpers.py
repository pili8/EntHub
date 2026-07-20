"""企业数据工具函数：电话和股东的多值字段拆分、同步、合并。

抽离自 app.py，供 Web 路由、REST API、导入流程复用。
"""
from utils import normalize_phone, normalize_person_name


# ── 电话 ────────────────────────────────────────────────────────────────────

def split_phones(phone_str, other_phone_str):
    """拆分 phone 和 other_phone 字段。

    返回 [(raw_phone, normalized_phone), ...]，第一条为主号。
    去重按 normalized_phone。
    """
    result = []
    seen = set()

    # 主号
    if phone_str and str(phone_str).strip():
        raw = str(phone_str).strip()
        norm = normalize_phone(raw)
        if norm and norm not in seen:
            result.append((raw, norm))
            seen.add(norm)

    # 副号 - 按 ; ； , 拆分
    if other_phone_str and str(other_phone_str).strip():
        parts = (str(other_phone_str)
                 .replace("；", ";")
                 .replace(",", ";")
                 .split(";"))
        for p in parts:
            raw = p.strip()
            if not raw:
                continue
            norm = normalize_phone(raw)
            if norm and norm not in seen:
                result.append((raw, norm))
                seen.add(norm)

    return result


def _split_recommended(recommended_str):
    """拆分推荐电话字段。"""
    if not recommended_str or not str(recommended_str).strip():
        return []
    parts = (str(recommended_str)
             .replace("；", ";")
             .replace(",", ";")
             .split(";"))
    result = []
    for p in parts:
        raw = p.strip()
        if raw:
            norm = normalize_phone(raw)
            if norm:
                result.append((raw, norm))
    return result


def sync_phones(db, company_id, phone_str, other_phone_str,
                recommended_str=""):
    """全量重建：先删除该公司所有电话，再插入。

    用于新增/编辑/导入新公司。
    """
    db.execute(
        "DELETE FROM company_phones WHERE company_id = ?",
        [company_id]
    )
    phones = split_phones(phone_str, other_phone_str)
    for i, (raw, norm) in enumerate(phones):
        db.execute(
            "INSERT INTO company_phones "
            "(company_id, phone, normalized_phone, is_primary) "
            "VALUES (?, ?, ?, ?)",
            [company_id, raw, norm, 1 if i == 0 else 0]
        )

    # 推荐电话：单独标记，展示时排主号之后
    for raw, norm in _split_recommended(recommended_str):
        db.execute(
            "INSERT INTO company_phones "
            "(company_id, phone, normalized_phone, is_primary, is_recommended) "
            "VALUES (?, ?, ?, 0, 1)",
            [company_id, raw, norm]
        )


def merge_phones(db, company_id, phone_str, other_phone_str,
                 recommended_str=""):
    """增量合并：保留已有电话，仅追加新号码。

    用于导入时遇到已存在公司，避免覆盖主号。
    """
    # 已有号码集合
    existing = db.execute(
        "SELECT normalized_phone FROM company_phones WHERE company_id = ?",
        [company_id]
    ).fetchall()
    existing_norms = {row["normalized_phone"] for row in existing}

    # 是否已有主号
    has_primary = db.execute(
        "SELECT COUNT(*) AS count FROM company_phones "
        "WHERE company_id = ? AND is_primary = 1",
        [company_id]
    ).fetchone()["count"] > 0

    # 追加新号码
    for raw, norm in split_phones(phone_str, other_phone_str):
        if norm and norm not in existing_norms:
            is_primary = 1 if not has_primary else 0
            db.execute(
                "INSERT INTO company_phones "
                "(company_id, phone, normalized_phone, is_primary) "
                "VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, is_primary]
            )
            existing_norms.add(norm)
            if is_primary:
                has_primary = True

    # 追加推荐电话
    for raw, norm in _split_recommended(recommended_str):
        if norm and norm not in existing_norms:
            db.execute(
                "INSERT INTO company_phones "
                "(company_id, phone, normalized_phone, is_primary, is_recommended) "
                "VALUES (?, ?, ?, 0, 1)",
                [company_id, raw, norm]
            )
            existing_norms.add(norm)


# ── 股东 ────────────────────────────────────────────────────────────────────

def split_shareholders(shareholders_str):
    """拆分股东字符串为 [(raw, normalized), ...]。"""
    if not shareholders_str:
        return []
    parts = (str(shareholders_str)
             .replace("；", ";")
             .replace(",", ";")
             .replace("，", ";")
             .split(";"))
    result = []
    for p in parts:
        raw = p.strip()
        if raw and raw != "-":
            norm = normalize_person_name(raw)
            if norm:
                result.append((raw, norm))
    return result


def sync_shareholders(db, company_id, shareholders_str):
    """全量重建：先删除该公司所有股东，再插入。

    用于新增/编辑。
    """
    db.execute(
        "DELETE FROM company_shareholders WHERE company_id = ?",
        [company_id]
    )
    for raw, norm in split_shareholders(shareholders_str):
        if norm:
            db.execute(
                "INSERT INTO company_shareholders "
                "(company_id, name, normalized_name) "
                "VALUES (?, ?, ?)",
                [company_id, raw, norm]
            )


def merge_shareholders(db, company_id, shareholders_str):
    """增量合并：仅追加新股东，按 normalized_name 去重。

    用于导入时遇到已存在公司。
    """
    existing = db.execute(
        "SELECT normalized_name FROM company_shareholders WHERE company_id = ?",
        [company_id]
    ).fetchall()
    existing_norms = {row["normalized_name"] for row in existing}

    for raw, norm in split_shareholders(shareholders_str):
        if norm and norm not in existing_norms:
            db.execute(
                "INSERT INTO company_shareholders "
                "(company_id, name, normalized_name) "
                "VALUES (?, ?, ?)",
                [company_id, raw, norm]
            )
            existing_norms.add(norm)
