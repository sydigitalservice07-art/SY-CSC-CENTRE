"""Server side permissions: operators are limited by their permission flags."""
from tests.conftest import (make_application, make_customer, make_operator,
                            login)


def test_operator_without_permission_cannot_add_customer(client, app, conn):
    make_operator(app, username="op_nocust", password="Operator@123",
                  permissions={"can_add_customer": 0})
    login(client, "op_nocust", "Operator@123")
    assert client.get("/customers/new").status_code == 403
    assert client.post("/customers/new", data={"mobile": "9000000001"}).status_code == 403
    assert conn.scalar("SELECT COUNT(*) FROM customers") == 0


def test_operator_with_permission_can_add_customer(client, app, conn):
    make_operator(app, username="op_cust", password="Operator@123",
                  permissions={"can_add_customer": 1})
    login(client, "op_cust", "Operator@123")
    assert client.get("/customers/new").status_code == 200
    response = client.post("/customers/new", data={
        "surname": "Rana", "first_name": "Bhavna", "mobile": "9000000002",
        "village": "Nadiad", "taluka": "Nadiad", "post_office": "Nadiad",
        "district": "Kheda", "state": "Gujarat", "pincode": "387001"},
        follow_redirects=True)
    assert response.status_code == 200
    assert conn.scalar("SELECT COUNT(*) FROM customers WHERE mobile='9000000002'") == 1


def test_operator_cannot_open_admin_portal(client, app):
    make_operator(app, username="op_admin_check", password="Operator@123")
    login(client, "op_admin_check", "Operator@123")
    assert client.get("/operators/").status_code == 403
    assert client.get("/operators/new").status_code == 403
    assert client.get("/backup/").status_code == 403
    assert client.get("/accounts/").status_code == 403
    assert client.get("/services/new").status_code == 403


def test_operator_cannot_change_service_prices(client, app, conn):
    make_operator(app, username="op_price", password="Operator@123")
    login(client, "op_price", "Operator@123")
    assert client.post("/services/1/edit", data={"name": "Hacked"}).status_code == 403
    assert conn.scalar("SELECT COUNT(*) FROM services WHERE name='Hacked'") == 0


def test_reports_permission_is_enforced(client, app):
    make_operator(app, username="op_noreport", password="Operator@123",
                  permissions={"can_view_reports": 0})
    login(client, "op_noreport", "Operator@123")
    assert client.get("/reports/").status_code == 403

    make_operator(app, username="op_report", password="Operator@123",
                  permissions={"can_view_reports": 1})
    login(client, "op_report", "Operator@123")
    assert client.get("/reports/").status_code == 200


def test_archive_permission_is_enforced(client, app):
    make_operator(app, username="op_noarch", password="Operator@123",
                  permissions={"can_access_archive": 0})
    customer_id = make_customer(app, mobile="9500000001")
    app_id, _code = make_application(app, customer_id)
    login(client, "op_noarch", "Operator@123")
    assert client.get("/applications/archived").status_code == 403
    assert client.post("/applications/%d/archive" % app_id,
                       data={"reason": "x"}).status_code == 403


def test_inactive_operator_cannot_login(client, app):
    make_operator(app, username="op_off", password="Operator@123", status="inactive")
    response = login(client, "op_off", "Operator@123")
    assert response.status_code == 200
    assert "deactivated" in response.get_data(as_text=True) or \
        "\u0aac\u0a82\u0aa7" in response.get_data(as_text=True)
    assert client.get("/").status_code == 302          # still not logged in


def test_operator_dashboard_is_separate(client, app):
    make_operator(app, username="op_dash", password="Operator@123")
    login(client, "op_dash", "Operator@123")
    body = client.get("/").get_data(as_text=True)
    assert "My Applications Today" in body or "\u0a86\u0a9c\u0aa8\u0ac0" in body
    # the admin-only operator portal link is not offered
    assert 'href="/operators/"' not in body
    assert 'href="/backup/"' not in body


def test_login_required_for_every_module(client):
    for path in ["/", "/customers/", "/applications/", "/payments/", "/accounts/",
                 "/reports/", "/messages/", "/profile", "/backup/", "/services/"]:
        response = client.get(path)
        assert response.status_code == 302, path
        assert "/login" in response.headers["Location"], path
