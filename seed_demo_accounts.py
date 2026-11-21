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

57 accounts: the 7 wave-1 companies emailed 20 Nov 2026, plus the 50
wave-2 companies (see german-pilot-wave2-50-targets.md). Wave-2 emails
use plain ASCII slugs (umlauts written out: oe, ae, ue).

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
    # Wave 1 (emailed 20 Nov 2026)
    ("demo-heinrichs@autoinvoice.demo", "HEIN"),    # Heinrichs GmbH
    ("demo-memminger@autoinvoice.demo", "MEMM"),    # Memminger-IRO GmbH
    ("demo-wittlich@autoinvoice.demo", "WITT"),     # Elektromaschinenbau Wittlich
    ("demo-wickert@autoinvoice.demo", "WICK"),      # Wickert Maschinenbau GmbH
    ("demo-burwinkel@autoinvoice.demo", "BURW"),    # Burwinkel Kunststoffwerk
    ("demo-hdindustries@autoinvoice.demo", "HDIN"),  # HD Industries
    ("demo-sikka@autoinvoice.demo", "SIKK"),        # Sikka Precision
    # Wave 2 (50 companies, german-pilot-wave2-50-targets.md)
    ("demo-eder-maschinenbau@autoinvoice.demo", "EDMA"),  # EDER Maschinenbau GmbH
    ("demo-awm@autoinvoice.demo", "ARNS"),          # Arnstädter Werkzeug- und Maschinenbau GmbH
    ("demo-anlagenbau-guenther@autoinvoice.demo", "GUEN"),  # Anlagenbau Günther GmbH
    ("demo-bock@autoinvoice.demo", "BOCK"),         # Bock Maschinenbau GmbH
    ("demo-forthaus@autoinvoice.demo", "FORT"),     # Forthaus Sondermaschinen GmbH & Co. KG
    ("demo-edis@autoinvoice.demo", "EDIS"),         # EDIS Anlagenbau GmbH
    ("demo-ahmeyer@autoinvoice.demo", "MEYE"),      # A.H. Meyer Maschinenfabrik GmbH
    ("demo-em-geraetebau@autoinvoice.demo", "EMGE"),  # EM Gerätebau GmbH
    ("demo-erbatech@autoinvoice.demo", "ERBA"),     # Erbatech GmbH
    ("demo-woelz@autoinvoice.demo", "WOEL"),        # Siegfried Wölz Stahl- und Metallbau GmbH & Co. KG
    ("demo-elting@autoinvoice.demo", "ELTI"),       # Elting Metalltechnik GmbH & Co. KG
    ("demo-haenel@autoinvoice.demo", "HAEN"),       # Zahnradfabrik Hänel GmbH & Co. KG
    ("demo-mueller-bauer@autoinvoice.demo", "MUBA"),  # Müller & Bauer GmbH & Co. KG
    ("demo-stegmueller@autoinvoice.demo", "STEG"),  # Stegmüller Stahl- und Metallbau GmbH
    ("demo-gsf@autoinvoice.demo", "GSFT"),          # GSF Sonderfahrzeugbau GmbH
    ("demo-kollinger@autoinvoice.demo", "KOLL"),    # CB Kollinger Metallbau GmbH
    ("demo-reimann@autoinvoice.demo", "REIM"),      # Reimann GmbH
    ("demo-plersch@autoinvoice.demo", "PLER"),      # Robert Plersch Edelstahltechnik GmbH
    ("demo-atka@autoinvoice.demo", "ATKA"),         # atka Kunststoffverarbeitung GmbH
    ("demo-wibo@autoinvoice.demo", "WIBO"),         # WIBO Kunststofftechnik GmbH
    ("demo-weiss@autoinvoice.demo", "WEIS"),        # Weiss Kunststoffverarbeitung GmbH & Co. KG
    ("demo-hecker@autoinvoice.demo", "HECK"),       # Hecker Kunststofftechnik GmbH & Co. KG
    ("demo-fbfood@autoinvoice.demo", "FBFO"),       # FB Food GmbH
    ("demo-egesun@autoinvoice.demo", "EGES"),       # EgeSun GmbH & Co. KG
    ("demo-schnitzer@autoinvoice.demo", "SCHN"),    # Schnitzer GmbH & Co. KG
    ("demo-soebbeke@autoinvoice.demo", "SOEB"),     # Molkerei Söbbeke GmbH
    ("demo-brand@autoinvoice.demo", "BRAN"),        # Brand Qualitätsfleisch GmbH & Co. KG
    ("demo-held@autoinvoice.demo", "HELD"),         # Gebr. Held Hydraulik Technischer Großhandel GmbH
    ("demo-brechmann@autoinvoice.demo", "BREC"),    # Brechmann Handels GmbH & Co. KG
    ("demo-blumenbecker@autoinvoice.demo", "BLUM"),  # Blumenbecker Industriebedarf GmbH
    ("demo-gockel@autoinvoice.demo", "GOCK"),       # Meinolf Gockel GmbH & Co. KG
    ("demo-hoechsmann@autoinvoice.demo", "HOEC"),   # Höchsmann GmbH
    ("demo-broemmelhaus@autoinvoice.demo", "BROE"),  # Spedition Brömmelhaus GmbH & Co. KG
    ("demo-brocker@autoinvoice.demo", "BROC"),      # Brocker Logistik GmbH & Co. KG
    ("demo-treese@autoinvoice.demo", "TREE"),       # Treese Logistik GmbH & Co. KG
    ("demo-bruhn@autoinvoice.demo", "BRUH"),        # Bruhn Spedition GmbH
    ("demo-convent@autoinvoice.demo", "CONV"),      # Convent Spedition GmbH
    ("demo-eder-bau@autoinvoice.demo", "EDBA"),     # Eder-Bau-GmbH
    ("demo-hahn@autoinvoice.demo", "HAHN"),         # J.D. Hahn GmbH & Co. KG
    ("demo-rossaro@autoinvoice.demo", "ROSS"),      # Rossaro Bauunternehmung GmbH & Co. KG
    ("demo-steinbrenner@autoinvoice.demo", "STEI"),  # Rudolf Steinbrenner Bauunternehmung GmbH & Co. KG
    ("demo-greve@autoinvoice.demo", "GREV"),        # Erich Greve GmbH & Co. KG
    ("demo-schlau@autoinvoice.demo", "SCHL"),       # SCHLAU Elektrotechnik GmbH
    ("demo-hepp-schwamborn@autoinvoice.demo", "HEPP"),  # Hepp-Schwamborn GmbH & Co. KG
    ("demo-inro@autoinvoice.demo", "INRO"),         # INRO Elektrotechnik GmbH
    ("demo-spangler@autoinvoice.demo", "SPAN"),     # SPANGLER GMBH
    ("demo-pago@autoinvoice.demo", "PAGO"),         # PAGO Elektric GmbH
    ("demo-schramm@autoinvoice.demo", "SCHR"),      # SCHRAMM GmbH
    ("demo-labelprint24@autoinvoice.demo", "LABE"),  # harder-online GmbH (Labelprint24)
    ("demo-fernholz@autoinvoice.demo", "FERN"),     # W. u. H. Fernholz GmbH & Co. KG
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
