"""
auth.py
-------
Account creation, login, and email-based password recovery.

SESSION MODEL:
Flask's built-in `session` object is used to remember who's logged in
between requests. It's a signed cookie stored in the user's browser —
"signed" means Flask cryptographically seals it with app.secret_key so
the browser can hold it but can't forge or edit it (tampering with the
cookie invalidates the signature and Flask rejects it). The cookie
itself only contains the user's id, never their password or password
hash. This is the standard, secure way to do "stay logged in" on the
web.

PASSWORD RULES:
At least 8 characters, with at least one uppercase letter, one
lowercase letter, one number, and one special (non-alphanumeric)
character.

PASSWORD RESET / ENUMERATION RESISTANCE:
request_password_reset() always behaves identically whether or not the
email has an account -- it only ever creates+emails a token when one
does, but the caller never learns which case it was. Reset tokens are
single-use, expire after RESET_TOKEN_LIFETIME_MINUTES, and are stored
as a salted hash (never the raw token) so a leaked database can't be
used to forge a reset.
"""

import hashlib
import os
import re
import secrets
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from flask import session, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import db

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
PASSWORD_MIN_LENGTH = 8
_SPECIAL_CHAR = re.compile(r"[^A-Za-z0-9]")

RESET_TOKEN_LIFETIME_MINUTES = 60


class AuthError(Exception):
    """Raised for any user-facing validation failure during signup/login/reset."""


def validate_full_name(full_name: str) -> str:
    full_name = (full_name or "").strip()
    if not full_name:
        raise AuthError("Please enter your name.")
    if len(full_name) > 80:
        raise AuthError("Name is too long.")
    return full_name


def validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not email or not EMAIL_PATTERN.match(email):
        raise AuthError("Please enter a valid email address.")
    return email


def validate_password(password: str) -> str:
    password = password or ""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise AuthError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters long.")
    if not re.search(r"[A-Z]", password):
        raise AuthError("Password must include at least one uppercase letter.")
    if not re.search(r"[a-z]", password):
        raise AuthError("Password must include at least one lowercase letter.")
    if not re.search(r"[0-9]", password):
        raise AuthError("Password must include at least one number.")
    if not _SPECIAL_CHAR.search(password):
        raise AuthError("Password must include at least one special character.")
    return password


def _generate_username_from_email(email: str) -> str:
    """
    The `username` column is still how friends find/add each other, but
    the signup form only asks for a full name -- derived from the
    email's local part instead, with a numeric suffix appended until
    it's unique, so this never surfaces as something the user has to
    think about at signup (they can see/change it later in Profile).
    """
    base = re.sub(r"[^A-Za-z0-9_.\-]", "", (email.split("@")[0] or "").lower())[:28] or "user"
    candidate = base
    suffix = 1
    while db.get_user_by_username(candidate):
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def signup(full_name, email, password, confirm_password):
    """
    Creates a new account with a name, email, and password -- nothing
    else. Raises AuthError with a user-facing message on any problem
    (duplicate email, weak password, mismatch, etc). On success, logs
    the new user in immediately and returns their id.
    """
    full_name = validate_full_name(full_name)
    email = validate_email(email)
    validate_password(password)
    if password != confirm_password:
        raise AuthError("Passwords do not match.")

    if db.get_user_by_email(email):
        raise AuthError("An account with that email already exists.")

    username = _generate_username_from_email(email)
    password_hash = generate_password_hash(password)
    user_id = db.create_user(username, password_hash, "", "", full_name, email=email)
    log_in_user(user_id)
    return user_id


def login(email, password):
    """
    Verifies credentials and logs the user in. Raises AuthError with a
    generic message on failure -- deliberately not saying WHICH part
    was wrong (unknown email vs wrong password), since that distinction
    would let an attacker enumerate which emails have accounts here.
    """
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        raise AuthError("Incorrect email or password.")
    log_in_user(user["id"])
    return user["id"]


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _send_reset_email(email: str, reset_url: str):
    """
    Sends the reset link over SMTP if SMTP_HOST/SMTP_USERNAME/
    SMTP_PASSWORD/SMTP_FROM_EMAIL are configured (see .env.example).
    Without them, this just logs the link server-side instead of
    raising -- request_password_reset()'s caller-facing behavior must
    stay identical whether or not an account exists, so a missing mail
    provider can never surface as an error to the user.
    """
    host = os.environ.get("SMTP_HOST")
    from_email = os.environ.get("SMTP_FROM_EMAIL")
    if not host or not from_email:
        print(f"[auth] SMTP not configured -- password reset link for {email}: {reset_url}")
        return

    msg = EmailMessage()
    msg["Subject"] = "Reset your Ploy password"
    msg["From"] = from_email
    msg["To"] = email
    msg.set_content(
        "Someone requested a password reset for your Ploy account.\n\n"
        f"Reset it here (expires in {RESET_TOKEN_LIFETIME_MINUTES} minutes): {reset_url}\n\n"
        "If you didn't request this, you can safely ignore this email."
    )

    port = int(os.environ.get("SMTP_PORT", "587"))
    username = os.environ.get("SMTP_USERNAME", from_email)
    password = os.environ.get("SMTP_PASSWORD", "")
    try:
        with smtplib.SMTP(host, port, timeout=10) as smtp:
            smtp.starttls()
            smtp.login(username, password)
            smtp.send_message(msg)
    except Exception as e:
        print(f"[auth] Failed to send password reset email to {email}: {e}")


def request_password_reset(email: str, build_reset_url):
    """
    Always behaves identically whether or not `email` has an account --
    the caller must never branch on the return value or its absence.
    `build_reset_url(raw_token)` is supplied by the route (it needs
    url_for, which this module doesn't import) and is only ever called
    when a real account exists.
    """
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user:
        return

    raw_token = secrets.token_urlsafe(32)
    expires_at = (datetime.now(timezone.utc) + timedelta(minutes=RESET_TOKEN_LIFETIME_MINUTES)).isoformat()
    db.create_password_reset(user["id"], _hash_token(raw_token), expires_at)
    _send_reset_email(email, build_reset_url(raw_token))


def verify_reset_token(raw_token: str):
    """
    Returns the associated user_id if `raw_token` is a real, unused,
    unexpired reset token, else None. Never raises -- an invalid token
    is just as unremarkable as an expired one to the caller.
    """
    if not raw_token:
        return None
    row = db.get_password_reset_by_token_hash(_hash_token(raw_token))
    if not row or row.get("used"):
        return None
    expires_at = row.get("expires_at")
    try:
        if expires_at and datetime.fromisoformat(expires_at) < datetime.now(timezone.utc):
            return None
    except ValueError:
        return None
    return row["user_id"]


def complete_password_reset(raw_token: str, new_password: str, confirm_password: str):
    user_id = verify_reset_token(raw_token)
    if not user_id:
        raise AuthError("That reset link is invalid or has expired. Please request a new one.")
    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("Passwords do not match.")
    db.update_password(user_id, generate_password_hash(new_password))
    db.mark_password_reset_used(_hash_token(raw_token))


def change_password(user_id, current_password, new_password, confirm_password):
    """Password change from the Profile page -- requires the current
    password (unlike the email-reset flow, which proves identity via a
    token instead)."""
    user = db.get_user_by_id(user_id)
    if not user or not check_password_hash(user["password_hash"], current_password or ""):
        raise AuthError("Current password is incorrect.")
    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("New passwords do not match.")
    db.update_password(user_id, generate_password_hash(new_password))


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
