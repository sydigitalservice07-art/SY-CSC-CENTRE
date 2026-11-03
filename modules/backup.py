"""
SY CSC CENTRE - Backup / Restore / Reset
=======================================
* Backup  : one click JSON download of every business table + metadata.
* Restore : structure validation, automatic safety backup first, then an
            all-or-nothing transaction.
* Reset   : admin only, requires the admin password AND the typed phrase
            RESET-SYCSC, writes an audit row and a safety backup.

Uploaded files (photos, signatures, documents) are NOT inside the JSON - the
page explains exactly how to copy the uploads folder.
"""
import io
import json
import os
from datetime import date, datetime
from decimal import Decimal

from flask import (Blueprint, Response, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)

from db import D, get_db
from modules.i18n import translate
from modules.security import (admin_required, current_user, login_required,
                              verify_password, write_audit)

bp = Blueprint("backup", __name__, url_prefix="/backup")

BACKUP_VERSION = 1

# insert order (parents first) - restore uses this, reset deletes in reverse
TABLES = [
    "users", "operator_permissions", "customers", "customer_documents",
    "services", "sub_services", "applications", "application_details",
    "application_archive", "payments", "ledger_entries", "account_balances",
    "messages", "attendance", "operator_salaries", "salary_payments", "leaves",
    "settings", "id_sequences", "audit_logs", "backup_logs",
]

# reset keeps the login accounts, the service catalogue, settings and opening
# balances; everything transactional is cleared.
RESET_TABLES = [
    "salary_payments", "operator_salaries", "attendance", "leaves", "messages",
    "ledger_entries", "payments", "application_archive", "application_details",
    "applications", "customer_documents", "customers", "id_sequences",
    "audit_logs", "backup_logs",
]


def _(key):
    return translate(key, session.get("lang", "en"))


def _jsonable(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, (bytes, bytearray)):
        return value.decode("utf-8", "replace")
    return value


def build_backup(conn):
    tables = {}
    for table in TABLES:
        rows = conn.query("SELECT * FROM " + table)
        tables[table] = [{k: _jsonable(v) for k, v in row.items()} for row in rows]
    return {
        "meta": {
            "application": "SY CSC CENTRE",
            "backup_version": BACKUP_VERSION,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "engine": conn.engine,
            "counts": {table: len(rows) for table, rows in tables.items()},
        },
        "tables": tables,
    }


def _table_columns(conn, table):
    if conn.engine == "sqlite":
        rows = conn.raw.execute("PRAGMA table_info(%s)" % table).fetchall()
        return [row[1] for row in rows]
    rows = conn.query("SHOW COLUMNS FROM " + table)
    return [row["Field"] for row in rows]


def validate_backup(payload):
    """Return (ok, message)."""
    if not isinstance(payload, dict):
        return False, "The file is not a valid SY CSC CENTRE backup (no JSON object)."
    if "tables" not in payload or not isinstance(payload["tables"], dict):
        return False, "The backup has no 'tables' section."
    meta = payload.get("meta") or {}
    if meta.get("backup_version") not in (None, BACKUP_VERSION):
        return False, "Unsupported backup version: %s" % meta.get("backup_version")
    missing = [t for t in TABLES if t not in payload["tables"]]
    if missing:
        return False, "The backup is missing these tables: %s" % ", ".join(missing)
    for table, rows in payload["tables"].items():
        if not isinstance(rows, list):
            return False, "Table '%s' is not a list of records." % table
        for row in rows[:50]:
            if not isinstance(row, dict):
                return False, "Table '%s' contains a record that is not an object." % table
    return True, "ok"


def _safety_backup(conn):
    folder = os.path.join(current_app.config["UPLOAD_FOLDER"], "backups")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    filename = "safety_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = build_backup(conn)
    with io.open(os.path.join(folder, filename), "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False))
    return filename


# ------------------------------------------------------------------------ page
@bp.route("/")
@login_required
@admin_required
def index():
    conn = get_db().connect()
    try:
        counts = {}
        for table in TABLES:
            counts[table] = conn.scalar("SELECT COUNT(*) FROM " + table, default=0)
        logs = conn.query("SELECT * FROM backup_logs ORDER BY id DESC LIMIT 15")
    finally:
        conn.close()
    return render_template("backup.html", counts=counts, logs=logs,
                           tables=TABLES, upload_folder=current_app.config["UPLOAD_FOLDER"])


# -------------------------------------------------------------------- download
@bp.route("/download")
@login_required
@admin_required
def download():
    conn = get_db().connect()
    try:
        payload = build_backup(conn)
        with get_db().transaction() as tx:
            tx.execute("INSERT INTO backup_logs (action, file_name, record_count,"
                       " status, message, created_at, created_by)"
                       " VALUES ('backup',%s,%s,'success',%s,%s,%s)",
                       ("download", sum(payload["meta"]["counts"].values()),
                        "Manual JSON backup", datetime.now(), g.user["id"]))
            write_audit(tx, "backup_download", "backup", None,
                        "JSON backup downloaded")
    finally:
        conn.close()
    filename = "sycsc_backup_%s.json" % datetime.now().strftime("%Y%m%d_%H%M%S")
    return Response(json.dumps(payload, ensure_ascii=False, indent=1),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=" + filename})


# --------------------------------------------------------------------- restore
@bp.route("/restore", methods=["POST"])
@login_required
@admin_required
def restore():
    upload = request.files.get("backup_file")
    confirm = request.form.get("confirm")
    if not upload or not upload.filename:
        flash("Please choose a backup JSON file.", "danger")
        return redirect(url_for("backup.index"))
    if not confirm:
        flash("Please tick the confirmation box before restoring.", "danger")
        return redirect(url_for("backup.index"))

    try:
        payload = json.loads(upload.read().decode("utf-8"))
    except Exception as exc:
        flash("The file is not valid JSON: %s" % exc, "danger")
        return redirect(url_for("backup.index"))

    ok, message = validate_backup(payload)
    if not ok:
        flash(message, "danger")
        return redirect(url_for("backup.index"))

    database = get_db()
    safety_name = None
    try:
        conn = database.connect()
        try:
            safety_name = _safety_backup(conn)
        finally:
            conn.close()

        with database.transaction() as tx:
            for table in reversed(TABLES):
                tx.execute("DELETE FROM " + table)
            inserted = 0
            for table in TABLES:
                rows = payload["tables"].get(table) or []
                if not rows:
                    continue
                columns = _table_columns(tx, table)
                for row in rows:
                    keys = [k for k in row.keys() if k in columns]
                    if not keys:
                        continue
                    sql = ("INSERT INTO " + table + " (" + ",".join(keys) + ") VALUES ("
                           + ",".join(["%s"] * len(keys)) + ")")
                    tx.execute(sql, tuple(row[k] for k in keys))
                    inserted += 1
            tx.execute("INSERT INTO backup_logs (action, file_name, record_count,"
                       " status, message, created_at, created_by)"
                       " VALUES ('restore',%s,%s,'success',%s,%s,%s)",
                       (upload.filename, inserted,
                        "Safety backup: %s" % safety_name, datetime.now(), g.user["id"]))
            write_audit(tx, "backup_restore", "backup", None,
                        "Restored %d records from %s (safety backup %s)"
                        % (inserted, upload.filename, safety_name))
        flash(_("backup.restore_done") + " %d records. Safety backup: %s"
              % (inserted, safety_name), "success")
    except Exception as exc:
        flash("Restore failed and was rolled back: %s" % exc, "danger")
    return redirect(url_for("backup.index"))


# ----------------------------------------------------------------------- reset
@bp.route("/reset", methods=["POST"])
@login_required
@admin_required
def reset():
    user = current_user()
    password = request.form.get("password") or ""
    phrase = (request.form.get("confirm_phrase") or "").strip()
    if phrase != "RESET-SYCSC":
        flash("Type RESET-SYCSC exactly to confirm.", "danger")
        return redirect(url_for("backup.index"))

    conn = get_db().connect()
    try:
        row = conn.query_one("SELECT password_hash FROM users WHERE id=%s", (user["id"],))
    finally:
        conn.close()
    if not row or not verify_password(row["password_hash"], password):
        flash("Admin password is wrong. Reset cancelled.", "danger")
        return redirect(url_for("backup.index"))

    database = get_db()
    safety_name = None
    try:
        conn = database.connect()
        try:
            safety_name = _safety_backup(conn)
        finally:
            conn.close()
        with database.transaction() as tx:
            for table in RESET_TABLES:
                tx.execute("DELETE FROM " + table)
            tx.execute("INSERT INTO backup_logs (action, file_name, record_count,"
                       " status, message, created_at, created_by)"
                       " VALUES ('reset',%s,0,'success',%s,%s,%s)",
                       (safety_name, "All business data cleared", datetime.now(),
                        user["id"]))
            write_audit(tx, "data_reset", "backup", None,
                        "All business data reset. Safety backup: %s" % safety_name)
        flash(_("backup.reset_done") + " Safety backup saved as %s" % safety_name,
              "success")
    except Exception as exc:
        flash("Reset failed: %s" % exc, "danger")
    return redirect(url_for("backup.index"))
