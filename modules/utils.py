"""
SY CSC CENTRE - shared domain helpers
=====================================
ID generation, validation, date helpers and constants.

Nothing in here touches Flask request state, so it is easy to unit test.
"""
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import db as dbmod
from db import D, ZERO

# ------------------------------------------------------------------ constants
APPLICATION_STATUSES = ["Draft", "Pending", "In Progress", "Submitted",
                        "Completed", "Rejected", "Cancelled"]
ACTIVE_STATUSES = ["Draft", "Pending", "In Progress", "Submitted"]
PAYMENT_MODES = ["Cash", "Bank", "UPI"]
ACCOUNTS = ["Cash", "Bank", "UPI"]
GENDERS = ["Male", "Female", "Other"]
MARITAL_STATUSES = ["Single", "Married", "Divorced", "Widowed", "Other"]
INCOME_CATEGORIES = ["Application Payment", "Other Income", "Interest", "Commission"]
EXPENSE_CATEGORIES = ["Office Purchase", "Government Charge Paid", "Salary",
                      "Rent", "Electricity", "Internet", "Transport", "Other Expense"]

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
AADHAAR_RE = re.compile(r"^[2-9]\d{11}$")
PINCODE_RE = re.compile(r"^\d{6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")


# ----------------------------------------------------------------- validation
def normalise_mobile(value):
    """'+91 98765 43210' / '098765 43210' -> '9876543210'  (or None)."""
    if value is None:
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]
    return digits if MOBILE_RE.match(digits) else None


def format_mobile(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 10:
        return "+91 %s %s" % (digits[:5], digits[5:])
    return value or ""


def valid_aadhaar(value):
    digits = re.sub(r"\D", "", str(value or ""))
    return bool(AADHAAR_RE.match(digits))


def mask_aadhaar(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 12:
        return ""
    return "XXXX XXXX %s" % digits[-4:]


def mask_mobile(value):
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) != 10:
        return value or ""
    return "XXXXX%s" % digits[5:]


def clean_text(value, max_length=None):
    text = (value or "").strip()
    if max_length:
        text = text[:max_length]
    return text


def parse_date(value):
    """Accept 'YYYY-MM-DD' or 'DD/MM/YYYY'. Returns date or None."""
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_decimal(value, default=ZERO):
    if value is None or value == "":
        return default
    try:
        return D(str(value).replace(",", "").replace("\u20b9", "").strip())
    except (InvalidOperation, ValueError):
        return default


def parse_int(value, default=None):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def calc_age(dob):
    if not dob:
        return None
    today = date.today()
    years = today.year - dob.year
    if (today.month, today.day) < (dob.month, dob.day):
        years -= 1
    return years if years >= 0 else None


def calc_total_days(start, end):
    """Inclusive day count between two dates (None when either is missing)."""
    if not start or not end:
        return None
    delta = (end - start).days
    return delta + 1 if delta >= 0 else None


# ------------------------------------------------------------ ID generation
def next_sequence(conn, scope, key_part):
    """
    Transaction-safe counter.  Must be called inside a transaction.

    MySQL   : SELECT ... FOR UPDATE inside the caller's transaction
    SQLite  : BEGIN IMMEDIATE (write lock) taken by the caller
    The UNIQUE(scope, key_part) constraint makes a lost update impossible.
    """
    row = conn.query_one(
        "SELECT next_val FROM id_sequences WHERE scope=%s AND key_part=%s"
        + conn.lock_clause(), (scope, key_part))
    if row:
        value = int(row["next_val"])
        conn.execute("UPDATE id_sequences SET next_val=%s WHERE scope=%s AND key_part=%s",
                     (value + 1, scope, key_part))
        return value
    conn.execute("INSERT INTO id_sequences (scope, key_part, next_val) VALUES (%s,%s,%s)",
                 (scope, key_part, 2))
    return 1


def mobile_key(mobile):
    digits = re.sub(r"\D", "", str(mobile or ""))
    return digits[-5:] if len(digits) >= 5 else "00000"


def generate_customer_code(conn, mobile):
    """SYCSC/CUST/<last 5 mobile digits>/<4 digit sequence>"""
    key = mobile_key(mobile)
    return "SYCSC/CUST/%s/%04d" % (key, next_sequence(conn, "CUST", key))


def generate_app_code(conn, mobile):
    """AP/<last 5 mobile digits>/<5 digit sequence>"""
    key = mobile_key(mobile)
    return "AP/%s/%05d" % (key, next_sequence(conn, "APP", key))


def generate_payment_code(conn, mobile, when=None):
    """PAY/<YYYYMMDDHHMM>/<last 5 mobile digits>/<3 digit sequence>"""
    when = when or datetime.now()
    key = "%s-%s" % (mobile_key(mobile), when.strftime("%Y%m%d"))
    seq = next_sequence(conn, "PAY", key)
    return "PAY/%s/%s/%03d" % (when.strftime("%Y%m%d%H%M"), mobile_key(mobile), seq)


def generate_voucher_no(conn, when=None):
    when = when or datetime.now()
    key = when.strftime("%Y%m")
    return "VCH/%s/%05d" % (key, next_sequence(conn, "VCH", key))


# --------------------------------------------------------------- accounting
def charge_classification(conn):
    row = conn.query_one(
        "SELECT setting_value FROM settings WHERE setting_key=%s", ("charge_classification",))
    value = (row or {}).get("setting_value") or "csc_income"
    return value if value in ("csc_income", "govt_charge", "other_income") else "csc_income"


def csc_revenue_expression(conn, alias="p"):
    """
    SQL fragment returning only the CSC revenue part of a payment row.
    The PDF 'Charge' column is classified through the setting
    charge_classification so that its meaning stays configurable.
    """
    classification = charge_classification(conn)
    if classification == "csc_income":
        return "(%s.csc_amount + %s.charge_amount)" % (alias, alias)
    return "%s.csc_amount" % alias


def allocate_payment(application, amount):
    """
    Split a payment across the three charge components (charge / govt / csc)
    in proportion to what is still pending.  Returns (charge, govt, csc) as
    Decimals whose sum is exactly `amount`.
    """
    amount = D(amount)
    pending = {
        "charge": D(application.get("charge")) - D(application.get("charge_paid")),
        "govt": D(application.get("govt_charge")) - D(application.get("govt_paid")),
        "csc": D(application.get("csc_charge")) - D(application.get("csc_paid")),
    }
    pending = {k: (v if v > 0 else ZERO) for k, v in pending.items()}
    total_pending = sum(pending.values(), ZERO)
    if total_pending <= ZERO:
        return ZERO, ZERO, amount      # nothing pending - everything is CSC income
    if amount >= total_pending:
        return pending["charge"], pending["govt"], pending["csc"] + (amount - total_pending)

    result = {}
    allocated = ZERO
    for key in ("charge", "govt"):
        part = (amount * pending[key] / total_pending).quantize(Decimal("0.01"))
        result[key] = part
        allocated += part
    result["csc"] = amount - allocated
    return result["charge"], result["govt"], result["csc"]
