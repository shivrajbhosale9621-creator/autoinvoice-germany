"""Seed per-company demo accounts for the temporary Railway pilot deployment.

Railway's free tier has no persistent disk, so the SQLite database is wiped
whenever the container restarts. This script runs at container startup
(see the Dockerfile CMD change) and recreates the demo accounts if they are
missing. It is idempotent: existing accounts are left untouched, so a
restart never wipes a company's uploaded invoices twice over and never
resets a password.

Accounts: one per pilot company, so companies cannot see each other's
invoices. Passwords are deterministic per company (Pilot-<CODE>-2026!) so
they can be printed in each company's pilot email without storing any
secret in the repo. These are demo-only accounts on a throwaway
deployment; the whole project is deleted when the pilot ends.

Shivraj's own account is created only when SHIVRAJ_DEMO_PASSWORD is set
in the deployment environment, so his access stays under his control.

Tables are created first via the app's own init_db (idempotent), which
covers the users table. User ids MUST be stored as uuid.hex (32 chars,
no hyphens): SQLAlchemy's Uuid type on SQLite stores and queries hex, and
a hyphenated id lets login succeed but every later request 401s.

Run: python seed_demo_accounts.py   (from the repo root, /app in the image)
"""
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.security import hash_password  # noqa: E402
from chat_store import init_db  # noqa: E402
from config import CHAT_DB_PATH  # noqa: E402

# email, company code (password is Pilot-<CODE>-2026!)
DEMO_ACCOUNTS = [
    ("demo-heinrichs@autoinvoice.demo", "HEIN"),    # Heinrichs GmbH
    ("demo-memminger@autoinvoice.demo", "MEMM"),    # Memminger-IRO GmbH
    ("demo-wittlich@autoinvoice.demo", "WITT"),     # Elektromaschinenbau Wittlich
    ("demo-wickert@autoinvoice.demo", "WICK"),      # Wickert Maschinenbau GmbH
    ("demo-burwinkel@autoinvoice.demo", "BURW"),    # Burwinkel Kunststoffwerk
    ("demo-hdindustries@autoinvoice.demo", "HDIN"),  # HD Industries
    ("demo-sikka@autoinvoice.demo", "SIKK"),        # Sikka Precision
]

SHIVRAJ_EMAIL = "shivrajbhosale9621@gmail.com"


def demo_password(code):
    return f"Pilot-{code}-2026!"


def main():
    db_path = CHAT_DB_PATH
    print(f"[seed] database: {db_path}")
    init_db(db_path)

    conn = sqlite3.connect(db_path)
    try:
        created = 0
        existing = 0
        for email, code in DEMO_ACCOUNTS:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if row:
                existing += 1
                continue
            conn.execute(
                "INSERT INTO users (id, email, password_hash, is_verified,"
                " is_active, created_at) VALUES (?, ?, ?, 1, 1, ?)",
                (uuid.uuid4().hex, email,
                 hash_password(demo_password(code)),
                 datetime.now(timezone.utc).isoformat()),
            )
            created += 1
            print(f"[seed] created demo account: {email}")

        admin_pw = os.getenv("SHIVRAJ_DEMO_PASSWORD", "")
        if admin_pw:
            row = conn.execute(
                "SELECT id FROM users WHERE email = ?", (SHIVRAJ_EMAIL,)
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE users SET is_verified = 1, is_active = 1,"
                    " password_hash = ? WHERE email = ?",
                    (hash_password(admin_pw), SHIVRAJ_EMAIL),
                )
                print(f"[seed] reset {SHIVRAJ_EMAIL} password from env")
            else:
                conn.execute(
                    "INSERT INTO users (id, email, password_hash,"
                    " is_verified, is_active, created_at)"
                    " VALUES (?, ?, ?, 1, 1, ?)",
                    (uuid.uuid4().hex, SHIVRAJ_EMAIL,
                     hash_password(admin_pw),
                     datetime.now(timezone.utc).isoformat()),
                )
                print(f"[seed] created {SHIVRAJ_EMAIL} from env")
        conn.commit()
        print(f"[seed] done: {created} created, {existing} already present")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
