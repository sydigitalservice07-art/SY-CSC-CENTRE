"""ID generation: customer, application, payment and voucher numbers."""
import re
from datetime import datetime

import pytest

import db as dbmod
from modules.utils import (generate_app_code, generate_customer_code,
                           generate_payment_code, generate_voucher_no,
                           mobile_key)


def test_mobile_key():
    assert mobile_key("9876543210") == "43210"
    assert mobile_key("+91 98765 43210") == "43210"
    assert mobile_key("") == "00000"


def test_customer_code_format_and_sequence(app):
    with dbmod.get_db().transaction() as tx:
        first = generate_customer_code(tx, "9876543210")
        second = generate_customer_code(tx, "9876543210")
        other = generate_customer_code(tx, "9812300001")
    assert re.match(r"^SYCSC/CUST/43210/\d{4}$", first)
    assert first == "SYCSC/CUST/43210/0001"
    assert second == "SYCSC/CUST/43210/0002"
    # a different mobile keeps its own sequence
    assert other == "SYCSC/CUST/00001/0001"
    assert first != second


def test_customer_code_sequence_is_not_row_count(app):
    """Sequence must survive deletions - it is never COUNT(*)+1."""
    with dbmod.get_db().transaction() as tx:
        first = generate_customer_code(tx, "9876511111")
        tx.execute(
            "INSERT INTO customers (customer_code, surname, first_name, mobile,"
            " village, taluka, post_office, district, state, pincode, status,"
            " created_at) VALUES (%s,'A','B','9876511111','V','T','P','D','Gujarat',"
            "'387001','active',%s)", (first, datetime.now()))
        tx.execute("DELETE FROM customers WHERE customer_code=%s", (first,))
        again = generate_customer_code(tx, "9876511111")
    assert first == "SYCSC/CUST/11111/0001"
    assert again == "SYCSC/CUST/11111/0002"


def test_customer_code_unique_constraint(app):
    from tests.conftest import make_customer
    make_customer(app, mobile="9876522222")
    with pytest.raises(Exception):
        with dbmod.get_db().transaction() as tx:
            tx.execute(
                "INSERT INTO customers (customer_code, surname, first_name, mobile,"
                " village, taluka, post_office, district, state, pincode, status,"
                " created_at) VALUES ('SYCSC/CUST/22222/0001','A','B','9876522222',"
                "'V','T','P','D','Gujarat','387001','active',%s)", (datetime.now(),))


def test_application_code_format(app):
    with dbmod.get_db().transaction() as tx:
        first = generate_app_code(tx, "9876543210")
        second = generate_app_code(tx, "9876543210")
    assert re.match(r"^AP/43210/\d{5}$", first)
    assert first == "AP/43210/00001"
    assert second == "AP/43210/00002"


def test_payment_code_format(app):
    when = datetime(2026, 10, 27, 16, 35)
    with dbmod.get_db().transaction() as tx:
        first = generate_payment_code(tx, "9876543210", when)
        second = generate_payment_code(tx, "9876543210", when)
    assert re.match(r"^PAY/\d{12}/43210/\d{3}$", first)
    assert first == "PAY/202610271635/43210/001"
    assert second == "PAY/202610271635/43210/002"


def test_voucher_number_format(app):
    when = datetime(2026, 10, 27, 10, 0)
    with dbmod.get_db().transaction() as tx:
        first = generate_voucher_no(tx, when)
        second = generate_voucher_no(tx, when)
    assert first == "VCH/202610/00001"
    assert second == "VCH/202610/00002"


def test_sequences_are_independent_per_scope(app):
    with dbmod.get_db().transaction() as tx:
        generate_customer_code(tx, "9876543210")
        app_code = generate_app_code(tx, "9876543210")
    assert app_code == "AP/43210/00001"
