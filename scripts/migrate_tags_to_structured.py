#!/usr/bin/env python3
"""一次性迁移脚本：将 companies.tags 文本字段迁移到结构化标签（tags + company_tags 表）。

迁移逻辑：
1. 查询所有 tags 文本字段非空的企业
2. 按分号拆分标签名
3. 对每个标签名：若 tags 表中不存在则自动创建（随机颜色）
4. 通过 company_tags 关联表建立企业-标签关系（INSERT OR IGNORE 去重）
5. 迁移完成后删除 companies.tags 列

运行方式：
    python scripts/migrate_tags_to_structured.py
"""
import sqlite3
import random
import sys
from pathlib import Path

# 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "enthub.db"

# 预设色板（与详情页标签选择器的颜色一致）
TAG_COLORS = [
    "#ef4444",  # 红色
    "#f97316",  # 橙色
    "#eab308",  # 黄色
    "#22c55e",  # 绿色
    "#06b6d4",  # 青色
    "#3b82f6",  # 蓝色
    "#8b5cf6",  # 紫色
    "#ec4899",  # 粉色
    "#6b7280",  # 灰色
]


def split_tags(raw):
    """按中英文分号拆分标签名，去空白，去空串。"""
    if not raw:
        return []
    parts = raw.replace("；", ";").split(";")
    return [p.strip() for p in parts if p.strip()]


def main():
    if not DB_PATH.exists():
        print(f"❌ 数据库文件不存在: {DB_PATH}")
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # 检查 tags 列是否存在
    cols = [r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()]
    if "tags" not in cols:
        print("ℹ️  companies 表中没有 tags 列，无需迁移。")
        conn.close()
        return

    # 查询有 tags 文本数据的企业
    rows = conn.execute(
        "SELECT id, name, tags FROM companies WHERE tags IS NOT NULL AND tags != ''"
    ).fetchall()

    if not rows:
        print("ℹ️  没有需要迁移的 tags 数据。")
    else:
        print(f"📋 发现 {len(rows)} 家企业有标签文本数据，开始迁移...")

        migrated_companies = 0
        created_tags = 0
        linked_tags = 0

        for row in rows:
            company_id = row["id"]
            tag_names = split_tags(row["tags"])
            if not tag_names:
                continue

            for tag_name in tag_names:
                # 查找已有标签
                existing = conn.execute(
                    "SELECT id FROM tags WHERE name = ?", [tag_name]
                ).fetchone()

                if existing:
                    tag_id = existing["id"]
                else:
                    # 创建新标签，随机颜色
                    color = random.choice(TAG_COLORS)
                    cursor = conn.execute(
                        "INSERT INTO tags (name, color) VALUES (?, ?)",
                        [tag_name, color]
                    )
                    tag_id = cursor.lastrowid
                    created_tags += 1

                # 关联企业-标签（去重）
                conn.execute(
                    "INSERT OR IGNORE INTO company_tags (company_id, tag_id) VALUES (?, ?)",
                    [company_id, tag_id]
                )
                linked_tags += 1

            migrated_companies += 1

        conn.commit()
        print(f"✅ 迁移完成：{migrated_companies} 家企业，新建 {created_tags} 个标签，"
              f"建立 {linked_tags} 个关联。")

    # 删除 tags 列（SQLite 3.35+ 支持 ALTER TABLE DROP COLUMN）
    print("🗑️  删除 companies.tags 列...")
    conn.execute("ALTER TABLE companies DROP COLUMN tags")
    conn.commit()

    # 验证
    cols_after = [r[1] for r in conn.execute("PRAGMA table_info(companies)").fetchall()]
    assert "tags" not in cols_after, "tags 列仍然存在！"
    print("✅ companies.tags 列已删除。")

    conn.close()
    print("\n🎉 迁移全部完成。")


if __name__ == "__main__":
    main()
