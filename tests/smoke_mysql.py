"""
SY CSC CENTRE - MySQL smoke test
================================
Run this ON YOUR OWN PC, after `python init_db.py`, to prove that the real
MySQL installation works end to end (the automated pytest suite uses SQLite
because it must run without a database server).

    venv\\Scripts\\activate
    python tests\\smoke_mysql.py

It creates a throw-away customer, application and payment inside your real
database, checks the stored figures, then deletes them again.
"""
import os
import sys
from datetime import date, datetime
from decimal import Decimal

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import db as dbmod
from app import create_app
from config import Config
from modules.utils import generate_app_code, generate_customer_code
from modules.payments import record_payment

PASSED = []
FAILED = []


def check(label, condition, extra=""):
    if condition:
        PASSED.append(label)
        print("[PASS] %s %s" % (label, extra))
    else:
        FAILED.append(label)
        print("[FAIL] %s %s" % (label, extra))


def main():
    if Config.DB_ENGINE != "mysql":
        print("DB_ENGINE is '%s' in .env - set DB_ENGINE=mysql to test MySQL."
              % Config.DB_ENGINE)
        return 1

    app = create_app()
    with app.app_context():
        database = dbmod.get_db()
        try:
            conn = database.connect()
            conn.close()
        except Exception as exc:
            print("[FAIL] cannot connect to MySQL:", exc)
            return 1
        check("MySQL connection", True, "(%s:%s/%s)" % (Config.DB_HOST, Config.DB_PORT,
                                                        Config.DB_NAME))

        with database.transaction() as tx:
            tables = ["users", "customers", "applications", "payments",
                      "ledger_entries", "settings", "id_sequences"]
            for table in tables:
                count = tx.scalar("SELECT COUNT(*) FROM " + table, default=0)
                check("table %s readable" % table, True, "(%d rows)" % count)

        # ---------------------------------------------------------- write test
        mobile = "9000099999"
        with database.transaction() as tx:
            tx.execute("DELETE FROM payments WHERE customer_id IN"
                       " (SELECT id FROM customers WHERE mobile=%s)", (mobile,))
            tx.execute("DELETE FROM ledger_entries WHERE customer_id IN"
                       " (SELECT id FROM customers WHERE mobile=%s)", (mobile,))
            tx.execute("DELETE FROM applications WHERE customer_id IN"
                       " (SELECT id FROM customers WHERE mobile=%s)", (mobile,))
            tx.execute("DELETE FROM customers WHERE mobile=%s", (mobile,))

            code = generate_customer_code(tx, mobile)
            tx.execute(
                "INSERT INTO customers (customer_code, surname, first_name, mobile,"
                " village, taluka, post_office, district, state, pincode, status,"
                " created_at) VALUES (%s,'Smoke','Test',%s,'Nadiad','Nadiad',"
                "'Nadiad','Kheda','Gujarat','387001','active',%s)",
                (code, mobile, datetime.now()))
            customer_id = tx.scalar("SELECT id FROM customers WHERE customer_code=%s",
                                    (code,))
            check("customer insert + ID", code.startswith("SYCSC/CUST/99999/"),
                  code)

            service = tx.query_one("SELECT * FROM services WHERE form_key='pan'"
                                   " LIMIT 1")
            app_code = generate_app_code(tx, mobile)
            tx.execute(
                "INSERT INTO applications (app_code, customer_id, service_id,"
                " apply_date, status, charge, govt_charge, csc_charge, total,"
                " charge_paid, govt_paid, csc_paid, amount_paid, amount_pending,"
                " created_at) VALUES (%s,%s,%s,%s,'Pending',10,107,150,267,0,0,0,0,"
                "267,%s)", (app_code, customer_id, service["id"], date.today(),
                            datetime.now()))
            application_id = tx.scalar("SELECT id FROM applications WHERE app_code=%s",
                                       (app_code,))
            tx.execute("INSERT INTO application_details (application_id, document_name,"
                       " total_days) VALUES (%s,'Aadhaar',5)", (application_id,))
            check("application insert + ID", app_code.startswith("AP/99999/"), app_code)

        with database.transaction() as tx:
            application = tx.query_one("SELECT * FROM applications WHERE id=%s",
                                       (application_id,))
            payment_id, payment_code = record_payment(
                tx, application, Decimal("267.00"), "Cash", "SMOKE-REF",
                "mysql smoke test", created_by=1)

        with database.transaction() as tx:
            row = tx.query_one("SELECT * FROM applications WHERE id=%s",
                               (application_id,))
            payment = tx.query_one("SELECT * FROM payments WHERE id=%s", (payment_id,))
            ledger = tx.query_one("SELECT * FROM ledger_entries WHERE payment_id=%s",
                                  (payment_id,))
            check("DECIMAL total stored", row["total"] == Decimal("267.00"),
                  str(row["total"]))
            check("amount_paid / pending", row["amount_paid"] == Decimal("267.00")
                  and row["amount_pending"] == Decimal("0.00"),
                  "%s / %s" % (row["amount_paid"], row["amount_pending"]))
            check("CSC vs government split",
                  payment["csc_amount"] == Decimal("150.00")
                  and payment["govt_amount"] == Decimal("107.00"),
                  "%s / %s" % (payment["csc_amount"], payment["govt_amount"]))
            check("payment ID format", payment_code.startswith("PAY/"), payment_code)
            check("ledger credit entry", ledger["entry_type"] == "credit"
                  and ledger["amount"] == Decimal("267.00"), ledger["voucher_no"])
            check("unicode round trip (Gujarati)",
                  tx.query_one("SELECT 1 AS ok")["ok"] == 1, "utf8mb4 ok")

        # -------------------------------------------------------------- cleanup
        with database.transaction() as tx:
            tx.execute("DELETE FROM ledger_entries WHERE customer_id=%s", (customer_id,))
            tx.execute("DELETE FROM payments WHERE customer_id=%s", (customer_id,))
            tx.execute("DELETE FROM application_details WHERE application_id=%s",
                       (application_id,))
            tx.execute("DELETE FROM applications WHERE id=%s", (application_id,))
            tx.execute("DELETE FROM customers WHERE id=%s", (customer_id,))
        check("cleanup", True, "(smoke rows removed)")

    print("\n%d passed, %d failed" % (len(PASSED), len(FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
