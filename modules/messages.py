"""
SY CSC CENTRE - MSJ message system
==================================
Admin <-> Operator messaging with read/unread tracking.

* Admin can message one operator or every operator (broadcast = one row per
  recipient, so each operator has an independent read state).
* Operators can message / reply to the admin only, and only when the
  "Reply to Messages" permission is switched on.
* Message bodies are escaped by the template engine before display.
"""
from datetime import datetime

from flask import (Blueprint, abort, flash, g, redirect, render_template,
                   request, session, url_for)

from db import get_db
from modules.i18n import translate
from modules.security import (current_user, has_permission, login_required,
                              write_audit)
from modules.utils import clean_text, parse_int

bp = Blueprint("messages", __name__, url_prefix="/messages")


def _(key):
    return translate(key, session.get("lang", "en"))


def unread_count(conn, user_id):
    return conn.scalar("SELECT COUNT(*) FROM messages WHERE recipient_id=%s"
                       " AND read_at IS NULL", (user_id,), default=0)


@bp.route("/")
@login_required
def index():
    user = current_user()
    conn = get_db().connect()
    try:
        inbox = conn.query(
            "SELECT m.id, m.subject, m.created_at, m.read_at, u.name AS sender_name,"
            " u.role AS sender_role FROM messages m JOIN users u ON u.id=m.sender_id"
            " WHERE m.recipient_id=%s ORDER BY m.id DESC LIMIT 100", (user["id"],))
        sent = conn.query(
            "SELECT m.id, m.subject, m.created_at, m.read_at, u.name AS recipient_name,"
            " m.is_broadcast FROM messages m JOIN users u ON u.id=m.recipient_id"
            " WHERE m.sender_id=%s ORDER BY m.id DESC LIMIT 100", (user["id"],))
        unread = unread_count(conn, user["id"])
    finally:
        conn.close()
    return render_template("messages/index.html", inbox=inbox, sent=sent,
                           unread=unread, can_reply=has_permission("can_reply_messages"))


@bp.route("/new", methods=["GET", "POST"])
@login_required
def compose():
    user = current_user()
    if not has_permission("can_reply_messages"):
        abort(403)
    conn = get_db().connect()
    try:
        if user["role"] == "admin":
            operators = conn.query(
                "SELECT id, name, username FROM users WHERE role='operator'"
                " AND status='active' ORDER BY name")
        else:
            operators = conn.query(
                "SELECT id, name, username FROM users WHERE role='admin'"
                " AND status='active' ORDER BY name")

        if request.method == "POST":
            subject = clean_text(request.form.get("subject"), 160)
            body = clean_text(request.form.get("body"), 5000)
            target = request.form.get("recipient_id")
            errors = []
            if not subject:
                errors.append("Subject is required.")
            if not body:
                errors.append("Message cannot be empty.")
            recipients = []
            if target == "all" and user["role"] == "admin":
                recipients = [row["id"] for row in operators]
                if not recipients:
                    errors.append("There are no active operators to message.")
            else:
                recipient_id = parse_int(target)
                valid = [row["id"] for row in operators]
                if recipient_id not in valid:
                    errors.append("Please choose a valid recipient.")
                else:
                    recipients = [recipient_id]

            if not errors:
                with get_db().transaction() as tx:
                    for recipient_id in recipients:
                        tx.execute(
                            "INSERT INTO messages (sender_id, recipient_id, parent_id,"
                            " subject, body, is_broadcast, created_at)"
                            " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (user["id"], recipient_id, None, subject, body,
                             1 if target == "all" else 0, datetime.now()))
                    write_audit(tx, "message_send", "messages", None,
                                "Message '%s' sent to %d recipient(s)"
                                % (subject, len(recipients)))
                flash(_("msg.sent_ok"), "success")
                return redirect(url_for("messages.index"))
            for message in errors:
                flash(message, "danger")
            return render_template("messages/compose.html", operators=operators,
                                   form=request.form, is_admin=user["role"] == "admin")
        return render_template("messages/compose.html", operators=operators, form={},
                               is_admin=user["role"] == "admin")
    finally:
        conn.close()


@bp.route("/<int:message_id>")
@login_required
def view(message_id):
    user = current_user()
    conn = get_db().connect()
    try:
        message = conn.query_one(
            "SELECT m.*, su.name AS sender_name, ru.name AS recipient_name"
            " FROM messages m JOIN users su ON su.id=m.sender_id"
            " JOIN users ru ON ru.id=m.recipient_id WHERE m.id=%s", (message_id,))
        if not message:
            abort(404)
        if user["id"] not in (message["sender_id"], message["recipient_id"]):
            abort(403)
        if message["recipient_id"] == user["id"] and not message["read_at"]:
            with get_db().transaction() as tx:
                tx.execute("UPDATE messages SET read_at=%s WHERE id=%s",
                           (datetime.now(), message_id))
        thread = conn.query(
            "SELECT m.id, m.subject, m.body, m.created_at, m.read_at,"
            " su.name AS sender_name, m.sender_id, m.recipient_id FROM messages m"
            " JOIN users su ON su.id=m.sender_id"
            " WHERE m.id=%s OR m.parent_id=%s ORDER BY m.id",
            (message["parent_id"] or message_id, message["parent_id"] or message_id))
    finally:
        conn.close()
    return render_template("messages/view.html", message=message, thread=thread,
                           can_reply=has_permission("can_reply_messages"))


@bp.route("/<int:message_id>/reply", methods=["POST"])
@login_required
def reply(message_id):
    user = current_user()
    if not has_permission("can_reply_messages"):
        abort(403)
    body = clean_text(request.form.get("body"), 5000)
    if not body:
        flash("Reply cannot be empty.", "danger")
        return redirect(url_for("messages.view", message_id=message_id))
    conn = get_db().connect()
    try:
        original = conn.query_one("SELECT * FROM messages WHERE id=%s", (message_id,))
        if not original:
            abort(404)
        if user["id"] not in (original["sender_id"], original["recipient_id"]):
            abort(403)
        recipient_id = original["sender_id"] if original["recipient_id"] == user["id"] \
            else original["recipient_id"]
        parent = original["parent_id"] or original["id"]
    finally:
        conn.close()
    with get_db().transaction() as tx:
        tx.execute(
            "INSERT INTO messages (sender_id, recipient_id, parent_id, subject, body,"
            " is_broadcast, created_at) VALUES (%s,%s,%s,%s,%s,0,%s)",
            (user["id"], recipient_id, parent,
             "Re: " + original["subject"], body, datetime.now()))
        write_audit(tx, "message_reply", "messages", message_id, "Reply sent")
    flash(_("msg.sent_ok"), "success")
    return redirect(url_for("messages.view", message_id=message_id))
