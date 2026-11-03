"""
SY CSC CENTRE - authentication, session handling and profile
============================================================
"""
from datetime import datetime

from flask import (Blueprint, flash, g, redirect, render_template, request,
                   session, url_for)

import db as dbmod
from db import get_db
from modules.i18n import translate
from modules.security import (current_user, hash_password, login_required,
                              password_problem, verify_password, write_audit)
from modules.utils import clean_text

bp = Blueprint("auth", __name__)


def _(key):
    return translate(key, session.get("lang", "en"))


# ---------------------------------------------------------------------------
def load_logged_in_user():
    """Populate g.user / g.permissions for the current request."""
    g.user = None
    g.permissions = {}
    user_id = session.get("user_id")
    if not user_id:
        return
    conn = get_db().connect()
    try:
        row = conn.query_one(
            "SELECT id, name, username, role, mobile, email, address, status,"
            " last_login, created_at FROM users WHERE id=%s", (user_id,))
        if not row or row["status"] != "active":
            session.clear()
            return
        g.user = row
        if row["role"] == "operator":
            perms = conn.query_one(
                "SELECT can_add_customer, can_edit_customer, can_add_application,"
                " can_update_status, can_view_reports, can_reply_messages,"
                " can_access_archive, can_override_duplicate"
                " FROM operator_permissions WHERE user_id=%s", (user_id,))
            g.permissions = perms or {}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("dashboard.home"))

    if request.method == "POST":
        username = clean_text(request.form.get("username"), 60).lower()
        password = request.form.get("password") or ""
        conn = get_db().connect()
        try:
            row = conn.query_one("SELECT * FROM users WHERE username=%s", (username,))
            if not row or not verify_password(row["password_hash"], password):
                write_audit(conn, "login_failed", "users", username,
                            "Failed login attempt for username '%s'" % username)
                conn.commit()
                flash(_("login.invalid"), "danger")
                return render_template("login.html", username=username)
            if row["status"] != "active":
                flash(_("login.inactive"), "danger")
                return render_template("login.html", username=username)

            # session fixation protection: brand new session after login
            session.clear()
            session["user_id"] = row["id"]
            session["lang"] = session.get("lang", "gu")
            session.permanent = True
            conn.execute("UPDATE users SET last_login=%s WHERE id=%s",
                         (datetime.now(), row["id"]))
            write_audit(conn, "login", "users", row["id"], "User logged in")
            conn.commit()
        finally:
            conn.close()
        return redirect(url_for("dashboard.home"))

    return render_template("login.html", username="")


@bp.route("/logout")
def logout():
    user = current_user()
    if user:
        conn = get_db().connect()
        try:
            write_audit(conn, "logout", "users", user["id"], "User logged out")
            conn.commit()
        finally:
            conn.close()
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/language/<code>")
def set_language(code):
    if code in ("gu", "en"):
        session["lang"] = code
    target = request.referrer or url_for("dashboard.home")
    if "://" in target:
        # only redirect back inside this application
        from urllib.parse import urlparse
        parsed = urlparse(target)
        target = parsed.path + ("?" + parsed.query if parsed.query else "")
    return redirect(target or url_for("dashboard.home"))


# ------------------------------------------------------------------ profile
@bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        name = clean_text(request.form.get("name"), 120)
        mobile = clean_text(request.form.get("mobile"), 20)
        email = clean_text(request.form.get("email"), 120)
        address = clean_text(request.form.get("address"), 255)
        if not name:
            flash("Name is required.", "danger")
        else:
            conn = get_db().connect()
            try:
                conn.execute(
                    "UPDATE users SET name=%s, mobile=%s, email=%s, address=%s,"
                    " updated_at=%s, updated_by=%s WHERE id=%s",
                    (name, mobile, email, address, datetime.now(), user["id"], user["id"]))
                write_audit(conn, "profile_update", "users", user["id"], "Profile updated")
                conn.commit()
            finally:
                conn.close()
            flash(_("profile.updated"), "success")
            return redirect(url_for("auth.profile"))

    conn = get_db().connect()
    try:
        fresh = conn.query_one("SELECT * FROM users WHERE id=%s", (user["id"],))
    finally:
        conn.close()
    return render_template("profile.html", profile_user=fresh)


@bp.route("/profile/password", methods=["POST"])
@login_required
def change_password():
    user = current_user()
    current = request.form.get("current_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm_password") or ""

    conn = get_db().connect()
    try:
        row = conn.query_one("SELECT password_hash FROM users WHERE id=%s", (user["id"],))
        if not row or not verify_password(row["password_hash"], current):
            flash("Current password is wrong.", "danger")
            return redirect(url_for("auth.profile"))
        problem = password_problem(new)
        if problem:
            flash(problem, "danger")
            return redirect(url_for("auth.profile"))
        if new != confirm:
            flash("New password and confirmation do not match.", "danger")
            return redirect(url_for("auth.profile"))
        if new == current:
            flash("New password must be different from the current password.", "danger")
            return redirect(url_for("auth.profile"))

        conn.execute("UPDATE users SET password_hash=%s, updated_at=%s WHERE id=%s",
                     (hash_password(new), datetime.now(), user["id"]))
        write_audit(conn, "password_change", "users", user["id"], "Password changed")
        conn.commit()
    finally:
        conn.close()
    flash("Password changed successfully.", "success")
    return redirect(url_for("auth.profile"))
