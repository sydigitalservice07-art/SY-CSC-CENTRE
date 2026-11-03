"""
SY CSC CENTRE - Account Sheet (bookkeeping ledger)
=================================================
Sign convention (documented in README as well):

    credit = money coming INTO an account (income, receipts)
    debit  = money going OUT of an account (expenses, refunds, payments)

    balance = opening_balance + SUM(credits) - SUM(debits)

Every financial event creates exactly one ledger row (transfers create a paired
debit+credit with a shared transfer group, so the pair can never drift apart).
Application payments, refunds, salary payments and government charge postings
all land in this single auditable ledger.
"""
import os
from datetime import date, datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, session, url_for)

from db import D, ZERO, get_db
from modules.i18n import translate
from modules.security import (admin_required, login_required, save_image,
                              write_audit)
from modules.utils import (ACCOUNTS, EXPENSE_CATEGORIES, INCOME_CATEGORIES,
                           clean_text, generate_voucher_no, parse_date,
                           parse_decimal, parse_int)

bp = Blueprint("accounts", __name__, url_prefix="/accounts")


def _(key):
    return translate(key, session.get("lang", "en"))


# ---------------------------------------------------------------------------
def account_balances(conn, as_of=None):
    """Opening balance + credits - debits for each account."""
    out = {}
    for account in ACCOUNTS:
        opening = D(conn.scalar(
            "SELECT opening_balance FROM account_balances WHERE account=%s",
            (account,), default=0))
        base = ("SELECT COALESCE(SUM(amount),0) FROM ledger_entries"
                " WHERE account=%s AND is_reversed=0")
        params = [account]
        if as_of:
            base += " AND entry_date <= %s"
            params.append(as_of)
        credits = D(conn.scalar(base + " AND entry_type='credit'",
                                tuple(params), default=0))
        debits = D(conn.scalar(base + " AND entry_type='debit'",
                               tuple(params), default=0))
        out[account] = {"opening": opening, "credits": credits, "debits": debits,
                        "balance": opening + credits - debits}
    return out


def government_payable(conn):
    """Government money collected minus what has been handed over."""
    collected = D(conn.scalar(
        "SELECT COALESCE(SUM(govt_amount),0) FROM payments WHERE status='active'",
        default=0))
    paid_out = D(conn.scalar(
        "SELECT COALESCE(SUM(amount),0) FROM ledger_entries WHERE entry_type='debit'"
        " AND category='Government Charge Paid' AND is_reversed=0", default=0))
    return {"collected": collected, "paid_out": paid_out,
            "payable": collected - paid_out}


# ------------------------------------------------------------------------ page
@bp.route("/")
@login_required
@admin_required
def index():
    conn = get_db().connect()
    try:
        account = request.args.get("account", "")
        entry_type = request.args.get("entry_type", "")
        category = clean_text(request.args.get("category"), 60)
        q = clean_text(request.args.get("q"), 80)
        date_from = parse_date(request.args.get("from"))
        date_to = parse_date(request.args.get("to"))
        page = max(1, parse_int(request.args.get("page"), 1))
        per_page = current_app.config["ITEMS_PER_PAGE"]

        where = ["1=1"]
        params = []
        if account in ACCOUNTS:
            where.append("l.account=%s")
            params.append(account)
        if entry_type in ("credit", "debit"):
            where.append("l.entry_type=%s")
            params.append(entry_type)
        if category:
            where.append("l.category=%s")
            params.append(category)
        if date_from:
            where.append("l.entry_date >= %s")
            params.append(date_from)
        if date_to:
            where.append("l.entry_date <= %s")
            params.append(date_to)
        if q:
            like = "%" + q + "%"
            where.append("(l.voucher_no LIKE %s OR l.description LIKE %s"
                         " OR l.reference_no LIKE %s)")
            params.extend([like, like, like])
        where_sql = " AND ".join(where)

        total = conn.scalar("SELECT COUNT(*) FROM ledger_entries l WHERE " + where_sql,
                            tuple(params), default=0)
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        rows = conn.query(
            "SELECT l.*, u.name AS created_by_name FROM ledger_entries l"
            " LEFT JOIN users u ON u.id=l.created_by WHERE " + where_sql +
            " ORDER BY l.entry_date DESC, l.id DESC LIMIT %s OFFSET %s",
            tuple(params + [per_page, (page - 1) * per_page]))
        sums = conn.query_one(
            "SELECT COALESCE(SUM(CASE WHEN entry_type='credit' THEN amount ELSE 0 END),0)"
            " AS credits, COALESCE(SUM(CASE WHEN entry_type='debit' THEN amount ELSE 0"
            " END),0) AS debits FROM ledger_entries l WHERE " + where_sql,
            tuple(params))
        balances = account_balances(conn)
        govt = government_payable(conn)
        page_info = {"page": page, "pages": pages, "total": total}
        settings_rows = conn.query("SELECT setting_key, setting_value FROM settings")
        csc_settings = {r["setting_key"]: r["setting_value"] for r in settings_rows}
    finally:
        conn.close()

    return render_template("accounts/ledger.html", entries=rows, balances=balances,
                           govt=govt, sums=sums, page_info=page_info,
                           accounts=ACCOUNTS, income_categories=INCOME_CATEGORIES,
                           expense_categories=EXPENSE_CATEGORIES,
                           settings_name=csc_settings.get("csc_name", ""),
                           settings_phone=csc_settings.get("csc_phone", ""),
                           settings_address=csc_settings.get("csc_address", ""),
                           settings_footer=csc_settings.get("receipt_footer", ""),
                           settings_classification=csc_settings.get(
                               "charge_classification", "csc_income"),
                           filters={"account": account, "entry_type": entry_type,
                                    "category": category, "q": q,
                                    "from": request.args.get("from", ""),
                                    "to": request.args.get("to", "")})


# ----------------------------------------------------------------------- entry
@bp.route("/entry", methods=["POST"])
@login_required
@admin_required
def add_entry():
    entry_type = request.form.get("entry_type")
    account = request.form.get("account")
    category = clean_text(request.form.get("category"), 60)
    description = clean_text(request.form.get("description"), 255)
    reference = clean_text(request.form.get("reference_no"), 80)
    amount = parse_decimal(request.form.get("amount"))
    entry_date = parse_date(request.form.get("entry_date")) or date.today()

    errors = []
    if entry_type not in ("credit", "debit"):
        errors.append("Entry type must be credit or debit.")
    if account not in ACCOUNTS:
        errors.append("Account must be Cash, Bank or UPI.")
    if amount <= ZERO:
        errors.append("Amount must be greater than zero.")
    if not category:
        errors.append("Category is required.")
    if errors:
        for message in errors:
            flash(message, "danger")
        return redirect(url_for("accounts.index"))

    with get_db().transaction() as tx:
        if reference:
            duplicate = tx.query_one(
                "SELECT voucher_no FROM ledger_entries WHERE reference_no=%s"
                " AND account=%s AND entry_type=%s AND amount=%s AND entry_date=%s"
                " AND is_reversed=0",
                (reference, account, entry_type, amount, entry_date))
            if duplicate:
                flash("This reference number is already posted (voucher %s)."
                      % duplicate["voucher_no"], "danger")
                return redirect(url_for("accounts.index"))
        voucher = generate_voucher_no(tx, datetime.now())
        tx.execute(
            "INSERT INTO ledger_entries (voucher_no, entry_date, account, entry_type,"
            " category, amount, description, reference_no, created_at, created_by)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (voucher, entry_date, account, entry_type, category, amount,
             description, reference, datetime.now(), g.user["id"]))
        write_audit(tx, "ledger_entry", "ledger_entries", voucher,
                    "%s %s %s on %s (%s)" % (entry_type, amount, account,
                                             entry_date, category))
    flash("Ledger entry posted (%s)." % voucher, "success")
    return redirect(url_for("accounts.index"))


# -------------------------------------------------------------------- transfer
@bp.route("/transfer", methods=["POST"])
@login_required
@admin_required
def transfer():
    from_account = request.form.get("from_account")
    to_account = request.form.get("to_account")
    amount = parse_decimal(request.form.get("amount"))
    description = clean_text(request.form.get("description"), 255)
    entry_date = parse_date(request.form.get("entry_date")) or date.today()

    if from_account not in ACCOUNTS or to_account not in ACCOUNTS:
        flash("Choose a valid from and to account.", "danger")
        return redirect(url_for("accounts.index"))
    if from_account == to_account:
        flash("From and To account cannot be the same.", "danger")
        return redirect(url_for("accounts.index"))
    if amount <= ZERO:
        flash("Transfer amount must be greater than zero.", "danger")
        return redirect(url_for("accounts.index"))

    with get_db().transaction() as tx:
        balances = account_balances(tx)
        if amount > balances[from_account]["balance"]:
            flash("Not enough balance in %s (available %s)."
                  % (from_account, balances[from_account]["balance"]), "danger")
            return redirect(url_for("accounts.index"))
        voucher = generate_voucher_no(tx, datetime.now())
        group = "TRF-%s-%s" % (voucher.replace("/", ""), from_account)
        note = description or "Transfer %s -> %s" % (from_account, to_account)
        tx.execute(
            "INSERT INTO ledger_entries (voucher_no, entry_date, account, entry_type,"
            " category, amount, description, transfer_group, created_at, created_by)"
            " VALUES (%s,%s,%s,'debit','Transfer',%s,%s,%s,%s,%s)",
            (voucher, entry_date, from_account, amount,
             note + " (out)", group, datetime.now(), g.user["id"]))
        tx.execute(
            "INSERT INTO ledger_entries (voucher_no, entry_date, account, entry_type,"
            " category, amount, description, transfer_group, created_at, created_by)"
            " VALUES (%s,%s,%s,'credit','Transfer',%s,%s,%s,%s,%s)",
            (voucher, entry_date, to_account, amount,
             note + " (in)", group, datetime.now(), g.user["id"]))
        write_audit(tx, "account_transfer", "ledger_entries", voucher,
                    "Transfer %s from %s to %s" % (amount, from_account, to_account))
    flash(_("account.transfer_ok"), "success")
    return redirect(url_for("accounts.index"))


# ------------------------------------------------------------- opening balance
@bp.route("/opening", methods=["POST"])
@login_required
@admin_required
def opening_balance():
    account = request.form.get("account")
    amount = parse_decimal(request.form.get("opening_balance"))
    if account not in ACCOUNTS:
        abort(400)
    with get_db().transaction() as tx:
        existing = tx.query_one("SELECT account FROM account_balances WHERE account=%s",
                                (account,))
        if existing:
            tx.execute("UPDATE account_balances SET opening_balance=%s, updated_at=%s,"
                       " updated_by=%s WHERE account=%s",
                       (amount, datetime.now(), g.user["id"], account))
        else:
            tx.execute("INSERT INTO account_balances (account, opening_balance,"
                       " updated_at, updated_by) VALUES (%s,%s,%s,%s)",
                       (account, amount, datetime.now(), g.user["id"]))
        write_audit(tx, "opening_balance", "account_balances", account,
                    "Opening balance %s set to %s" % (account, amount))
    flash("Opening balance updated.", "success")
    return redirect(url_for("accounts.index"))


# --------------------------------------------------------------- CSC settings
@bp.route("/settings", methods=["POST"])
@login_required
@admin_required
def settings():
    keys = ["csc_name", "csc_address", "csc_phone", "csc_email", "receipt_footer",
            "charge_classification"]
    with get_db().transaction() as tx:
        for key in keys:
            value = clean_text(request.form.get(key), 500)
            if key == "charge_classification" and value not in (
                    "csc_income", "govt_charge", "other_income"):
                value = "csc_income"
            existing = tx.query_one("SELECT setting_key FROM settings"
                                    " WHERE setting_key=%s", (key,))
            if existing:
                tx.execute("UPDATE settings SET setting_value=%s, updated_at=%s,"
                           " updated_by=%s WHERE setting_key=%s",
                           (value, datetime.now(), g.user["id"], key))
            else:
                tx.execute("INSERT INTO settings (setting_key, setting_value,"
                           " updated_at, updated_by) VALUES (%s,%s,%s,%s)",
                           (key, value, datetime.now(), g.user["id"]))
        signature = request.files.get("csc_signature")
        if signature and signature.filename:
            try:
                filename = save_image(
                    signature,
                    os.path.join(current_app.config["UPLOAD_FOLDER"], "signatures"),
                    current_app.config["SIGNATURE_MAX_BYTES"],
                    current_app.config["ALLOWED_IMAGE_EXT"],
                    current_app.config["IMAGE_MAX_PIXELS"])
                existing = tx.query_one("SELECT setting_key FROM settings"
                                        " WHERE setting_key='csc_signature'")
                if existing:
                    tx.execute("UPDATE settings SET setting_value=%s WHERE"
                               " setting_key='csc_signature'", (filename,))
                else:
                    tx.execute("INSERT INTO settings (setting_key, setting_value)"
                               " VALUES ('csc_signature',%s)", (filename,))
            except ValueError as exc:
                flash(str(exc), "danger")
        write_audit(tx, "settings_update", "settings", "csc",
                    "CSC / receipt settings updated")
    flash("Settings saved.", "success")
    return redirect(url_for("accounts.index"))
