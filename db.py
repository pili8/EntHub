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
            phone                 TEXT,
            normalized_phone      TEXT,
            other_phone           TEXT,
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
        CREATE INDEX IF NOT EXISTS idx_phone           ON companies(normalized_phone);
        CREATE INDEX IF NOT EXISTS idx_credit_code     ON companies(credit_code);
        CREATE INDEX IF NOT EXISTS idx_status          ON companies(status);

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

    conn.commit()
    conn.close()


def _migrate(conn, table, column, col_type):
    """Add column if it doesn't exist yet."""
    cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
