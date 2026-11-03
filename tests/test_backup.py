"""JSON backup, restore validation and the guarded reset."""
import io
import json

import db as dbmod
from modules.backup import BACKUP_VERSION, TABLES, validate_backup
from tests.conftest import make_application, make_customer


def test_backup_download_contains_metadata_and_tables(admin_client, app):
    make_customer(app, mobile="9400000001")
    response = admin_client.get("/backup/download")
    assert response.status_code == 200
    assert "attachment" in response.headers["Content-Disposition"]
    payload = json.loads(response.get_data(as_text=True))
    assert payload["meta"]["application"] == "SY CSC CENTRE"
    assert payload["meta"]["backup_version"] == BACKUP_VERSION
    assert set(payload["tables"].keys()) == set(TABLES)
    assert payload["meta"]["counts"]["customers"] >= 1
    assert payload["tables"]["customers"][0]["customer_code"].startswith("SYCSC/CUST/")


def test_validate_backup_rejects_bad_payloads():
    ok, _ = validate_backup({"meta": {"backup_version": BACKUP_VERSION},
                             "tables": {t: [] for t in TABLES}})
    assert ok is True

    ok, message = validate_backup({"meta": {}, "tables": {}})
    assert ok is False and "missing" in message

    ok, message = validate_backup("not a dict")
    assert ok is False

    ok, message = validate_backup({"meta": {}, "tables": {t: "wrong" for t in TABLES}})
    assert ok is False

    ok, message = validate_backup({"meta": {"backup_version": 99},
                                   "tables": {t: [] for t in TABLES}})
    assert ok is False and "Unsupported backup version" in message


def test_restore_roundtrip(admin_client, app, conn):
    customer_id = make_customer(app, mobile="9400000002", first_name="Restore")
    backup = json.loads(admin_client.get("/backup/download").get_data(as_text=True))

    with dbmod.get_db().transaction() as tx:
        tx.execute("DELETE FROM customers WHERE id=%s", (customer_id,))
    assert conn.scalar("SELECT COUNT(*) FROM customers WHERE mobile='9400000002'") == 0

    response = admin_client.post("/backup/restore", data={
        "confirm": "1",
        "backup_file": (io.BytesIO(json.dumps(backup).encode("utf-8")), "backup.json"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert response.status_code == 200
    assert "Restore completed" in response.get_data(as_text=True) or \
        "\u0ab0\u0ac0\u0ab8\u0acd\u0a9f\u0acb\u0ab0" in response.get_data(as_text=True)
    restored = conn.query_one("SELECT * FROM customers WHERE mobile='9400000002'")
    assert restored is not None
    assert restored["first_name"] == "Restore"


def test_restore_without_confirmation_is_refused(admin_client, app, conn):
    backup = json.loads(admin_client.get("/backup/download").get_data(as_text=True))
    response = admin_client.post("/backup/restore", data={
        "backup_file": (io.BytesIO(json.dumps(backup).encode("utf-8")), "b.json"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "confirmation box" in response.get_data(as_text=True)


def test_restore_of_invalid_json_is_refused(admin_client, app, conn):
    response = admin_client.post("/backup/restore", data={
        "confirm": "1",
        "backup_file": (io.BytesIO(b"{not json"), "b.json"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert "not valid JSON" in response.get_data(as_text=True)


def test_safety_backup_is_written_before_restore(admin_client, app):
    import os
    backup = json.loads(admin_client.get("/backup/download").get_data(as_text=True))
    admin_client.post("/backup/restore", data={
        "confirm": "1",
        "backup_file": (io.BytesIO(json.dumps(backup).encode("utf-8")), "b.json"),
    }, content_type="multipart/form-data", follow_redirects=True)
    folder = os.path.join(app.config["UPLOAD_FOLDER"], "backups")
    assert os.path.isdir(folder)
    assert any(name.startswith("safety_") for name in os.listdir(folder))


def test_reset_requires_phrase_password_and_keeps_admin(admin_client, app, conn):
    make_customer(app, mobile="9400000003")

    # wrong phrase
    response = admin_client.post("/backup/reset", data={
        "password": "Admin@123", "confirm_phrase": "reset"}, follow_redirects=True)
    assert "RESET-SYCSC" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM customers") == 1

    # right phrase, wrong password
    response = admin_client.post("/backup/reset", data={
        "password": "wrong", "confirm_phrase": "RESET-SYCSC"}, follow_redirects=True)
    assert "password is wrong" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM customers") == 1

    # correct phrase + password
    response = admin_client.post("/backup/reset", data={
        "password": "Admin@123", "confirm_phrase": "RESET-SYCSC"},
        follow_redirects=True)
    assert conn.scalar("SELECT COUNT(*) FROM customers") == 0
    assert conn.scalar("SELECT COUNT(*) FROM applications") == 0
    assert conn.scalar("SELECT COUNT(*) FROM users WHERE role='admin'") == 1
    assert conn.scalar("SELECT COUNT(*) FROM services") >= 5
    assert conn.scalar("SELECT COUNT(*) FROM audit_logs WHERE action='data_reset'") == 1
