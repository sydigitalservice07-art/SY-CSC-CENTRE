"""Payments: totals, partial payments, CSC income vs government charge, refunds."""
from datetime import date
from decimal import Decimal

import db as dbmod
from modules.utils import allocate_payment
from tests.conftest import application_row, make_application, make_customer


def test_allocate_payment_full_and_partial():
    application = {"charge": Decimal("0"), "govt_charge": Decimal("107"),
                   "csc_charge": Decimal("150"), "charge_paid": Decimal("0"),
                   "govt_paid": Decimal("0"), "csc_paid": Decimal("0")}
    charge, govt, csc = allocate_payment(application, Decimal("257"))
    assert (charge, govt, csc) == (Decimal("0"), Decimal("107"), Decimal("150"))

    charge, govt, csc = allocate_payment(application, Decimal("128.50"))
    assert charge + govt + csc == Decimal("128.50")
    assert csc > 0 and govt > 0


def test_payment_updates_application_totals(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000001")
    app_id, _code = make_application(app, customer_id, charge="0", govt="107",
                                     csc="150")
    response = admin_client.post("/payments/application/%d/new" % app_id,
                                 data={"amount": "257", "payment_mode": "Cash"},
                                 follow_redirects=True)
    assert response.status_code == 200
    row = application_row(app, app_id)
    assert row["amount_paid"] == Decimal("257")
    assert row["amount_pending"] == Decimal("0")
    assert row["csc_paid"] == Decimal("150")
    assert row["govt_paid"] == Decimal("107")

    payment = conn.query_one("SELECT * FROM payments ORDER BY id DESC LIMIT 1")
    assert payment["amount"] == Decimal("257")
    assert payment["csc_amount"] == Decimal("150")
    assert payment["govt_amount"] == Decimal("107")
    assert payment["payment_code"].startswith("PAY/")

    ledger = conn.query_one("SELECT * FROM ledger_entries WHERE payment_id=%s",
                            (payment["id"],))
    assert ledger["entry_type"] == "credit"
    assert ledger["account"] == "Cash"
    assert ledger["amount"] == Decimal("257")


def test_partial_payments_accumulate(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000002")
    app_id, _code = make_application(app, customer_id, charge="0", govt="0",
                                     csc="100")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "40", "payment_mode": "Cash"},
                      follow_redirects=True)
    row = application_row(app, app_id)
    assert row["amount_paid"] == Decimal("40")
    assert row["amount_pending"] == Decimal("60")

    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "60", "payment_mode": "UPI"},
                      follow_redirects=True)
    row = application_row(app, app_id)
    assert row["amount_paid"] == Decimal("100")
    assert row["amount_pending"] == Decimal("0")
    assert conn.scalar("SELECT COUNT(*) FROM payments") == 2


def test_overpayment_is_rejected(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000003")
    app_id, _code = make_application(app, customer_id, charge="0", govt="0",
                                     csc="100")
    response = admin_client.post("/payments/application/%d/new" % app_id,
                                 data={"amount": "150", "payment_mode": "Cash"},
                                 follow_redirects=True)
    assert "more than the pending amount" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM payments") == 0


def test_csc_income_excludes_government_charge(admin_client, app, conn):
    """Today's revenue must count only the CSC part actually collected."""
    customer_id = make_customer(app, mobile="9700000004")
    app_id, _code = make_application(app, customer_id, charge="0", govt="107",
                                     csc="150")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "257", "payment_mode": "Cash"},
                      follow_redirects=True)

    # the dashboard revenue helper must report only the CSC part
    from datetime import datetime, timedelta
    from modules.dashboard import _revenue
    today = date.today()
    start = datetime(today.year, today.month, today.day)
    with dbmod.get_db().transaction() as tx:
        revenue = _revenue(tx, start, start + timedelta(days=1))
    assert revenue == Decimal("150.00")

    report = admin_client.get("/reports/?type=csc_income&period=today")
    assert report.status_code == 200
    body = report.get_data(as_text=True)
    assert "150.00" in body
    assert "107.00" in body                # shown separately, not as income

    govt_report = admin_client.get("/reports/?type=govt_charge&period=today")
    assert "107.00" in govt_report.get_data(as_text=True)


def test_unpaid_amount_is_not_revenue(admin_client, app):
    customer_id = make_customer(app, mobile="9700000005")
    make_application(app, customer_id, charge="0", govt="107", csc="150")
    dashboard = admin_client.get("/").get_data(as_text=True)
    assert "150.00" not in dashboard


def test_payment_reversal_creates_negative_entry(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000006")
    app_id, _code = make_application(app, customer_id, charge="0", govt="0",
                                     csc="100")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "100", "payment_mode": "Cash"},
                      follow_redirects=True)
    payment = conn.query_one("SELECT * FROM payments ORDER BY id DESC LIMIT 1")
    admin_client.post("/payments/%d/reverse" % payment["id"],
                      data={"reason": "customer cancelled"}, follow_redirects=True)

    row = application_row(app, app_id)
    assert row["amount_paid"] == Decimal("0")
    assert row["amount_pending"] == Decimal("100")

    original = conn.query_one("SELECT * FROM payments WHERE id=%s", (payment["id"],))
    assert original["status"] == "reversed"
    refund = conn.query_one("SELECT * FROM payments WHERE is_refund=1")
    assert refund["amount"] == Decimal("-100")
    debit = conn.query_one("SELECT * FROM ledger_entries WHERE entry_type='debit'"
                           " AND category='Refund'")
    assert debit["amount"] == Decimal("100")


def test_cancelled_application_cannot_take_payment(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000007")
    app_id, _code = make_application(app, customer_id, status="Cancelled")
    response = admin_client.post("/payments/application/%d/new" % app_id,
                                 data={"amount": "10", "payment_mode": "Cash"},
                                 follow_redirects=True)
    assert "cancelled application cannot receive a payment" in \
        response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM payments") == 0


def test_duplicate_reference_number_is_refused(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000008")
    app_id, _code = make_application(app, customer_id, charge="0", govt="0",
                                     csc="100")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "30", "payment_mode": "UPI",
                            "reference_no": "UPI-XYZ"}, follow_redirects=True)
    response = admin_client.post("/payments/application/%d/new" % app_id,
                                 data={"amount": "30", "payment_mode": "UPI",
                                       "reference_no": "UPI-XYZ"},
                                 follow_redirects=True)
    assert "already exists" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM payments") == 1


def test_receipt_page_shows_all_amounts(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9700000009")
    app_id, code = make_application(app, customer_id, charge="0", govt="107",
                                    csc="150")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "257", "payment_mode": "Cash"},
                      follow_redirects=True)
    payment = conn.query_one("SELECT id FROM payments ORDER BY id DESC LIMIT 1")
    body = admin_client.get("/payments/%d/receipt" % payment["id"]).get_data(as_text=True)
    assert code in body
    assert "257.00" in body and "107.00" in body and "150.00" in body
