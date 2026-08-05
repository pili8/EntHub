#!/usr/bin/env python3
"""迁移脚本：按新规则重新分配所有公司的主号。

新优先级规则：
1. 如果旧库中有 is_recommended=1 的号码 → 第一个推荐号为主号
2. 否则保持现有主号（如果有）
3. 否则按 id 顺序取第一个号码为主号

同时清理：确保每个公司最多一个主号。

用法：
    python3 scripts/migrate_primary_phones.py [--dry-run]
"""
import sqlite3
import sys
import os

# 添加项目根目录到 path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from db import DB_PATH


def check_column_exists(conn, table, column):
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    return column in cols


def main():
    dry_run = "--dry-run" in sys.argv

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    has_is_recommended = check_column_exists(conn, "company_phones", "is_recommended")

    # 获取所有有电话的公司
    companies = conn.execute("""
        SELECT DISTINCT company_id FROM company_phones ORDER BY company_id
    """).fetchall()

    stats = {
        "total_companies": len(companies),
        "already_ok": 0,
        "fixed_no_primary": 0,
        "fixed_multi_primary": 0,
        "fixed_recommended": 0,
    }

    for company in companies:
        cid = company["company_id"]

        # 查看当前主号情况
        primaries = conn.execute(
            "SELECT id, normalized_phone FROM company_phones WHERE company_id = ? AND is_primary = 1 ORDER BY id",
            [cid]
        ).fetchall()

        # 检查是否有推荐号（旧库可能有）
        recommended_phone = None
        if has_is_recommended:
            rec = conn.execute(
                "SELECT id, normalized_phone FROM company_phones WHERE company_id = ? AND is_recommended = 1 ORDER BY id LIMIT 1",
                [cid]
            ).fetchone()
            if rec:
                recommended_phone = rec["normalized_phone"]

        if recommended_phone:
            # 优先用推荐号做主号
            conn.execute("UPDATE company_phones SET is_primary = 0 WHERE company_id = ?", [cid])
            conn.execute(
                "UPDATE company_phones SET is_primary = 1 WHERE company_id = ? AND normalized_phone = ?",
                [cid, recommended_phone]
            )
            stats["fixed_recommended"] += 1
        elif len(primaries) == 0:
            # 没有主号 → 取第一个
            first = conn.execute(
                "SELECT id FROM company_phones WHERE company_id = ? ORDER BY id LIMIT 1",
                [cid]
            ).fetchone()
            if first:
                conn.execute("UPDATE company_phones SET is_primary = 1 WHERE id = ?", [first["id"]])
            stats["fixed_no_primary"] += 1
        elif len(primaries) > 1:
            # 多个主号 → 保留第一个，清除其余
            keep_id = primaries[0]["id"]
            conn.execute(
                "UPDATE company_phones SET is_primary = 0 WHERE company_id = ? AND id != ?",
                [cid, keep_id]
            )
            stats["fixed_multi_primary"] += 1
        else:
            # 正好一个主号 → 无需改动
            stats["already_ok"] += 1

    if dry_run:
        conn.rollback()
        print("【试运行模式】以下为将要执行的变更：")
    else:
        conn.commit()
        print("【已执行】主号迁移完成。")

    print(f"\n统计：")
    print(f"  总公司数:           {stats['total_companies']}")
    print(f"  已正确（无需改动）:  {stats['already_ok']}")
    print(f"  修复（无主号→补主号）: {stats['fixed_no_primary']}")
    print(f"  修复（多主号→留一个）: {stats['fixed_multi_primary']}")
    if has_is_recommended:
        print(f"  修复（推荐号→设主号）: {stats['fixed_recommended']}")
    else:
        print(f"  推荐号迁移:         跳过（库中无 is_recommended 列）")

    conn.close()


if __name__ == "__main__":
    main()
