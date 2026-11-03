"""
SY CSC CENTRE - Application management
======================================
One module, five separate PDF-based forms:

    PAN Card        -> token no, ack no, e-pan date, postal tracking,
                       physical PAN date, document name
    Ration Card     -> ration card no, AP no, pay no, done date, document name
    Ayushman Card   -> ration card no, done date, document name
    ABHA Card       -> done date, document name
    Voting Card     -> done date, document name

Every form keeps the three financial columns of the PDF (Charge, Government
Charge, CSC Charge) and computes Total = Charge + Government Charge + CSC
Charge.  "Total Days" is always computed from the two dates, never typed.

A "Completed and fully paid" application disappears from the default active
list but stays in customer history, the archive, search and reports.
"""
from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)

from db import D, ZERO, get_db
from modules.i18n import translate
from modules.security import (current_user, has_permission, login_required,
                              permission_required, write_audit)
from modules.utils import (APPLICATION_STATUSES, calc_total_days, clean_text,
                           generate_app_code, parse_date, parse_decimal,
                           parse_int)

bp = Blueprint("applications", __name__, url_prefix="/applications")

FORM_META = {
    "pan": {
        "label": "PAN Card Application",
        "template": "applications/form_pan.html",
        "fields": ["token_no", "ack_no", "epan_date", "postal_tracking_no",
                   "physical_pan_date", "document_name"],
        "day_to": "physical_pan_date",
    },
    "ration": {
        "label": "Ration Card Application",
        "template": "applications/form_ration.html",
        "fields": ["ration_card_no", "ap_number", "pay_number", "done_date",
                   "document_name"],
        "day_to": "done_date",
    },
    "ayushman": {
        "label": "Ayushman Card Application",
        "template": "applications/form_ayushman.html",
        "fields": ["ration_card_no", "done_date", "document_name"],
        "day_to": "done_date",
    },
    "abha": {
        "label": "ABHA Card Application",
        "template": "applications/form_abha.html",
        "fields": ["done_date", "document_name"],
        "day_to": "done_date",
    },
    "voting": {
        "label": "Voting Card Application",
        "template": "applications/form_voting.html",
        "fields": ["done_date", "document_name"],
        "day_to": "done_date",
    },
    "generic": {
        "label": "Application",
        "template": "applications/form_generic.html",
        "fields": ["document_name"],
        "day_to": "done_date",
    },
}

DATE_FIELDS = {"epan_date", "physical_pan_date", "done_date"}


def _(key):
    return translate(key, session.get("lang", "en"))


# --------------------------------------------------------------------- helpers
def _paginate(page, total, per_page):
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    return {"page": page, "pages": pages, "per_page": per_page,
            "offset": (page - 1) * per_page, "total": total}


def find_duplicates(conn, customer_id, service_id, exclude_id=None):
    """
    Any earlier application of the same customer for the same service counts -
    active, completed, cancelled or archived.  Deleted rows are included too,
    so a cancelled/voided application cannot be silently recreated.
    """
    sql = ("SELECT a.id, a.app_code, a.apply_date, a.status, a.is_archived,"
           " a.is_deleted FROM applications a"
           " WHERE a.customer_id=%s AND a.service_id=%s")
    params = [customer_id, service_id]
    if exclude_id:
        sql += " AND a.id<>%s"
        params.append(exclude_id)
    sql += " ORDER BY a.id DESC"
    return conn.query(sql, tuple(params))


def _detail_values(form, form_key):
    values = {}
    for field in FORM_META[form_key]["fields"]:
        if field in DATE_FIELDS:
            values[field] = parse_date(form.get(field))
        else:
            values[field] = clean_text(form.get(field), 160)
    return values


def _charge_values(form):
    return (parse_decimal(form.get("charge")),
            parse_decimal(form.get("govt_charge")),
            parse_decimal(form.get("csc_charge")))


def _save_details(conn, application_id, form_key, values, total_days):
    columns = ["application_id"] + FORM_META[form_key]["fields"] + ["total_days"]
    params = [application_id] + [values.get(f) for f in FORM_META[form_key]["fields"]] \
        + [total_days]
    placeholders = ",".join(["%s"] * len(columns))
    conn.execute("INSERT INTO application_details (%s) VALUES (%s)"
                 % (",".join(columns), placeholders), tuple(params))


def _update_details(conn, application_id, form_key, values, total_days):
    assignments = ", ".join(["%s=%%s" % f for f in FORM_META[form_key]["fields"]]
                            + ["total_days=%s"])
    params = [values.get(f) for f in FORM_META[form_key]["fields"]] + [total_days,
                                                                      application_id]
    conn.execute("UPDATE application_details SET " + assignments
                 + " WHERE application_id=%s", tuple(params))


def _form_context(form_key, service, customer, application=None, details=None,
                  duplicate=None, errors=None):
    sub_services = []
    try:
        probe = get_db().connect()
        try:
            sub_services = probe.query(
                "SELECT id, name FROM sub_services WHERE service_id=%s"
                " AND is_active=1 ORDER BY name", (service["id"],))
        finally:
            probe.close()
    except Exception:
        sub_services = []
    return {
        "sub_services": sub_services,
        "form_meta": FORM_META[form_key],
        "form_key": form_key,
        "service": service,
        "customer": customer,
        "application": application,
        "details": details or {},
        "duplicate": duplicate,
        "errors": errors or [],
        "statuses": APPLICATION_STATUSES,
    }


# ------------------------------------------------------------------------ list
@bp.route("/")
@login_required
def index():
    conn = get_db().connect()
    try:
        q = clean_text(request.args.get("q"), 80)
        status = request.args.get("status", "")
        service_id = parse_int(request.args.get("service_id"))
        show = request.args.get("show", "active")
        date_from = parse_date(request.args.get("from"))
        date_to = parse_date(request.args.get("to"))
        page = max(1, parse_int(request.args.get("page"), 1))
        per_page = current_app.config["ITEMS_PER_PAGE"]

        where = ["a.is_deleted=0"]
        params = []
        if show != "all":
            where.append("a.is_archived=0")
        if show in ("active", ""):
            where.append("NOT (a.status='Completed' AND a.amount_pending <= 0)")
        if status:
            where.append("a.status=%s")
            params.append(status)
        if service_id:
            where.append("a.service_id=%s")
            params.append(service_id)
        if date_from:
            where.append("a.apply_date >= %s")
            params.append(date_from)
        if date_to:
            where.append("a.apply_date <= %s")
            params.append(date_to)
        if q:
            like = "%" + q + "%"
            where.append("(a.app_code LIKE %s OR c.first_name LIKE %s"
                         " OR c.surname LIKE %s OR c.mobile LIKE %s"
                         " OR c.customer_code LIKE %s)")
            params.extend([like, like, like, like, like])
        where_sql = " AND ".join(where)

        total = conn.scalar(
            "SELECT COUNT(*) FROM applications a JOIN customers c ON c.id=a.customer_id"
            " WHERE " + where_sql, tuple(params), default=0)
        page_info = _paginate(page, total, per_page)
        rows = conn.query(
            "SELECT a.id, a.app_code, a.apply_date, a.status, a.charge,"
            " a.govt_charge, a.csc_charge, a.total, a.amount_paid, a.amount_pending,"
            " a.is_archived, c.customer_code, c.first_name, c.surname, c.mobile,"
            " s.name AS service_name, u.name AS operator_name"
            " FROM applications a JOIN customers c ON c.id=a.customer_id"
            " JOIN services s ON s.id=a.service_id"
            " LEFT JOIN users u ON u.id=a.operator_id"
            " WHERE " + where_sql + " ORDER BY a.id DESC LIMIT %s OFFSET %s",
            tuple(params + [per_page, page_info["offset"]]))
        services = conn.query("SELECT id, name, is_active FROM services ORDER BY name")
    finally:
        conn.close()

    return render_template("applications/list.html", applications=rows, q=q,
                           status=status, service_id=service_id, show=show,
                           date_from=request.args.get("from", ""),
                           date_to=request.args.get("to", ""),
                           services=services, statuses=APPLICATION_STATUSES,
                           page_info=page_info)


# ---------------------------------------------------------------------- create
@bp.route("/new", methods=["GET", "POST"])
@login_required
@permission_required("can_add_application")
def create():
    conn = get_db().connect()
    try:
        customer_id = parse_int(request.form.get("customer_id") or
                                request.args.get("customer_id"))
        service_id = parse_int(request.form.get("service_id") or
                               request.args.get("service_id"))
        customer = conn.query_one(
            "SELECT * FROM customers WHERE id=%s", (customer_id,)) if customer_id else None
        service = conn.query_one(
            "SELECT * FROM services WHERE id=%s", (service_id,)) if service_id else None

        # ---- step 1: nothing chosen yet -> chooser page
        if not customer or not service:
            customers = conn.query(
                "SELECT id, customer_code, first_name, surname, mobile, village"
                " FROM customers WHERE status='active' ORDER BY id DESC LIMIT 300")
            services = conn.query(
                "SELECT * FROM services WHERE is_active=1 ORDER BY name")
            if request.method == "POST":
                flash("Please select both a customer and a service.", "danger")
            return render_template("applications/choose.html", customers=customers,
                                   services=services)

        if int(service["is_active"]) != 1:
            flash("This service is deactivated and cannot be used for a new application.",
                  "danger")
            return redirect(url_for("applications.create"))

        form_key = service["form_key"] or "generic"
        meta = FORM_META[form_key]

        if request.method == "GET":
            duplicate = find_duplicates(conn, customer["id"], service["id"])
            details = {}
            return render_template(
                meta["template"],
                **_form_context(form_key, service, customer, None, details,
                                duplicate[0] if duplicate else None))

        # ---- POST: validate
        form = request.form
        errors = []
        apply_date = parse_date(form.get("apply_date")) or date.today()
        charge, govt, csc = _charge_values(form)
        if charge < 0 or govt < 0 or csc < 0:
            errors.append("Charges cannot be negative.")
        status = form.get("status") if form.get("status") in APPLICATION_STATUSES \
            else "Pending"
        values = _detail_values(form, form_key)
        total_days = calc_total_days(apply_date, values.get(meta["day_to"]))
        if values.get(meta["day_to"]) and values.get(meta["day_to"]) < apply_date:
            errors.append("The completion date cannot be earlier than the apply date.")
        total = charge + govt + csc

        duplicate = find_duplicates(conn, customer["id"], service["id"])
        override_allowed = has_permission("can_override_duplicate")
        override_reason = clean_text(form.get("duplicate_reason"), 255)
        overriding = bool(form.get("override_duplicate")) and override_allowed \
            and override_reason

        if duplicate and not overriding:
            if form.get("override_duplicate") and not override_allowed:
                errors.append("You are not allowed to override the duplicate warning.")
            return render_template(
                meta["template"],
                **_form_context(form_key, service, customer, None, values,
                                duplicate[0], errors))
        if errors:
            return render_template(
                meta["template"],
                **_form_context(form_key, service, customer, None, values,
                                duplicate[0] if duplicate else None, errors))

        with get_db().transaction() as tx:
            code = generate_app_code(tx, customer["mobile"])
            tx.execute(
                "INSERT INTO applications (app_code, customer_id, service_id,"
                " sub_service_id, operator_id, apply_date, status, charge,"
                " govt_charge, csc_charge, total, charge_paid, govt_paid, csc_paid,"
                " amount_paid, amount_pending, notes, is_archived, is_deleted,"
                " created_at, created_by)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,%s,%s)",
                (code, customer["id"], service["id"],
                 parse_int(form.get("sub_service_id")) or None,
                 g.user["id"], apply_date, status, charge, govt, csc, total,
                 ZERO, ZERO, ZERO, ZERO, total,
                 clean_text(form.get("notes"), 1000), datetime.now(), g.user["id"]))
            new_id = tx.scalar("SELECT id FROM applications WHERE app_code=%s", (code,))
            _save_details(tx, new_id, form_key, values, total_days)
            if status == "Completed":
                tx.execute("UPDATE applications SET completed_at=%s WHERE id=%s",
                           (datetime.now(), new_id))
            write_audit(tx, "application_create", "applications", new_id,
                        "Created %s for %s (%s) total %s"
                        % (code, customer["customer_code"], service["name"], total))
            if duplicate and overriding:
                write_audit(tx, "duplicate_override", "applications", new_id,
                            "Duplicate override reason: %s (previous: %s)"
                            % (override_reason, duplicate[0]["app_code"]))
        flash("Application created: %s" % code, "success")
        return redirect(url_for("applications.detail", application_id=new_id))
    finally:
        conn.close()


# ---------------------------------------------------------------------- detail
@bp.route("/<int:application_id>")
@login_required
def detail(application_id):
    conn = get_db().connect()
    try:
        application = conn.query_one(
            "SELECT a.*, c.customer_code, c.first_name, c.surname, c.mobile,"
            " c.village, c.taluka, c.district, c.photo_path, c.signature_path,"
            " s.name AS service_name, s.form_key, s.required_documents,"
            " ss.name AS sub_service_name, u.name AS operator_name"
            " FROM applications a JOIN customers c ON c.id=a.customer_id"
            " JOIN services s ON s.id=a.service_id"
            " LEFT JOIN sub_services ss ON ss.id=a.sub_service_id"
            " LEFT JOIN users u ON u.id=a.operator_id WHERE a.id=%s",
            (application_id,))
        if not application:
            abort(404)
        details = conn.query_one("SELECT * FROM application_details WHERE application_id=%s",
                                 (application_id,)) or {}
        payments = conn.query(
            "SELECT p.*, u.name AS created_by_name FROM payments p"
            " LEFT JOIN users u ON u.id=p.created_by"
            " WHERE p.application_id=%s ORDER BY p.id DESC", (application_id,))
        history = conn.query(
            "SELECT action, username, details, created_at FROM audit_logs"
            " WHERE entity='applications' AND entity_id=%s ORDER BY id DESC LIMIT 30",
            (str(application_id),))
        archive_row = conn.query_one(
            "SELECT aa.*, u.name AS archived_by_name FROM application_archive aa"
            " LEFT JOIN users u ON u.id=aa.archived_by WHERE aa.application_id=%s",
            (application_id,))
    finally:
        conn.close()

    form_key = application["form_key"] or "generic"
    return render_template("applications/detail.html", application=application,
                           details=details, payments=payments, history=history,
                           archive_row=archive_row,
                           form_meta=FORM_META.get(form_key, FORM_META["generic"]),
                           form_key=form_key, statuses=APPLICATION_STATUSES)


# ------------------------------------------------------------------------ edit
@bp.route("/<int:application_id>/edit", methods=["GET", "POST"])
@login_required
def edit(application_id):
    conn = get_db().connect()
    try:
        application = conn.query_one(
            "SELECT a.*, s.name AS service_name, s.form_key FROM applications a"
            " JOIN services s ON s.id=a.service_id WHERE a.id=%s", (application_id,))
        if not application:
            abort(404)
        customer = conn.query_one("SELECT * FROM customers WHERE id=%s",
                                  (application["customer_id"],))
        service = conn.query_one("SELECT * FROM services WHERE id=%s",
                                 (application["service_id"],))
        details = conn.query_one("SELECT * FROM application_details WHERE application_id=%s",
                                 (application_id,)) or {}
        form_key = application["form_key"] or "generic"
        meta = FORM_META[form_key]

        can_change_status = has_permission("can_update_status")
        if request.method == "GET":
            return render_template(meta["template"],
                                   **_form_context(form_key, service, customer,
                                                   application, details))

        form = request.form
        errors = []
        apply_date = parse_date(form.get("apply_date")) or application["apply_date"]
        charge, govt, csc = _charge_values(form)
        if charge < 0 or govt < 0 or csc < 0:
            errors.append("Charges cannot be negative.")
        values = _detail_values(form, form_key)
        total_days = calc_total_days(apply_date, values.get(meta["day_to"]))
        if values.get(meta["day_to"]) and values.get(meta["day_to"]) < apply_date:
            errors.append("The completion date cannot be earlier than the apply date.")
        total = charge + govt + csc
        paid = D(application["amount_paid"])
        if total < paid:
            errors.append("Total cannot be lower than the amount already paid"
                          " (%s)." % paid)
        status = application["status"]
        if can_change_status:
            requested = form.get("status")
            if requested in APPLICATION_STATUSES:
                status = requested

        if errors:
            merged = dict(application)
            merged.update({"charge": charge, "govt_charge": govt, "csc_charge": csc,
                           "total": total, "apply_date": apply_date,
                           "status": status})
            return render_template(meta["template"],
                                   **_form_context(form_key, service, customer,
                                                   merged, values, None, errors))

        with get_db().transaction() as tx:
            tx.execute(
                "UPDATE applications SET apply_date=%s, status=%s, charge=%s,"
                " govt_charge=%s, csc_charge=%s, total=%s, amount_pending=%s,"
                " notes=%s, sub_service_id=%s, updated_at=%s, updated_by=%s"
                " WHERE id=%s",
                (apply_date, status, charge, govt, csc, total,
                 total - paid, clean_text(form.get("notes"), 1000),
                 parse_int(form.get("sub_service_id")) or None,
                 datetime.now(), g.user["id"], application_id))
            _update_details(tx, application_id, form_key, values, total_days)
            if status == "Completed" and not application["completed_at"]:
                tx.execute("UPDATE applications SET completed_at=%s WHERE id=%s",
                           (datetime.now(), application_id))
            if application["status"] != status:
                write_audit(tx, "status_change", "applications", application_id,
                            "Status %s -> %s" % (application["status"], status))
            write_audit(tx, "application_update", "applications", application_id,
                        "Updated %s. Charges: charge %s -> %s, govt %s -> %s,"
                        " csc %s -> %s"
                        % (application["app_code"], application["charge"], charge,
                           application["govt_charge"], govt,
                           application["csc_charge"], csc))
        flash("Application updated.", "success")
        return redirect(url_for("applications.detail", application_id=application_id))
    finally:
        conn.close()


# ---------------------------------------------------------------- status update
@bp.route("/<int:application_id>/status", methods=["POST"])
@login_required
@permission_required("can_update_status")
def update_status(application_id):
    new_status = request.form.get("status")
    if new_status not in APPLICATION_STATUSES:
        flash("Invalid status.", "danger")
        return redirect(url_for("applications.detail", application_id=application_id))
    with get_db().transaction() as tx:
        application = tx.query_one("SELECT app_code, status FROM applications"
                                   " WHERE id=%s", (application_id,))
        if not application:
            abort(404)
        tx.execute("UPDATE applications SET status=%s, updated_at=%s, updated_by=%s"
                   " WHERE id=%s",
                   (new_status, datetime.now(), g.user["id"], application_id))
        if new_status == "Completed":
            tx.execute("UPDATE applications SET completed_at=%s WHERE id=%s",
                       (datetime.now(), application_id))
        write_audit(tx, "status_change", "applications", application_id,
                    "Status %s -> %s" % (application["status"], new_status))
    flash("Status changed to %s." % new_status, "success")
    return redirect(url_for("applications.detail", application_id=application_id))


# ------------------------------------------------------------------ archive
@bp.route("/<int:application_id>/archive", methods=["POST"])
@login_required
def archive(application_id):
    if not (has_permission("can_access_archive") or
            (current_user() and current_user()["role"] == "admin")):
        abort(403)
    reason = clean_text(request.form.get("reason"), 255) or "Archived"
    with get_db().transaction() as tx:
        application = tx.query_one("SELECT app_code, is_archived FROM applications"
                                   " WHERE id=%s", (application_id,))
        if not application:
            abort(404)
        if int(application["is_archived"]):
            flash("This application is already archived.", "warning")
            return redirect(url_for("applications.detail",
                                    application_id=application_id))
        tx.execute("UPDATE applications SET is_archived=1, updated_at=%s,"
                   " updated_by=%s WHERE id=%s",
                   (datetime.now(), g.user["id"], application_id))
        tx.execute("INSERT INTO application_archive (application_id, archived_at,"
                   " archived_by, reason) VALUES (%s,%s,%s,%s)",
                   (application_id, datetime.now(), g.user["id"], reason))
        write_audit(tx, "application_archive", "applications", application_id,
                    "Archived %s - %s" % (application["app_code"], reason))
    flash("Application archived.", "success")
    return redirect(url_for("applications.archived"))


@bp.route("/<int:application_id>/restore", methods=["POST"])
@login_required
def restore(application_id):
    if not (has_permission("can_access_archive") or
            (current_user() and current_user()["role"] == "admin")):
        abort(403)
    with get_db().transaction() as tx:
        application = tx.query_one("SELECT app_code, is_archived FROM applications"
                                   " WHERE id=%s", (application_id,))
        if not application:
            abort(404)
        # restore works on the SAME row - no duplicate application is created
        tx.execute("UPDATE applications SET is_archived=0, updated_at=%s,"
                   " updated_by=%s WHERE id=%s",
                   (datetime.now(), g.user["id"], application_id))
        tx.execute("DELETE FROM application_archive WHERE application_id=%s",
                   (application_id,))
        write_audit(tx, "application_restore", "applications", application_id,
                    "Restored %s from archive" % application["app_code"])
    flash("Application restored from archive.", "success")
    return redirect(url_for("applications.detail", application_id=application_id))


@bp.route("/archived")
@login_required
def archived():
    if not (has_permission("can_access_archive") or
            (current_user() and current_user()["role"] == "admin")):
        abort(403)
    conn = get_db().connect()
    try:
        q = clean_text(request.args.get("q"), 80)
        date_from = parse_date(request.args.get("from"))
        date_to = parse_date(request.args.get("to"))
        page = max(1, parse_int(request.args.get("page"), 1))
        per_page = 25
        where = ["a.is_archived=1", "a.is_deleted=0"]
        params = []
        if q:
            like = "%" + q + "%"
            where.append("(a.app_code LIKE %s OR c.first_name LIKE %s"
                         " OR c.surname LIKE %s OR c.mobile LIKE %s"
                         " OR c.customer_code LIKE %s OR s.name LIKE %s)")
            params.extend([like] * 6)
        if date_from:
            where.append("a.apply_date >= %s")
            params.append(date_from)
        if date_to:
            where.append("a.apply_date <= %s")
            params.append(date_to)
        where_sql = " AND ".join(where)
        total = conn.scalar(
            "SELECT COUNT(*) FROM applications a JOIN customers c ON c.id=a.customer_id"
            " JOIN services s ON s.id=a.service_id WHERE " + where_sql,
            tuple(params), default=0)
        page_info = _paginate(page, total, per_page)
        rows = conn.query(
            "SELECT a.id, a.app_code, a.apply_date, a.status, a.csc_charge,"
            " a.govt_charge, a.total, a.amount_paid, a.amount_pending,"
            " c.customer_code, c.first_name, c.surname, c.mobile,"
            " s.name AS service_name, u.name AS operator_name,"
            " aa.archived_at, au.name AS archived_by_name, aa.reason"
            " FROM applications a JOIN customers c ON c.id=a.customer_id"
            " JOIN services s ON s.id=a.service_id"
            " LEFT JOIN users u ON u.id=a.operator_id"
            " JOIN application_archive aa ON aa.application_id=a.id"
            " LEFT JOIN users au ON au.id=aa.archived_by"
            " WHERE " + where_sql + " ORDER BY aa.archived_at DESC LIMIT %s OFFSET %s",
            tuple(params + [per_page, page_info["offset"]]))
    finally:
        conn.close()
    return render_template("applications/archive.html", applications=rows, q=q,
                           date_from=request.args.get("from", ""),
                           date_to=request.args.get("to", ""),
                           page_info=page_info)


# --------------------------------------------------------- cancel / delete
@bp.route("/<int:application_id>/cancel", methods=["POST"])
@login_required
@permission_required("can_update_status")
def cancel(application_id):
    reason = clean_text(request.form.get("reason"), 255)
    if not reason:
        flash("A cancellation reason is required.", "danger")
        return redirect(url_for("applications.detail", application_id=application_id))
    with get_db().transaction() as tx:
        application = tx.query_one("SELECT app_code, status FROM applications"
                                   " WHERE id=%s", (application_id,))
        if not application:
            abort(404)
        tx.execute("UPDATE applications SET status='Cancelled', notes=%s,"
                   " updated_at=%s, updated_by=%s WHERE id=%s",
                   (reason, datetime.now(), g.user["id"], application_id))
        write_audit(tx, "application_cancel", "applications", application_id,
                    "Cancelled %s - reason: %s"
                    % (application["app_code"], reason))
    flash("Application cancelled. Payment history is preserved.", "success")
    return redirect(url_for("applications.detail", application_id=application_id))


@bp.route("/<int:application_id>/delete", methods=["POST"])
@login_required
def delete(application_id):
    user = current_user()
    if user["role"] != "admin":
        abort(403)
    reason = clean_text(request.form.get("reason"), 255)
    if not reason:
        flash("A deletion reason is required.", "danger")
        return redirect(url_for("applications.detail", application_id=application_id))
    with get_db().transaction() as tx:
        application = tx.query_one("SELECT app_code FROM applications WHERE id=%s",
                                   (application_id,))
        if not application:
            abort(404)
        payment_count = tx.scalar(
            "SELECT COUNT(*) FROM payments WHERE application_id=%s",
            (application_id,), default=0)
        ledger_count = tx.scalar(
            "SELECT COUNT(*) FROM ledger_entries WHERE application_id=%s",
            (application_id,), default=0)
        if payment_count or ledger_count:
            flash("This application has payments or ledger entries, so it cannot be"
                  " deleted. It has been marked as Cancelled instead - use the"
                  " cancellation option, and post a refund in the Account Sheet if"
                  " money has to go back.", "danger")
            return redirect(url_for("applications.detail",
                                    application_id=application_id))
        # soft delete keeps the row and the whole audit trail
        tx.execute("UPDATE applications SET is_deleted=1, delete_reason=%s,"
                   " deleted_at=%s, deleted_by=%s WHERE id=%s",
                   (reason, datetime.now(), user["id"], application_id))
        write_audit(tx, "application_delete", "applications", application_id,
                    "Soft deleted %s - reason: %s"
                    % (application["app_code"], reason))
    flash("Application deleted (soft delete - the record and its audit trail are kept).",
          "success")
    return redirect(url_for("applications.index"))
