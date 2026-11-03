"""Applications: the five PDF forms, totals, total days, duplicates, archive,
delete permissions and the completed-and-paid hiding rule."""
from datetime import date
from decimal import Decimal

import db as dbmod
from modules.utils import calc_total_days
from tests.conftest import (application_row, get_service, make_application,
                            make_customer, make_operator)

FORM_CASES = [
    ("pan", {"token_no": "T-9", "ack_no": "ACK-9", "epan_date": "2026-10-05",
             "postal_tracking_no": "PT-9", "physical_pan_date": "2026-10-15",
             "document_name": "Aadhaar, Photo"}, "physical_pan_date", 15),
    ("ration", {"ration_card_no": "RC-1", "ap_number": "AP-1", "pay_number": "PN-1",
                "done_date": "2026-10-10", "document_name": "Ration Card"},
     "done_date", 10),
    ("ayushman", {"ration_card_no": "RC-2", "done_date": "2026-10-03",
                  "document_name": "Ration"}, "done_date", 3),
    ("abha", {"done_date": "2026-10-02", "document_name": "Aadhaar"}, "done_date", 2),
    ("voting", {"done_date": "2026-10-20", "document_name": "Address proof"},
     "done_date", 20),
]


def test_calc_total_days():
    assert calc_total_days(date(2026, 10, 1), date(2026, 10, 15)) == 15
    assert calc_total_days(date(2026, 10, 1), date(2026, 10, 1)) == 1
    assert calc_total_days(date(2026, 10, 5), date(2026, 10, 1)) is None
    assert calc_total_days(None, date(2026, 10, 1)) is None


def test_five_service_forms_are_separate(admin_client, app):
    """Each service must render its own form with its own fields."""
    customer_id = make_customer(app, mobile="9800000001")
    rendered = {}
    for form_key, _data, _day_field, _days in FORM_CASES:
        service = get_service(app, form_key)
        response = admin_client.get(
            "/applications/new?customer_id=%d&service_id=%d"
            % (customer_id, service["id"]))
        assert response.status_code == 200
        rendered[form_key] = response.get_data(as_text=True)

    assert 'name="token_no"' in rendered["pan"]
    assert 'name="postal_tracking_no"' in rendered["pan"]
    assert 'name="token_no"' not in rendered["abha"]
    assert 'name="ration_card_no"' in rendered["ration"]
    assert 'name="ap_number"' in rendered["ration"]
    assert 'name="pay_number"' in rendered["ration"]
    assert 'name="ration_card_no"' in rendered["ayushman"]
    assert 'name="ap_number"' not in rendered["ayushman"]
    assert 'name="done_date"' in rendered["abha"]
    assert 'name="done_date"' in rendered["voting"]


def test_service_form_saves_all_fields_and_totals(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000002")
    for form_key, data, day_field, expected_days in FORM_CASES:
        service = get_service(app, form_key)
        payload = {
            "customer_id": customer_id, "service_id": service["id"],
            "apply_date": "2026-10-01", "charge": "10.00",
            "govt_charge": "107.00", "csc_charge": "150.00",
            "notes": "form test", "status": "Pending",
        }
        payload.update(data)
        response = admin_client.post("/applications/new", data=payload,
                                     follow_redirects=True)
        assert response.status_code == 200

        row = conn.query_one(
            "SELECT a.*, d.* FROM applications a JOIN application_details d"
            " ON d.application_id=a.id WHERE a.service_id=%s ORDER BY a.id DESC LIMIT 1",
            (service["id"],))
        assert row["charge"] == Decimal("10")
        assert row["govt_charge"] == Decimal("107")
        assert row["csc_charge"] == Decimal("150")
        assert row["total"] == Decimal("267")
        assert row["amount_pending"] == Decimal("267")
        assert row["total_days"] == expected_days
        for key, value in data.items():
            if key.endswith("_date"):
                continue
            assert row[key] == value, "%s not saved for %s" % (key, form_key)


def test_total_is_charge_plus_govt_plus_csc(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000003")
    service = get_service(app, "pan")
    admin_client.post("/applications/new", data={
        "customer_id": customer_id, "service_id": service["id"],
        "apply_date": "2026-10-01", "charge": "25.50", "govt_charge": "107.00",
        "csc_charge": "150.25", "physical_pan_date": "2026-10-02",
    }, follow_redirects=True)
    row = conn.query_one("SELECT total FROM applications ORDER BY id DESC LIMIT 1")
    assert row["total"] == Decimal("282.75")


def test_duplicate_application_warning_and_block(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000004")
    service = get_service(app, "pan")
    payload = {"customer_id": customer_id, "service_id": service["id"],
               "apply_date": "2026-10-01", "charge": "0", "govt_charge": "107",
               "csc_charge": "150", "physical_pan_date": "2026-10-05"}
    admin_client.post("/applications/new", data=payload, follow_redirects=True)
    first = conn.query_one("SELECT * FROM applications ORDER BY id DESC LIMIT 1")

    response = admin_client.post("/applications/new", data=payload)
    body = response.get_data(as_text=True)
    # Gujarati warning text (also matched in English builds via the app id)
    assert "already applied" in body or "\u0a85\u0a97\u0abe\u0a89" in body
    assert first["app_code"] in body            # previous application is shown
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 1


def test_different_service_is_not_a_duplicate(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000005")
    pan = get_service(app, "pan")
    abha = get_service(app, "abha")
    base = {"customer_id": customer_id, "apply_date": "2026-10-01", "charge": "0",
            "govt_charge": "107", "csc_charge": "150"}
    admin_client.post("/applications/new",
                      data=dict(base, service_id=pan["id"],
                                physical_pan_date="2026-10-02"),
                      follow_redirects=True)
    response = admin_client.post("/applications/new",
                                 data=dict(base, service_id=abha["id"],
                                           done_date="2026-10-02"),
                                 follow_redirects=True)
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 2
    assert "already applied" not in response.get_data(as_text=True)


def test_duplicate_override_requires_permission_and_reason(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000006")
    service = get_service(app, "pan")
    payload = {"customer_id": customer_id, "service_id": service["id"],
               "apply_date": "2026-10-01", "charge": "0", "govt_charge": "107",
               "csc_charge": "150", "physical_pan_date": "2026-10-05"}
    admin_client.post("/applications/new", data=payload, follow_redirects=True)

    # no reason given -> still blocked
    blocked = admin_client.post("/applications/new",
                                data=dict(payload, override_duplicate="1"))
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 1
    assert "already applied" in blocked.get_data(as_text=True) or \
        "\u0a85\u0a97\u0abe\u0a89" in blocked.get_data(as_text=True)

    # admin + reason -> allowed and audited
    admin_client.post("/applications/new",
                      data=dict(payload, override_duplicate="1",
                                duplicate_reason="Old application was cancelled"),
                      follow_redirects=True)
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 2
    audit = conn.query_one("SELECT * FROM audit_logs WHERE action='duplicate_override'")
    assert audit is not None
    assert "Old application was cancelled" in audit["details"]


def test_completed_and_paid_application_is_hidden_from_default_list(admin_client, app,
                                                                    conn):
    customer_id = make_customer(app, mobile="9800000007")
    app_id, code = make_application(app, customer_id, charge="0", govt="0", csc="100")
    with dbmod.get_db().transaction() as tx:
        tx.execute("UPDATE applications SET status='Completed', amount_paid=total,"
                   " amount_pending=0 WHERE id=%s", (app_id,))

    default_list = admin_client.get("/applications/").get_data(as_text=True)
    assert code not in default_list
    all_list = admin_client.get("/applications/?show=all").get_data(as_text=True)
    assert code in all_list
    # still in the customer history
    detail = admin_client.get("/customers/%d" % customer_id).get_data(as_text=True)
    assert code in detail
    # and still searchable
    assert code in admin_client.get("/applications/?q=" + code).get_data(as_text=True)


def test_pending_application_stays_in_default_list(admin_client, app):
    customer_id = make_customer(app, mobile="9800000008")
    _app_id, code = make_application(app, customer_id)
    assert code in admin_client.get("/applications/").get_data(as_text=True)


def test_archive_and_restore_keeps_single_record(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000009")
    app_id, code = make_application(app, customer_id)
    admin_client.post("/applications/%d/archive" % app_id,
                      data={"reason": "closed"}, follow_redirects=True)

    assert conn.scalar("SELECT is_archived FROM applications WHERE id=%s", (app_id,)) == 1
    assert conn.scalar("SELECT COUNT(*) FROM application_archive") == 1
    assert code not in admin_client.get("/applications/").get_data(as_text=True)
    archived_page = admin_client.get("/applications/archived").get_data(as_text=True)
    assert code in archived_page
    assert "closed" in archived_page

    admin_client.post("/applications/%d/restore" % app_id, follow_redirects=True)
    assert conn.scalar("SELECT is_archived FROM applications WHERE id=%s", (app_id,)) == 0
    assert conn.scalar("SELECT COUNT(*) FROM application_archive") == 0
    # no duplicate application was created
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 1
    assert code in admin_client.get("/applications/").get_data(as_text=True)


def test_operator_cannot_delete_application(client, app, conn):
    make_operator(app, username="op_del", password="Operator@123",
                  permissions={"can_update_status": 1})
    customer_id = make_customer(app, mobile="9800000010")
    app_id, code = make_application(app, customer_id)
    client.post("/login", data={"username": "op_del", "password": "Operator@123"})
    response = client.post("/applications/%d/delete" % app_id,
                           data={"reason": "should not work"})
    assert response.status_code == 403
    assert conn.scalar("SELECT is_deleted FROM applications WHERE id=%s", (app_id,)) == 0


def test_admin_delete_is_soft_and_refused_when_payments_exist(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000011")
    app_id, code = make_application(app, customer_id)

    # no payments yet -> soft delete allowed
    admin_client.post("/applications/%d/delete" % app_id,
                      data={"reason": "test delete"}, follow_redirects=True)
    assert conn.scalar("SELECT is_deleted FROM applications WHERE id=%s", (app_id,)) == 1
    assert conn.scalar("SELECT COUNT(*) FROM applications WHERE id=%s", (app_id,)) == 1
    assert code not in admin_client.get("/applications/").get_data(as_text=True)

    # a paid application must not be deletable
    app_id2, code2 = make_application(app, customer_id, csc="50")
    admin_client.post("/payments/application/%d/new" % app_id2,
                      data={"amount": "50", "payment_mode": "Cash"},
                      follow_redirects=True)
    response = admin_client.post("/applications/%d/delete" % app_id2,
                                 data={"reason": "try"}, follow_redirects=True)
    assert "cannot be deleted" in response.get_data(as_text=True)
    assert conn.scalar("SELECT is_deleted FROM applications WHERE id=%s",
                       (app_id2,)) == 0


def test_cancel_application_records_reason(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000012")
    app_id, _code = make_application(app, customer_id)
    admin_client.post("/applications/%d/cancel" % app_id,
                      data={"reason": "customer withdrew"}, follow_redirects=True)
    row = conn.query_one("SELECT status, notes FROM applications WHERE id=%s", (app_id,))
    assert row["status"] == "Cancelled"
    assert row["notes"] == "customer withdrew"
    assert conn.scalar("SELECT COUNT(*) FROM audit_logs WHERE action='application_cancel'") == 1


def test_deactivated_service_cannot_be_used(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000013")
    service = get_service(app, "abha")
    with dbmod.get_db().transaction() as tx:
        tx.execute("UPDATE services SET is_active=0 WHERE id=%s", (service["id"],))
    response = admin_client.post("/applications/new", data={
        "customer_id": customer_id, "service_id": service["id"],
        "apply_date": "2026-10-01", "charge": "0", "govt_charge": "0",
        "csc_charge": "30"}, follow_redirects=True)
    assert "deactivated" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 0


def test_editing_application_keeps_history(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9800000014")
    app_id, code = make_application(app, customer_id, charge="0", govt="107",
                                    csc="150")
    admin_client.post("/applications/%d/edit" % app_id, data={
        "apply_date": "2026-10-02", "charge": "0", "govt_charge": "107",
        "csc_charge": "200", "physical_pan_date": "2026-10-06",
        "token_no": "T-77", "document_name": "Aadhaar"}, follow_redirects=True)
    row = application_row(app, app_id)
    assert row["csc_charge"] == Decimal("200")
    assert row["total"] == Decimal("307")
    assert row["app_code"] == code
    audit = conn.query_one("SELECT details FROM audit_logs WHERE action='application_update'")
    assert "150" in audit["details"] and "200" in audit["details"]
