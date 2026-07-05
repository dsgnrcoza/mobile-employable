"""
auth.py
-------
Account creation, login, logout, and password recovery via a security
question (no email/SMS service involved — see db.py's docstring for
why a security question is the right tradeoff for this app).

SESSION MODEL:
Flask's built-in `session` object is used to remember who's logged in
between requests. It's a signed cookie stored in the user's browser —
"signed" means Flask cryptographically seals it with app.secret_key so
the browser can hold it but can't forge or edit it (tampering with the
cookie invalidates the signature and Flask rejects it). The cookie
itself only contains the user's id, never their password or password
hash. This is the standard, secure way to do "stay logged in" on the
web — it is NOT the same thing as the old desktop app's idea of
"caching to disk," and that's intentional: a session is short-lived
and tied to one browser, while account data lives safely in the
database, scoped to that one account, regardless of which browser or
device logs in.

PASSWORD RULES:
Minimum 8 characters. This is intentionally simple rather than forcing
arbitrary complexity rules (one uppercase, one symbol, etc.) which
security research has increasingly found pushes people toward
predictable patterns (Password1!) rather than actually stronger
passwords. Length matters more than forced complexity.
"""

import re
from functools import wraps
from flask import session, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import db

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")


class AuthError(Exception):
    """Raised for any user-facing validation failure during signup/login/reset."""


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise AuthError("Please enter a username.")
    if not USERNAME_PATTERN.match(username):
        raise AuthError(
            "Usernames must be 3-32 characters: letters, numbers, underscores, "
            "dots, or hyphens only."
        )
    return username


def validate_password(password: str) -> str:
    if not password or len(password) < 8:
        raise AuthError("Password must be at least 8 characters long.")
    return password


def validate_security_answer(answer: str) -> str:
    answer = (answer or "").strip()
    if not answer:
        raise AuthError("Please provide an answer to your security question.")
    return answer.lower()  # normalize so answer comparison isn't case-sensitive


def validate_full_name(full_name: str) -> str:
    full_name = (full_name or "").strip()
    if not full_name:
        raise AuthError("Please enter your name.")
    if len(full_name) > 80:
        raise AuthError("Name is too long.")
    return full_name


def signup(username, password, confirm_password, security_question, security_answer, full_name=""):
    """
    Creates a new account. Raises AuthError with a user-facing message
    on any problem (duplicate username, weak password, mismatch, etc.)
    On success, returns the new user's id and logs them in immediately.

    full_name is what the user typed on the signup screen — this is
    deliberately the source of truth for the name shown around the app
    (e.g. the dashboard header), not whatever name analyzer.py later
    extracts from an uploaded CV. pipeline.py's post-analysis profile
    fill only ever sets full_name when it's still empty, so providing
    it here permanently takes priority over the AI-guessed version.
    """
    username = validate_username(username)
    validate_password(password)
    if password != confirm_password:
        raise AuthError("Passwords do not match.")
    full_name = validate_full_name(full_name)

    security_question = (security_question or "").strip()
    if not security_question:
        raise AuthError("Please choose a security question.")
    security_answer = validate_security_answer(security_answer)

    if db.get_user_by_username(username):
        raise AuthError("That username is already taken. Please choose another.")

    password_hash = generate_password_hash(password)
    answer_hash = generate_password_hash(security_answer)

    user_id = db.create_user(username, password_hash, security_question, answer_hash, full_name)
    log_in_user(user_id)
    return user_id


def login(username, password):
    """
    Verifies credentials. Raises AuthError with a generic message on
    failure — deliberately not saying WHICH part was wrong (unknown
    username vs wrong password), since that distinction would let an
    attacker enumerate which usernames exist on the system.
    """
    username = (username or "").strip()
    user = db.get_user_by_username(username)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        raise AuthError("Incorrect username or password.")
    log_in_user(user["id"])
    return user["id"]


def get_security_question(username):
    """Returns the stored security question for a username, or None if no such user."""
    user = db.get_user_by_username((username or "").strip())
    return user["security_question"] if user else None


def reset_password(username, security_answer, new_password, confirm_password):
    """
    Verifies the security answer, then sets a new password. Raises
    AuthError on any mismatch.
    """
    username = (username or "").strip()
    user = db.get_user_by_username(username)
    if not user:
        raise AuthError("We couldn't verify that account. Please check the username.")

    answer = validate_security_answer(security_answer)
    if not check_password_hash(user["security_answer_hash"], answer):
        raise AuthError("That answer doesn't match what we have on file.")

    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("Passwords do not match.")

    db.update_password(user["id"], generate_password_hash(new_password))


def log_in_user(user_id):
    session.clear()
    session["user_id"] = user_id
    session.permanent = True


def log_out_user():
    session.clear()


def current_user():
    """
    Returns the full user row (as a dict) for whoever is logged in
    this session, or None if no one is logged in. Cached on flask's
    `g` object for the duration of one request so multiple calls in
    the same request don't each re-query the database.
    """
    if "user_id" not in session:
        return None
    if not hasattr(g, "_cached_user"):
        g._cached_user = db.get_user_by_id(session["user_id"])
    return g._cached_user


def login_required(view_func):
    """
    Decorator for Flask routes that should only be reachable while
    logged in. Redirects to the login page otherwise, and remembers
    nothing about the attempted page (no open-redirect surface).
    """

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login_page"))
        return view_func(*args, **kwargs)

    return wrapped
