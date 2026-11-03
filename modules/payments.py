"""
SY CSC CENTRE - Payments and receipts
=====================================
Recording a payment is one database transaction:
    payment row  ->  application paid/pending update  ->  ledger entry -> audit

Payment allocation: the amount is split across the three PDF charge columns
(Charge / Government Charge / CSC Charge) in proportion to what is still
pending, so "CSC revenue collected" is always exact even for partial payments.
Government charge money is tracked separately and is never counted as CSC
income.  Refunds are posted as a negative payment plus a debit ledger entry, so
the history stays complete and auditable.
"""
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)

from db import D, ZERO, get_db
from modules.i18n import translate
from modules.security import (admin_required, current_user, login_required,
                              permission_required, write_audit)
from modules.utils import (PAYMENT_MODES, allocate_payment, clean_text,
                           generate_payment_code, generate_voucher_no,
                           parse_date, parse_decimal, parse_int)

bp = Blueprint("payments", __name__, url_prefix="/payments")


def _(key):
    return translate(key, session.get("lang", "en"))


def settings_map(conn):
    rows = conn.query("SELECT setting_key, setting_value FROM settings")
    return {r["setting_key"]: r["setting_value"] for r in rows}


# ---------------------------------------------------------------------------
def record_payment(conn, application, amount, mode, reference="", notes="",
                   when=None, created_by=None, is_refund=False, split=None):
    """
    Core posting routine.  MUST be called inside a transaction.
    `application` is the application row (dict) before the payment.
    Returns (payment_id, payment_code).
    """
    amount = D(amount)
    if is_refund:
        amount = -abs(amount)          # a refund always takes money back out
    elif amount <= ZERO:
        raise ValueError("Payment amount must be greater than zero.")
    pending = D(application["amount_pending"])
    if not is_refund and amount > pending:
        raise ValueError("Payment is more than the pending amount (%s)." % pending)
    if mode not in PAYMENT_MODES:
        raise ValueError("Payment mode must be Cash, Bank or UPI.")

    when = when or datetime.now()
    if split is not None:
        charge_part, govt_part, csc_part = [D(x) for x in split]
    else:
        charge_part, govt_part, csc_part = allocate_payment(application, amount)
    customer = conn.query_one("SELECT mobile FROM customers WHERE id=%s",
                              (application["customer_id"],))
    mobile = customer["mobile"] if customer else ""
    code = generate_payment_code(conn, mobile, when)

    conn.execute(
        "INSERT INTO payments (payment_code, customer_id, application_id,"
        " payment_date, amount, charge_amount, govt_amount, csc_amount,"
        " payment_mode, reference_no, notes, is_refund, status, created_at,"
        " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'active',%s,%s)",
        (code, application["customer_id"], application["id"], when, amount,
         charge_part, govt_part, csc_part, mode, reference, notes,
         1 if is_refund else 0, when, created_by))
    payment_id = conn.scalar("SELECT id FROM payments WHERE payment_code=%s", (code,))

    new_charge_paid = D(application["charge_paid"]) + charge_part
    new_govt_paid = D(application["govt_paid"]) + govt_part
    new_csc_paid = D(application["csc_paid"]) + csc_part
    new_paid = D(application["amount_paid"]) + amount
    new_pending = D(application["total"]) - new_paid
    conn.execute(
        "UPDATE applications SET charge_paid=%s, govt_paid=%s, csc_paid=%s,"
        " amount_paid=%s, amount_pending=%s, updated_at=%s WHERE id=%s",
        (new_charge_paid, new_govt_paid, new_csc_paid, new_paid, new_pending,
         datetime.now(), application["id"]))

    voucher = generate_voucher_no(conn, when)
    conn.execute(
        "INSERT INTO ledger_entries (voucher_no, entry_date, account, entry_type,"
        " category, amount, description, reference_no, payment_id, application_id,"
        " customer_id, created_at, created_by)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (voucher, when.date(), mode, "debit" if is_refund else "credit",
         "Refund" if is_refund else "Application Payment", abs(amount),
         ("Refund for " if is_refund else "Payment for ") + code
         + " (" + (notes or "application payment") + ")",
         reference, payment_id, application["id"], application["customer_id"],
         when, created_by))
    write_audit(conn, "payment_refund" if is_refund else "payment_create",
                "payments", payment_id,
                "%s %s for application %s" % ("Refund" if is_refund else "Payment",
                                              amount, application["app_code"]))
    return payment_id, code


# ------------------------------------------------------------------ new payment
@bp.route("/application/<int:application_id>/new", methods=["POST"])
@login_required
def create(application_id):
    amount = parse_decimal(request.form.get("amount"))
    mode = request.form.get("payment_mode", "Cash")
    reference = clean_text(request.form.get("reference_no"), 80)
    notes = clean_text(request.form.get("notes"), 255)
    pay_date = parse_date(request.form.get("payment_date"))

    conn = get_db().connect()
    try:
        application = conn.query_one(
            "SELECT a.*, c.mobile, c.first_name, c.surname FROM applications a"
            " JOIN customers c ON c.id=a.customer_id WHERE a.id=%s", (application_id,))
        if not application:
            abort(404)
        if int(application["is_deleted"]) or int(application["is_archived"]):
            flash("Restore this application from the archive before taking a payment.",
                  "danger")
            return redirect(url_for("applications.detail", application_id=application_id))
        if application["status"] == "Cancelled":
            flash("A cancelled application cannot receive a payment.", "danger")
            return redirect(url_for("applications.detail", application_id=application_id))
        if reference:
            duplicate = conn.query_one(
                "SELECT payment_code FROM payments WHERE application_id=%s"
                " AND reference_no=%s AND status='active'",
                (application_id, reference))
            if duplicate:
                flash("A payment with this reference number already exists (%s)."
                      % duplicate["payment_code"], "danger")
                return redirect(url_for("applications.detail",
                                        application_id=application_id))
    finally:
        conn.close()

    try:
        when = datetime.combine(pay_date, datetime.now().time()) if pay_date else None
        with get_db().transaction() as tx:
            app_row = tx.query_one("SELECT * FROM applications WHERE id=%s",
                                   (application_id,))
            payment_id, code = record_payment(
                tx, app_row, amount, mode, reference, notes, when=when,
                created_by=g.user["id"])
        flash("Payment recorded: %s" % code, "success")
        return redirect(url_for("payments.receipt", payment_id=payment_id))
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("applications.detail", application_id=application_id))


# ---------------------------------------------------------------------- list
@bp.route("/")
@login_required
def index():
    conn = get_db().connect()
    try:
        q = clean_text(request.args.get("q"), 80)
        mode = request.args.get("mode", "")
        date_from = parse_date(request.args.get("from"))
        date_to = parse_date(request.args.get("to"))
        page = max(1, parse_int(request.args.get("page"), 1))
        per_page = current_app.config["ITEMS_PER_PAGE"]

        where = ["1=1"]
        params = []
        if mode in PAYMENT_MODES:
            where.append("p.payment_mode=%s")
            params.append(mode)
        if date_from:
            where.append("p.payment_date >= %s")
            params.append(datetime.combine(date_from, datetime.min.time()))
        if date_to:
            where.append("p.payment_date < %s")
            params.append(datetime.combine(date_to, datetime.max.time()))
        if q:
            like = "%" + q + "%"
            where.append("(p.payment_code LIKE %s OR a.app_code LIKE %s"
                         " OR c.first_name LIKE %s OR c.surname LIKE %s"
                         " OR c.mobile LIKE %s)")
            params.extend([like] * 5)
        where_sql = " AND ".join(where)

        total = conn.scalar(
            "SELECT COUNT(*) FROM payments p JOIN applications a ON a.id=p.application_id"
            " JOIN customers c ON c.id=p.customer_id WHERE " + where_sql,
            tuple(params), default=0)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = conn.query(
            "SELECT p.*, a.app_code, c.first_name, c.surname, c.customer_code,"
            " u.name AS created_by_name FROM payments p"
            " JOIN applications a ON a.id=p.application_id"
            " JOIN customers c ON c.id=p.customer_id"
            " LEFT JOIN users u ON u.id=p.created_by"
            " WHERE " + where_sql + " ORDER BY p.id DESC LIMIT %s OFFSET %s",
            tuple(params + [per_page, (page - 1) * per_page]))
        totals = conn.query_one(
            "SELECT COALESCE(SUM(p.amount),0) AS amount,"
            " COALESCE(SUM(p.csc_amount),0) AS csc,"
            " COALESCE(SUM(p.govt_amount),0) AS govt"
            " FROM payments p JOIN applications a ON a.id=p.application_id"
            " JOIN customers c ON c.id=p.customer_id"
            " WHERE " + where_sql + " AND p.status='active'", tuple(params))
        page_info = {"page": page, "pages": pages, "total": total,
                     "per_page": per_page}
    finally:
        conn.close()

    return render_template("payments/list.html", payments=rows, q=q, mode=mode,
                           date_from=request.args.get("from", ""),
                           date_to=request.args.get("to", ""),
                           modes=PAYMENT_MODES, totals=totals, page_info=page_info)


# -------------------------------------------------------------------- receipt
@bp.route("/<int:payment_id>/receipt")
@login_required
def receipt(payment_id):
    conn = get_db().connect()
    try:
        payment = conn.query_one(
            "SELECT p.*, a.app_code, a.total, a.amount_paid, a.amount_pending,"
            " a.charge, a.govt_charge, a.csc_charge, a.apply_date,"
            " s.name AS service_name, ss.name AS sub_service_name,"
            " c.customer_code, c.first_name, c.surname, c.mobile, c.village,"
            " c.taluka, c.district, c.signature_path, u.name AS created_by_name"
            " FROM payments p JOIN applications a ON a.id=p.application_id"
            " JOIN customers c ON c.id=p.customer_id"
            " JOIN services s ON s.id=a.service_id"
            " LEFT JOIN sub_services ss ON ss.id=a.sub_service_id"
            " LEFT JOIN users u ON u.id=p.created_by WHERE p.id=%s", (payment_id,))
        if not payment:
            abort(404)
        settings = settings_map(conn)
    finally:
        conn.close()
    return render_template("payments/receipt.html", payment=payment,
                           settings=settings)


# --------------------------------------------------------------------- refund
@bp.route("/<int:payment_id>/reverse", methods=["POST"])
@login_required
@admin_required
def reverse(payment_id):
    reason = clean_text(request.form.get("reason"), 255)
    if not reason:
        flash("A reason is required to reverse a payment.", "danger")
        return redirect(url_for("payments.index"))
    with get_db().transaction() as tx:
        payment = tx.query_one("SELECT * FROM payments WHERE id=%s", (payment_id,))
        if not payment:
            abort(404)
        if payment["status"] != "active" or D(payment["amount"]) <= ZERO:
            flash("This payment is already reversed.", "warning")
            return redirect(url_for("payments.index"))
        application = tx.query_one("SELECT * FROM applications WHERE id=%s",
                                   (payment["application_id"],))
        tx.execute("UPDATE payments SET status='reversed', reversed_at=%s,"
                   " reversed_by=%s, reversal_reason=%s WHERE id=%s",
                   (datetime.now(), g.user["id"], reason, payment_id))
        # negative payment row + debit ledger entry = traceable accounting reversal
        # the exact original split is reversed, so CSC / government parts stay exact
        record_payment(tx, application, D(payment["amount"]),
                       payment["payment_mode"], payment["reference_no"] or "",
                       "Reversal of %s: %s" % (payment["payment_code"], reason),
                       created_by=g.user["id"], is_refund=True,
                       split=(-D(payment["charge_amount"]), -D(payment["govt_amount"]),
                              -D(payment["csc_amount"])))
        write_audit(tx, "payment_reverse", "payments", payment_id,
                    "Reversed %s - %s" % (payment["payment_code"], reason))
    flash("Payment reversed with a proper accounting entry.", "success")
    return redirect(url_for("payments.index"))
