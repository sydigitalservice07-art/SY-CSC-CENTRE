"""
SY CSC CENTRE - Reports
=======================
21 report types, all driven by the same date-range machinery.

Every money figure comes from DECIMAL columns through Python's Decimal, and CSC
income is always separated from government charges:

    CSC income   = payments.csc_amount (+ payments.charge_amount when the
                   "Charge" column is classified as csc_income)
    Govt charge  = payments.govt_amount  (never counted as CSC income)
"""
import csv
import io
from datetime import date, datetime, timedelta

from flask import (Blueprint, Response, abort, current_app, render_template,
                   request, session, url_for)

from db import D, ZERO, get_db
from modules.i18n import translate
from modules.security import has_permission, login_required
from modules.utils import charge_classification, clean_text, csc_revenue_expression, parse_date

bp = Blueprint("reports", __name__, url_prefix="/reports")

M = "money"
T = "text"
DT = "date"
N = "int"

REPORT_LIST = [
    ("customer", "Customer Report"),
    ("application", "Application Report"),
    ("service_wise", "Service-wise Report"),
    ("operator_wise", "Operator-wise Report"),
    ("completed", "Completed Application Report"),
    ("pending", "Pending Application Report"),
    ("rejected", "Rejected Application Report"),
    ("cancelled", "Cancelled Application Report"),
    ("csc_income", "CSC Income Report"),
    ("govt_charge", "Government Charge Report"),
    ("payment", "Payment Report"),
    ("receipt", "Receipt Report"),
    ("credit_debit", "Credit / Debit Report"),
    ("expense", "Expense Report"),
    ("attendance", "Operator Attendance Report"),
    ("salary", "Operator Salary Report"),
    ("leave", "Operator Leave Report"),
    ("cash_summary", "Cash / Bank / UPI Summary"),
    ("pending_balance", "Pending Customer Balance"),
    ("receivables", "Receivables and Payables"),
    ("archive", "Application Archive Report"),
]


def _(key):
    return translate(key, session.get("lang", "en"))


# --------------------------------------------------------------------- helpers
def _app_rows(conn, start, end, statuses=None, extra_where="", extra_params=()):
    sql = ("SELECT a.app_code, a.apply_date, a.status, a.total, a.amount_paid,"
           " a.amount_pending, a.charge, a.govt_charge, a.csc_charge,"
           " c.customer_code, c.first_name, c.surname, c.mobile,"
           " s.name AS service_name, u.name AS operator_name"
           " FROM applications a JOIN customers c ON c.id=a.customer_id"
           " JOIN services s ON s.id=a.service_id"
           " LEFT JOIN users u ON u.id=a.operator_id"
           " WHERE a.is_deleted=0 AND a.apply_date >= %s AND a.apply_date <= %s")
    params = [start, end]
    if statuses:
        marks = ",".join(["%s"] * len(statuses))
        sql += " AND a.status IN (" + marks + ")"
        params.extend(statuses)
    if extra_where:
        sql += " AND " + extra_where
        params.extend(extra_params)
    sql += " ORDER BY a.apply_date DESC, a.id DESC"
    return conn.query(sql, tuple(params))


def _app_columns():
    return [("app_code", "Application ID", T), ("apply_date", "Apply Date", DT),
            ("customer_code", "Customer ID", T), ("name", "Customer", T),
            ("mobile", "Mobile", T), ("service_name", "Service", T),
            ("operator_name", "Operator", T), ("status", "Status", T),
            ("total", "Total", M), ("amount_paid", "Paid", M),
            ("amount_pending", "Pending", M)]


def _decorate(rows):
    out = []
    for row in rows:
        row = dict(row)
        row["name"] = "%s %s" % (row.get("first_name") or "", row.get("surname") or "")
        out.append(row)
    return out


def _dt_bounds(start, end):
    """Datetime columns (payment_date, created_at, archived_at) need full-day
    bounds - a plain date would exclude everything after midnight."""
    return (datetime.combine(start, datetime.min.time()),
            datetime.combine(end, datetime.max.time()))


def _sum(rows, key):
    total = ZERO
    for row in rows:
        total += D(row.get(key))
    return total


# ---------------------------------------------------------------------- builder
def build_report(conn, report_key, start, end):
    data = {"columns": [], "rows": [], "totals": [], "note": ""}
    classification = charge_classification(conn)

    # ------------------------------------------------------------ customers
    if report_key == "customer":
        rows = conn.query(
            "SELECT c.customer_code, c.first_name, c.surname, c.mobile, c.village,"
            " c.taluka, c.district, c.status, c.created_at,"
            " (SELECT COUNT(*) FROM applications a WHERE a.customer_id=c.id"
            "  AND a.is_deleted=0) AS app_count"
            " FROM customers c WHERE c.created_at >= %s AND c.created_at <= %s"
            " ORDER BY c.id DESC", _dt_bounds(start, end))
        data["columns"] = [("customer_code", "Customer ID", T),
                           ("name", "Customer", T), ("mobile", "Mobile", T),
                           ("village", "Village", T), ("taluka", "Taluka", T),
                           ("district", "District", T),
                           ("app_count", "Applications", N),
                           ("status", "Status", T), ("created_at", "Created", DT)]
        data["rows"] = _decorate(rows)
        data["totals"] = [("Customers", len(rows))]

    # --------------------------------------------------------- applications
    elif report_key == "application":
        rows = _decorate(_app_rows(conn, start, end))
        data["columns"] = _app_columns()
        data["rows"] = rows
        data["totals"] = [("Applications", len(rows)),
                          ("Total", _sum(rows, "total")),
                          ("Paid", _sum(rows, "amount_paid")),
                          ("Pending", _sum(rows, "amount_pending"))]

    # -------------------------------------------------------- service wise
    elif report_key == "service_wise":
        rows = conn.query(
            "SELECT s.name AS service_name, COUNT(a.id) AS app_count,"
            " COALESCE(SUM(a.total),0) AS total, COALESCE(SUM(a.csc_charge),0) AS csc,"
            " COALESCE(SUM(a.govt_charge),0) AS govt,"
            " COALESCE(SUM(a.amount_paid),0) AS paid,"
            " COALESCE(SUM(a.amount_pending),0) AS pending"
            " FROM services s LEFT JOIN applications a ON a.service_id=s.id"
            " AND a.is_deleted=0 AND a.apply_date >= %s AND a.apply_date <= %s"
            " GROUP BY s.id, s.name ORDER BY app_count DESC, s.name",
            (start, end))
        data["columns"] = [("service_name", "Service", T), ("app_count", "Applications", N),
                           ("total", "Total", M), ("csc", "CSC Charge", M),
                           ("govt", "Government Charge", M), ("paid", "Paid", M),
                           ("pending", "Pending", M)]
        data["rows"] = rows
        data["totals"] = [("Total", _sum(rows, "total")),
                          ("CSC Charge", _sum(rows, "csc")),
                          ("Government Charge", _sum(rows, "govt"))]

    # ------------------------------------------------------- operator wise
    elif report_key == "operator_wise":
        rows = conn.query(
            "SELECT u.name AS operator_name, COUNT(a.id) AS app_count,"
            " COALESCE(SUM(a.total),0) AS total, COALESCE(SUM(a.csc_charge),0) AS csc,"
            " COALESCE(SUM(a.amount_paid),0) AS paid,"
            " COALESCE(SUM(a.amount_pending),0) AS pending"
            " FROM users u LEFT JOIN applications a ON a.operator_id=u.id"
            " AND a.is_deleted=0 AND a.apply_date >= %s AND a.apply_date <= %s"
            " WHERE u.role='operator' GROUP BY u.id, u.name ORDER BY app_count DESC",
            (start, end))
        data["columns"] = [("operator_name", "Operator", T),
                           ("app_count", "Applications", N), ("total", "Total", M),
                           ("csc", "CSC Charge", M), ("paid", "Collected", M),
                           ("pending", "Pending", M)]
        data["rows"] = rows
        data["totals"] = [("Total", _sum(rows, "total")),
                          ("CSC Charge", _sum(rows, "csc"))]

    # ------------------------------------------- completed / pending / etc.
    elif report_key in ("completed", "pending", "rejected", "cancelled"):
        statuses = {
            "completed": ["Completed"],
            "pending": ["Draft", "Pending", "In Progress", "Submitted"],
            "rejected": ["Rejected"],
            "cancelled": ["Cancelled"],
        }[report_key]
        rows = _decorate(_app_rows(conn, start, end, statuses))
        data["columns"] = _app_columns()
        data["rows"] = rows
        data["totals"] = [("Applications", len(rows)),
                          ("Total", _sum(rows, "total")),
                          ("Paid", _sum(rows, "amount_paid")),
                          ("Pending", _sum(rows, "amount_pending"))]

    # ----------------------------------------------------------- CSC income
    elif report_key == "csc_income":
        expr = csc_revenue_expression(conn, "p")
        rows = conn.query(
            "SELECT p.payment_code, p.payment_date, a.app_code, c.customer_code,"
            " c.first_name, c.surname, s.name AS service_name, p.payment_mode,"
            " p.csc_amount, p.govt_amount, p.charge_amount, p.amount,"
            " u.name AS collected_by FROM payments p"
            " JOIN applications a ON a.id=p.application_id"
            " JOIN customers c ON c.id=p.customer_id"
            " JOIN services s ON s.id=a.service_id"
            " LEFT JOIN users u ON u.id=p.created_by"
            " WHERE p.status='active' AND p.payment_date >= %s AND p.payment_date <= %s"
            " ORDER BY p.payment_date DESC", _dt_bounds(start, end))
        for row in rows:
            row["name"] = "%s %s" % (row["first_name"], row["surname"])
            row["csc_revenue"] = D(row["csc_amount"]) + (
                D(row["charge_amount"]) if classification == "csc_income" else ZERO)
        data["columns"] = [("payment_code", "Payment ID", T),
                           ("payment_date", "Date", DT), ("app_code", "Application", T),
                           ("customer_code", "Customer ID", T), ("name", "Customer", T),
                           ("service_name", "Service", T),
                           ("payment_mode", "Mode", T), ("csc_revenue", "CSC Income", M),
                           ("govt_amount", "Government Charge", M),
                           ("amount", "Total Received", M),
                           ("collected_by", "Collected By", T)]
        data["rows"] = rows
        data["totals"] = [("CSC Income", _sum(rows, "csc_revenue")),
                          ("Government Charge (not income)", _sum(rows, "govt_amount")),
                          ("Total Received", _sum(rows, "amount"))]
        data["note"] = ("CSC income counts only CSC charge actually collected"
                        " (+ Charge column when classified as %s). Government"
                        " charges are excluded." % classification)

    # ------------------------------------------------------ government charge
    elif report_key == "govt_charge":
        rows = conn.query(
            "SELECT a.app_code, a.apply_date, c.customer_code, c.first_name,"
            " c.surname, s.name AS service_name, a.govt_charge, a.govt_paid,"
            " (a.govt_charge - a.govt_paid) AS govt_pending"
            " FROM applications a JOIN customers c ON c.id=a.customer_id"
            " JOIN services s ON s.id=a.service_id"
            " WHERE a.is_deleted=0 AND a.apply_date >= %s AND a.apply_date <= %s"
            " AND a.govt_charge > 0 ORDER BY a.apply_date DESC", (start, end))
        paid_out = D(conn.scalar(
            "SELECT COALESCE(SUM(amount),0) FROM ledger_entries"
            " WHERE entry_type='debit' AND category='Government Charge Paid'"
            " AND entry_date >= %s AND entry_date <= %s", (start, end), default=0))
        data["columns"] = [("app_code", "Application ID", T),
                           ("apply_date", "Apply Date", DT),
                           ("customer_code", "Customer ID", T), ("name", "Customer", T),
                           ("service_name", "Service", T),
                           ("govt_charge", "Government Charge", M),
                           ("govt_paid", "Collected", M),
                           ("govt_pending", "Not Collected", M)]
        data["rows"] = _decorate(rows)
        data["totals"] = [("Government Charge Billed", _sum(rows, "govt_charge")),
                          ("Collected", _sum(rows, "govt_paid")),
                          ("Paid to Government in range", paid_out)]
        data["note"] = "Government charge is a liability - it is never CSC income."

    # --------------------------------------------------------------- payment
    elif report_key in ("payment", "receipt"):
        rows = conn.query(
            "SELECT p.payment_code, p.payment_date, a.app_code, c.customer_code,"
            " c.first_name, c.surname, c.mobile, p.payment_mode, p.amount,"
            " p.charge_amount, p.govt_amount, p.csc_amount, p.is_refund, p.status,"
            " p.reference_no, u.name AS created_by_name FROM payments p"
            " JOIN applications a ON a.id=p.application_id"
            " JOIN customers c ON c.id=p.customer_id"
            " LEFT JOIN users u ON u.id=p.created_by"
            " WHERE p.payment_date >= %s AND p.payment_date <= %s"
            + (" AND p.status='active' AND p.is_refund=0" if report_key == "receipt" else "")
            + " ORDER BY p.payment_date DESC", _dt_bounds(start, end))
        for row in rows:
            row["name"] = "%s %s" % (row["first_name"], row["surname"])
        data["columns"] = [("payment_code", "Receipt / Payment ID", T),
                           ("payment_date", "Date", DT), ("app_code", "Application", T),
                           ("customer_code", "Customer ID", T), ("name", "Customer", T),
                           ("payment_mode", "Mode", T), ("amount", "Amount", M),
                           ("csc_amount", "CSC Charge", M),
                           ("govt_amount", "Government Charge", M),
                           ("reference_no", "Reference", T),
                           ("created_by_name", "Received By", T)]
        data["rows"] = rows
        active = [r for r in rows if r["status"] == "active"]
        data["totals"] = [("Payments", len(rows)), ("Collected", _sum(active, "amount")),
                          ("CSC Charge", _sum(active, "csc_amount")),
                          ("Government Charge", _sum(active, "govt_amount"))]

    # --------------------------------------------------------- credit/debit
    elif report_key == "credit_debit":
        rows = conn.query(
            "SELECT category,"
            " COALESCE(SUM(CASE WHEN entry_type='credit' THEN amount ELSE 0 END),0)"
            " AS credits,"
            " COALESCE(SUM(CASE WHEN entry_type='debit' THEN amount ELSE 0 END),0)"
            " AS debits FROM ledger_entries"
            " WHERE entry_date >= %s AND entry_date <= %s AND is_reversed=0"
            " GROUP BY category ORDER BY category", (start, end))
        for row in rows:
            row["net"] = D(row["credits"]) - D(row["debits"])
        data["columns"] = [("category", "Category", T), ("credits", "Credit (In)", M),
                           ("debits", "Debit (Out)", M), ("net", "Net", M)]
        data["rows"] = rows
        data["totals"] = [("Total Credit", _sum(rows, "credits")),
                          ("Total Debit", _sum(rows, "debits")),
                          ("Net", _sum(rows, "net"))]

    # --------------------------------------------------------------- expense
    elif report_key == "expense":
        rows = conn.query(
            "SELECT entry_date, voucher_no, account, category, description, amount"
            " FROM ledger_entries WHERE entry_type='debit' AND is_reversed=0"
            " AND entry_date >= %s AND entry_date <= %s"
            " AND category NOT IN ('Transfer','Refund')"
            " ORDER BY entry_date DESC, id DESC", (start, end))
        data["columns"] = [("entry_date", "Date", DT), ("voucher_no", "Voucher", T),
                           ("account", "Account", T), ("category", "Category", T),
                           ("description", "Description", T), ("amount", "Amount", M)]
        data["rows"] = rows
        data["totals"] = [("Entries", len(rows)), ("Total Expense", _sum(rows, "amount"))]

    # ------------------------------------------------------------ attendance
    elif report_key == "attendance":
        rows = conn.query(
            "SELECT u.name AS operator_name, t.att_date, t.check_in, t.check_out,"
            " t.notes FROM attendance t JOIN users u ON u.id=t.operator_id"
            " WHERE t.att_date >= %s AND t.att_date <= %s"
            " ORDER BY t.att_date DESC, u.name", (start, end))
        data["columns"] = [("operator_name", "Operator", T), ("att_date", "Date", DT),
                           ("check_in", "Check-in", T), ("check_out", "Check-out", T),
                           ("notes", "Notes", T)]
        data["rows"] = rows
        data["totals"] = [("Attendance records", len(rows))]

    # ---------------------------------------------------------------- salary
    elif report_key == "salary":
        rows = conn.query(
            "SELECT u.name AS operator_name, s.salary_month, s.salary_amount,"
            " s.paid_amount, (s.salary_amount - s.paid_amount) AS pending_amount,"
            " s.payment_date FROM operator_salaries s JOIN users u ON u.id=s.operator_id"
            " WHERE s.salary_month >= %s AND s.salary_month <= %s"
            " ORDER BY s.salary_month DESC, u.name",
            (start.strftime("%Y-%m"), end.strftime("%Y-%m")))
        data["columns"] = [("operator_name", "Operator", T), ("salary_month", "Month", T),
                           ("salary_amount", "Salary", M), ("paid_amount", "Paid", M),
                           ("pending_amount", "Pending", M),
                           ("payment_date", "Payment Date", DT)]
        data["rows"] = rows
        data["totals"] = [("Salary", _sum(rows, "salary_amount")),
                          ("Paid", _sum(rows, "paid_amount")),
                          ("Pending", _sum(rows, "pending_amount"))]

    # ----------------------------------------------------------------- leave
    elif report_key == "leave":
        rows = conn.query(
            "SELECT u.name AS operator_name, l.from_date, l.to_date, l.reason,"
            " l.request_date, l.status, l.admin_remarks FROM leaves l"
            " JOIN users u ON u.id=l.operator_id"
            " WHERE l.from_date >= %s AND l.from_date <= %s"
            " ORDER BY l.from_date DESC", (start, end))
        data["columns"] = [("operator_name", "Operator", T), ("from_date", "From", DT),
                           ("to_date", "To", DT), ("reason", "Reason", T),
                           ("request_date", "Requested", DT), ("status", "Status", T),
                           ("admin_remarks", "Remarks", T)]
        data["rows"] = rows
        data["totals"] = [("Leave requests", len(rows))]

    # ---------------------------------------------------------- cash summary
    elif report_key == "cash_summary":
        rows = []
        for account in ("Cash", "Bank", "UPI"):
            opening = D(conn.scalar(
                "SELECT opening_balance FROM account_balances WHERE account=%s",
                (account,), default=0))
            before_c = D(conn.scalar(
                "SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account=%s"
                " AND entry_type='credit' AND is_reversed=0 AND entry_date < %s",
                (account, start), default=0))
            before_d = D(conn.scalar(
                "SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account=%s"
                " AND entry_type='debit' AND is_reversed=0 AND entry_date < %s",
                (account, start), default=0))
            in_range_c = D(conn.scalar(
                "SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account=%s"
                " AND entry_type='credit' AND is_reversed=0 AND entry_date >= %s"
                " AND entry_date <= %s", (account, start, end), default=0))
            in_range_d = D(conn.scalar(
                "SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE account=%s"
                " AND entry_type='debit' AND is_reversed=0 AND entry_date >= %s"
                " AND entry_date <= %s", (account, start, end), default=0))
            opening_range = opening + before_c - before_d
            rows.append({"account": account, "opening": opening_range,
                         "credits": in_range_c, "debits": in_range_d,
                         "closing": opening_range + in_range_c - in_range_d})
        data["columns"] = [("account", "Account", T), ("opening", "Opening", M),
                           ("credits", "Credit (In)", M), ("debits", "Debit (Out)", M),
                           ("closing", "Closing", M)]
        data["rows"] = rows
        data["totals"] = [("Closing Cash+Bank+UPI", _sum(rows, "closing"))]

    # -------------------------------------------------------- pending balance
    elif report_key == "pending_balance":
        rows = conn.query(
            "SELECT c.customer_code, c.first_name, c.surname, c.mobile, c.village,"
            " COUNT(a.id) AS app_count, COALESCE(SUM(a.total),0) AS billed,"
            " COALESCE(SUM(a.amount_paid),0) AS paid,"
            " COALESCE(SUM(a.amount_pending),0) AS pending FROM applications a"
            " JOIN customers c ON c.id=a.customer_id WHERE a.is_deleted=0"
            " AND a.amount_pending > 0 AND a.apply_date >= %s AND a.apply_date <= %s"
            " GROUP BY c.id, c.customer_code, c.first_name, c.surname, c.mobile, c.village"
            " HAVING COALESCE(SUM(a.amount_pending),0) > 0 ORDER BY pending DESC",
            (start, end))
        data["columns"] = [("customer_code", "Customer ID", T), ("name", "Customer", T),
                           ("mobile", "Mobile", T), ("village", "Village", T),
                           ("app_count", "Applications", N), ("billed", "Billed", M),
                           ("paid", "Paid", M), ("pending", "Pending", M)]
        data["rows"] = _decorate(rows)
        data["totals"] = [("Customers", len(rows)), ("Billed", _sum(rows, "billed")),
                          ("Paid", _sum(rows, "paid")),
                          ("Total Pending", _sum(rows, "pending"))]

    # --------------------------------------------------------- receivables
    elif report_key == "receivables":
        rows = _decorate(_app_rows(conn, start, end, extra_where="a.amount_pending > 0"))
        for row in rows:
            row["days_old"] = (date.today() - row["apply_date"]).days \
                if row["apply_date"] else 0
        data["columns"] = [("app_code", "Application ID", T),
                           ("apply_date", "Apply Date", DT), ("name", "Customer", T),
                           ("mobile", "Mobile", T), ("service_name", "Service", T),
                           ("total", "Total", M), ("amount_paid", "Paid", M),
                           ("amount_pending", "Receivable", M),
                           ("days_old", "Days Old", N)]
        data["rows"] = rows
        payable = D(conn.scalar(
            "SELECT COALESCE(SUM(govt_amount),0) FROM payments WHERE status='active'",
            default=0))
        data["totals"] = [("Receivable from customers", _sum(rows, "amount_pending")),
                          ("Government charge collected (payable)", payable)]

    # ---------------------------------------------------------------- archive
    elif report_key == "archive":
        rows = conn.query(
            "SELECT a.app_code, c.customer_code, c.first_name, c.surname,"
            " s.name AS service_name, a.apply_date, a.status, a.csc_charge,"
            " a.govt_charge, a.amount_paid, a.amount_pending, aa.archived_at,"
            " au.name AS archived_by_name, aa.reason FROM applications a"
            " JOIN customers c ON c.id=a.customer_id JOIN services s ON s.id=a.service_id"
            " JOIN application_archive aa ON aa.application_id=a.id"
            " LEFT JOIN users au ON au.id=aa.archived_by"
            " WHERE aa.archived_at >= %s AND aa.archived_at <= %s"
            " ORDER BY aa.archived_at DESC", _dt_bounds(start, end))
        data["columns"] = [("app_code", "Application ID", T),
                           ("customer_code", "Customer ID", T), ("name", "Customer", T),
                           ("service_name", "Service", T),
                           ("apply_date", "Apply Date", DT), ("status", "Status", T),
                           ("csc_charge", "CSC Charge", M),
                           ("govt_charge", "Government Charge", M),
                           ("amount_paid", "Paid", M),
                           ("amount_pending", "Pending", M),
                           ("archived_at", "Archive Date", DT),
                           ("archived_by_name", "Archived By", T),
                           ("reason", "Reason", T)]
        data["rows"] = _decorate(rows)
        data["totals"] = [("Archived applications", len(rows))]

    else:
        abort(404)
    return data


# ------------------------------------------------------------------ date range
def resolve_range(period, date_from, date_to):
    today = date.today()
    if period == "today":
        return today, today
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return today.replace(day=1), today
    if period == "year":
        return today.replace(month=1, day=1), today
    start = parse_date(date_from) or today.replace(day=1)
    end = parse_date(date_to) or today
    if end < start:
        start, end = end, start
    return start, end


# ------------------------------------------------------------------------ page
def _report_allowed():
    from flask import abort as _abort
    from modules.security import current_user
    user = current_user()
    if not user:
        _abort(403)
    if user["role"] != "admin" and not has_permission("can_view_reports"):
        _abort(403)
    return True


@bp.route("/")
@login_required
def index():
    _report_allowed()
    report_key = request.args.get("type", "application")
    if report_key not in dict(REPORT_LIST):
        report_key = "application"
    period = request.args.get("period", "month")
    start, end = resolve_range(period, request.args.get("from"), request.args.get("to"))

    conn = get_db().connect()
    try:
        data = build_report(conn, report_key, start, end)
    finally:
        conn.close()

    title = dict(REPORT_LIST)[report_key]
    return render_template("reports/index.html", report_list=REPORT_LIST,
                           report_key=report_key, title=title, data=data,
                           start=start, end=end, period=period,
                           date_from=request.args.get("from", ""),
                           date_to=request.args.get("to", ""))


@bp.route("/export")
@login_required
def export():
    _report_allowed()
    report_key = request.args.get("type", "application")
    if report_key not in dict(REPORT_LIST):
        abort(404)
    period = request.args.get("period", "month")
    start, end = resolve_range(period, request.args.get("from"), request.args.get("to"))

    conn = get_db().connect()
    try:
        data = build_report(conn, report_key, start, end)
    finally:
        conn.close()

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([col[1] for col in data["columns"]])
    for row in data["rows"]:
        line = []
        for col in data["columns"]:
            value = row.get(col[0])
            if isinstance(value, datetime):
                value = value.strftime("%Y-%m-%d %H:%M")
            elif isinstance(value, date):
                value = value.strftime("%Y-%m-%d")
            line.append("" if value is None else value)
        writer.writerow(line)
    writer.writerow([])
    for label, value in data["totals"]:
        writer.writerow([label, value])

    filename = "sycsc_%s_%s_%s.csv" % (report_key, start.strftime("%Y%m%d"),
                                       end.strftime("%Y%m%d"))
    return Response("\ufeff" + buffer.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=" + filename})
