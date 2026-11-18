"""
chat_store.py - Persistent SQLite schema & storage for conversations, settings,
vendors, SEPA payment runs, copilot approval actions, and month-end checklists.
"""

import sqlite3
import re
import uuid
import logging
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "invoices.db")

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    id              TEXT PRIMARY KEY,
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    is_verified     INTEGER NOT NULL DEFAULT 0,
    is_active       INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_users_email ON users(email);

CREATE TABLE IF NOT EXISTS otp_codes (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    purpose         TEXT NOT NULL,
    code_hash       TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    attempts        INTEGER NOT NULL DEFAULT 0,
    consumed        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_otp_codes_user_id ON otp_codes(user_id);

CREATE TABLE IF NOT EXISTS refresh_tokens (
    id              TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    token_hash      TEXT NOT NULL,
    expires_at      TEXT NOT NULL,
    revoked         INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_refresh_tokens_token_hash ON refresh_tokens(token_hash);

CREATE TABLE IF NOT EXISTS invoices (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id              TEXT,
    user_id             TEXT NOT NULL,
    file_name           TEXT,
    vendor_name         TEXT,
    vendor_address      TEXT,
    vat_id              TEXT,
    tax_number          TEXT,
    vendor_iban         TEXT,
    vendor_bic          TEXT,
    buyer_name          TEXT,
    buyer_address       TEXT,
    buyer_vat_id        TEXT,
    leitweg_id          TEXT,
    invoice_number      TEXT,
    document_type       TEXT DEFAULT 'invoice',
    invoice_date        TEXT,
    delivery_date       TEXT,
    due_date            TEXT,
    payment_terms       TEXT,
    skonto_terms        TEXT,
    skonto_percent      REAL DEFAULT 0.0,
    skonto_days         INTEGER DEFAULT 0,
    skonto_deadline     TEXT,
    skonto_amount       REAL DEFAULT 0.0,
    items               TEXT,
    vat_breakdown       TEXT,
    subtotal            REAL DEFAULT 0.0,
    tax                 REAL DEFAULT 0.0,
    tax_amount          REAL DEFAULT 0.0,
    discount            REAL DEFAULT 0.0,
    total_amount        REAL DEFAULT 0.0,
    currency            TEXT DEFAULT 'EUR',
    payment_status      TEXT DEFAULT 'Unpaid',
    validation_report   TEXT,
    validation_status   TEXT DEFAULT 'PENDING',
    vies_status         TEXT DEFAULT 'unchecked',
    vies_checked_at     TEXT,
    vies_details        TEXT,
    iban_change_flag    INTEGER DEFAULT 0,
    previous_iban       TEXT DEFAULT '',
    duplicate_flag      INTEGER DEFAULT 0,
    duplicate_of        TEXT DEFAULT '',
    datev_booked        INTEGER DEFAULT 0,
    datev_account       TEXT DEFAULT '',
    payment_run_id      TEXT DEFAULT '',
    uploaded_at         TEXT,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, file_name)
);

CREATE INDEX IF NOT EXISTS ix_invoices_user_id ON invoices(user_id);
CREATE INDEX IF NOT EXISTS ix_invoices_doc_id ON invoices(doc_id);
CREATE INDEX IF NOT EXISTS ix_invoices_invoice_number ON invoices(invoice_number);

CREATE VIRTUAL TABLE IF NOT EXISTS invoices_fts USING fts5(
    doc_id UNINDEXED,
    user_id UNINDEXED,
    vendor_name,
    invoice_number,
    item_descriptions,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS user_settings (
    user_id                 TEXT PRIMARY KEY,
    company_name            TEXT DEFAULT 'Rheinwerk Anlagenbau AG',
    company_address         TEXT DEFAULT 'Hafenstraße 88, 47119 Duisburg',
    vat_id                  TEXT DEFAULT 'DE119283746',
    tax_number              TEXT DEFAULT '109/5720/1842',
    iban                    TEXT DEFAULT 'DE89370400440532013000',
    bic                     TEXT DEFAULT 'COBADEFFXXX',
    leitweg_id              TEXT DEFAULT '',
    datev_chart             TEXT DEFAULT 'SKR03',
    datev_consultant_number TEXT DEFAULT '12345',
    datev_client_number     TEXT DEFAULT '40217',
    currency                TEXT DEFAULT 'EUR',
    number_format           TEXT DEFAULT '1.234,56',
    date_format             TEXT DEFAULT 'DD.MM.YYYY',
    language                TEXT DEFAULT 'de',
    updated_at              TEXT
);

CREATE TABLE IF NOT EXISTS vendors (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    vendor_name     TEXT NOT NULL,
    address         TEXT DEFAULT '',
    vat_id          TEXT DEFAULT '',
    tax_number      TEXT DEFAULT '',
    iban            TEXT DEFAULT '',
    bic             TEXT DEFAULT '',
    payment_terms   TEXT DEFAULT '',
    vies_status     TEXT DEFAULT 'unchecked',
    vies_checked_at TEXT DEFAULT '',
    updated_at      TEXT,
    UNIQUE(user_id, vendor_name)
);

CREATE INDEX IF NOT EXISTS ix_vendors_user_id ON vendors(user_id);

CREATE TABLE IF NOT EXISTS payment_runs (
    run_id                  TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    message_id              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    execution_date          TEXT NOT NULL,
    debtor_name             TEXT NOT NULL,
    debtor_iban             TEXT NOT NULL,
    debtor_bic              TEXT NOT NULL,
    tx_count                INTEGER NOT NULL,
    control_sum             REAL NOT NULL,
    file_name               TEXT NOT NULL,
    xml_content             TEXT NOT NULL,
    paid_invoices_json      TEXT DEFAULT '[]',
    blocked_invoices_json   TEXT DEFAULT '[]',
    approved_by             TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_payment_runs_user_id ON payment_runs(user_id);

CREATE TABLE IF NOT EXISTS copilot_actions (
    action_id               TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    conversation_id         TEXT NOT NULL,
    action_type             TEXT NOT NULL,
    target_invoices_json    TEXT NOT NULL,
    description             TEXT NOT NULL,
    status                  TEXT DEFAULT 'pending',
    created_at              TEXT NOT NULL,
    resolved_at             TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_copilot_actions_user_id ON copilot_actions(user_id);

CREATE TABLE IF NOT EXISTS month_end_checks (
    user_id     TEXT NOT NULL,
    month_key   TEXT NOT NULL,
    item_key    TEXT NOT NULL,
    checked     INTEGER DEFAULT 0,
    updated_at  TEXT,
    PRIMARY KEY (user_id, month_key, item_key)
);

CREATE TABLE IF NOT EXISTS conversations (
    conversation_id TEXT PRIMARY KEY,
    user_id         TEXT NOT NULL,
    title           TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    metadata        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_conversations_user_id ON conversations(user_id);
CREATE INDEX IF NOT EXISTS idx_conversations_updated_at ON conversations(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    message_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    role            TEXT NOT NULL,
    content         TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    idempotency_key TEXT,
    metadata        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_created_at      ON messages(conversation_id, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_idempotency
    ON messages(conversation_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS conversation_summaries (
    summary_id      TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
    summary_text    TEXT NOT NULL,
    summarized_up_to_message_id TEXT,
    created_at      TEXT NOT NULL,
    metadata        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_summaries_conversation_id ON conversation_summaries(conversation_id);

CREATE TABLE IF NOT EXISTS dashboard_charts (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    title       TEXT NOT NULL,
    prompt      TEXT NOT NULL,
    chart_path  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_dashboard_charts_user_id ON dashboard_charts(user_id);
"""

_INVOICE_MIGRATION_COLUMNS = [
    ("doc_id", "TEXT"),
    ("uploaded_at", "TEXT"),
    ("tax_amount", "REAL DEFAULT 0.0"),
    ("due_date", "TEXT"),
    ("delivery_date", "TEXT"),
    ("vat_id", "TEXT"),
    ("tax_number", "TEXT"),
    ("vendor_iban", "TEXT"),
    ("vendor_bic", "TEXT"),
    ("buyer_vat_id", "TEXT"),
    ("leitweg_id", "TEXT"),
    ("document_type", "TEXT DEFAULT 'invoice'"),
    ("payment_terms", "TEXT"),
    ("skonto_terms", "TEXT"),
    ("skonto_percent", "REAL DEFAULT 0.0"),
    ("skonto_days", "INTEGER DEFAULT 0"),
    ("skonto_deadline", "TEXT"),
    ("skonto_amount", "REAL DEFAULT 0.0"),
    ("vat_breakdown", "TEXT"),
    ("currency", "TEXT DEFAULT 'EUR'"),
    ("payment_status", "TEXT DEFAULT 'Unpaid'"),
    ("validation_report", "TEXT"),
    ("validation_status", "TEXT DEFAULT 'PENDING'"),
    ("vies_status", "TEXT DEFAULT 'unchecked'"),
    ("vies_checked_at", "TEXT"),
    ("vies_details", "TEXT"),
    ("iban_change_flag", "INTEGER DEFAULT 0"),
    ("previous_iban", "TEXT DEFAULT ''"),
    ("duplicate_flag", "INTEGER DEFAULT 0"),
    ("duplicate_of", "TEXT DEFAULT ''"),
    ("datev_booked", "INTEGER DEFAULT 0"),
    ("datev_account", "TEXT DEFAULT ''"),
    ("payment_run_id", "TEXT DEFAULT ''"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_db(db_path: str = _DEFAULT_DB_PATH) -> None:
    """
    Create and migrate all tables (including invoices, FTS5, settings, vendors,
    payment_runs, and chat history) in the SQLite database if they do not exist.
    Safe to call repeatedly (idempotent).
    """
    try:
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.executescript(_SCHEMA_SQL)

        existing_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(invoices)").fetchall()
        }
        for col_name, col_type in _INVOICE_MIGRATION_COLUMNS:
            if col_name not in existing_cols:
                try:
                    conn.execute(f"ALTER TABLE invoices ADD COLUMN {col_name} {col_type}")
                except sqlite3.OperationalError:
                    pass

        conn.commit()
        conn.close()
        logger.debug("Database schema initialised in %s", db_path)
    except sqlite3.Error as exc:
        logger.error("Failed to initialise database schema: %s", exc)
        raise


@contextmanager
def _get_conn(db_path: str = _DEFAULT_DB_PATH):
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# User Settings CRUD (Task 2)
# ─────────────────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "company_name": "Rheinwerk Anlagenbau AG",
    "company_address": "Hafenstraße 88, 47119 Duisburg",
    "vat_id": "DE119283746",
    "tax_number": "109/5720/1842",
    "iban": "DE89 3704 0044 0532 0130 00",
    "bic": "COBADEFFXXX",
    "leitweg_id": "04011000-1234512345-06",
    "datev_chart": "SKR03",
    "datev_consultant_number": "12345",
    "datev_client_number": "40217",
    "currency": "EUR",
    "number_format": "1.234,56",
    "date_format": "DD.MM.YYYY",
    "language": "de",
}


def get_user_settings(user_id: str, db_path: str = _DEFAULT_DB_PATH) -> Dict[str, Any]:
    init_db(db_path)
    uid = str(user_id or "default_user")
    with _get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM user_settings WHERE user_id = ?",
            (uid,),
        ).fetchone()
        if not row:
            now = _now_iso()
            conn.execute(
                """
                INSERT INTO user_settings (
                    user_id, company_name, company_address, vat_id, tax_number,
                    iban, bic, leitweg_id, datev_chart, datev_consultant_number,
                    datev_client_number, currency, number_format, date_format,
                    language, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uid,
                    DEFAULT_SETTINGS["company_name"],
                    DEFAULT_SETTINGS["company_address"],
                    DEFAULT_SETTINGS["vat_id"],
                    DEFAULT_SETTINGS["tax_number"],
                    DEFAULT_SETTINGS["iban"],
                    DEFAULT_SETTINGS["bic"],
                    DEFAULT_SETTINGS["leitweg_id"],
                    DEFAULT_SETTINGS["datev_chart"],
                    DEFAULT_SETTINGS["datev_consultant_number"],
                    DEFAULT_SETTINGS["datev_client_number"],
                    DEFAULT_SETTINGS["currency"],
                    DEFAULT_SETTINGS["number_format"],
                    DEFAULT_SETTINGS["date_format"],
                    DEFAULT_SETTINGS["language"],
                    now,
                ),
            )
            return {**DEFAULT_SETTINGS, "user_id": uid, "updated_at": now}
        return dict(row)


def update_user_settings(
    user_id: str,
    updates: Dict[str, Any],
    db_path: str = _DEFAULT_DB_PATH,
) -> Dict[str, Any]:
    current = get_user_settings(user_id, db_path)
    uid = str(user_id or "default_user")
    merged = {**current}
    for k in DEFAULT_SETTINGS:
        if k in updates and updates[k] is not None:
            merged[k] = str(updates[k]).strip()

    if merged.get("datev_chart") not in ("SKR03", "SKR04"):
        merged["datev_chart"] = "SKR03"
    if merged.get("language") not in ("de", "en"):
        merged["language"] = "de"
    merged["currency"] = merged.get("currency") or "EUR"
    merged["number_format"] = "1.234,56"
    merged["date_format"] = "DD.MM.YYYY"
    now = _now_iso()

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            UPDATE user_settings SET
                company_name = ?,
                company_address = ?,
                vat_id = ?,
                tax_number = ?,
                iban = ?,
                bic = ?,
                leitweg_id = ?,
                datev_chart = ?,
                datev_consultant_number = ?,
                datev_client_number = ?,
                currency = ?,
                number_format = ?,
                date_format = ?,
                language = ?,
                updated_at = ?
            WHERE user_id = ?
            """,
            (
                merged["company_name"],
                merged["company_address"],
                merged["vat_id"],
                merged["tax_number"],
                merged["iban"],
                merged["bic"],
                merged["leitweg_id"],
                merged["datev_chart"],
                merged["datev_consultant_number"],
                merged["datev_client_number"],
                merged["currency"],
                merged["number_format"],
                merged["date_format"],
                merged["language"],
                now,
                uid,
            ),
        )
    merged["updated_at"] = now
    return merged


# ─────────────────────────────────────────────────────────────────────────────
# Conversation CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_conversation(
    user_id: str,
    title: str = "",
    conversation_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    init_db(db_path)
    cid = conversation_id or str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(metadata or {})

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO conversations (conversation_id, user_id, title, created_at, updated_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO NOTHING
            """,
            (cid, user_id, title or "", now, now, meta_json),
        )

    return cid


def get_conversation(
    conversation_id: str,
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> Optional[dict]:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
    if row is None:
        return None
    return dict(row)


def list_conversations(
    user_id: str,
    limit: int = 50,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def _touch_conversation(conn: sqlite3.Connection, conversation_id: str) -> None:
    conn.execute(
        "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
        (_now_iso(), conversation_id),
    )


def _derive_title(content: str, max_len: int = 48) -> str:
    text = " ".join((content or "").split())
    text = re.sub(r"\[Attached file saved at:[^\]]*\]", "", text).strip()
    text = text.strip(" .!?")
    if not text:
        return ""
    if len(text) <= max_len:
        return text
    cut = text[:max_len].rsplit(" ", 1)[0] or text[:max_len]
    return cut.strip() + "..."


def _title_conversation_from_first_message(conn: sqlite3.Connection, conversation_id: str, content: str) -> None:
    row = conn.execute(
        "SELECT title FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if not row or (row[0] or "").strip():
        return
    count = conn.execute(
        "SELECT COUNT(*) FROM messages WHERE conversation_id = ? AND role = 'user'",
        (conversation_id,),
    ).fetchone()[0]
    if count != 1:
        return
    title = _derive_title(content)
    if title:
        conn.execute(
            "UPDATE conversations SET title = ? WHERE conversation_id = ?",
            (title, conversation_id),
        )


def save_message(
    conversation_id: str,
    role: str,
    content: str,
    idempotency_key: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> Optional[str]:
    init_db(db_path)
    mid = str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(metadata or {})

    with _get_conn(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO messages
                    (message_id, conversation_id, role, content, created_at, idempotency_key, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (mid, conversation_id, role, content, now, idempotency_key, meta_json),
            )
            _touch_conversation(conn, conversation_id)
            if role == "user":
                _title_conversation_from_first_message(conn, conversation_id, content)
        except sqlite3.IntegrityError:
            return None

    return mid


def get_messages(
    conversation_id: str,
    user_id: str,
    limit: Optional[int] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    if not get_conversation(conversation_id, user_id, db_path):
        return []

    with _get_conn(db_path) as conn:
        if limit:
            rows = conn.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY created_at DESC
                    LIMIT ?
                ) ORDER BY created_at ASC
                """,
                (conversation_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM messages
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                """,
                (conversation_id,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_all_messages_raw(
    conversation_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def save_summary(
    conversation_id: str,
    summary_text: str,
    summarized_up_to_message_id: Optional[str] = None,
    metadata: Optional[dict] = None,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    init_db(db_path)
    sid = str(uuid.uuid4())
    now = _now_iso()
    meta_json = json.dumps(metadata or {})

    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO conversation_summaries
                (summary_id, conversation_id, summary_text, summarized_up_to_message_id, created_at, metadata)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (sid, conversation_id, summary_text, summarized_up_to_message_id, now, meta_json),
        )
    return sid


def get_latest_summary(
    conversation_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> Optional[dict]:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        row = conn.execute(
            """
            SELECT * FROM conversation_summaries
            WHERE conversation_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (conversation_id,),
        ).fetchone()
    return dict(row) if row else None


def get_all_summaries(
    conversation_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM conversation_summaries
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_or_create_conversation(
    conversation_id: str,
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> str:
    existing = get_conversation(conversation_id, user_id, db_path)
    if existing:
        return conversation_id
    return create_conversation(
        user_id=user_id,
        conversation_id=conversation_id,
        db_path=db_path,
    )


def add_dashboard_chart(
    user_id: str,
    title: str,
    prompt: str,
    chart_path: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> dict:
    init_db(db_path)
    chart_id = f"dchart_{uuid.uuid4().hex[:12]}"
    created_at = _now_iso()
    with _get_conn(db_path) as conn:
        conn.execute(
            """
            INSERT INTO dashboard_charts (id, user_id, title, prompt, chart_path, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (chart_id, user_id, title, prompt, chart_path, created_at),
        )
    return {
        "id": chart_id,
        "user_id": user_id,
        "title": title,
        "prompt": prompt,
        "chart_path": chart_path,
        "created_at": created_at,
    }


def list_dashboard_charts(
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> list[dict]:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        rows = conn.execute(
            """
            SELECT id, user_id, title, prompt, chart_path, created_at
            FROM dashboard_charts
            WHERE user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def delete_dashboard_chart(
    chart_id: str,
    user_id: str,
    db_path: str = _DEFAULT_DB_PATH,
) -> bool:
    init_db(db_path)
    with _get_conn(db_path) as conn:
        cursor = conn.execute(
            """
            DELETE FROM dashboard_charts
            WHERE id = ? AND user_id = ?
            """,
            (chart_id, user_id),
        )
        return cursor.rowcount > 0
