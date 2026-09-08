"""清理数据库中已有的「主电话」标记。

删除 phone_tag_map 中所有指向「主电话」的记录，
以及 phone_tags 表中的「主电话」定义。
"""
import sqlite3

db = sqlite3.connect("data/enthub.db")
db.row_factory = sqlite3.Row

tag = db.execute("SELECT id FROM phone_tags WHERE name = '主电话'").fetchone()
if tag:
    tag_id = tag["id"]
    count = db.execute(
        "SELECT COUNT(*) FROM phone_tag_map WHERE tag_id = ?", [tag_id]
    ).fetchone()[0]
    print(f"「主电话」标记 id={tag_id}，共 {count} 个号码被标记")

    db.execute("DELETE FROM phone_tag_map WHERE tag_id = ?", [tag_id])
    db.execute("DELETE FROM phone_tags WHERE id = ?", [tag_id])
    db.commit()
    print(f"已删除 {count} 条标记关联记录 + 1 条标记定义")
else:
    print("数据库中没有「主电话」标记，无需清理")

db.close()
