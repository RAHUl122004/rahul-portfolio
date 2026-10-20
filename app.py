import json
import logging
import os
import re
import sqlite3
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, flash, jsonify, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash


BASE_DIR = Path(__file__).resolve().parent
DATABASE = Path(os.getenv("DATABASE_PATH", BASE_DIR / "messages.db"))

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "local-dev-key-change-before-deployment")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("HTTPS_ONLY") == "1",
    MAX_CONTENT_LENGTH=16 * 1024,
)

logger = logging.getLogger(__name__)
recent_attempts = defaultdict(list)


def connect_db():
    connection = sqlite3.connect(DATABASE)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with connect_db() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(24).hex()

    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


def valid_csrf():
    supplied = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
    return bool(supplied) and supplied == session.get("csrf_token")


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect(url_for("login"))

        return function(*args, **kwargs)

    return wrapper


def send_to_formspree(name, email, message):
    form_id = os.getenv("FORMSPREE_FORM_ID", "").strip()

    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", form_id):
        raise ValueError("Formspree form ID is not configured")

    payload = json.dumps(
        {
            "name": name,
            "email": email,
            "message": message,
        }
    ).encode("utf-8")

    outgoing = Request(
        f"https://formspree.io/f/{form_id}",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    with urlopen(outgoing, timeout=10) as response:
        body = response.read(4096)

        if not body:
            return

        try:
            result = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return

        if isinstance(result, dict) and result.get("ok") is False:
            raise RuntimeError("Contact provider rejected the submission")


@app.get("/")
def home():
    return render_template("index.html")


@app.post("/contact")
def contact():
    if not valid_csrf():
        return jsonify(error="Your session expired. Refresh the page and try again."), 403

    if request.form.get("website"):
        return jsonify(message="Thanks for reaching out!")

    ip = request.remote_addr or "unknown"
    now = time.monotonic()

    recent_attempts[ip] = [
        timestamp for timestamp in recent_attempts[ip] if now - timestamp < 3600
    ]

    if len(recent_attempts[ip]) >= 5:
        return jsonify(error="Too many messages. Please try again later."), 429

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    message = request.form.get("message", "").strip()

    is_valid = (
        2 <= len(name) <= 80
        and 5 <= len(email) <= 254
        and "@" in email
        and "." in email.split("@")[-1]
        and 10 <= len(message) <= 2000
    )

    if not is_valid:
        return jsonify(
            error="Please enter a valid name, email and a message of at least 10 characters."
        ), 400

    if os.getenv("CONTACT_DELIVERY", "").lower().strip() == "local":
        with connect_db() as connection:
            connection.execute(
                """
                INSERT INTO messages (name, email, message, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    name,
                    email,
                    message,
                    datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                ),
            )

        recent_attempts[ip].append(now)
        return jsonify(message="Test message stored in local SQLite (not emailed).")

    try:
        send_to_formspree(name, email, message)

    except ValueError:
        logger.error("Formspree is not configured")
        return jsonify(
            error="Contact form is not configured yet. Please use the Gmail link instead."
        ), 503

    except HTTPError as error:
        logger.warning("Contact service returned HTTP %s", error.code)

        if error.code == 429:
            return jsonify(
                error="The contact service is busy. Please wait a little and try again."
            ), 429

        return jsonify(
            error="The contact service could not accept this message. Please try again later or email me directly."
        ), 502

    except (URLError, TimeoutError, OSError, RuntimeError) as error:
        logger.warning("Contact service error: %s", type(error).__name__)
        return jsonify(
            error="Could not send your message right now. Please try again later or email me directly."
        ), 502

    recent_attempts[ip].append(now)
    return jsonify(
        message="Message submitted! Thank you — I will check my inbox and get back to you."
    )


@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if not valid_csrf():
            flash("Invalid session. Refresh and try again.")
        else:
            password_hash = os.getenv("ADMIN_PASSWORD_HASH", "")
            password = request.form.get("password", "")

            if password_hash and check_password_hash(password_hash, password):
                session.clear()
                session["admin_logged_in"] = True
                return redirect(url_for("inbox"))

            flash("Incorrect password, or admin access is not configured.")

    return render_template("login.html")


@app.get("/admin")
@admin_required
def inbox():
    with connect_db() as connection:
        messages = connection.execute(
            """
            SELECT id, name, email, message, created_at
            FROM messages
            ORDER BY id DESC
            LIMIT 200
            """
        ).fetchall()

    return render_template("admin.html", messages=messages)


@app.post("/admin/logout")
@admin_required
def logout():
    if not valid_csrf():
        return "Invalid session", 403

    session.clear()
    return redirect(url_for("home"))


@app.post("/admin/delete/<int:message_id>")
@admin_required
def delete_message(message_id):
    if not valid_csrf():
        return "Invalid session", 403

    with connect_db() as connection:
        connection.execute("DELETE FROM messages WHERE id = ?", (message_id,))

    return redirect(url_for("inbox"))


init_db()


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
