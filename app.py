"""
SY CSC CENTRE - application entry point
=======================================
Run with:   python app.py
Then open:  http://127.0.0.1:5000      (this PC)
            http://<HOST-IP>:5000      (Android / other PCs on the same Wi-Fi)

Everything runs offline.  No CDN, no external fonts, no internet required.
"""
import os
import socket
import sys
from datetime import date, datetime
from decimal import Decimal

from flask import (Flask, abort, g, jsonify, redirect, render_template, request,
                   send_from_directory, session, url_for)

import db as dbmod
from config import Config, BASE_DIR
from db import D, get_db
from modules import (accounts, applications, auth, backup, customers, dashboard,
                     i18n, messages, operators, payments, reports, security,
                     services)

# endpoints that do not need a login / CSRF token
PUBLIC_ENDPOINTS = {"auth.login", "auth.set_language", "static", "health"}


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)

    # ------------------------------------------------------------- database
    dbmod.init_app(app)

    # ----------------------------------------------------------- blueprints
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    customers.register(app)                     # has its own register() helper
    app.register_blueprint(services.bp)
    app.register_blueprint(applications.bp)
    app.register_blueprint(payments.bp)
    app.register_blueprint(accounts.bp)
    app.register_blueprint(reports.bp)
    app.register_blueprint(messages.bp)
    app.register_blueprint(operators.bp)
    app.register_blueprint(backup.bp)

    # -------------------------------------------------------------- helpers
    def _current_lang():
        return session.get("lang", app.config["DEFAULT_LANG"])

    def translate_key(key):
        return i18n.translate(key, _current_lang())

    app.jinja_env.globals["_"] = translate_key
    app.jinja_env.globals["csrf_token"] = security.csrf_token
    app.jinja_env.globals["has_permission"] = security.has_permission
    app.jinja_env.globals["languages"] = i18n.LANGUAGES
    app.jinja_env.globals["app_name"] = app.config["APP_NAME"]
    app.jinja_env.globals["currency"] = app.config["CURRENCY"]

    # --------------------------------------------------------------- filters
    @app.template_filter("money")
    def money_filter(value):
        return dbmod.fmt_money(value)

    @app.template_filter("money_raw")
    def money_raw(value):
        return "%.2f" % D(value)

    @app.template_filter("date_fmt")
    def date_filter(value):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:10]
        return value.strftime("%d/%m/%Y")

    @app.template_filter("iso_date")
    def iso_date_filter(value):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:10]
        return value.strftime("%Y-%m-%d")

    @app.template_filter("datetime_fmt")
    def datetime_filter(value):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:16]
        return value.strftime("%d/%m/%Y %H:%M")

    @app.template_filter("time_fmt")
    def time_filter(value):
        if not value:
            return ""
        if isinstance(value, str):
            return value[:5]
        return value.strftime("%H:%M")

    @app.template_filter("age_of")
    def age_filter(value):
        from modules.utils import calc_age
        if isinstance(value, str):
            from modules.utils import parse_date
            value = parse_date(value)
        return calc_age(value) or ""

    @app.context_processor
    def inject_globals():
        user = g.get("user")
        unread = 0
        if user:
            try:
                conn = get_db().connect()
                try:
                    unread = conn.scalar(
                        "SELECT COUNT(*) FROM messages WHERE recipient_id=%s"
                        " AND read_at IS NULL", (user["id"],), default=0)
                finally:
                    conn.close()
            except Exception:
                unread = 0
        return {"user": user, "lang": _current_lang(), "unread_count": unread,
                "now": datetime.now(), "today": date.today()}

    # ------------------------------------------------------------- lifecycle
    @app.before_request
    def before_request():
        auth.load_logged_in_user()
        if request.endpoint in PUBLIC_ENDPOINTS or request.endpoint is None:
            return None
        if not g.get("user"):
            if request.method == "GET":
                return redirect(url_for("auth.login", next=request.path))
            return redirect(url_for("auth.login"))
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if not security.csrf_ok():
                abort(400, description="CSRF token missing or invalid.")
        return None

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline';"
            " script-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'self'")
        return response

    # ---------------------------------------------------------------- routes
    @app.route("/health")
    def health():
        try:
            conn = get_db().connect()
            try:
                conn.scalar("SELECT 1", default=1)
            finally:
                conn.close()
            return jsonify(status="ok", engine=app.config["DB_ENGINE"])
        except Exception as exc:
            return jsonify(status="error", message=str(exc)), 500

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        if not g.get("user"):
            abort(403)
        folder = app.config["UPLOAD_FOLDER"]
        for sub in ("photos", "signatures", "documents"):
            candidate = os.path.join(folder, sub, filename)
            if os.path.isfile(candidate):
                return send_from_directory(os.path.join(folder, sub), filename)
        abort(404)

    @app.route("/")
    def root():
        return redirect(url_for("dashboard.home"))

    # ------------------------------------------------------------- error pages
    @app.errorhandler(400)
    def bad_request(error):
        return render_template("error.html", code=400,
                               title=translate_key("error.403_title"),
                               message=getattr(error, "description", "")
                               or "Bad request."), 400

    @app.errorhandler(403)
    def forbidden(error):
        return render_template("error.html", code=403,
                               title=translate_key("error.403_title"),
                               message=translate_key("error.403")), 403

    @app.errorhandler(404)
    def not_found(error):
        return render_template("error.html", code=404,
                               title=translate_key("error.404_title"),
                               message=translate_key("error.404")), 404

    @app.errorhandler(500)
    def server_error(error):                       # pragma: no cover
        return render_template("error.html", code=500,
                               title=translate_key("error.500_title"),
                               message=translate_key("error.500")), 500

    return app


def lan_addresses():
    """Best-effort list of local IPv4 addresses to show in the console."""
    addresses = []
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            address = info[4][0]
            if address not in addresses and not address.startswith("127."):
                addresses.append(address)
    except Exception:
        pass
    if not addresses:
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.connect(("8.8.8.8", 80))
            addresses.append(probe.getsockname()[0])
            probe.close()
        except Exception:
            pass
    return addresses


def main():
    app = create_app()
    print("=" * 62)
    print("  SY CSC CENTRE  -  offline CSC management system")
    print("  database engine : %s" % app.config["DB_ENGINE"])
    print("  this PC         : http://127.0.0.1:%d" % app.config["PORT"])
    for address in lan_addresses():
        print("  phone / other PC: http://%s:%d" % (address, app.config["PORT"]))
    print("  Stop the server : press CTRL+C in this window")
    print("=" * 62)
    app.run(host=app.config["HOST"], port=app.config["PORT"],
            debug=app.config["DEBUG"], threaded=True)


if __name__ == "__main__":
    main()
