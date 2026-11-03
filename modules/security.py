"""
SY CSC CENTRE - security helpers
================================
* password hashing (Werkzeug PBKDF2, never plain text)
* CSRF tokens
* login / role / permission decorators
* safe file upload handling with image compression
"""
import hmac
import os
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import db as dbmod

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:                                # pragma: no cover
    PIL_AVAILABLE = False


# ------------------------------------------------------------------ password
def hash_password(password):
    return generate_password_hash(password or "", method="pbkdf2:sha256")


def verify_password(password_hash, password):
    try:
        return check_password_hash(password_hash or "", password or "")
    except Exception:
        return False


def password_problem(password):
    """Returns an error string, or None when the password is acceptable."""
    if not password or len(password) < 8:
        return "Password must be at least 8 characters long."
    if not any(c.isalpha() for c in password):
        return "Password must contain at least one letter."
    if not any(c.isdigit() for c in password):
        return "Password must contain at least one number."
    return None


# ---------------------------------------------------------------------- CSRF
def csrf_token():
    token = session.get("_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["_csrf_token"] = token
    return token


def csrf_ok():
    """Validate the CSRF token attached to a state changing request."""
    from flask import current_app
    if not current_app.config.get("CSRF_ENABLED", True):
        return True
    sent = (request.form.get("csrf_token")
            or request.headers.get("X-CSRFToken") or "")
    expected = session.get("_csrf_token") or ""
    if not expected or not sent:
        return False
    return hmac.compare_digest(str(sent), str(expected))


# -------------------------------------------------------------------- users
def current_user():
    return g.get("user")


def has_permission(permission):
    user = current_user()
    if not user:
        return False
    if user["role"] == "admin":
        return True
    perms = g.get("permissions") or {}
    return bool(int(perms.get(permission) or 0))


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user():
            if request.method == "GET":
                return redirect(url_for("auth.login", next=request.path))
            return redirect(url_for("auth.login"))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = current_user()
        if not user:
            return redirect(url_for("auth.login"))
        if user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def permission_required(permission):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = current_user()
            if not user:
                return redirect(url_for("auth.login"))
            if not has_permission(permission):
                flash("You do not have permission for this action.", "danger")
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


# ------------------------------------------------------------- file uploads
def allowed_extension(filename, allowed):
    if not filename or "." not in filename:
        return False
    return filename.rsplit(".", 1)[1].lower() in allowed


def _safe_stem(filename):
    name = secure_filename(filename or "file")
    stem = os.path.splitext(name)[0][:40] or "file"
    return stem


def save_image(file_storage, folder, max_bytes, allowed_ext, max_pixels=1600):
    """
    Validate + compress an uploaded image, return the relative path or None.
    Raises ValueError with a friendly message when the file is not acceptable.
    """
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_extension(file_storage.filename, allowed_ext):
        raise ValueError("Only these image types are allowed: %s"
                         % ", ".join(sorted(allowed_ext)))
    if not PIL_AVAILABLE:                        # pragma: no cover
        raise ValueError("Image library (Pillow) is not installed.")

    data = file_storage.read()
    if len(data) > 6 * 1024 * 1024:
        raise ValueError("Image is too large (maximum 6 MB before compression).")

    import io
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception:
        raise ValueError("The uploaded file is not a readable image.")

    if image.mode in ("RGBA", "LA", "P"):
        image = image.convert("RGB")
    if max(image.size) > max_pixels:
        image.thumbnail((max_pixels, max_pixels), Image.LANCZOS)

    filename = "%s_%s.jpg" % (datetime_stamp(), _safe_stem(file_storage.filename))
    target_dir = os.path.join(folder)
    if not os.path.isdir(target_dir):
        os.makedirs(target_dir)
    full_path = os.path.join(target_dir, filename)

    quality = 85
    while quality >= 30:
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=quality, optimize=True)
        if buffer.tell() <= max_bytes or quality == 30:
            with open(full_path, "wb") as fh:
                fh.write(buffer.getvalue())
            break
        quality -= 10
    return filename


def save_document(file_storage, folder, max_bytes, allowed_ext):
    """Store a document as-is (validated by extension and size)."""
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_extension(file_storage.filename, allowed_ext):
        raise ValueError("This file type is not allowed.")
    data = file_storage.read()
    if len(data) > max_bytes:
        raise ValueError("File is too large (maximum %.1f MB)." % (max_bytes / 1048576.0))
    if not data:
        raise ValueError("The uploaded file is empty.")
    filename = "%s_%s.%s" % (datetime_stamp(),
                             _safe_stem(file_storage.filename),
                             file_storage.filename.rsplit(".", 1)[1].lower())
    if not os.path.isdir(folder):
        os.makedirs(folder)
    with open(os.path.join(folder, filename), "wb") as fh:
        fh.write(data)
    return filename


def datetime_stamp():
    from datetime import datetime
    return datetime.now().strftime("%Y%m%d%H%M%S")


# --------------------------------------------------------------------- audit
def write_audit(conn, action, entity=None, entity_id=None, details=None):
    """Record one audit row. Never raises - auditing must not break a request."""
    try:
        from datetime import datetime
        user = current_user()
        conn.execute(
            "INSERT INTO audit_logs (user_id, username, action, entity, entity_id,"
            " details, ip_address, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (user["id"] if user else None,
             user["username"] if user else None,
             action, entity, str(entity_id) if entity_id is not None else None,
             (details or "")[:2000],
             (request.remote_addr if request else "") or "",
             datetime.now()))
    except Exception:
        pass
