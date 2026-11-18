"""add german autoinvoice columns and fts5 index

Revision ID: 5c6d7e8f9a0b
Revises: 4b5c6d7e8f9a
Create Date: 2026-11-18

Adds all required German invoice columns (uploaded_at, subtotal, tax_amount,
discount, due_date, buyer_name, vat_id, currency, leitweg_id, skonto, validation,
vies, sepa, iban_change_flag, duplicate_flag) and creates the SQLite FTS5 virtual
table invoices_fts on vendor_name, invoice_number, and item_descriptions.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = "5c6d7e8f9a0b"
down_revision: Union[str, Sequence[str], None] = "4b5c6d7e8f9a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_COLUMNS = [
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


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "invoices" in inspector.get_table_names():
        existing_cols = {c["name"] for c in inspector.get_columns("invoices")}
        for col_name, col_type in NEW_COLUMNS:
            if col_name not in existing_cols:
                bind.exec_driver_sql(f"ALTER TABLE invoices ADD COLUMN {col_name} {col_type}")

    bind.exec_driver_sql(
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS invoices_fts USING fts5(
            doc_id UNINDEXED,
            user_id UNINDEXED,
            vendor_name,
            invoice_number,
            item_descriptions,
            tokenize='unicode61'
        );
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.exec_driver_sql("DROP TABLE IF EXISTS invoices_fts;")
