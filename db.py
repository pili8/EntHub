"""Database layer for EntHub."""
import json
import sqlite3
from pathlib import Path

import bootstrap

# 启动时确保 bootstrap.json 存在（首次升级自动迁移旧 DB）
bootstrap.ensure_bootstrap()

DB_PATH = bootstrap.get_db_path()

# Safety flag to prevent accidental deletion of production database
_PRODUCTION_DB_PROTECTED = True


def get_db():
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def reset_for_testing():
    """Reset database for testing purposes.
    
    WARNING: This will delete all data! Only use with test database.
    Set _PRODUCTION_DB_PROTECTED = False only for test databases.
    """
    if _PRODUCTION_DB_PROTECTED:
        raise RuntimeError(
            "Cannot delete production database! "
            "Set _PRODUCTION_DB_PROTECTED = False only for test databases."
        )
    if DB_PATH.exists():
        DB_PATH.unlink()
    init_db()


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS companies (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            name                  TEXT NOT NULL,
            normalized_name       TEXT,
            address               TEXT,
            annual_report_address TEXT,
            credit_code           TEXT,
            taxpayer_id           TEXT,
            registration_no       TEXT,
            org_code              TEXT,
            legal_person          TEXT,
            registered_capital    TEXT,
            paid_capital          TEXT,
            established_date      TEXT,
            approved_date         TEXT,
            business_term         TEXT,
            province              TEXT,
            city                  TEXT,
            district              TEXT,
            insured_count         TEXT,
            company_type          TEXT,
            industry              TEXT,
            former_name           TEXT,
            website               TEXT,
            normalized_legal_person TEXT,
            business_scope        TEXT,
            business_status       TEXT,
            enterprise_scale      TEXT,
            shareholders          TEXT,
            mailing_address       TEXT,
    english_name          TEXT,
    source_file           TEXT,
    note                  TEXT,
            status                TEXT NOT NULL DEFAULT 'active',
            source                TEXT DEFAULT 'manual',
            created_at            TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at            TEXT DEFAULT (datetime('now', 'localtime'))
        );

        CREATE INDEX IF NOT EXISTS idx_name            ON companies(name);
        CREATE INDEX IF NOT EXISTS idx_normalized_name ON companies(normalized_name);
        CREATE INDEX IF NOT EXISTS idx_credit_code     ON companies(credit_code);
        CREATE INDEX IF NOT EXISTS idx_status          ON companies(status);
        CREATE INDEX IF NOT EXISTS idx_province        ON companies(province);
        CREATE INDEX IF NOT EXISTS idx_city            ON companies(city);
        CREATE INDEX IF NOT EXISTS idx_district        ON companies(district);
        CREATE INDEX IF NOT EXISTS idx_business_status ON companies(business_status);
        CREATE INDEX IF NOT EXISTS idx_industry        ON companies(industry);
        CREATE INDEX IF NOT EXISTS idx_enterprise_scale ON companies(enterprise_scale);
        CREATE INDEX IF NOT EXISTS idx_norm_legal_person ON companies(normalized_legal_person);
        CREATE INDEX IF NOT EXISTS idx_registered_capital ON companies(registered_capital);
        CREATE INDEX IF NOT EXISTS idx_insured_count   ON companies(insured_count);

        CREATE TABLE IF NOT EXISTS import_preview (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            batch_id          TEXT NOT NULL,
            row_num           INTEGER,
            name              TEXT,
            normalized_name   TEXT,
            phone             TEXT,
            normalized_phone  TEXT,
            address           TEXT,
            credit_code       TEXT,
            legal_person      TEXT,
            is_duplicate      INTEGER DEFAULT 0,
            duplicate_reason  TEXT,
            will_update       INTEGER DEFAULT 0
        );

        CREATE INDEX IF NOT EXISTS idx_preview_batch ON import_preview(batch_id);

        CREATE TABLE IF NOT EXISTS company_phones (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id       INTEGER NOT NULL,
            phone            TEXT NOT NULL,
            normalized_phone TEXT NOT NULL,
            is_primary       INTEGER DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        );

        CREATE INDEX IF NOT EXISTS idx_cp_norm_phone ON company_phones(normalized_phone);
        CREATE INDEX IF NOT EXISTS idx_cp_company_id ON company_phones(company_id);

        CREATE TABLE IF NOT EXISTS company_shareholders (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id        INTEGER NOT NULL,
            name              TEXT NOT NULL,
            normalized_name   TEXT NOT NULL,
            position          TEXT,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        );

        CREATE INDEX IF NOT EXISTS idx_csh_norm_name   ON company_shareholders(normalized_name);
        CREATE INDEX IF NOT EXISTS idx_csh_company_id  ON company_shareholders(company_id);

        CREATE TABLE IF NOT EXISTS company_emails (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id        INTEGER NOT NULL,
            email             TEXT NOT NULL,
            normalized_email  TEXT NOT NULL,
            is_primary        INTEGER DEFAULT 0,
            FOREIGN KEY (company_id) REFERENCES companies(id)
        );

        CREATE INDEX IF NOT EXISTS idx_ce_norm_email   ON company_emails(normalized_email);
        CREATE INDEX IF NOT EXISTS idx_ce_company_id    ON company_emails(company_id);

        CREATE TABLE IF NOT EXISTS recent_searches (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            q           TEXT NOT NULL,
            query_type  TEXT,
            result_count INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_recent_created ON recent_searches(created_at DESC);

        CREATE TABLE IF NOT EXISTS tags (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            color       TEXT DEFAULT '#3b82f6',
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );
        CREATE INDEX IF NOT EXISTS idx_tag_name ON tags(name);

        CREATE TABLE IF NOT EXISTS company_tags (
            company_id  INTEGER NOT NULL,
            tag_id      INTEGER NOT NULL,
            created_at  TEXT DEFAULT (datetime('now', 'localtime')),
            PRIMARY KEY (company_id, tag_id),
            FOREIGN KEY (company_id) REFERENCES companies(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_ct_company ON company_tags(company_id);
        CREATE INDEX IF NOT EXISTS idx_ct_tag ON company_tags(tag_id);

        -- 电话标记定义表（跟现有 tags 表独立，专用于电话号码）
        CREATE TABLE IF NOT EXISTS phone_tags (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL UNIQUE,
            color       TEXT DEFAULT '#3b82f6',
            sort_order  INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT (datetime('now', 'localtime'))
        );

        -- 电话-标记关联表（每个号码只有一个标记，normalized_phone 为单主键）
        CREATE TABLE IF NOT EXISTS phone_tag_map (
            normalized_phone TEXT PRIMARY KEY,
            tag_id           INTEGER NOT NULL,
            created_at       TEXT DEFAULT (datetime('now', 'localtime')),
            FOREIGN KEY (tag_id) REFERENCES phone_tags(id) ON DELETE CASCADE
        );

        -- 电话微信号关联表（每个号码一条微信信息，normalized_phone 为单主键）
        CREATE TABLE IF NOT EXISTS phone_wechat (
            normalized_phone TEXT PRIMARY KEY,
            wechat_name      TEXT NOT NULL,
            wechat_id        TEXT,
            note             TEXT,
            created_at       TEXT DEFAULT (datetime('now', 'localtime')),
            updated_at       TEXT DEFAULT (datetime('now', 'localtime'))
        );
    """)

    # Add columns for databases created before the new schema
    _migrate(conn, "companies", "enterprise_scale", "TEXT")
    _migrate(conn, "companies", "shareholders", "TEXT")
    _migrate(conn, "companies", "mailing_address", "TEXT")
    _migrate(conn, "companies", "english_name", "TEXT")
    _migrate(conn, "companies", "source_file", "TEXT")
    _migrate(conn, "companies", "note", "TEXT")
    # is_recommended 已废弃，不再迁移。旧库中该列若存在不影响查询。
    _migrate(conn, "import_preview", "will_update", "INTEGER DEFAULT 0")
    _migrate(conn, "companies", "normalized_legal_person", "TEXT")
    _migrate(conn, "company_shareholders", "position", "TEXT")

    # 清理已废弃的列（sort_order, is_recommended）
    # SQLite 3.35+ 支持 DROP COLUMN，旧版本安全跳过（列存在不影响功能）
    _try_drop_column(conn, "company_phones", "sort_order")
    _try_drop_column(conn, "company_phones", "is_recommended")

    # 迁移：如果 phone_tag_map 还是旧的多标签 schema（复合主键），重建为单标签
    _pk_cols = [r for r in conn.execute("PRAGMA table_info(phone_tag_map)").fetchall() if r[5]]
    if len(_pk_cols) > 1:
        conn.execute("ALTER TABLE phone_tag_map RENAME TO phone_tag_map_old")
        conn.execute("""
            CREATE TABLE phone_tag_map (
                normalized_phone TEXT PRIMARY KEY,
                tag_id           INTEGER NOT NULL,
                created_at       TEXT DEFAULT (datetime('now', 'localtime')),
                FOREIGN KEY (tag_id) REFERENCES phone_tags(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            INSERT OR REPLACE INTO phone_tag_map (normalized_phone, tag_id, created_at)
            SELECT normalized_phone, tag_id, MAX(created_at)
            FROM phone_tag_map_old
            GROUP BY normalized_phone
        """)
        conn.execute("DROP TABLE phone_tag_map_old")

    # 插入默认电话标记（仅首次创建时）
    default_phone_tags = [
        ("有效", "#22c55e", 1),
        ("无效", "#ef4444", 2),
        ("推销电话", "#f97316", 3),
        ("中介", "#eab308", 4),
        ("代理记账", "#8b5cf6", 5),
    ]
    for name, color, order in default_phone_tags:
        conn.execute(
            "INSERT OR IGNORE INTO phone_tags (name, color, sort_order) VALUES (?, ?, ?)",
            (name, color, order)
        )

    conn.execute("""
        CREATE VIEW IF NOT EXISTS company_export AS
        SELECT 
            c.id, c.name, c.normalized_name, c.credit_code,
            c.legal_person, c.normalized_legal_person,
            (SELECT group_concat(e.email, '; ')
             FROM company_emails e
             WHERE e.company_id = c.id
             ORDER BY e.is_primary DESC) AS email_all,
            c.address, c.annual_report_address, c.mailing_address,
            c.province, c.city, c.district,
            c.registered_capital, c.paid_capital,
            c.established_date, c.approved_date, c.business_term,
            c.insured_count, c.enterprise_scale, c.company_type,
            c.industry, c.business_status,
            c.former_name, c.english_name, c.website, c.business_scope,
            c.source_file, c.status, c.source,
            c.created_at, c.updated_at,
            
            (SELECT group_concat(phone, '; ') 
             FROM company_phones p 
             WHERE p.company_id = c.id 
             ORDER BY p.is_primary DESC) AS phone_all,
            
            (SELECT group_concat(name, '; ') 
             FROM company_shareholders s 
             WHERE s.company_id = c.id) AS shareholder_all,
            
            (SELECT group_concat(t.name, '; ') 
             FROM company_tags ct 
             JOIN tags t ON ct.tag_id = t.id 
             WHERE ct.company_id = c.id) AS tag_all
        
        FROM companies c;
    """)

    # ── FTS5 全文搜索索引 ──────────────────────────────────────────────
    # trigram 分词器支持中文，3字以上查询走 FTS5（0.002s），2字回退 LIKE。
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS companies_fts USING fts5(
            content, tokenize='trigram'
        )
    """)

    # 检查是否需要重建 FTS 索引（首次添加微信内容时）
    _need_fts_rebuild = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='trigger' AND name='wechat_fts_ai'"
    ).fetchone() is None and conn.execute(
        "SELECT COUNT(*) FROM companies_fts"
    ).fetchone()[0] > 0

    # 重建 FTS 触发器：先删除旧的（可能不含微信内容），再创建新的
    for _t in ("companies_fts_ai", "companies_fts_ad", "companies_fts_au",
               "emails_fts_ai", "emails_fts_ad", "emails_fts_au"):
        conn.execute(f"DROP TRIGGER IF EXISTS {_t}")

    # 触发器：companies 表变更时同步 FTS
    # FTS 内容包含：名称/曾用名/地址/法人/股东/网站/邮箱/微信昵称+备注
    def _fts_content(ref):
        """构建 FTS 内容表达式，ref 为 'new'/'old'/'c' 等。"""
        return (
            f"COALESCE({ref}.normalized_name, '') || ' ' || "
            f"COALESCE({ref}.former_name, '') || ' ' || "
            f"COALESCE({ref}.address, '') || ' ' || "
            f"COALESCE({ref}.legal_person, '') || ' ' || "
            f"COALESCE({ref}.shareholders, '') || ' ' || "
            f"COALESCE({ref}.website, '') || ' ' || "
            f"COALESCE((SELECT group_concat(email, ' ') "
            f"         FROM company_emails WHERE company_id = {ref}.id), '') || ' ' || "
            f"COALESCE((SELECT group_concat(pw.wechat_name || ' ' || COALESCE(pw.note, ''), ' ') "
            f"         FROM phone_wechat pw "
            f"         JOIN company_phones cp ON cp.normalized_phone = pw.normalized_phone "
            f"         WHERE cp.company_id = {ref}.id), '') || ' ' || "
            f"COALESCE({ref}.note, '')"
        )

    _fts_content_expr = _fts_content('new')
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS companies_fts_ai AFTER INSERT ON companies BEGIN
            INSERT INTO companies_fts(rowid, content) VALUES (new.id, {_fts_content_expr});
        END
    """)
    conn.execute("""
        CREATE TRIGGER IF NOT EXISTS companies_fts_ad AFTER DELETE ON companies BEGIN
            DELETE FROM companies_fts WHERE rowid = old.id;
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS companies_fts_au AFTER UPDATE ON companies BEGIN
            DELETE FROM companies_fts WHERE rowid = old.id;
            INSERT INTO companies_fts(rowid, content) VALUES (new.id, {_fts_content_expr});
        END
    """)

    # 触发器：company_emails 变更时重建对应公司的 FTS 行
    # 注意：INSERT/UPDATE 用 new.company_id，DELETE 用 old.company_id
    def _build_email_trigger(action, ref):
        return (
            f"UPDATE companies_fts SET content = ("
            f"  SELECT {_fts_content('c')}"
            f"  FROM companies c WHERE c.id = {ref}.company_id"
            f") WHERE rowid = {ref}.company_id"
        )
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS emails_fts_ai AFTER INSERT ON company_emails BEGIN
            {_build_email_trigger('INSERT', 'new')};
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS emails_fts_ad AFTER DELETE ON company_emails BEGIN
            {_build_email_trigger('DELETE', 'old')};
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS emails_fts_au AFTER UPDATE ON company_emails BEGIN
            {_build_email_trigger('UPDATE', 'new')};
        END
    """)

    # 触发器：phone_wechat 变更时重建所有关联企业的 FTS 行
    # 一个号码可能被多家企业共用，需更新所有含该号码的企业。
    def _build_wechat_trigger(ref):
        return (
            f"UPDATE companies_fts SET content = ("
            f"  SELECT {_fts_content('c')}"
            f"  FROM companies c WHERE c.id = companies_fts.rowid"
            f") WHERE rowid IN ("
            f"  SELECT DISTINCT company_id FROM company_phones "
            f"  WHERE normalized_phone = {ref}.normalized_phone"
            f")"
        )
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS wechat_fts_ai AFTER INSERT ON phone_wechat BEGIN
            {_build_wechat_trigger('new')};
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS wechat_fts_ad AFTER DELETE ON phone_wechat BEGIN
            {_build_wechat_trigger('old')};
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS wechat_fts_au AFTER UPDATE ON phone_wechat BEGIN
            {_build_wechat_trigger('new')};
        END
    """)

    # 触发器：company_phones 变更时重建对应企业的 FTS 行（微信内容可能变化）
    def _build_phone_trigger(ref):
        return (
            f"UPDATE companies_fts SET content = ("
            f"  SELECT {_fts_content('c')}"
            f"  FROM companies c WHERE c.id = {ref}.company_id"
            f") WHERE rowid = {ref}.company_id"
        )
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS phones_fts_ai AFTER INSERT ON company_phones BEGIN
            {_build_phone_trigger('new')};
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS phones_fts_ad AFTER DELETE ON company_phones BEGIN
            {_build_phone_trigger('old')};
        END
    """)
    conn.execute(f"""
        CREATE TRIGGER IF NOT EXISTS phones_fts_au AFTER UPDATE ON company_phones BEGIN
            {_build_phone_trigger('new')};
        END
    """)

    # 如果 FTS 表为空但 companies 有数据，执行一次性填充
    fts_count = conn.execute("SELECT COUNT(*) FROM companies_fts").fetchone()[0]
    comp_count = conn.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
    if fts_count == 0 and comp_count > 0:
        print(f"[FTS5] 首次构建全文索引，共 {comp_count} 条记录…")
        conn.execute(f"""
            INSERT INTO companies_fts(rowid, content)
            SELECT c.id, {_fts_content('c')}
            FROM companies c
        """)
        print(f"[FTS5] 全文索引构建完成")

    # 升级迁移：FTS 索引存在但内容不含微信 → 重建
    if _need_fts_rebuild and comp_count > 0:
        print(f"[FTS5] 重建全文索引（添加微信内容），共 {comp_count} 条记录…")
        conn.execute("DELETE FROM companies_fts")
        conn.execute(f"""
            INSERT INTO companies_fts(rowid, content)
            SELECT c.id, {_fts_content('c')}
            FROM companies c
        """)
        print(f"[FTS5] 全文索引重建完成")

    # ── settings 表（key-value 配置存储，替代 config.json）──
    conn.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key         TEXT PRIMARY KEY,
            value       TEXT NOT NULL,
            updated_at  TEXT DEFAULT (datetime('now', 'localtime'))
        )
    """)

    # 从旧 config.json 迁移配置到 settings 表（仅首次执行）
    _migrate_config_to_settings(conn)

    conn.commit()
    conn.close()


def _migrate_config_to_settings(conn):
    """将旧 config.json 内容导入 settings 表（仅首次执行，完成后重命名旧文件）。"""
    old_config_path = Path(__file__).parent / "config.json"
    if not old_config_path.exists():
        return

    # 检查是否已迁移
    existing = conn.execute("SELECT COUNT(*) FROM settings WHERE key = 'config'").fetchone()[0]
    if existing > 0:
        # 已迁移过，但旧文件还在，重命名掉
        try:
            old_config_path.rename(old_config_path.with_suffix(".json.migrated"))
        except OSError:
            pass
        return

    try:
        with open(old_config_path, "r", encoding="utf-8") as f:
            old_config = json.load(f)
    except (json.JSONDecodeError, IOError):
        return

    conn.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now', 'localtime'))",
        ["config", json.dumps(old_config, ensure_ascii=False)],
    )
    conn.commit()
    print(f"[迁移] 旧 config.json 内容已导入 settings 表")

    # 重命名旧文件，避免重复迁移
    try:
        old_config_path.rename(old_config_path.with_suffix(".json.migrated"))
        print(f"[迁移] 旧 config.json 已重命名为 config.json.migrated")
    except OSError:
        pass


# ── settings 表辅助函数 ─────────────────────────────────────────────────────

def get_setting(key, default=None):
    """从 settings 表读取单个配置值。"""
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", [key]).fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_setting(key, value):
    """写入或更新 settings 表中的单个配置值。"""
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, datetime('now', 'localtime')) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = datetime('now', 'localtime')",
            [key, value],
        )
        conn.commit()
    finally:
        conn.close()


def get_all_settings() -> dict:
    """读取 settings 表所有键值对，返回 dict。"""
    conn = get_db()
    try:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        conn.close()


def _migrate(conn, table, column, col_type):
    """Add column if it doesn't exist yet."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


def _try_drop_column(conn, table, column):
    """安全删除列：SQLite 3.35+ 支持 DROP COLUMN，旧版本安全跳过。"""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        return
    try:
        conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    except sqlite3.OperationalError:
        pass  # 旧版 SQLite 不支持 DROP COLUMN，安全跳过
