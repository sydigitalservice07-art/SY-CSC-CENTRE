"""
SY CSC CENTRE - Operator management
===================================
Operators, their permissions, attendance, salary, leave and activity.

Salary payments post a debit entry into the Account Sheet so the books stay
consistent (salary is an expense, never a CSC income adjustment).
"""
from datetime import date, datetime

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)

from db import D, ZERO, get_db
from modules.i18n import translate
from modules.security import (admin_required, current_user, hash_password,
                              login_required, password_problem, write_audit)
from modules.utils import (ACCOUNTS, PAYMENT_MODES, clean_text, generate_voucher_no,
                           normalise_mobile, parse_date, parse_decimal, parse_int)

bp = Blueprint("operators", __name__, url_prefix="/operators")

PERMISSION_FIELDS = [
    ("can_add_customer", "Add Customer"),
    ("can_edit_customer", "Edit Customer"),
    ("can_add_application", "Add Application"),
    ("can_update_status", "Update Application Status"),
    ("can_view_reports", "View Reports"),
    ("can_reply_messages", "Reply to Messages"),
    ("can_access_archive", "Access Archive"),
    ("can_override_duplicate", "Override Duplicate Warning"),
]


def _(key):
    return translate(key, session.get("lang", "en"))


def _default_permissions():
    return {"can_add_customer": 0, "can_edit_customer": 1, "can_add_application": 1,
            "can_update_status": 1, "can_view_reports": 0, "can_reply_messages": 1,
            "can_access_archive": 0, "can_override_duplicate": 0}


def _save_permissions(tx, user_id, form, updated_by):
    values = [1 if form.get(field) else 0 for field, _label in PERMISSION_FIELDS]
    existing = tx.query_one("SELECT user_id FROM operator_permissions WHERE user_id=%s",
                            (user_id,))
    if existing:
        assignments = ", ".join(["%s=%%s" % field for field, _l in PERMISSION_FIELDS])
        tx.execute("UPDATE operator_permissions SET " + assignments
                   + ", updated_at=%s, updated_by=%s WHERE user_id=%s",
                   tuple(values + [datetime.now(), updated_by, user_id]))
    else:
        columns = ", ".join([field for field, _l in PERMISSION_FIELDS])
        marks = ",".join(["%s"] * len(PERMISSION_FIELDS))
        tx.execute("INSERT INTO operator_permissions (user_id, " + columns
                   + ", updated_at, updated_by) VALUES (%s," + marks + ",%s,%s)",
                   tuple([user_id] + values + [datetime.now(), updated_by]))


# ------------------------------------------------------------------------ list
@bp.route("/")
@login_required
@admin_required
def index():
    conn = get_db().connect()
    try:
        operators = conn.query(
            "SELECT u.id, u.name, u.username, u.mobile, u.email, u.status,"
            " u.last_login, u.created_at,"
            " (SELECT COUNT(*) FROM applications a WHERE a.operator_id=u.id"
            "  AND a.is_deleted=0) AS app_count"
            " FROM users u WHERE u.role='operator' ORDER BY u.status DESC, u.name")
        perms = conn.query("SELECT * FROM operator_permissions")
    finally:
        conn.close()
    perm_map = {row["user_id"]: row for row in perms}
    return render_template("operators/list.html", operators=operators,
                           perm_map=perm_map, permission_fields=PERMISSION_FIELDS)


# ---------------------------------------------------------------------- create
@bp.route("/new", methods=["GET", "POST"])
@login_required
@admin_required
def create():
    if request.method == "POST":
        name = clean_text(request.form.get("name"), 120)
        username = clean_text(request.form.get("username"), 60).lower()
        password = request.form.get("password") or ""
        mobile = normalise_mobile(request.form.get("mobile"))
        email = clean_text(request.form.get("email"), 120)
        address = clean_text(request.form.get("address"), 255)
        errors = []
        if not name:
            errors.append("Operator name is required.")
        if not username or len(username) < 3:
            errors.append("Username must be at least 3 characters.")
        problem = password_problem(password)
        if problem:
            errors.append(problem)
        conn = get_db().connect()
        try:
            if username and conn.query_one("SELECT id FROM users WHERE username=%s",
                                          (username,)):
                errors.append("This username is already taken.")
        finally:
            conn.close()
        if not errors:
            with get_db().transaction() as tx:
                tx.execute(
                    "INSERT INTO users (name, username, password_hash, role, mobile,"
                    " email, address, status, created_at, created_by)"
                    " VALUES (%s,%s,%s,'operator',%s,%s,%s,'active',%s,%s)",
                    (name, username, hash_password(password), mobile, email, address,
                     datetime.now(), g.user["id"]))
                new_id = tx.scalar("SELECT id FROM users WHERE username=%s", (username,))
                _save_permissions(tx, new_id, request.form, g.user["id"])
                write_audit(tx, "operator_create", "users", new_id,
                            "Created operator %s" % username)
            flash("Operator created.", "success")
            return redirect(url_for("operators.index"))
        for message in errors:
            flash(message, "danger")
        return render_template("operators/form.html", operator=request.form,
                               mode="create", permission_fields=PERMISSION_FIELDS,
                               permissions=request.form)
    return render_template("operators/form.html", operator={}, mode="create",
                           permission_fields=PERMISSION_FIELDS,
                           permissions=_default_permissions())


# ------------------------------------------------------------------------ edit
@bp.route("/<int:operator_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit(operator_id):
    conn = get_db().connect()
    try:
        operator = conn.query_one("SELECT * FROM users WHERE id=%s AND role='operator'",
                                  (operator_id,))
        if not operator:
            abort(404)
        permissions = conn.query_one("SELECT * FROM operator_permissions"
                                     " WHERE user_id=%s", (operator_id,)) \
            or _default_permissions()

        if request.method == "POST":
            name = clean_text(request.form.get("name"), 120)
            mobile = normalise_mobile(request.form.get("mobile"))
            email = clean_text(request.form.get("email"), 120)
            address = clean_text(request.form.get("address"), 255)
            new_password = request.form.get("password") or ""
            errors = []
            if not name:
                errors.append("Operator name is required.")
            if new_password:
                problem = password_problem(new_password)
                if problem:
                    errors.append(problem)
            if not errors:
                with get_db().transaction() as tx:
                    tx.execute("UPDATE users SET name=%s, mobile=%s, email=%s,"
                               " address=%s, updated_at=%s, updated_by=%s WHERE id=%s",
                               (name, mobile, email, address, datetime.now(),
                                g.user["id"], operator_id))
                    if new_password:
                        tx.execute("UPDATE users SET password_hash=%s WHERE id=%s",
                                   (hash_password(new_password), operator_id))
                        write_audit(tx, "operator_password_reset", "users",
                                    operator_id, "Password reset by admin")
                    _save_permissions(tx, operator_id, request.form, g.user["id"])
                    write_audit(tx, "operator_update", "users", operator_id,
                                "Updated operator %s" % operator["username"])
                flash("Operator updated.", "success")
                return redirect(url_for("operators.detail", operator_id=operator_id))
            for message in errors:
                flash(message, "danger")
            return render_template("operators/form.html", operator=request.form,
                                   mode="edit", operator_id=operator_id,
                                   permission_fields=PERMISSION_FIELDS,
                                   permissions=request.form)
        return render_template("operators/form.html", operator=operator, mode="edit",
                               operator_id=operator_id,
                               permission_fields=PERMISSION_FIELDS,
                               permissions=permissions)
    finally:
        conn.close()


@bp.route("/<int:operator_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle(operator_id):
    with get_db().transaction() as tx:
        operator = tx.query_one("SELECT name, status FROM users WHERE id=%s"
                                " AND role='operator'", (operator_id,))
        if not operator:
            abort(404)
        new_status = "inactive" if operator["status"] == "active" else "active"
        tx.execute("UPDATE users SET status=%s, updated_at=%s, updated_by=%s"
                   " WHERE id=%s",
                   (new_status, datetime.now(), g.user["id"], operator_id))
        write_audit(tx, "operator_toggle", "users", operator_id,
                    "Operator %s set to %s" % (operator["name"], new_status))
    flash("Operator %s." % new_status, "success")
    return redirect(url_for("operators.index"))


# ---------------------------------------------------------------------- detail
@bp.route("/<int:operator_id>")
@login_required
@admin_required
def detail(operator_id):
    conn = get_db().connect()
    try:
        operator = conn.query_one("SELECT * FROM users WHERE id=%s AND role='operator'",
                                  (operator_id,))
        if not operator:
            abort(404)
        attendance = conn.query(
            "SELECT * FROM attendance WHERE operator_id=%s ORDER BY att_date DESC"
            " LIMIT 60", (operator_id,))
        salaries = conn.query(
            "SELECT * FROM operator_salaries WHERE operator_id=%s"
            " ORDER BY salary_month DESC", (operator_id,))
        salary_payments = conn.query(
            "SELECT sp.*, s.salary_month FROM salary_payments sp"
            " JOIN operator_salaries s ON s.id=sp.salary_id"
            " WHERE s.operator_id=%s ORDER BY sp.id DESC LIMIT 40", (operator_id,))
        leaves = conn.query(
            "SELECT * FROM leaves WHERE operator_id=%s ORDER BY id DESC LIMIT 40",
            (operator_id,))
        activity = conn.query(
            "SELECT action, details, created_at FROM audit_logs WHERE user_id=%s"
            " ORDER BY id DESC LIMIT 40", (operator_id,))
        stats = conn.query_one(
            "SELECT COUNT(*) AS total_apps,"
            " COALESCE(SUM(CASE WHEN status='Completed' THEN 1 ELSE 0 END),0)"
            " AS completed, COALESCE(SUM(csc_paid),0) AS csc_collected"
            " FROM applications WHERE operator_id=%s AND is_deleted=0", (operator_id,))
    finally:
        conn.close()
    return render_template("operators/detail.html", operator=operator,
                           attendance=attendance, salaries=salaries,
                           salary_payments=salary_payments, leaves=leaves,
                           activity=activity, stats=stats,
                           today=date.today().isoformat(), modes=PAYMENT_MODES)


# ------------------------------------------------------------------ attendance
@bp.route("/<int:operator_id>/attendance", methods=["POST"])
@login_required
@admin_required
def attendance(operator_id):
    att_date = parse_date(request.form.get("att_date")) or date.today()
    check_in = clean_text(request.form.get("check_in"), 8) or None
    check_out = clean_text(request.form.get("check_out"), 8) or None
    notes = clean_text(request.form.get("notes"), 255)
    with get_db().transaction() as tx:
        operator = tx.query_one("SELECT name FROM users WHERE id=%s AND role='operator'",
                                (operator_id,))
        if not operator:
            abort(404)
        existing = tx.query_one("SELECT id FROM attendance WHERE operator_id=%s"
                                " AND att_date=%s", (operator_id, att_date))
        if existing:
            tx.execute("UPDATE attendance SET check_in=%s, check_out=%s, notes=%s,"
                       " updated_at=%s WHERE id=%s",
                       (check_in, check_out, notes, datetime.now(), existing["id"]))
        else:
            tx.execute("INSERT INTO attendance (operator_id, att_date, check_in,"
                       " check_out, notes, created_at, created_by)"
                       " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                       (operator_id, att_date, check_in, check_out, notes,
                        datetime.now(), g.user["id"]))
        write_audit(tx, "attendance_save", "attendance", operator_id,
                    "Attendance %s for %s" % (att_date, operator["name"]))
    flash("Attendance saved.", "success")
    return redirect(url_for("operators.detail", operator_id=operator_id))


@bp.route("/attendance/self", methods=["POST"])
@login_required
def attendance_self():
    user = current_user()
    if user["role"] != "operator":
        return redirect(url_for("operators.index"))
    att_date = date.today()
    check_in = clean_text(request.form.get("check_in"), 8) or None
    check_out = clean_text(request.form.get("check_out"), 8) or None
    with get_db().transaction() as tx:
        existing = tx.query_one("SELECT id FROM attendance WHERE operator_id=%s"
                                " AND att_date=%s", (user["id"], att_date))
        if existing:
            tx.execute("UPDATE attendance SET check_in=COALESCE(%s, check_in),"
                       " check_out=COALESCE(%s, check_out), updated_at=%s WHERE id=%s",
                       (check_in, check_out, datetime.now(), existing["id"]))
        else:
            tx.execute("INSERT INTO attendance (operator_id, att_date, check_in,"
                       " check_out, created_at, created_by) VALUES (%s,%s,%s,%s,%s,%s)",
                       (user["id"], att_date, check_in, check_out, datetime.now(),
                        user["id"]))
        write_audit(tx, "attendance_self", "attendance", user["id"],
                    "Self attendance for %s" % att_date)
    flash("Attendance recorded for today.", "success")
    return redirect(url_for("dashboard.home"))


# ---------------------------------------------------------------------- salary
@bp.route("/<int:operator_id>/salary", methods=["POST"])
@login_required
@admin_required
def salary(operator_id):
    month = clean_text(request.form.get("salary_month"), 7)
    amount = parse_decimal(request.form.get("salary_amount"))
    notes = clean_text(request.form.get("notes"), 255)
    if len(month) != 7:
        flash("Month must be in YYYY-MM format.", "danger")
        return redirect(url_for("operators.detail", operator_id=operator_id))
    with get_db().transaction() as tx:
        operator = tx.query_one("SELECT name FROM users WHERE id=%s AND role='operator'",
                                (operator_id,))
        if not operator:
            abort(404)
        existing = tx.query_one("SELECT id FROM operator_salaries WHERE operator_id=%s"
                                " AND salary_month=%s", (operator_id, month))
        if existing:
            tx.execute("UPDATE operator_salaries SET salary_amount=%s, notes=%s,"
                       " updated_at=%s, updated_by=%s WHERE id=%s",
                       (amount, notes, datetime.now(), g.user["id"], existing["id"]))
        else:
            tx.execute("INSERT INTO operator_salaries (operator_id, salary_month,"
                       " salary_amount, paid_amount, notes, created_at, created_by)"
                       " VALUES (%s,%s,%s,0,%s,%s,%s)",
                       (operator_id, month, amount, notes, datetime.now(),
                        g.user["id"]))
        write_audit(tx, "salary_save", "operator_salaries", operator_id,
                    "Salary %s for %s" % (amount, month))
    flash("Salary record saved.", "success")
    return redirect(url_for("operators.detail", operator_id=operator_id))


@bp.route("/salary/<int:salary_id>/pay", methods=["POST"])
@login_required
@admin_required
def salary_pay(salary_id):
    amount = parse_decimal(request.form.get("amount"))
    mode = request.form.get("payment_mode", "Cash")
    paid_on = parse_date(request.form.get("paid_on")) or date.today()
    if amount <= ZERO:
        flash("Payment amount must be greater than zero.", "danger")
        return redirect(url_for("operators.index"))
    if mode not in PAYMENT_MODES:
        mode = "Cash"
    with get_db().transaction() as tx:
        record = tx.query_one("SELECT * FROM operator_salaries WHERE id=%s", (salary_id,))
        if not record:
            abort(404)
        pending = D(record["salary_amount"]) - D(record["paid_amount"])
        if amount > pending:
            flash("Payment is more than the pending salary (%s)." % pending, "danger")
            return redirect(url_for("operators.detail",
                                    operator_id=record["operator_id"]))
        tx.execute("INSERT INTO salary_payments (salary_id, amount, paid_on,"
                   " payment_mode, notes, created_at, created_by)"
                   " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                   (salary_id, amount, paid_on, mode,
                    clean_text(request.form.get("notes"), 255), datetime.now(),
                    g.user["id"]))
        tx.execute("UPDATE operator_salaries SET paid_amount=%s, payment_date=%s,"
                   " updated_at=%s WHERE id=%s",
                   (D(record["paid_amount"]) + amount, paid_on, datetime.now(), salary_id))
        voucher = generate_voucher_no(tx, datetime.now())
        tx.execute("INSERT INTO ledger_entries (voucher_no, entry_date, account,"
                   " entry_type, category, amount, description, created_at, created_by)"
                   " VALUES (%s,%s,%s,'debit','Salary',%s,%s,%s,%s)",
                   (voucher, paid_on, mode, amount,
                    "Salary payment for " + record["salary_month"], datetime.now(),
                    g.user["id"]))
        write_audit(tx, "salary_payment", "operator_salaries", salary_id,
                    "Paid %s salary for %s" % (amount, record["salary_month"]))
        operator_id = record["operator_id"]
    flash("Salary payment recorded and posted to the Account Sheet.", "success")
    return redirect(url_for("operators.detail", operator_id=operator_id))


# ----------------------------------------------------------------------- leave
@bp.route("/leave/request", methods=["POST"])
@login_required
def leave_request():
    user = current_user()
    if user["role"] == "admin":
        operator_id = parse_int(request.form.get("operator_id"))
    else:
        operator_id = user["id"]
    from_date = parse_date(request.form.get("from_date"))
    to_date = parse_date(request.form.get("to_date")) or from_date
    reason = clean_text(request.form.get("reason"), 255)
    if not operator_id or not from_date or not reason:
        flash("Leave dates and reason are required.", "danger")
        return redirect(url_for("dashboard.home"))
    if to_date < from_date:
        flash("End date cannot be before the start date.", "danger")
        return redirect(url_for("dashboard.home"))
    with get_db().transaction() as tx:
        tx.execute("INSERT INTO leaves (operator_id, from_date, to_date, reason,"
                   " request_date, status, created_at)"
                   " VALUES (%s,%s,%s,%s,%s,'Pending',%s)",
                   (operator_id, from_date, to_date, reason, date.today(),
                    datetime.now()))
        write_audit(tx, "leave_request", "leaves", operator_id,
                    "Leave %s to %s requested" % (from_date, to_date))
    flash("Leave request submitted.", "success")
    if user["role"] == "admin":
        return redirect(url_for("operators.detail", operator_id=operator_id))
    return redirect(url_for("dashboard.home"))


@bp.route("/leave/<int:leave_id>/<decision>", methods=["POST"])
@login_required
@admin_required
def leave_decision(leave_id, decision):
    if decision not in ("approve", "reject"):
        abort(400)
    status = "Approved" if decision == "approve" else "Rejected"
    with get_db().transaction() as tx:
        leave = tx.query_one("SELECT operator_id, from_date, to_date FROM leaves"
                             " WHERE id=%s", (leave_id,))
        if not leave:
            abort(404)
        tx.execute("UPDATE leaves SET status=%s, admin_remarks=%s, approved_by=%s,"
                   " approved_at=%s WHERE id=%s",
                   (status, clean_text(request.form.get("remarks"), 255),
                    g.user["id"], datetime.now(), leave_id))
        write_audit(tx, "leave_" + decision, "leaves", leave_id,
                    "Leave %s to %s %s" % (leave["from_date"], leave["to_date"], status))
    flash("Leave %s." % status.lower(), "success")
    return redirect(url_for("operators.detail", operator_id=leave["operator_id"]))
