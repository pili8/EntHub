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
            email                 TEXT,
            normalized_email      TEXT,
            normalized_legal_person TEXT,
            other_email           TEXT,
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
        CREATE INDEX IF NOT EXISTS idx_email           ON companies(email);
        CREATE INDEX IF NOT EXISTS idx_norm_email        ON companies(normalized_email);
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
            FOREIGN KEY (company_id) REFERENCES companies(id)
        );

        CREATE INDEX IF NOT EXISTS idx_csh_norm_name   ON company_shareholders(normalized_name);
        CREATE INDEX IF NOT EXISTS idx_csh_company_id  ON company_shareholders(company_id);

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
    _migrate(conn, "companies", "normalized_email", "TEXT")

    conn.commit()
    conn.close()


def _migrate(conn, table, column, col_type):
    """Add column if it doesn't exist yet."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
