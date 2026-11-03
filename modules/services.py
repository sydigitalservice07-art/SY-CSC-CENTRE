"""
SY CSC CENTRE - Service management
==================================
Admin adds / edits services, manages sub-services, activates or deactivates
them and sets default charges.

Important rule implemented here: changing a service charge NEVER touches the
charges already stored on existing applications (they are copied onto the
application row at creation time).
"""
from datetime import datetime

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)

from db import D, get_db
from modules.i18n import translate
from modules.security import (admin_required, login_required, write_audit)
from modules.utils import clean_text, parse_decimal, parse_int

bp = Blueprint("services", __name__, url_prefix="/services")

FORM_KEYS = ["pan", "ration", "ayushman", "abha", "voting", "generic"]


def _(key):
    return translate(key, session.get("lang", "en"))


@bp.route("/")
@login_required
def index():
    q = clean_text(request.args.get("q"), 60)
    conn = get_db().connect()
    try:
        sql = ("SELECT s.*, (SELECT COUNT(*) FROM sub_services ss WHERE ss.service_id=s.id)"
               " AS sub_count, (SELECT COUNT(*) FROM applications a WHERE"
               " a.service_id=s.id AND a.is_deleted=0) AS app_count FROM services s")
        params = []
        if q:
            sql += " WHERE s.name LIKE %s OR s.category LIKE %s"
            params = ["%" + q + "%", "%" + q + "%"]
        sql += " ORDER BY s.is_active DESC, s.name ASC"
        rows = conn.query(sql, tuple(params))
        subs = conn.query("SELECT * FROM sub_services ORDER BY service_id, name")
    finally:
        conn.close()
    grouped = {}
    for sub in subs:
        grouped.setdefault(sub["service_id"], []).append(sub)
    return render_template("services/list.html", services=rows, subs=grouped, q=q,
                           form_keys=FORM_KEYS)


def _payload(form):
    return {
        "name": clean_text(form.get("name"), 120),
        "category": clean_text(form.get("category"), 80),
        "description": clean_text(form.get("description"), 500),
        "form_key": form.get("form_key") if form.get("form_key") in FORM_KEYS else "generic",
        "default_charge": parse_decimal(form.get("default_charge")),
        "default_govt_charge": parse_decimal(form.get("default_govt_charge")),
        "default_csc_charge": parse_decimal(form.get("default_csc_charge")),
        "processing_days": parse_int(form.get("processing_days")),
        "required_documents": clean_text(form.get("required_documents"), 255),
        "is_active": 1 if form.get("is_active") else 0,
    }


@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
def create():
    if request.method == "POST":
        data = _payload(request.form)
        errors = []
        if not data["name"]:
            errors.append("Service name is required.")
        if data["default_charge"] < 0 or data["default_govt_charge"] < 0 \
                or data["default_csc_charge"] < 0:
            errors.append("Charges cannot be negative.")
        conn = get_db().connect()
        try:
            if data["name"] and conn.query_one("SELECT id FROM services WHERE name=%s",
                                               (data["name"],)):
                errors.append("A service with this name already exists.")
        finally:
            conn.close()
        if not errors:
            with get_db().transaction() as tx:
                tx.execute(
                    "INSERT INTO services (name, category, description, form_key,"
                    " default_charge, default_govt_charge, default_csc_charge,"
                    " processing_days, required_documents, is_active, created_at,"
                    " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (data["name"], data["category"], data["description"],
                     data["form_key"], data["default_charge"],
                     data["default_govt_charge"], data["default_csc_charge"],
                     data["processing_days"], data["required_documents"],
                     data["is_active"], datetime.now(), g.user["id"]))
                new_id = tx.scalar("SELECT id FROM services WHERE name=%s",
                                   (data["name"],))
                write_audit(tx, "service_create", "services", new_id,
                            "Created service %s" % data["name"])
            flash("Service created.", "success")
            return redirect(url_for("services.index"))
        for message in errors:
            flash(message, "danger")
        return render_template("services/form.html", service=request.form,
                               mode="create", form_keys=FORM_KEYS)
    return render_template("services/form.html", service={}, mode="create",
                           form_keys=FORM_KEYS)


@bp.route("/<int:service_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit(service_id):
    conn = get_db().connect()
    try:
        service = conn.query_one("SELECT * FROM services WHERE id=%s", (service_id,))
        if not service:
            abort(404)
        if request.method == "POST":
            data = _payload(request.form)
            errors = []
            if not data["name"]:
                errors.append("Service name is required.")
            duplicate = conn.query_one(
                "SELECT id FROM services WHERE name=%s AND id<>%s",
                (data["name"], service_id))
            if duplicate:
                errors.append("Another service already uses this name.")
            if not errors:
                with get_db().transaction() as tx:
                    tx.execute(
                        "UPDATE services SET name=%s, category=%s, description=%s,"
                        " form_key=%s, default_charge=%s, default_govt_charge=%s,"
                        " default_csc_charge=%s, processing_days=%s,"
                        " required_documents=%s, is_active=%s, updated_at=%s,"
                        " updated_by=%s WHERE id=%s",
                        (data["name"], data["category"], data["description"],
                         data["form_key"], data["default_charge"],
                         data["default_govt_charge"], data["default_csc_charge"],
                         data["processing_days"], data["required_documents"],
                         data["is_active"], datetime.now(), g.user["id"], service_id))
                    write_audit(tx, "service_update", "services", service_id,
                                "Updated service %s (existing applications keep their"
                                " saved charges)" % data["name"])
                flash("Service updated. Existing applications keep their original charges.",
                      "success")
                return redirect(url_for("services.index"))
            for message in errors:
                flash(message, "danger")
            return render_template("services/form.html", service=request.form,
                                   mode="edit", form_keys=FORM_KEYS,
                                   service_id=service_id)
        return render_template("services/form.html", service=service, mode="edit",
                               form_keys=FORM_KEYS, service_id=service_id)
    finally:
        conn.close()


@bp.route("/<int:service_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle(service_id):
    with get_db().transaction() as tx:
        service = tx.query_one("SELECT name, is_active FROM services WHERE id=%s",
                               (service_id,))
        if not service:
            abort(404)
        new_state = 0 if int(service["is_active"]) else 1
        tx.execute("UPDATE services SET is_active=%s, updated_at=%s, updated_by=%s"
                   " WHERE id=%s", (new_state, datetime.now(), g.user["id"], service_id))
        write_audit(tx, "service_toggle", "services", service_id,
                    "%s service %s" % ("Activated" if new_state else "Deactivated",
                                       service["name"]))
    flash("Service %s." % ("activated" if new_state else "deactivated"), "success")
    return redirect(url_for("services.index"))


# -------------------------------------------------------------- sub-services
@bp.route("/<int:service_id>/sub-services", methods=["POST"])
@login_required
@admin_required
def add_sub_service(service_id):
    name = clean_text(request.form.get("sub_name"), 120)
    if not name:
        flash("Sub-service name is required.", "danger")
        return redirect(url_for("services.index"))
    with get_db().transaction() as tx:
        service = tx.query_one("SELECT name FROM services WHERE id=%s", (service_id,))
        if not service:
            abort(404)
        existing = tx.query_one(
            "SELECT id FROM sub_services WHERE service_id=%s AND name=%s",
            (service_id, name))
        if existing:
            flash("This sub-service already exists for the service.", "danger")
            return redirect(url_for("services.index"))
        tx.execute(
            "INSERT INTO sub_services (service_id, name, default_charge,"
            " default_govt_charge, default_csc_charge, is_active, created_at,"
            " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (service_id, name, parse_decimal(request.form.get("sub_charge")),
             parse_decimal(request.form.get("sub_govt_charge")),
             parse_decimal(request.form.get("sub_csc_charge")), 1,
             datetime.now(), g.user["id"]))
        write_audit(tx, "subservice_create", "services", service_id,
                    "Added sub-service %s" % name)
    flash("Sub-service added.", "success")
    return redirect(url_for("services.index"))


@bp.route("/sub-services/<int:sub_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_sub_service(sub_id):
    with get_db().transaction() as tx:
        sub = tx.query_one("SELECT service_id, name, is_active FROM sub_services"
                           " WHERE id=%s", (sub_id,))
        if not sub:
            abort(404)
        new_state = 0 if int(sub["is_active"]) else 1
        tx.execute("UPDATE sub_services SET is_active=%s, updated_at=%s WHERE id=%s",
                   (new_state, datetime.now(), sub_id))
        write_audit(tx, "subservice_toggle", "services", sub["service_id"],
                    "Sub-service %s %s" % (sub["name"],
                                           "activated" if new_state else "deactivated"))
    return redirect(url_for("services.index"))
