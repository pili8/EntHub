#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清理 company_phones 表中的脏数据（含分号/分隔符的号码）。

脏数据来源：去重流程错误拼接产生，如：
    phone            = '13348917630;028-68739065'
    normalized_phone = '1334891763002868739065'  (22 位，错误拼接)

本脚本：
  1. 用分隔符拆分 phone 字段
  2. 对每个子号调 normalize_phone 重新归一化
  3. 与同公司已有 normalized_phone 比对去重
  4. 删除脏记录，插入净化的新记录

使用方式：
    # 默认 dry-run（只看不改）
    venv/bin/python scripts/cleanup_dirty_phones.py

    # 指定公司做小样本验证
    venv/bin/python scripts/cleanup_dirty_phones.py --dry-run --company-id 332774

    # 确认执行全量清理
    venv/bin/python scripts/cleanup_dirty_phones.py --confirm
"""
import sys
import os
import re
import shutil
import sqlite3
import argparse
from datetime import datetime

# 让脚本能 import 项目根目录下的 utils.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from utils import normalize_phone  # noqa: E402

# ── 常量 ────────────────────────────────────────────────────────────────────
# 拆分脏号码用的分隔符（半角/全角；,、以及斜杠）
SEPARATOR_PATTERN = re.compile(r"[;；,，、/]")
# NOT NULL 字段不能填空字符串以外的 NULL，但归一化后空字符串本身没意义，
# 直接跳过即可。
DEFAULT_DB = "data/enthub.db"
BACKUP_DIR = os.path.expanduser("~/.enthub/backups")

# 安全门槛：归一化后超过此长度的视为异常，不再插入
MAX_NORMALIZED_LEN = 18  # 合法座机+分机可达 17 位（如 0851-86770999-8035）；脏数据拼接动辄 50+ 位


def parse_args():
    p = argparse.ArgumentParser(
        description="清理 company_phones 表中含分隔符的脏号码")
    p.add_argument("--db", default=DEFAULT_DB,
                   help=f"数据库路径（默认 {DEFAULT_DB}）")
    p.add_argument("--dry-run", action="store_true",
                   help="只统计不修改（不带 --confirm 时的默认行为）")
    p.add_argument("--confirm", action="store_true",
                   help="确认执行修改（不加永远是 dry-run）")
    p.add_argument("--company-id", type=int, default=None,
                   help="只处理指定 company_id（用于测试）")
    return p.parse_args()


def make_backup(db_path):
    """备份 db 到 ~/.enthub/backups/before-phone-cleanup-YYYYMMDD-HHMMSS.db。
    失败抛异常，调用方终止。
    """
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = os.path.join(
        BACKUP_DIR, f"before-phone-cleanup-{ts}.db")
    shutil.copy2(db_path, backup_path)
    # 校验备份大小
    orig_size = os.path.getsize(db_path)
    backup_size = os.path.getsize(backup_path)
    if backup_size != orig_size:
        raise RuntimeError(
            f"备份大小不一致，原 {orig_size} 字节 / 备份 {backup_size} 字节，"
            f"已删除残缺备份 {backup_path}")
    print(f"已备份：{backup_path} ({backup_size:,} 字节)")
    return backup_path


def split_phone(raw):
    """拆分脏号码字符串，返回去空后的子号列表。"""
    parts = SEPARATOR_PATTERN.split(str(raw))
    out = []
    for p in parts:
        p = p.strip()
        if p:
            out.append(p)
    return out


def fetch_dirty_rows(cur, company_id=None):
    """抓取所有 phone 含分号的脏记录。"""
    if company_id is None:
        cur.execute(
            "SELECT id, company_id, phone, normalized_phone "
            "FROM company_phones WHERE phone LIKE '%;%'")
    else:
        cur.execute(
            "SELECT id, company_id, phone, normalized_phone "
            "FROM company_phones WHERE phone LIKE '%;%' AND company_id=?",
            (company_id,))
    return cur.fetchall()


def get_existing_normalized(cur, company_id):
    """返回某公司当前所有 normalized_phone 集合。"""
    cur.execute(
        "SELECT normalized_phone FROM company_phones WHERE company_id=?",
        (company_id,))
    return {row[0] for row in cur.fetchall()}


def compute_plan(cur, dirty_rows):
    """根据脏记录计算清理计划（不写库）。

    返回 dict：
        to_delete:      [dirty_id, ...]              要删除的脏记录 id
        to_insert:      [(company_id, phone, norm), ...] 要新增的记录
        skipped_dup:    int                          因已存在跳过的子号数
        per_company:    set of company_id            受影响的公司
    """
    to_delete = []
    to_insert = []
    skipped_dup = 0
    per_company = set()

    # 每家公司的"已存在集合"在循环中维护：脏记录互拆出的同号也算重复
    company_existing = {}

    for dirty_id, company_id, phone, _bad_norm in dirty_rows:
        per_company.add(company_id)

        if company_id not in company_existing:
            # 注意：集合里包含脏记录自己的 normalized_phone（错误拼接串），
            # 但拆出的子号归一化后不会撞上错误拼接串，无影响。
            company_existing[company_id] = get_existing_normalized(
                cur, company_id)

        existing = company_existing[company_id]

        for sub in split_phone(phone):
            norm = normalize_phone(sub)
            if not norm:
                continue
            if len(norm) > MAX_NORMALIZED_LEN:
                # 异常长，跳过不插
                continue
            if norm in existing:
                skipped_dup += 1
                continue
            to_insert.append((company_id, sub, norm))
            existing.add(norm)  # 同公司后续子号撞它也算重复

        to_delete.append(dirty_id)

    return {
        "to_delete": to_delete,
        "to_insert": to_insert,
        "skipped_dup": skipped_dup,
        "per_company": per_company,
    }


def run_cleanup(conn, company_id=None):
    """执行清理（在已开启的事务里），返回统计 dict。"""
    cur = conn.cursor()
    dirty_rows = fetch_dirty_rows(cur, company_id)
    plan = compute_plan(cur, dirty_rows)

    # 真正写库
    if plan["to_delete"]:
        cur.executemany(
            "DELETE FROM company_phones WHERE id=?",
            [(rid,) for rid in plan["to_delete"]])
    if plan["to_insert"]:
        cur.executemany(
            "INSERT INTO company_phones "
            "(company_id, phone, normalized_phone, is_primary) "
            "VALUES (?, ?, ?, 0)",
            plan["to_insert"])

    return {
        "scanned": len(dirty_rows),
        "companies": len(plan["per_company"]),
        "split_out": len(plan["to_insert"]) + plan["skipped_dup"],
        "skipped_dup": plan["skipped_dup"],
        "inserted": len(plan["to_insert"]),
        "deleted": len(plan["to_delete"]),
    }


def count_total(cur):
    cur.execute("SELECT COUNT(*) FROM company_phones")
    return cur.fetchone()[0]


def count_distinct_normalized(cur):
    cur.execute("SELECT COUNT(DISTINCT normalized_phone) FROM company_phones")
    return cur.fetchone()[0]


def count_dirty_remaining(cur):
    cur.execute("SELECT COUNT(*) FROM company_phones WHERE phone LIKE '%;%'")
    return cur.fetchone()[0]


def count_overlong_normalized(cur):
    cur.execute(
        "SELECT COUNT(*) FROM company_phones WHERE LENGTH(normalized_phone) > ?",
        (MAX_NORMALIZED_LEN,))
    return cur.fetchone()[0]


def main():
    args = parse_args()

    # 没显式带 --confirm 时永远是 dry-run
    is_dry_run = (not args.confirm) or args.dry_run
    if args.dry_run and args.confirm:
        # 同时给了两个，按 dry-run 处理（保守）
        is_dry_run = True
    # 注意：上面逻辑下，--dry-run --confirm 也算 dry-run；
    # 真要执行就只用 --confirm 即可。

    db_path = args.db
    if not os.path.exists(db_path):
        print(f"错误：数据库文件不存在：{db_path}", file=sys.stderr)
        sys.exit(1)

    mode = "DRY-RUN（只统计不修改）" if is_dry_run else "CONFIRM（将写入数据库）"
    print(f"模式：{mode}")
    print(f"数据库：{db_path}")
    if args.company_id is not None:
        print(f"限定 company_id：{args.company_id}")
    print()

    # ── 备份 ────────────────────────────────────────────────────────────────
    # 即使 dry-run 也做备份无害，但 dry-run 完全不写库，没必要污染备份目录。
    # 只有 confirm 模式才备份。
    if not is_dry_run:
        try:
            make_backup(db_path)
        except Exception as e:
            print(f"备份失败，终止：{e}", file=sys.stderr)
            sys.exit(2)
        print()

    start = datetime.now()

    # ── 连库 ────────────────────────────────────────────────────────────────
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # 清理前快照
        before_total = count_total(conn.cursor())
        before_distinct = count_distinct_normalized(conn.cursor())
        print(f"清理前：总记录 {before_total:,} 条，"
              f"去重 normalized_phone {before_distinct:,} 条")

        if is_dry_run:
            # dry-run：在临时事务里 SELECT 算计划，再回滚
            conn.execute("BEGIN")
            stats = run_cleanup(conn, args.company_id)
            conn.execute("ROLLBACK")
        else:
            # confirm：在事务里做，验证后提交
            conn.execute("BEGIN")
            try:
                stats = run_cleanup(conn, args.company_id)

                # 清理后快照（事务内可见）
                after_total = count_total(conn.cursor())
                after_distinct = count_distinct_normalized(conn.cursor())
                dirty_left = count_dirty_remaining(conn.cursor())
                overlong_left = count_overlong_normalized(conn.cursor())

                # 如果限定了 company_id，安全检查只在该公司范围内做更合理；
                # 但全表级别的"剩余脏数 >= 0"仍可校验。
                ok = True
                if args.company_id is None:
                    # 全量模式：必须保证全表 0 脏号、0 超长
                    if dirty_left != 0:
                        print(f"安全检查失败：清理后仍残留脏记录 {dirty_left} 条",
                              file=sys.stderr)
                        ok = False
                    if overlong_left != 0:
                        print(f"安全检查失败：清理后仍残留超长 normalized "
                              f"{overlong_left} 条", file=sys.stderr)
                        ok = False
                else:
                    # 单公司模式：只校验该公司范围内
                    cur = conn.cursor()
                    cur.execute(
                        "SELECT COUNT(*) FROM company_phones "
                        "WHERE phone LIKE '%;%' AND company_id=?",
                        (args.company_id,))
                    if cur.fetchone()[0] != 0:
                        print(f"安全检查失败：company_id={args.company_id} "
                              f"仍残留脏记录", file=sys.stderr)
                        ok = False

                if not ok:
                    raise RuntimeError("安全检查未通过，回滚")

                # 输出对比
                delta_total = after_total - before_total
                delta_distinct = after_distinct - before_distinct
                print(f"清理后：总记录 {after_total:,} 条（{delta_total:+d}），"
                      f"去重 normalized_phone {after_distinct:,} 条"
                      f"（{delta_distinct:+d}）")
                if args.company_id is None:
                    print(f"清理后残留脏记录（含分号）：{dirty_left} 条（应为 0）")
                    print(f"清理后残留超长 normalized（>{MAX_NORMALIZED_LEN}）："
                          f"{overlong_left} 条（应为 0）")

                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

        elapsed = (datetime.now() - start).total_seconds()

        # ── 统计输出 ────────────────────────────────────────────────────────
        print()
        print("=== 清理统计 ===")
        print(f"扫描脏记录: {stats['scanned']:,} 条")
        print(f"涉及企业: {stats['companies']:,} 家")
        print(f"拆分出新号码: {stats['split_out']:,} 条")
        print(f"其中已在库（去重跳过）: {stats['skipped_dup']:,} 条")
        print(f"实际新增: {stats['inserted']:,} 条")
        print(f"删除脏记录: {stats['deleted']:,} 条")
        print(f"耗时: {elapsed:.2f} 秒")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
