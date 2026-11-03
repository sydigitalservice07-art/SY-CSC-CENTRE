"""Customer entry form: validation, duplicate warnings, masking, archiving."""
from modules.utils import (calc_age, format_mobile, mask_aadhaar,
                           normalise_mobile, valid_aadhaar)


def test_mobile_normalisation_and_validation():
    assert normalise_mobile("9876543210") == "9876543210"
    assert normalise_mobile("+91 98765 43210") == "9876543210"
    assert normalise_mobile("098765 43210") == "9876543210"
    assert normalise_mobile("12345") is None
    assert normalise_mobile("5876543210") is None      # must start 6-9
    assert format_mobile("9876543210") == "+91 98765 43210"


def test_aadhaar_validation_and_masking():
    assert valid_aadhaar("234567890123") is True
    assert valid_aadhaar("034567890123") is False      # cannot start with 0/1
    assert valid_aadhaar("23456789012") is False
    assert mask_aadhaar("234567890123") == "XXXX XXXX 0123"
    assert mask_aadhaar("bad") == ""


def test_age_calculation():
    from datetime import date
    assert calc_age(date.today().replace(year=date.today().year - 30)) == 30
    assert calc_age(None) is None


CUSTOMER_FORM = {
    "surname": "Patel", "first_name": "Kiran", "father_surname": "Patel",
    "father_name": "Ramesh", "mother_surname": "Patel", "mother_name": "Sita",
    "dob": "1990-05-20", "gender": "Male", "marital_status": "Married",
    "mobile": "9812345678", "alt_mobile": "", "email": "kiran@example.com",
    "house_no": "7", "street": "Bazaar Road", "society": "Om", "colony": "Center",
    "village": "Nadiad", "taluka": "Nadiad", "post_office": "Nadiad",
    "district": "Kheda", "state": "Gujarat", "pincode": "387001",
    "aadhaar_name": "Kiran Patel", "aadhaar_number": "234567890123",
}


def test_create_customer_generates_id_and_shows_popup(admin_client, conn):
    response = admin_client.post("/customers/new", data=CUSTOMER_FORM,
                                 follow_redirects=True)
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "SYCSC/CUST/45678/0001" in body
    row = conn.query_one("SELECT * FROM customers WHERE mobile='9812345678'")
    assert row is not None
    assert row["pincode"] == "387001"
    assert row["village"] == "Nadiad"
    # masked aadhaar in the list view
    listing = admin_client.get("/customers/").get_data(as_text=True)
    assert "XXXX XXXX 0123" in listing
    assert "234567890123" not in listing


def test_customer_form_required_fields(admin_client, conn):
    bad = dict(CUSTOMER_FORM)
    bad.update({"surname": "", "mobile": "", "pincode": "12", "village": ""})
    response = admin_client.post("/customers/new", data=bad)
    body = response.get_data(as_text=True)
    assert "Surname is required" in body
    assert "valid 10 digit mobile number is required" in body
    assert "Pincode must be exactly 6 digits" in body
    assert "Village is required" in body
    assert conn.query_one("SELECT COUNT(*) AS c FROM customers WHERE mobile=''")["c"] == 0


def test_duplicate_mobile_is_refused(admin_client, conn):
    admin_client.post("/customers/new", data=CUSTOMER_FORM, follow_redirects=True)
    response = admin_client.post("/customers/new", data=CUSTOMER_FORM,
                                 follow_redirects=True)
    assert "already belongs to customer" in response.get_data(as_text=True)
    assert conn.scalar("SELECT COUNT(*) FROM customers WHERE mobile='9812345678'") == 1


def test_invalid_email_and_future_dob(admin_client):
    bad = dict(CUSTOMER_FORM)
    bad.update({"mobile": "9812345679", "email": "not-an-email", "dob": "2999-01-01"})
    body = admin_client.post("/customers/new", data=bad).get_data(as_text=True)
    assert "Email address is not valid" in body
    assert "Date of birth cannot be in the future" in body


def test_customer_edit_keeps_code(admin_client, conn):
    admin_client.post("/customers/new", data=CUSTOMER_FORM, follow_redirects=True)
    row = conn.query_one("SELECT * FROM customers WHERE mobile='9812345678'")
    edited = dict(CUSTOMER_FORM)
    edited["first_name"] = "Kiranbhai"
    admin_client.post("/customers/%d/edit" % row["id"], data=edited,
                      follow_redirects=True)
    fresh = conn.query_one("SELECT * FROM customers WHERE id=%s", (row["id"],))
    assert fresh["first_name"] == "Kiranbhai"
    assert fresh["customer_code"] == row["customer_code"]


def test_customer_archive_and_restore(admin_client, conn):
    admin_client.post("/customers/new", data=CUSTOMER_FORM, follow_redirects=True)
    row = conn.query_one("SELECT * FROM customers WHERE mobile='9812345678'")
    admin_client.post("/customers/%d/archive" % row["id"],
                      data={"reason": "test"}, follow_redirects=True)
    assert conn.query_one("SELECT status FROM customers WHERE id=%s",
                          (row["id"],))["status"] == "archived"
    admin_client.post("/customers/%d/restore" % row["id"], follow_redirects=True)
    assert conn.query_one("SELECT status FROM customers WHERE id=%s",
                          (row["id"],))["status"] == "active"


def test_customer_search_by_mobile_and_aadhaar(admin_client):
    admin_client.post("/customers/new", data=CUSTOMER_FORM, follow_redirects=True)
    # search by part of the mobile number (the list shows it as +91 98123 45678)
    assert "Kiran" in admin_client.get("/customers/?q=9812").get_data(as_text=True)
    # search by full Aadhaar number (list still shows it masked)
    body = admin_client.get("/customers/?q=234567890123").get_data(as_text=True)
    assert "Kiran" in body and "XXXX XXXX 0123" in body
    # and by customer id
    assert "Kiran" in admin_client.get(
        "/customers/?q=SYCSC/CUST/45678").get_data(as_text=True)
