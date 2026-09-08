"""一次性迁移脚本：将 companies 表中的 email/other_email 数据迁移到 company_emails 表。

运行方式：python3 scripts/migrate_emails.py
"""
import sqlite3
import re
import os
import sys

sys.path.insert(0, os.path.dirname(__file__) + '/..')
from db import DB_PATH


def main():
    db = sqlite3.connect(str(DB_PATH))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")

    # 1. companies.email -> company_emails (is_primary=1)
    rows = db.execute("""
        SELECT id, email, normalized_email FROM companies
        WHERE email IS NOT NULL AND email <> '' AND email <> '-'
    """).fetchall()
    inserted = 0
    for r in rows:
        email = r["email"].strip()
        if not email or email in ("-", "--"):
            continue
        norm = (r["normalized_email"] or email).strip().lower()
        db.execute(
            "INSERT INTO company_emails (company_id, email, normalized_email, is_primary) "
            "VALUES (?, ?, ?, 1)",
            [r["id"], email, norm],
        )
        inserted += 1
    print(f"email 主邮箱迁移: {inserted} 条")

    # 2. companies.other_email -> company_emails (is_primary=0, 支持分号分隔)
    rows2 = db.execute("""
        SELECT id, other_email FROM companies
        WHERE other_email IS NOT NULL AND other_email <> '' AND other_email <> '-'
    """).fetchall()
    inserted2 = 0
    for r in rows2:
        parts = [
            p.strip()
            for p in re.split(r"[;;,,、/]", r["other_email"])
            if p.strip() and p.strip() not in ("-", "--")
        ]
        for email in parts:
            norm = email.lower()
            db.execute(
                "INSERT INTO company_emails (company_id, email, normalized_email, is_primary) "
                "VALUES (?, ?, ?, 0)",
                [r["id"], email, norm],
            )
            inserted2 += 1
    print(f"other_email 副邮箱迁移: {inserted2} 条")

    db.commit()

    total = db.execute("SELECT COUNT(*) as n FROM company_emails").fetchone()
    print(f"company_emails 表最终记录: {total['n']} 条")

    uniq = db.execute(
        "SELECT COUNT(DISTINCT normalized_email) as n FROM company_emails"
    ).fetchone()
    print(f"去重邮箱数: {uniq['n']}")

    dup = db.execute("""
        SELECT COUNT(*) FROM (
            SELECT normalized_email FROM company_emails
            GROUP BY normalized_email HAVING COUNT(DISTINCT company_id) >= 2
        )
    """).fetchone()
    print(f"关联重复邮箱组: {dup[0]} 个")

    db.close()
    print("迁移完成。")


if __name__ == "__main__":
    main()
