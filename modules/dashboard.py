"""
SY CSC CENTRE - dashboards
==========================
Admin dashboard shows whole-shop figures, operator dashboard only what the
operator is allowed to see.

Money rule (used everywhere):
    CSC revenue  = SUM(payments.csc_amount) (+ payments.charge_amount when the
                   PDF "Charge" column is classified as csc_income)
    Government charge is NEVER counted as CSC income, and unpaid amounts are
    never counted as collected revenue.
"""
from datetime import date, datetime, timedelta

from flask import Blueprint, g, render_template, session

from db import D, get_db
from modules.security import current_user, has_permission, login_required
from modules.utils import csc_revenue_expression

bp = Blueprint("dashboard", __name__)


def _day_bounds(day=None):
    day = day or date.today()
    start = datetime(day.year, day.month, day.day)
    return start, start + timedelta(days=1)


def _revenue(conn, start, end, operator_id=None):
    expr = csc_revenue_expression(conn, "p")
    sql = ("SELECT SUM(" + expr + ") AS total FROM payments p"
           " WHERE p.status='active' AND p.payment_date >= %s AND p.payment_date < %s")
    params = [start, end]
    if operator_id:
        sql += " AND p.created_by=%s"
        params.append(operator_id)
    return D(conn.scalar(sql, tuple(params), default=0))


@bp.route("/")
@login_required
def home():
    user = current_user()
    conn = get_db().connect()
    try:
        if user["role"] == "admin":
            data = _admin_context(conn)
            return render_template("dashboard_admin.html", **data)
        data = _operator_context(conn, user)
        return render_template("dashboard_operator.html", **data)
    finally:
        conn.close()


# ------------------------------------------------------------------- admin
def _admin_context(conn):
    start, end = _day_bounds()
    data = {}
    data["total_customers"] = conn.scalar(
        "SELECT COUNT(*) FROM customers WHERE status='active'", default=0)
    data["total_operators"] = conn.scalar(
        "SELECT COUNT(*) FROM users WHERE role='operator' AND status='active'", default=0)
    data["today_income"] = _revenue(conn, start, end)
    data["customers_today"] = conn.scalar(
        "SELECT COUNT(*) FROM customers WHERE created_at >= %s AND created_at < %s",
        (start, end), default=0)
    data["apps_today"] = conn.scalar(
        "SELECT COUNT(*) FROM applications WHERE is_deleted=0 AND created_at >= %s"
        " AND created_at < %s", (start, end), default=0)
    data["recent_customers"] = conn.query(
        "SELECT id, customer_code, first_name, surname, mobile, village, created_at"
        " FROM customers WHERE status='active' ORDER BY id DESC LIMIT 6")
    data["recent_apps"] = conn.query(
        "SELECT a.id, a.app_code, a.status, a.total, a.amount_pending, a.apply_date,"
        " s.name AS service_name, c.first_name, c.surname"
        " FROM applications a JOIN customers c ON c.id=a.customer_id"
        " JOIN services s ON s.id=a.service_id"
        " WHERE a.is_deleted=0 ORDER BY a.id DESC LIMIT 6")
    data["active_services"] = conn.scalar(
        "SELECT COUNT(*) FROM services WHERE is_active=1", default=0)
    data["inactive_services"] = conn.scalar(
        "SELECT COUNT(*) FROM services WHERE is_active=0", default=0)
    data["recent_messages"] = conn.query(
        "SELECT m.id, m.subject, m.created_at, m.read_at, u.name AS sender_name"
        " FROM messages m JOIN users u ON u.id=m.sender_id"
        " ORDER BY m.id DESC LIMIT 6")
    data["unread"] = conn.scalar(
        "SELECT COUNT(*) FROM messages WHERE recipient_id=%s AND read_at IS NULL",
        (g.user["id"],), default=0)
    data["activities"] = conn.query(
        "SELECT action, entity, entity_id, username, details, created_at"
        " FROM audit_logs ORDER BY id DESC LIMIT 8")
    data["pending_apps"] = conn.scalar(
        "SELECT COUNT(*) FROM applications WHERE is_deleted=0 AND is_archived=0"
        " AND status IN ('Draft','Pending','In Progress','Submitted')", default=0)
    data["total_pending_amount"] = D(conn.scalar(
        "SELECT SUM(amount_pending) FROM applications WHERE is_deleted=0"
        " AND status NOT IN ('Cancelled')", default=0))
    return data


# ---------------------------------------------------------------- operator
def _operator_context(conn, user):
    start, end = _day_bounds()
    uid = user["id"]
    data = {}
    data["my_customers"] = conn.query(
        "SELECT id, customer_code, first_name, surname, mobile, village, created_at"
        " FROM customers WHERE created_by=%s AND status='active'"
        " ORDER BY id DESC LIMIT 6", (uid,))
    data["apps_today"] = conn.scalar(
        "SELECT COUNT(*) FROM applications WHERE is_deleted=0 AND operator_id=%s"
        " AND created_at >= %s AND created_at < %s", (uid, start, end), default=0)
    data["pending_apps"] = conn.scalar(
        "SELECT COUNT(*) FROM applications WHERE is_deleted=0 AND is_archived=0"
        " AND operator_id=%s AND status IN ('Draft','Pending','In Progress','Submitted')",
        (uid,), default=0)
    data["completed_apps"] = conn.scalar(
        "SELECT COUNT(*) FROM applications WHERE is_deleted=0 AND operator_id=%s"
        " AND status='Completed'", (uid,), default=0)
    data["today_income"] = _revenue(conn, start, end, operator_id=uid)
    data["unread"] = conn.scalar(
        "SELECT COUNT(*) FROM messages WHERE recipient_id=%s AND read_at IS NULL",
        (uid,), default=0)
    data["my_recent_apps"] = conn.query(
        "SELECT a.id, a.app_code, a.status, a.total, a.amount_pending, a.apply_date,"
        " s.name AS service_name, c.first_name, c.surname"
        " FROM applications a JOIN customers c ON c.id=a.customer_id"
        " JOIN services s ON s.id=a.service_id"
        " WHERE a.is_deleted=0 AND a.operator_id=%s ORDER BY a.id DESC LIMIT 6", (uid,))
    data["attendance_today"] = conn.query_one(
        "SELECT att_date, check_in, check_out, notes FROM attendance"
        " WHERE operator_id=%s AND att_date=%s", (uid, date.today()))
    data["my_leaves"] = conn.query(
        "SELECT from_date, to_date, reason, status FROM leaves WHERE operator_id=%s"
        " ORDER BY id DESC LIMIT 5", (uid,))
    data["can_reports"] = has_permission("can_view_reports")
    return data
