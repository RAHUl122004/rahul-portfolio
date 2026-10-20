import json
import logging
import os
import re
import time
from collections import defaultdict
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from flask import Flask, jsonify, render_template, request, session


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "local-development-secret-change-for-production")
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.getenv("HTTPS_ONLY") == "1",
    MAX_CONTENT_LENGTH=16 * 1024,
)

logger = logging.getLogger(__name__)
message_attempts = defaultdict(list)


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = os.urandom(24).hex()

    return session["csrf_token"]


app.jinja_env.globals["csrf_token"] = csrf_token


def valid_csrf():
    submitted = request.form.get("csrf_token", "")
    return bool(submitted) and submitted == session.get("csrf_token")


def send_to_formspree(name, email, message):
    form_id = os.getenv("FORMSPREE_FORM_ID", "").strip()

    if not re.fullmatch(r"[A-Za-z0-9]{6,32}", form_id):
        raise ValueError("Formspree form ID is not configured")

    payload = json.dumps({
        "name": name,
        "email": email,
        "message": message,
    }).encode("utf-8")

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
        response_body = response.read(4096)

        if response_body:
            try:
                result = json.loads(response_body.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                result = None

            if isinstance(result, dict) and result.get("ok") is False:
                raise RuntimeError("Formspree rejected the submission")


@app.get("/")
def home():
    return render_template("index.html")


@app.get("/about")
def about():
    return render_template("about.html")


@app.get("/projects")
def projects():
    return render_template("projects.html")


@app.get("/experience")
def experience():
    return render_template("experience.html")


@app.get("/resume")
def resume():
    return render_template("resume.html")


@app.get("/contact")
def contact_page():
    return render_template("contact.html")


@app.post("/contact")
def contact():
    if not valid_csrf():
        return jsonify(error="Your session expired. Refresh the page and try again."), 403

    if request.form.get("website"):
        return jsonify(message="Thanks for reaching out!")

    address = request.remote_addr or "unknown"
    now = time.monotonic()
    message_attempts[address] = [
        timestamp for timestamp in message_attempts[address]
        if now - timestamp < 3600
    ]

    if len(message_attempts[address]) >= 5:
        return jsonify(error="Too many messages. Please try again later."), 429

    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip()
    message = request.form.get("message", "").strip()

    valid = (
        2 <= len(name) <= 80
        and 5 <= len(email) <= 254
        and "@" in email
        and "." in email.rsplit("@", 1)[-1]
        and 10 <= len(message) <= 2000
    )

    if not valid:
        return jsonify(error="Please enter a valid name, email and message (10–2000 characters)."), 400

    try:
        send_to_formspree(name, email, message)
    except ValueError:
        logger.error("FORMSPREE_FORM_ID is not configured")
        return jsonify(error="Contact form is unavailable. Please use the email link instead."), 503
    except HTTPError as error:
        logger.warning("Formspree HTTP error: %s", error.code)
        if error.code == 429:
            return jsonify(error="Too many requests. Please try again later."), 429
        return jsonify(error="Could not send your message. Please try again later."), 502
    except (URLError, TimeoutError, OSError, RuntimeError) as error:
        logger.warning("Formspree delivery error: %s", type(error).__name__)
        return jsonify(error="Could not send your message. Please email me directly."), 502

    message_attempts[address].append(now)
    return jsonify(message="Message sent successfully! Thank you for reaching out.")


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=5000)
