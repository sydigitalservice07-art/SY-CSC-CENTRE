"""
SY CSC CENTRE - database initialiser
====================================
Creates the database (MySQL), runs schema.sql, creates indexes, seeds the
default admin user, the five PDF services and the account opening balances.

Usage (Windows CMD, inside the project folder):

    python init_db.py              create database + tables + seed data
    python init_db.py --force      DROP all tables first (destructive!)
    python init_db.py --demo       also insert a few demo rows
    python init_db.py --check      only test the connection

The same file also works with DB_ENGINE=sqlite (used by the test suite).
"""
import os
import re
import sys
from datetime import datetime, date
from decimal import Decimal

from config import Config, BASE_DIR
import db as dbmod
from modules.security import hash_password

SCHEMA_FILE = os.path.join(BASE_DIR, "schema.sql")


# --------------------------------------------------------------------------
# SQL dialect translation (MySQL DDL -> SQLite DDL)
# --------------------------------------------------------------------------
def to_sqlite_ddl(sql):
    out = sql
    out = re.sub(r"INT\s+AUTO_INCREMENT\s+PRIMARY\s+KEY", "INTEGER PRIMARY KEY AUTOINCREMENT", out, flags=re.I)
    out = re.sub(r"ENUM\([^)]*\)", "VARCHAR(30)", out, flags=re.I)
    out = re.sub(r"TINYINT\(1\)", "INTEGER", out, flags=re.I)
    out = re.sub(r"TINYINT", "INTEGER", out, flags=re.I)
    out = re.sub(r"ENGINE\s*=\s*InnoDB[^;]*", "", out, flags=re.I)
    out = re.sub(r"COLLATE\s*=\s*\w+", "", out, flags=re.I)
    out = re.sub(r"ON\s+UPDATE\s+CURRENT_TIMESTAMP", "", out, flags=re.I)
    return out


def split_statements(sql):
    statements, buffer = [], []
    for line in sql.splitlines():
        stripped = line.strip()
        if stripped.startswith("--") or not stripped:
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).rstrip(";").strip())
            buffer = []
    tail = "\n".join(buffer).strip()
    if tail:
        statements.append(tail)
    return statements


# Indexes are created separately so the same list works for MySQL and SQLite.
INDEXES = [
    ("idx_customers_mobile", "customers (mobile)"),
    ("idx_customers_name", "customers (first_name, surname)"),
    ("idx_customers_aadhaar", "customers (aadhaar_number)"),
    ("idx_customers_status", "customers (status)"),
    ("idx_app_customer", "applications (customer_id)"),
    ("idx_app_service", "applications (service_id)"),
    ("idx_app_status", "applications (status)"),
    ("idx_app_date", "applications (apply_date)"),
    ("idx_app_operator", "applications (operator_id)"),
    ("idx_pay_app", "payments (application_id)"),
    ("idx_pay_date", "payments (payment_date)"),
    ("idx_pay_customer", "payments (customer_id)"),
    ("idx_ledger_date", "ledger_entries (entry_date)"),
    ("idx_ledger_account", "ledger_entries (account)"),
    ("idx_msg_recipient", "messages (recipient_id, read_at)"),
    ("idx_audit_created", "audit_logs (created_at)"),
    ("idx_doc_customer", "customer_documents (customer_id)"),
]


DEFAULT_SERVICES = [
    # name, category, form_key, charge, govt, csc, days, documents
    ("PAN Card", "Identity", "pan", 0, 107, 150, 15,
     "Aadhaar Card, Photo, Signature"),
    ("Ration Card", "Food & Civil Supplies", "ration", 0, 0, 100, 30,
     "Aadhaar Card, Old Ration Card, Address Proof"),
    ("Ayushman Card", "Health", "ayushman", 0, 0, 50, 15,
     "Ration Card, Aadhaar Card"),
    ("ABHA Card", "Health", "abha", 0, 0, 30, 1,
     "Aadhaar Card, Mobile Number"),
    ("Voting Card", "Election", "voting", 0, 0, 60, 20,
     "Aadhaar Card, Address Proof, Photo"),
]

DEFAULT_SETTINGS = {
    "charge_classification": "csc_income",
    "csc_name": "SY CSC CENTRE",
    "csc_address": "",
    "csc_phone": "",
    "csc_email": "",
    "receipt_footer": "Thank you for visiting SY CSC CENTRE",
    "default_language": "gu",
}


def seed(conn, demo=False):
    now = datetime.now()
    # ---------------------------------------------------------------- admin
    existing = conn.query_one("SELECT id FROM users WHERE role='admin' LIMIT 1")
    if not existing:
        conn.execute(
            "INSERT INTO users (name, username, password_hash, role, mobile, email,"
            " status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            ("System Administrator", "admin", hash_password("Admin@123"), "admin",
             "", "", "active", now))
        admin_id = conn.scalar("SELECT id FROM users WHERE username=%s", ("admin",))
    else:
        admin_id = existing["id"]

    # ------------------------------------------------------------- services
    for (name, cat, form_key, charge, govt, csc, days, docs) in DEFAULT_SERVICES:
        if not conn.query_one("SELECT id FROM services WHERE name=%s", (name,)):
            conn.execute(
                "INSERT INTO services (name, category, description, form_key,"
                " default_charge, default_govt_charge, default_csc_charge,"
                " processing_days, required_documents, is_active, created_at, created_by)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (name, cat, name + " application service", form_key,
                 Decimal(charge), Decimal(govt), Decimal(csc), days, docs, 1,
                 now, admin_id))

    # ------------------------------------------------------------- settings
    for key, value in DEFAULT_SETTINGS.items():
        if not conn.query_one("SELECT setting_key FROM settings WHERE setting_key=%s", (key,)):
            conn.execute(
                "INSERT INTO settings (setting_key, setting_value, updated_at)"
                " VALUES (%s,%s,%s)", (key, value, now))

    # ------------------------------------------------------------ accounts
    for account in ("Cash", "Bank", "UPI"):
        if not conn.query_one("SELECT account FROM account_balances WHERE account=%s", (account,)):
            conn.execute(
                "INSERT INTO account_balances (account, opening_balance, updated_at)"
                " VALUES (%s,%s,%s)", (account, Decimal("0.00"), now))

    if demo:
        seed_demo(conn, admin_id, now)
    return admin_id


def seed_demo(conn, admin_id, now):
    """Optional sample rows - only created when you run: python init_db.py --demo"""
    if conn.query_one("SELECT id FROM users WHERE username=%s", ("operator1",)):
        return
    conn.execute(
        "INSERT INTO users (name, username, password_hash, role, mobile, status,"
        " created_at, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        ("Demo Operator", "operator1", hash_password("Operator@123"), "operator",
         "9876543210", "active", now, admin_id))
    op_id = conn.scalar("SELECT id FROM users WHERE username=%s", ("operator1",))
    conn.execute(
        "INSERT INTO operator_permissions (user_id, can_add_customer, can_edit_customer,"
        " can_add_application, can_update_status, can_view_reports, can_reply_messages,"
        " can_access_archive, can_override_duplicate, updated_at, updated_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (op_id, 0, 1, 1, 1, 0, 1, 0, 0, now, admin_id))
    conn.execute(
        "INSERT INTO customers (customer_code, surname, first_name, father_surname,"
        " father_name, dob, gender, marital_status, mobile, village, taluka,"
        " post_office, district, state, pincode, status, created_at, created_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        ("SYCSC/CUST/43210/0001", "Patel", "Ramesh", "Patel", "Suresh",
         date(1985, 6, 12), "Male", "Married", "9876543210", "Nadiad", "Nadiad",
         "Nadiad", "Kheda", "Gujarat", "387001", "active", now, admin_id))


# --------------------------------------------------------------------------
def create_mysql_database(cfg):
    """Connect without a database name and CREATE DATABASE if needed."""
    import pymysql
    raw = pymysql.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"],
                          password=cfg["password"], charset=cfg["charset"])
    try:
        cur = raw.cursor()
        cur.execute(
            "CREATE DATABASE IF NOT EXISTS `%s` CHARACTER SET utf8mb4 "
            "COLLATE utf8mb4_unicode_ci" % cfg["database"])
        raw.commit()
        cur.close()
    finally:
        raw.close()


def run_schema(conn, engine, force=False):
    with open(SCHEMA_FILE, "r", encoding="utf-8") as fh:
        sql = fh.read()
    if engine == "sqlite":
        sql = to_sqlite_ddl(sql)
    statements = split_statements(sql)

    if force:
        tables = ["backup_logs", "id_sequences", "settings", "audit_logs", "leaves",
                  "salary_payments", "operator_salaries", "attendance", "messages",
                  "account_balances", "ledger_entries", "payments", "application_archive",
                  "application_details", "applications", "sub_services", "services",
                  "customer_documents", "customers", "operator_permissions", "users"]
        if engine == "sqlite":
            conn.raw.execute("PRAGMA foreign_keys = OFF")
        else:
            conn.execute("SET FOREIGN_KEY_CHECKS=0")
        for table in tables:
            conn.execute("DROP TABLE IF EXISTS %s" % table)
        if engine == "sqlite":
            conn.raw.execute("PRAGMA foreign_keys = ON")
        else:
            conn.execute("SET FOREIGN_KEY_CHECKS=1")

    for statement in statements:
        conn.execute(statement)

    for index_name, target in INDEXES:
        try:
            conn.execute("CREATE INDEX %s ON %s" % (index_name, target))
        except Exception:
            pass          # index already exists - fine


def main():
    force = "--force" in sys.argv
    demo = "--demo" in sys.argv
    check_only = "--check" in sys.argv

    cfg = Config
    print("=" * 62)
    print(" SY CSC CENTRE - database initialiser")
    print(" engine   :", cfg.DB_ENGINE)
    if cfg.DB_ENGINE == "sqlite":
        print(" file     :", cfg.SQLITE_PATH)
    else:
        print(" server   : %s:%s" % (cfg.DB_HOST, cfg.DB_PORT))
        print(" database : %s  (user: %s)" % (cfg.DB_NAME, cfg.DB_USER))
    print("=" * 62)

    db_conf = {
        "engine": cfg.DB_ENGINE, "host": cfg.DB_HOST, "port": cfg.DB_PORT,
        "user": cfg.DB_USER, "password": cfg.DB_PASSWORD,
        "database": cfg.DB_NAME, "charset": cfg.DB_CHARSET,
        "sqlite_path": cfg.SQLITE_PATH,
    }

    try:
        if cfg.DB_ENGINE == "mysql":
            create_mysql_database(db_conf)
        database = dbmod.Database(**db_conf)
        conn = database.connect()
        print("[OK] connected to the database server")
    except Exception as exc:
        print("[FAIL] cannot connect:", exc)
        print("       Check that MySQL is running and .env has the right user/password.")
        return 1

    if check_only:
        conn.close()
        print("[OK] connection test finished")
        return 0

    try:
        run_schema(conn, cfg.DB_ENGINE, force=force)
        print("[OK] schema created / verified")
        with database.transaction() as tx:
            seed(tx, demo=demo)
        print("[OK] default data seeded")
        print("")
        print("  Admin login  ->  username: admin      password: Admin@123")
        if demo:
            print("  Demo operator ->  username: operator1  password: Operator@123")
        print("  >>> Change the admin password after the first login <<<")
        print("")
        print("Next step:  python app.py   then open http://127.0.0.1:5000")
    except Exception as exc:
        print("[FAIL] schema/seed error:", exc)
        raise
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
