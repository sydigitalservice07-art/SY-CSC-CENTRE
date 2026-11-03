"""
SY CSC CENTRE - Customer Management
===================================
Customer entry form follows the sections of the supplied PDF:
  A. Personal information      B. Parents information
  C. Contact details           D. Full address (separate fields)
  E. Additional information (Aadhaar, photo, signature, documents)

Customers are never hard-deleted: they are archived, because applications,
payments and ledger rows must stay traceable.
"""
import os
from datetime import datetime

from flask import (Blueprint, abort, current_app, flash, g, redirect,
                   render_template, request, send_from_directory, session,
                   url_for)

from db import D, get_db
from modules.i18n import translate
from modules.security import (admin_required, current_user, has_permission,
                              hash_password, login_required, permission_required,
                              save_document, save_image, verify_password,
                              write_audit)
from modules.utils import (GENDERS, MARITAL_STATUSES, calc_age, clean_text,
                           format_mobile, generate_customer_code, mask_aadhaar,
                           normalise_mobile, parse_date, valid_aadhaar)

bp = Blueprint("customers", __name__, url_prefix="/customers")

SORTABLE = {"code": "c.customer_code", "name": "c.first_name", "village": "c.village",
            "created": "c.created_at", "id": "c.id"}

UPLOAD_ROOT = None      # set in register()


def _(key):
    return translate(key, session.get("lang", "en"))


def register(app):
    global UPLOAD_ROOT
    UPLOAD_ROOT = app.config["UPLOAD_FOLDER"]
    app.register_blueprint(bp)


# --------------------------------------------------------------------- helpers
def _paginate(page, total, per_page):
    pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, pages))
    return {"page": page, "pages": pages, "per_page": per_page,
            "offset": (page - 1) * per_page, "total": total}


def _empty_customer():
    return {}


def _customer_payload(form, files):
    return {
        "surname": clean_text(form.get("surname"), 60),
        "first_name": clean_text(form.get("first_name"), 60),
        "father_surname": clean_text(form.get("father_surname"), 60),
        "father_name": clean_text(form.get("father_name"), 60),
        "mother_surname": clean_text(form.get("mother_surname"), 60),
        "mother_name": clean_text(form.get("mother_name"), 60),
        "dob": parse_date(form.get("dob")),
        "gender": clean_text(form.get("gender"), 20),
        "marital_status": clean_text(form.get("marital_status"), 20),
        "mobile": normalise_mobile(form.get("mobile")),
        "alt_mobile": normalise_mobile(form.get("alt_mobile")),
        "email": clean_text(form.get("email"), 120),
        "street": clean_text(form.get("street"), 120),
        "society": clean_text(form.get("society"), 120),
        "colony": clean_text(form.get("colony"), 120),
        "house_no": clean_text(form.get("house_no"), 40),
        "village": clean_text(form.get("village"), 80),
        "taluka": clean_text(form.get("taluka"), 80),
        "post_office": clean_text(form.get("post_office"), 80),
        "district": clean_text(form.get("district"), 80),
        "state": clean_text(form.get("state"), 80),
        "pincode": clean_text(form.get("pincode"), 6),
        "aadhaar_name": clean_text(form.get("aadhaar_name"), 120),
        "aadhaar_number": clean_text(form.get("aadhaar_number"), 12).replace(" ", ""),
        "notes": clean_text(form.get("notes"), 1000),
    }


def validate_customer(data, conn, customer_id=None):
    """Server side validation. Returns a list of error strings."""
    from modules.utils import EMAIL_RE, PINCODE_RE

    errors = []
    if not data["surname"]:
        errors.append("Surname is required.")
    if not data["first_name"]:
        errors.append("Name is required.")
    if not data["mobile"]:
        errors.append("A valid 10 digit mobile number is required (e.g. 9876543210).")
    if data["alt_mobile"] and data["alt_mobile"] == data["mobile"]:
        errors.append("Alternate mobile must be different from the mobile number.")
    if data["email"] and not EMAIL_RE.match(data["email"]):
        errors.append("Email address is not valid.")
    if not data["village"]:
        errors.append("Village is required.")
    if not data["taluka"]:
        errors.append("Taluka is required.")
    if not data["post_office"]:
        errors.append("Post Office is required.")
    if not data["district"]:
        errors.append("District is required.")
    if not data["state"]:
        errors.append("State is required.")
    if not data["pincode"] or not PINCODE_RE.match(data["pincode"]):
        errors.append("Pincode must be exactly 6 digits.")
    if data["gender"] and data["gender"] not in GENDERS:
        errors.append("Gender value is not valid.")
    if data["marital_status"] and data["marital_status"] not in MARITAL_STATUSES:
        errors.append("Marital status value is not valid.")
    if data["dob"] and data["dob"] > datetime.now().date():
        errors.append("Date of birth cannot be in the future.")
    if data["aadhaar_number"] and not valid_aadhaar(data["aadhaar_number"]):
        errors.append("Aadhaar number must be 12 digits and cannot start with 0 or 1.")

    # duplicate checks (mobile is the natural key of the customer)
    if data["mobile"]:
        sql = ("SELECT id, customer_code, first_name, surname FROM customers"
               " WHERE mobile=%s AND status='active'")
        params = [data["mobile"]]
        if customer_id:
            sql += " AND id<>%s"
            params.append(customer_id)
        existing = conn.query_one(sql, tuple(params))
        if existing:
            errors.append(
                "Mobile %s already belongs to customer %s (%s %s). "
                "Duplicate customer records are not allowed."
                % (format_mobile(data["mobile"]), existing["customer_code"],
                   existing["first_name"], existing["surname"]))
    if data["aadhaar_number"]:
        sql = "SELECT id, customer_code FROM customers WHERE aadhaar_number=%s"
        params = [data["aadhaar_number"]]
        if customer_id:
            sql += " AND id<>%s"
            params.append(customer_id)
        existing = conn.query_one(sql, tuple(params))
        if existing:
            errors.append("This Aadhaar number is already saved on customer %s."
                          % existing["customer_code"])
    return errors


def _save_media(customer_id, form, files, conn, existing=None):
    """Handle photo + signature uploads. Returns (photo_name, signature_name)."""
    cfg = current_app.config
    photo = existing["photo_path"] if existing else None
    signature = existing["signature_path"] if existing else None

    if files.get("photo") and files["photo"].filename:
        photo = save_image(files["photo"], os.path.join(UPLOAD_ROOT, "photos"),
                           cfg["PHOTO_MAX_BYTES"], cfg["ALLOWED_IMAGE_EXT"],
                           cfg["IMAGE_MAX_PIXELS"])
    if files.get("signature") and files["signature"].filename:
        signature = save_image(files["signature"], os.path.join(UPLOAD_ROOT, "signatures"),
                               cfg["SIGNATURE_MAX_BYTES"], cfg["ALLOWED_IMAGE_EXT"],
                               cfg["IMAGE_MAX_PIXELS"])
    return photo, signature


# ----------------------------------------------------------------------- list
@bp.route("/")
@login_required
def index():
    conn = get_db().connect()
    try:
        q = clean_text(request.args.get("q"), 80)
        status = request.args.get("status", "active")
        sort = request.args.get("sort", "id")
        direction = "ASC" if request.args.get("dir") == "asc" else "DESC"
        page = max(1, int(request.args.get("page", 1) or 1))
        per_page = current_app.config["ITEMS_PER_PAGE"]

        where = ["1=1"]
        params = []
        if status == "active":
            where.append("c.status='active'")
        elif status == "archived":
            where.append("c.status='archived'")
        if q:
            like = "%" + q + "%"
            where.append("(c.customer_code LIKE %s OR c.first_name LIKE %s"
                         " OR c.surname LIKE %s OR c.mobile LIKE %s"
                         " OR c.aadhaar_number LIKE %s OR c.village LIKE %s)")
            params.extend([like, like, like, like, like, like])

        where_sql = " AND ".join(where)
        total = conn.scalar("SELECT COUNT(*) FROM customers c WHERE " + where_sql,
                            tuple(params), default=0)
        page_info = _paginate(page, total, per_page)
        order_by = SORTABLE.get(sort, "c.id")

        rows = conn.query(
            "SELECT c.id, c.customer_code, c.first_name, c.surname, c.mobile,"
            " c.aadhaar_number, c.village, c.taluka, c.district, c.status,"
            " c.created_at, c.photo_path,"
            " (SELECT COUNT(*) FROM applications a WHERE a.customer_id=c.id"
            "  AND a.is_deleted=0) AS app_count"
            " FROM customers c WHERE " + where_sql +
            " ORDER BY " + order_by + " " + direction + " LIMIT %s OFFSET %s",
            tuple(params + [per_page, page_info["offset"]]))
    finally:
        conn.close()

    return render_template("customers/list.html", customers=rows, q=q, status=status,
                           sort=sort, direction=request.args.get("dir", "desc"),
                           page_info=page_info)


# --------------------------------------------------------------------- create
@bp.route("/new", methods=["GET", "POST"])
@login_required
@permission_required("can_add_customer")
def create():
    conn = get_db().connect()
    try:
        if request.method == "POST":
            data = _customer_payload(request.form, request.files)
            errors = validate_customer(data, conn)
            photo_name = signature_name = None
            if not errors:
                try:
                    photo_name = save_image(
                        request.files.get("photo"),
                        os.path.join(UPLOAD_ROOT, "photos"),
                        current_app.config["PHOTO_MAX_BYTES"],
                        current_app.config["ALLOWED_IMAGE_EXT"],
                        current_app.config["IMAGE_MAX_PIXELS"])
                    signature_name = save_image(
                        request.files.get("signature"),
                        os.path.join(UPLOAD_ROOT, "signatures"),
                        current_app.config["SIGNATURE_MAX_BYTES"],
                        current_app.config["ALLOWED_IMAGE_EXT"],
                        current_app.config["IMAGE_MAX_PIXELS"])
                except ValueError as exc:
                    errors.append(str(exc))

            if not errors:
                with get_db().transaction() as tx:
                    code = generate_customer_code(tx, data["mobile"])
                    tx.execute(
                        "INSERT INTO customers (customer_code, surname, first_name,"
                        " father_surname, father_name, mother_surname, mother_name,"
                        " dob, gender, marital_status, mobile, alt_mobile, email,"
                        " street, society, colony, house_no, village, taluka,"
                        " post_office, district, state, pincode, aadhaar_name,"
                        " aadhaar_number, photo_path, signature_path, notes, status,"
                        " created_at, created_by)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,"
                        "%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (code, data["surname"], data["first_name"],
                         data["father_surname"], data["father_name"],
                         data["mother_surname"], data["mother_name"], data["dob"],
                         data["gender"] or None, data["marital_status"] or None,
                         data["mobile"], data["alt_mobile"] or None,
                         data["email"] or None, data["street"], data["society"],
                         data["colony"], data["house_no"], data["village"],
                         data["taluka"], data["post_office"], data["district"],
                         data["state"], data["pincode"], data["aadhaar_name"],
                         data["aadhaar_number"] or None, photo_name,
                         signature_name, data["notes"], "active",
                         datetime.now(), g.user["id"]))
                    new_id = tx.scalar("SELECT id FROM customers WHERE customer_code=%s",
                                       (code,))
                    write_audit(tx, "customer_create", "customers", new_id,
                                "Created customer %s" % code)
                flash(_("customer.created_success") + ": " + code, "success")
                return redirect(url_for("customers.detail", customer_id=new_id,
                                        created=1))
            for message in errors:
                flash(message, "danger")
            form_data = request.form
        else:
            form_data = {}

        return render_template("customers/form.html", customer=form_data,
                               mode="create", errors=[], age=None,
                               genders=GENDERS, marital=MARITAL_STATUSES)
    finally:
        conn.close()


# --------------------------------------------------------------------- detail
@bp.route("/<int:customer_id>")
@login_required
def detail(customer_id):
    conn = get_db().connect()
    try:
        customer = conn.query_one("SELECT * FROM customers WHERE id=%s", (customer_id,))
        if not customer:
            abort(404)
        applications = conn.query(
            "SELECT a.id, a.app_code, a.apply_date, a.status, a.total, a.amount_paid,"
            " a.amount_pending, a.csc_charge, a.is_archived, s.name AS service_name"
            " FROM applications a JOIN services s ON s.id=a.service_id"
            " WHERE a.customer_id=%s AND a.is_deleted=0"
            " ORDER BY a.id DESC", (customer_id,))
        payments = conn.query(
            "SELECT p.id, p.payment_code, p.payment_date, p.amount, p.csc_amount,"
            " p.govt_amount, p.payment_mode, p.is_refund, p.status, a.app_code"
            " FROM payments p JOIN applications a ON a.id=p.application_id"
            " WHERE p.customer_id=%s ORDER BY p.id DESC", (customer_id,))
        documents = conn.query(
            "SELECT id, doc_name, doc_type, file_path, is_protected, created_at"
            " FROM customer_documents WHERE customer_id=%s ORDER BY id DESC",
            (customer_id,))
        activity = conn.query(
            "SELECT action, username, details, created_at FROM audit_logs"
            " WHERE entity='customers' AND entity_id=%s ORDER BY id DESC LIMIT 25",
            (str(customer_id),))
        totals = conn.query_one(
            "SELECT COALESCE(SUM(p.amount),0) AS paid FROM payments p"
            " WHERE p.customer_id=%s AND p.status='active'", (customer_id,))
        pending = conn.query_one(
            "SELECT COALESCE(SUM(a.amount_pending),0) AS due FROM applications a"
            " WHERE a.customer_id=%s AND a.is_deleted=0"
            " AND a.status NOT IN ('Cancelled')", (customer_id,))
    finally:
        conn.close()

    return render_template("customers/detail.html", customer=customer,
                           applications=applications, payments=payments,
                           documents=documents, activity=activity,
                           total_paid=D(totals["paid"] if totals else 0),
                           total_due=D(pending["due"] if pending else 0),
                           age=calc_age(customer["dob"]),
                           show_created=request.args.get("created") == "1")


# ----------------------------------------------------------------------- edit
@bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@permission_required("can_edit_customer")
def edit(customer_id):
    conn = get_db().connect()
    try:
        customer = conn.query_one("SELECT * FROM customers WHERE id=%s", (customer_id,))
        if not customer:
            abort(404)
        if request.method == "POST":
            data = _customer_payload(request.form, request.files)
            errors = validate_customer(data, conn, customer_id=customer_id)
            photo_name = signature_name = None
            if not errors:
                try:
                    photo_name, signature_name = _save_media(
                        customer_id, request.form, request.files, conn, customer)
                except ValueError as exc:
                    errors.append(str(exc))
            if not errors:
                with get_db().transaction() as tx:
                    tx.execute(
                        "UPDATE customers SET surname=%s, first_name=%s,"
                        " father_surname=%s, father_name=%s, mother_surname=%s,"
                        " mother_name=%s, dob=%s, gender=%s, marital_status=%s,"
                        " mobile=%s, alt_mobile=%s, email=%s, street=%s, society=%s,"
                        " colony=%s, house_no=%s, village=%s, taluka=%s,"
                        " post_office=%s, district=%s, state=%s, pincode=%s,"
                        " aadhaar_name=%s, aadhaar_number=%s, photo_path=%s,"
                        " signature_path=%s, notes=%s, updated_at=%s, updated_by=%s"
                        " WHERE id=%s",
                        (data["surname"], data["first_name"], data["father_surname"],
                         data["father_name"], data["mother_surname"],
                         data["mother_name"], data["dob"], data["gender"] or None,
                         data["marital_status"] or None, data["mobile"],
                         data["alt_mobile"] or None, data["email"] or None,
                         data["street"], data["society"], data["colony"],
                         data["house_no"], data["village"], data["taluka"],
                         data["post_office"], data["district"], data["state"],
                         data["pincode"], data["aadhaar_name"],
                         data["aadhaar_number"] or None, photo_name, signature_name,
                         data["notes"], datetime.now(), g.user["id"], customer_id))
                    write_audit(tx, "customer_update", "customers", customer_id,
                                "Updated customer %s" % customer["customer_code"])
                flash(_("common.saved"), "success")
                return redirect(url_for("customers.detail", customer_id=customer_id))
            for message in errors:
                flash(message, "danger")
            customer = dict(customer)
            customer.update({k: v for k, v in data.items() if v is not None})
            customer["dob"] = data["dob"]
            return render_template("customers/form.html", customer=customer,
                                   mode="edit", errors=errors, customer_id=customer_id,
                                   genders=GENDERS, marital=MARITAL_STATUSES,
                                   age=calc_age(data["dob"]))

        return render_template("customers/form.html", customer=customer, mode="edit",
                               errors=[], customer_id=customer_id, genders=GENDERS,
                               marital=MARITAL_STATUSES,
                               age=calc_age(customer["dob"]))
    finally:
        conn.close()


# ------------------------------------------------------------ archive/restore
@bp.route("/<int:customer_id>/archive", methods=["POST"])
@login_required
@admin_required
def archive(customer_id):
    reason = clean_text(request.form.get("reason"), 255) or "Archived by admin"
    with get_db().transaction() as tx:
        customer = tx.query_one("SELECT customer_code FROM customers WHERE id=%s",
                                (customer_id,))
        if not customer:
            abort(404)
        tx.execute(
            "UPDATE customers SET status='archived', archived_at=%s, archived_by=%s,"
            " archive_reason=%s, updated_at=%s WHERE id=%s",
            (datetime.now(), g.user["id"], reason, datetime.now(), customer_id))
        write_audit(tx, "customer_archive", "customers", customer_id,
                    "Archived %s - %s" % (customer["customer_code"], reason))
    flash("Customer archived. All history is preserved.", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/<int:customer_id>/restore", methods=["POST"])
@login_required
@admin_required
def restore(customer_id):
    with get_db().transaction() as tx:
        tx.execute("UPDATE customers SET status='active', archived_at=NULL,"
                   " archived_by=NULL, archive_reason=NULL, updated_at=%s WHERE id=%s",
                   (datetime.now(), customer_id))
        write_audit(tx, "customer_restore", "customers", customer_id,
                    "Customer restored from archive")
    flash("Customer restored.", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


# ------------------------------------------------------------------ documents
@bp.route("/<int:customer_id>/documents", methods=["POST"])
@login_required
@permission_required("can_edit_customer")
def add_document(customer_id):
    conn = get_db().connect()
    try:
        customer = conn.query_one("SELECT customer_code FROM customers WHERE id=%s",
                                  (customer_id,))
        if not customer:
            abort(404)
        doc_name = clean_text(request.form.get("doc_name"), 120)
        doc_type = clean_text(request.form.get("doc_type"), 60)
        is_protected = 1 if request.form.get("is_protected") else 0
        doc_password = request.form.get("doc_password") or ""
        file_storage = request.files.get("file")

        if not doc_name:
            flash("Document name is required.", "danger")
            return redirect(url_for("customers.detail", customer_id=customer_id))
        if not file_storage or not file_storage.filename:
            flash("Please choose a file to upload.", "danger")
            return redirect(url_for("customers.detail", customer_id=customer_id))
        if is_protected and len(doc_password) < 4:
            flash("A document password of at least 4 characters is required.", "danger")
            return redirect(url_for("customers.detail", customer_id=customer_id))
        try:
            filename = save_document(file_storage,
                                     os.path.join(UPLOAD_ROOT, "documents"),
                                     current_app.config["DOCUMENT_MAX_BYTES"],
                                     current_app.config["ALLOWED_DOC_EXT"])
        except ValueError as exc:
            flash(str(exc), "danger")
            return redirect(url_for("customers.detail", customer_id=customer_id))

        with get_db().transaction() as tx:
            tx.execute(
                "INSERT INTO customer_documents (customer_id, doc_name, doc_type,"
                " file_path, is_protected, password_hash, created_at, created_by)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (customer_id, doc_name, doc_type, filename, is_protected,
                 hash_password(doc_password) if is_protected else None,
                 datetime.now(), g.user["id"]))
            write_audit(tx, "document_add", "customers", customer_id,
                        "Document '%s' added" % doc_name)
        flash("Document uploaded.", "success")
    finally:
        conn.close()
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_document(doc_id):
    with get_db().transaction() as tx:
        doc = tx.query_one("SELECT * FROM customer_documents WHERE id=%s", (doc_id,))
        if not doc:
            abort(404)
        tx.execute("DELETE FROM customer_documents WHERE id=%s", (doc_id,))
        write_audit(tx, "document_delete", "customers", doc["customer_id"],
                    "Document '%s' deleted" % doc["doc_name"])
        customer_id = doc["customer_id"]
    flash("Document record deleted.", "success")
    return redirect(url_for("customers.detail", customer_id=customer_id))


@bp.route("/documents/<int:doc_id>/file", methods=["GET", "POST"])
@login_required
def download_document(doc_id):
    conn = get_db().connect()
    try:
        doc = conn.query_one("SELECT * FROM customer_documents WHERE id=%s", (doc_id,))
    finally:
        conn.close()
    if not doc:
        abort(404)
    if doc["is_protected"]:
        if request.method != "POST":
            return render_template("customers/doc_password.html", doc=doc)
        if not verify_password(doc["password_hash"], request.form.get("password") or ""):
            flash("Wrong document password.", "danger")
            return render_template("customers/doc_password.html", doc=doc)
    return send_from_directory(os.path.join(UPLOAD_ROOT, "documents"),
                               doc["file_path"], as_attachment=True,
                               download_name=doc["file_path"])


# ------------------------------------------------------------------ utilities
@bp.app_template_filter("mask_aadhaar")
def _mask_aadhaar(value):
    return mask_aadhaar(value)


@bp.app_template_filter("mobile_fmt")
def _mobile_fmt(value):
    return format_mobile(value)


@bp.route("/quick-search")
@login_required
def quick_search():
    """Small JSON endpoint used by the application form customer picker."""
    from flask import jsonify
    q = clean_text(request.args.get("q"), 40)
    if len(q) < 2:
        return jsonify(results=[])
    conn = get_db().connect()
    try:
        like = "%" + q + "%"
        rows = conn.query(
            "SELECT id, customer_code, first_name, surname, mobile, village"
            " FROM customers WHERE status='active' AND (customer_code LIKE %s"
            " OR mobile LIKE %s OR first_name LIKE %s OR surname LIKE %s)"
            " ORDER BY id DESC LIMIT 12", (like, like, like, like))
    finally:
        conn.close()
    return jsonify(results=[{
        "id": r["id"], "code": r["customer_code"],
        "name": "%s %s" % (r["first_name"], r["surname"]),
        "mobile": format_mobile(r["mobile"]), "village": r["village"] or "",
    } for r in rows])
