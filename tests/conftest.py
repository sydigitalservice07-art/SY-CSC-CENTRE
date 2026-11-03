"""
Shared pytest fixtures.

Every test gets a fresh SQLite database created from the real schema.sql, so
the same table definitions, constraints and indexes are exercised.  (The
production database is MySQL - see README.)
"""
import os
import sys
from datetime import date, datetime
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app import create_app                                  # noqa: E402
from config import Config                                   # noqa: E402
import db as dbmod                                          # noqa: E402
from init_db import run_schema, seed                        # noqa: E402
from modules.security import hash_password                  # noqa: E402
from modules.utils import (generate_app_code,               # noqa: E402
                           generate_customer_code)


class TestConfig(Config):
    DB_ENGINE = "sqlite"
    SECRET_KEY = "test-secret"
    TESTING = True
    CSRF_ENABLED = False
    ITEMS_PER_PAGE = 20


@pytest.fixture()
def app(tmp_path):
    TestConfig.SQLITE_PATH = str(tmp_path / "sycsc_test.db")
    TestConfig.UPLOAD_FOLDER = str(tmp_path / "uploads")
    application = create_app(TestConfig)

    conn = dbmod.get_db().connect()
    try:
        run_schema(conn, "sqlite", force=True)
    finally:
        conn.close()
    with dbmod.get_db().transaction() as tx:
        seed(tx)
    return application


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def conn(app):
    """A raw connection for direct assertions (caller must close)."""
    connection = dbmod.get_db().connect()
    yield connection
    connection.close()


@pytest.fixture()
def admin(app):
    return {"username": "admin", "password": "Admin@123"}


def login(client, username="admin", password="Admin@123"):
    client.get("/logout")          # always start from a clean session
    return client.post("/login", data={"username": username, "password": password},
                       follow_redirects=True)


@pytest.fixture()
def admin_client(client, admin):
    login(client)
    return client


# --------------------------------------------------------------------- helpers
def make_operator(app, username="op1", password="Operator@123", permissions=None,
                  status="active", name="Test Operator"):
    perms = {"can_add_customer": 0, "can_edit_customer": 1, "can_add_application": 1,
             "can_update_status": 1, "can_view_reports": 0, "can_reply_messages": 1,
             "can_access_archive": 0, "can_override_duplicate": 0}
    if permissions:
        perms.update(permissions)
    with dbmod.get_db().transaction() as tx:
        tx.execute(
            "INSERT INTO users (name, username, password_hash, role, mobile, status,"
            " created_at) VALUES (%s,%s,%s,'operator',%s,%s,%s)",
            (name, username, hash_password(password), "9000000000", status,
             datetime.now()))
        user_id = tx.scalar("SELECT id FROM users WHERE username=%s", (username,))
        tx.execute(
            "INSERT INTO operator_permissions (user_id, can_add_customer,"
            " can_edit_customer, can_add_application, can_update_status,"
            " can_view_reports, can_reply_messages, can_access_archive,"
            " can_override_duplicate, updated_at) VALUES"
            " (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (user_id, perms["can_add_customer"], perms["can_edit_customer"],
             perms["can_add_application"], perms["can_update_status"],
             perms["can_view_reports"], perms["can_reply_messages"],
             perms["can_access_archive"], perms["can_override_duplicate"],
             datetime.now()))
    return user_id


def make_customer(app, mobile="9876500001", first_name="Test", surname="Customer",
                  aadhaar=None, village="Nadiad"):
    with dbmod.get_db().transaction() as tx:
        code = generate_customer_code(tx, mobile)
        tx.execute(
            "INSERT INTO customers (customer_code, surname, first_name, mobile,"
            " village, taluka, post_office, district, state, pincode, aadhaar_number,"
            " status, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s)",
            (code, surname, first_name, mobile, village, "Nadiad", "Nadiad", "Kheda",
             "Gujarat", "387001", aadhaar, datetime.now()))
        return tx.scalar("SELECT id FROM customers WHERE customer_code=%s", (code,))


def get_service(app, form_key="pan"):
    conn = dbmod.get_db().connect()
    try:
        return conn.query_one("SELECT * FROM services WHERE form_key=%s LIMIT 1",
                              (form_key,))
    finally:
        conn.close()


def make_application(app, customer_id, service_id=None, charge="0", govt="107",
                     csc="150", status="Pending", operator_id=None,
                     apply_date=None, days_to=None):
    if service_id is None:
        service_id = get_service(app)["id"]
    charge, govt, csc = Decimal(charge), Decimal(govt), Decimal(csc)
    total = charge + govt + csc
    apply_date = apply_date or date.today()
    with dbmod.get_db().transaction() as tx:
        customer = tx.query_one("SELECT mobile FROM customers WHERE id=%s",
                                (customer_id,))
        code = generate_app_code(tx, customer["mobile"])
        tx.execute(
            "INSERT INTO applications (app_code, customer_id, service_id, operator_id,"
            " apply_date, status, charge, govt_charge, csc_charge, total, charge_paid,"
            " govt_paid, csc_paid, amount_paid, amount_pending, created_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,0,0,%s,%s)",
            (code, customer_id, service_id, operator_id, apply_date, status,
             charge, govt, csc, total, total, datetime.now()))
        app_id = tx.scalar("SELECT id FROM applications WHERE app_code=%s", (code,))
        tx.execute("INSERT INTO application_details (application_id, document_name,"
                   " total_days) VALUES (%s,%s,%s)", (app_id, "Aadhaar", 1))
    return app_id, code


def application_row(app, app_id):
    conn = dbmod.get_db().connect()
    try:
        return conn.query_one("SELECT * FROM applications WHERE id=%s", (app_id,))
    finally:
        conn.close()
