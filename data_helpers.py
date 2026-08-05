"""企业数据工具函数：电话、邮箱和股东的多值字段拆分、同步、合并。

抽离自 app.py，供 Web 路由、REST API、导入流程复用。
"""
import re
from utils import normalize_phone, normalize_person_name, normalize_email, validate_phone


# ── 电话 ────────────────────────────────────────────────────────────────────

def split_phones(phone_str):
    """拆分电话字段，返回 [(raw_phone, normalized_phone), ...]，第一条为主号。

    支持分隔符：; ； , ， 。 、 / 空格 换行
    智能识别：139 8485 2931（去掉空格后是合法号码）→ 作为一个号码
    """
    SEPS = [';', '；', ',', '，', '。', '、', '/']

    def _looks_like_single_with_spaces(s):
        """检查去掉所有空格后是否是一个合法号码（如 '139 8485 2931'）。"""
        no_space = re.sub(r'\s+', '', s)
        if not no_space:
            return False
        norm = normalize_phone(no_space)
        if not norm:
            return False
        valid, _, _ = validate_phone(norm)
        return valid

    def _split_line(s):
        if not s:
            return []
        s = str(s).strip()
        if not s:
            return []

        # 先按标点分隔符拆分
        result = [s]
        for sep in SEPS:
            new_result = []
            for part in result:
                new_result.extend(part.split(sep))
            result = new_result
        result = [p.strip() for p in result if p.strip()]

        # 再处理空格：如果去掉空格是合法号码则保留，否则按空格拆分
        final = []
        for part in result:
            if ' ' in part or '\t' in part:
                if _looks_like_single_with_spaces(part):
                    final.append(part)
                else:
                    for sub in part.split():
                        sub = sub.strip()
                        if sub:
                            final.append(sub)
            else:
                final.append(part)
        return final

    if not phone_str:
        return []

    # 先按换行拆分
    lines = str(phone_str).split('\n')

    result = []
    seen = set()
    for line in lines:
        for raw in _split_line(line):
            norm = normalize_phone(raw)
            if not norm or norm in seen:
                continue
            is_valid, _, _ = validate_phone(norm)
            if not is_valid:
                continue
            result.append((raw, norm))
            seen.add(norm)
    return result


def _split_recommended(recommended_str):
    """拆分推荐电话字段。"""
    if not recommended_str or not str(recommended_str).strip():
        return []
    SEPS = [';', '；', ',', '，', '。', '、', '/']
    result = [str(recommended_str)]
    for sep in SEPS:
        new_result = []
        for part in result:
            new_result.extend(part.split(sep))
        result = new_result
    out = []
    for p in result:
        raw = p.strip()
        if raw:
            norm = normalize_phone(raw)
            if norm:
                # 推荐电话也做校验
                is_valid, _, _ = validate_phone(norm)
                if is_valid:
                    out.append((raw, norm))
    return out


def sync_phones(db, company_id, phone_str, recommended_str=""):
    """全量重建：先删除该公司所有电话，再插入。

    用于新增/编辑/覆盖。
    保留已有主号：如果旧主号仍在重建后的号码列表中，保持其主号地位；
    旧主号不在列表中时，按优先级（推荐>联系）选主号。
    """
    # 记录当前主号（编辑/覆盖场景下需要保留）
    old_primary = db.execute(
        "SELECT normalized_phone FROM company_phones "
        "WHERE company_id = ? AND is_primary = 1 LIMIT 1",
        [company_id]
    ).fetchone()
    old_primary_norm = old_primary["normalized_phone"] if old_primary else None

    db.execute(
        "DELETE FROM company_phones WHERE company_id = ?",
        [company_id]
    )
    phones = split_phones(phone_str)
    recommended = _split_recommended(recommended_str)

    # 判断新列表中是否有旧主号
    primary_norm = None
    if old_primary_norm:
        for _raw, norm in phones + recommended:
            if norm == old_primary_norm:
                primary_norm = norm
                break
    if not primary_norm:
        # 无旧主号或旧主号已不在列表：按优先级选主号
        if recommended:
            primary_norm = recommended[0][1]
        elif phones:
            primary_norm = phones[0][1]

    seen = set()
    for raw, norm in phones:
        is_primary = 1 if norm == primary_norm else 0
        db.execute(
            "INSERT INTO company_phones "
            "(company_id, phone, normalized_phone, is_primary) "
            "VALUES (?, ?, ?, ?)",
            [company_id, raw, norm, is_primary]
        )
        seen.add(norm)

    # 推荐电话：并入主电话列表（去重）
    for raw, norm in recommended:
        if norm not in seen:
            is_primary = 1 if norm == primary_norm else 0
            db.execute(
                "INSERT INTO company_phones "
                "(company_id, phone, normalized_phone, is_primary) "
                "VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, is_primary]
            )
            seen.add(norm)


def merge_phones(db, company_id, phone_str, recommended_str=""):
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
    recommended = _split_recommended(recommended_str)
    primary_norm = None
    if not has_primary:
        if recommended:
            primary_norm = recommended[0][1]
        elif split_phones(phone_str):
            primary_norm = split_phones(phone_str)[0][1]

    for raw, norm in split_phones(phone_str):
        if norm and norm not in existing_norms:
            is_primary = 1 if (not has_primary and norm == primary_norm) else 0
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
    for raw, norm in recommended:
        if norm and norm not in existing_norms:
            is_primary = 1 if (not has_primary and norm == primary_norm) else 0
            db.execute(
                "INSERT INTO company_phones "
                "(company_id, phone, normalized_phone, is_primary) "
                "VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, is_primary]
            )
            existing_norms.add(norm)
            if is_primary:
                has_primary = True


# ── 邮箱 ────────────────────────────────────────────────────────────────────

def split_emails(email_str):
    """拆分邮箱字段，返回 [(raw_email, normalized_email), ...]，第一条为主邮箱。

    多邮箱按 ; ； , ， 。 、 / 分隔。
    去重按 normalized_email。
    """
    SEPS = [';', '；', ',', '，', '。', '、', '/']

    def _split(s):
        if not s:
            return []
        result = [str(s)]
        for sep in SEPS:
            new_result = []
            for part in result:
                new_result.extend(part.split(sep))
            result = new_result
        return [p.strip() for p in result if p.strip()]

    result = []
    seen = set()
    for raw in _split(email_str):
        norm = normalize_email(raw)
        if norm and norm not in seen and norm != '-':
            result.append((raw, norm))
            seen.add(norm)
    return result


def sync_emails(db, company_id, email_str):
    """全量重建：先删除该公司所有邮箱，再插入。

    用于新增/编辑/导入新公司。
    """
    db.execute(
        "DELETE FROM company_emails WHERE company_id = ?",
        [company_id]
    )
    emails = split_emails(email_str)
    for i, (raw, norm) in enumerate(emails):
        is_primary = 1 if i == 0 else 0
        db.execute(
            "INSERT INTO company_emails "
            "(company_id, email, normalized_email, is_primary) "
            "VALUES (?, ?, ?, ?)",
            [company_id, raw, norm, is_primary]
        )


def merge_emails(db, company_id, email_str):
    """增量合并：保留已有邮箱，仅追加新邮箱。

    用于导入时遇到已存在公司。
    """
    existing = db.execute(
        "SELECT normalized_email FROM company_emails WHERE company_id = ?",
        [company_id]
    ).fetchall()
    existing_norms = {row["normalized_email"] for row in existing}

    has_primary = db.execute(
        "SELECT COUNT(*) AS count FROM company_emails "
        "WHERE company_id = ? AND is_primary = 1",
        [company_id]
    ).fetchone()["count"] > 0

    for raw, norm in split_emails(email_str):
        if norm and norm not in existing_norms:
            is_primary = 1 if not has_primary else 0
            db.execute(
                "INSERT INTO company_emails "
                "(company_id, email, normalized_email, is_primary) "
                "VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, is_primary]
            )
            existing_norms.add(norm)
            if is_primary:
                has_primary = True


# ── 股东 ────────────────────────────────────────────────────────────────────

def split_shareholders(shareholders_str):
    """拆分股东字符串为 [(raw, normalized, position), ...]。

    支持 "姓名(职务)" 格式，如 "高海超(董事长);孙少闻(法定代表人,经理)"。
    """
    if not shareholders_str:
        return []
    parts = (str(shareholders_str)
             .replace("；", ";")
             .replace(",", ";")
             .replace("，", ";")
             .replace("、", ";")
             .split(";"))
    result = []
    for p in parts:
        raw = p.strip()
        if raw and raw != "-":
            position = ""
            # 解析 "姓名(职务)" 格式（兼容全角和半角括号）
            m = re.match(r'^(.+?)[（(](.+?)[）)]$', raw)
            if m:
                raw = m.group(1).strip()
                position = m.group(2).strip()
            norm = normalize_person_name(raw)
            if norm:
                result.append((raw, norm, position))
    return result


def sync_shareholders(db, company_id, shareholders_str):
    """全量重建：先删除该公司所有股东，再插入。

    用于新增/编辑。
    """
    db.execute(
        "DELETE FROM company_shareholders WHERE company_id = ?",
        [company_id]
    )
    for raw, norm, position in split_shareholders(shareholders_str):
        if norm:
            db.execute(
                "INSERT INTO company_shareholders "
                "(company_id, name, normalized_name, position) "
                "VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, position]
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

    for raw, norm, position in split_shareholders(shareholders_str):
        if norm and norm not in existing_norms:
            db.execute(
                "INSERT INTO company_shareholders "
                "(company_id, name, normalized_name, position) "
                "VALUES (?, ?, ?, ?)",
                [company_id, raw, norm, position]
            )
            existing_norms.add(norm)
