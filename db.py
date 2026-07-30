"""Database layer for EntHub."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "data" / "enthub.db"

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
            tags                  TEXT,
            source_file           TEXT,
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
            is_recommended   INTEGER DEFAULT 0,
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
    """)

    # Add columns for databases created before the new schema
    _migrate(conn, "companies", "enterprise_scale", "TEXT")
    _migrate(conn, "companies", "shareholders", "TEXT")
    _migrate(conn, "companies", "mailing_address", "TEXT")
    _migrate(conn, "companies", "english_name", "TEXT")
    _migrate(conn, "companies", "tags", "TEXT")
    _migrate(conn, "companies", "source_file", "TEXT")
    _migrate(conn, "company_phones", "is_recommended", "INTEGER DEFAULT 0")
    _migrate(conn, "import_preview", "will_update", "INTEGER DEFAULT 0")
    _migrate(conn, "companies", "normalized_legal_person", "TEXT")
    _migrate(conn, "company_shareholders", "position", "TEXT")

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
        ("主电话", "#3b82f6", 1),
        ("有效", "#22c55e", 2),
        ("无效", "#ef4444", 3),
        ("推销电话", "#f97316", 4),
        ("中介", "#eab308", 5),
        ("代理记账", "#8b5cf6", 6),
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
             ORDER BY p.is_primary DESC, p.is_recommended DESC) AS phone_all,
            
            (SELECT group_concat(name, '; ') 
             FROM company_shareholders s 
             WHERE s.company_id = c.id) AS shareholder_all,
            
            (SELECT group_concat(t.name, '; ') 
             FROM company_tags ct 
             JOIN tags t ON ct.tag_id = t.id 
             WHERE ct.company_id = c.id) AS tag_all
        
        FROM companies c;
    """)

    conn.commit()
    conn.close()


def _migrate(conn, table, column, col_type):
    """Add column if it doesn't exist yet."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
