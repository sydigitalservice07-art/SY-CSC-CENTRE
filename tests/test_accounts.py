"""Account sheet: balances, paired transfers, government payable."""
from datetime import date
from decimal import Decimal

import db as dbmod
from modules.accounts import account_balances, government_payable
from tests.conftest import make_application, make_customer


def post_entry(client, entry_type, account, amount, category="Other Income",
               reference=""):
    return client.post("/accounts/entry", data={
        "entry_type": entry_type, "account": account, "amount": amount,
        "category": category, "entry_date": date.today().isoformat(),
        "reference_no": reference, "description": "test"}, follow_redirects=True)


def test_balance_is_opening_plus_credits_minus_debits(admin_client, app):
    post_entry(admin_client, "credit", "Cash", "1000", "Other Income")
    post_entry(admin_client, "debit", "Cash", "250", "Office Purchase")
    with dbmod.get_db().transaction() as tx:
        balances = account_balances(tx)
    assert balances["Cash"]["credits"] == Decimal("1000")
    assert balances["Cash"]["debits"] == Decimal("250")
    assert balances["Cash"]["balance"] == Decimal("750")


def test_opening_balance_is_included(admin_client, app):
    admin_client.post("/accounts/opening", data={"account": "Bank",
                                                 "opening_balance": "5000"},
                      follow_redirects=True)
    post_entry(admin_client, "credit", "Bank", "500", "Other Income")
    with dbmod.get_db().transaction() as tx:
        balances = account_balances(tx)
    assert balances["Bank"]["balance"] == Decimal("5500")


def test_transfer_creates_paired_entries(admin_client, app, conn):
    post_entry(admin_client, "credit", "Cash", "1000", "Other Income")
    admin_client.post("/accounts/transfer", data={
        "from_account": "Cash", "to_account": "Bank", "amount": "400",
        "entry_date": date.today().isoformat(), "description": "deposit"},
        follow_redirects=True)

    rows = conn.query("SELECT * FROM ledger_entries WHERE category='Transfer'"
                      " ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["entry_type"] == "debit" and rows[0]["account"] == "Cash"
    assert rows[1]["entry_type"] == "credit" and rows[1]["account"] == "Bank"
    assert rows[0]["transfer_group"] == rows[1]["transfer_group"]

    with dbmod.get_db().transaction() as tx:
        balances = account_balances(tx)
    assert balances["Cash"]["balance"] == Decimal("600")
    assert balances["Bank"]["balance"] == Decimal("400")


def test_transfer_more_than_balance_is_refused(admin_client, app, conn):
    post_entry(admin_client, "credit", "Cash", "100", "Other Income")
    response = admin_client.post("/accounts/transfer", data={
        "from_account": "Cash", "to_account": "UPI", "amount": "500",
        "entry_date": date.today().isoformat()}, follow_redirects=True)
    assert "Not enough balance" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM ledger_entries"
                       " WHERE category='Transfer'") == 0


def test_duplicate_reference_is_refused(admin_client, app, conn):
    post_entry(admin_client, "credit", "Cash", "100", "Other Income", "REF-1")
    response = post_entry(admin_client, "credit", "Cash", "100", "Other Income",
                          "REF-1")
    assert "already posted" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM ledger_entries") == 1


def test_government_payable_tracks_collected_minus_paid(admin_client, app):
    customer_id = make_customer(app, mobile="9600000001")
    app_id, _code = make_application(app, customer_id, charge="0", govt="107",
                                     csc="150")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "257", "payment_mode": "Cash"},
                      follow_redirects=True)
    with dbmod.get_db().transaction() as tx:
        govt = government_payable(tx)
    assert govt["collected"] == Decimal("107")
    assert govt["payable"] == Decimal("107")

    post_entry(admin_client, "debit", "Cash", "107", "Government Charge Paid")
    with dbmod.get_db().transaction() as tx:
        govt = government_payable(tx)
    assert govt["payable"] == Decimal("0")


def test_salary_payment_posts_expense(admin_client, app, conn):
    from tests.conftest import make_operator
    operator_id = make_operator(app, username="op_sal")
    admin_client.post("/operators/%d/salary" % operator_id,
                      data={"salary_month": "2026-10", "salary_amount": "8000"},
                      follow_redirects=True)
    salary = conn.query_one("SELECT * FROM operator_salaries")
    admin_client.post("/operators/salary/%d/pay" % salary["id"],
                      data={"amount": "5000", "payment_mode": "Cash",
                            "paid_on": date.today().isoformat()},
                      follow_redirects=True)
    ledger = conn.query_one("SELECT * FROM ledger_entries WHERE category='Salary'")
    assert ledger["entry_type"] == "debit"
    assert ledger["amount"] == Decimal("5000")
    updated = conn.query_one("SELECT * FROM operator_salaries WHERE id=%s",
                             (salary["id"],))
    assert updated["paid_amount"] == Decimal("5000")
