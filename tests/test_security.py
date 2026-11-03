"""Security: CSRF, password hashing, session handling, XSS escaping."""
import re

import db as dbmod
from modules.security import hash_password, password_problem, verify_password
from tests.conftest import login, make_customer


def test_password_is_hashed_not_plain(app, conn):
    row = conn.query_one("SELECT password_hash FROM users WHERE username='admin'")
    assert row["password_hash"] != "Admin@123"
    assert row["password_hash"].startswith("pbkdf2:sha256")
    assert verify_password(row["password_hash"], "Admin@123") is True
    assert verify_password(row["password_hash"], "wrong") is False


def test_password_strength_rules():
    assert password_problem("short") is not None
    assert password_problem("alllettersonly") is not None
    assert password_problem("12345678") is not None
    assert password_problem("GoodPass1") is None


def test_csrf_is_enforced_when_enabled(app):
    app.config["CSRF_ENABLED"] = True
    client = app.test_client()
    login(client)                                   # login page token is used
    # POST without a token is rejected
    assert client.post("/customers/new", data={"mobile": "9111111111"}).status_code == 400
    page = client.get("/customers/new").get_data(as_text=True)
    token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    response = client.post("/customers/new", data={
        "csrf_token": token, "surname": "A", "first_name": "B", "mobile": "9111111111",
        "village": "V", "taluka": "T", "post_office": "P", "district": "D",
        "state": "Gujarat", "pincode": "387001"}, follow_redirects=True)
    assert response.status_code == 200


def test_failed_login_does_not_create_session(client, app):
    response = client.post("/login", data={"username": "admin", "password": "bad"},
                           follow_redirects=True)
    assert "Wrong username or password" in response.get_data(as_text=True)
    assert client.get("/").status_code == 302


def test_xss_payload_is_escaped(admin_client, app, conn):
    payload = "<script>alert('x')</script>"
    response = admin_client.post("/customers/new", data={
        "surname": payload, "first_name": "Xss", "mobile": "9111111112",
        "village": "V", "taluka": "T", "post_office": "P", "district": "D",
        "state": "Gujarat", "pincode": "387001"}, follow_redirects=True)
    body = response.get_data(as_text=True)
    assert "<script>alert('x')</script>" not in body
    assert "&lt;script&gt;" in body


def test_aadhaar_is_masked_in_lists(admin_client, app, conn):
    make_customer(app, mobile="9111111113", aadhaar="234567890123")
    body = admin_client.get("/customers/").get_data(as_text=True)
    assert "234567890123" not in body
    assert "XXXX XXXX 0123" in body


def test_security_headers_present(admin_client):
    response = admin_client.get("/")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert "Content-Security-Policy" in response.headers


def test_uploads_require_login(client):
    response = client.get("/uploads/photos/none.jpg")
    assert response.status_code in (302, 403)


def test_password_change_requires_current_password(admin_client, app, conn):
    response = admin_client.post("/profile/password", data={
        "current_password": "wrong", "new_password": "NewPass123",
        "confirm_password": "NewPass123"}, follow_redirects=True)
    assert "Current password is wrong" in response.get_data(as_text=True)
    row = conn.query_one("SELECT password_hash FROM users WHERE username='admin'")
    assert verify_password(row["password_hash"], "Admin@123")

    admin_client.post("/profile/password", data={
        "current_password": "Admin@123", "new_password": "NewPass123",
        "confirm_password": "NewPass123"}, follow_redirects=True)
    row = conn.query_one("SELECT password_hash FROM users WHERE username='admin'")
    assert verify_password(row["password_hash"], "NewPass123")
    assert conn.scalar("SELECT COUNT(*) FROM audit_logs WHERE action='password_change'") == 1


def test_document_password_is_hashed(admin_client, app, conn):
    import io
    customer_id = make_customer(app, mobile="9111111114")
    response = admin_client.post("/customers/%d/documents" % customer_id, data={
        "doc_name": "Aadhaar", "doc_type": "ID",
        "is_protected": "1", "doc_password": "secret123",
        "file": (io.BytesIO(b"%PDF-1.4 test"), "aadhaar.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    doc = conn.query_one("SELECT * FROM customer_documents")
    assert doc["is_protected"] == 1
    assert doc["password_hash"] != "secret123"
    assert verify_password(doc["password_hash"], "secret123")
