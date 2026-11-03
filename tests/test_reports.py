"""Reports: date ranges, totals and the CSC / government separation."""
from datetime import date
from decimal import Decimal

import db as dbmod
from modules.reports import REPORT_LIST, build_report, resolve_range
from tests.conftest import make_application, make_customer


def test_all_report_types_build(app):
    with dbmod.get_db().transaction() as tx:
        for key, _label in REPORT_LIST:
            data = build_report(tx, key, date(2020, 1, 1), date(2030, 12, 31))
            assert "columns" in data and "rows" in data and "totals" in data
            assert isinstance(data["rows"], list)
            assert data["columns"], key


def test_resolve_range_periods():
    today = date.today()
    start, end = resolve_range("today", None, None)
    assert start == end == today
    start, end = resolve_range("month", None, None)
    assert start == today.replace(day=1)
    start, end = resolve_range("custom", "2026-01-05", "2026-02-10")
    assert start == date(2026, 1, 5) and end == date(2026, 2, 10)
    # reversed input is corrected
    start, end = resolve_range("custom", "2026-03-10", "2026-01-01")
    assert start <= end


def test_csc_income_report_totals(admin_client, app):
    customer_id = make_customer(app, mobile="9300000001")
    app_id, _code = make_application(app, customer_id, charge="0", govt="107",
                                     csc="150")
    admin_client.post("/payments/application/%d/new" % app_id,
                      data={"amount": "257", "payment_mode": "Cash"},
                      follow_redirects=True)

    with dbmod.get_db().transaction() as tx:
        data = build_report(tx, "csc_income", date(2020, 1, 1), date(2030, 1, 1))
    totals = dict(data["totals"])
    assert totals["CSC Income"] == Decimal("150.00")
    assert totals["Government Charge (not income)"] == Decimal("107.00")
    assert totals["Total Received"] == Decimal("257.00")


def test_service_wise_report_counts(app):
    customer_id = make_customer(app, mobile="9300000002")
    make_application(app, customer_id, charge="0", govt="0", csc="100")
    with dbmod.get_db().transaction() as tx:
        data = build_report(tx, "service_wise", date(2020, 1, 1), date(2030, 1, 1))
    pan_row = [r for r in data["rows"] if r["service_name"] == "PAN Card"][0]
    assert pan_row["app_count"] == 1
    assert pan_row["csc"] == Decimal("100")


def test_pending_balance_report(app):
    customer_id = make_customer(app, mobile="9300000003")
    make_application(app, customer_id, charge="0", govt="0", csc="100")
    with dbmod.get_db().transaction() as tx:
        data = build_report(tx, "pending_balance", date(2020, 1, 1), date(2030, 1, 1))
    assert data["rows"][0]["pending"] == Decimal("100")


def test_report_date_range_filters_rows(app):
    customer_id = make_customer(app, mobile="9300000004")
    make_application(app, customer_id, charge="0", govt="0", csc="100",
                     apply_date=date(2025, 5, 5))
    with dbmod.get_db().transaction() as tx:
        inside = build_report(tx, "application", date(2025, 5, 1), date(2025, 5, 31))
        outside = build_report(tx, "application", date(2024, 5, 1), date(2024, 5, 31))
    assert len(inside["rows"]) == 1
    assert len(outside["rows"]) == 0


def test_csv_export(admin_client, app):
    customer_id = make_customer(app, mobile="9300000005")
    make_application(app, customer_id, charge="0", govt="0", csc="100")
    response = admin_client.get("/reports/export?type=application&period=year")
    assert response.status_code == 200
    assert response.mimetype == "text/csv"
    body = response.get_data(as_text=True)
    assert "Application ID" in body
    assert "AP/" in body
