"""
auth.py
-------
Account creation, login (with optional email-based 2FA), and password
recovery via a 6-digit emailed code -- see email_sender.py for how the
code itself gets delivered.

SESSION MODEL:
Flask's built-in `session` object is used to remember who's logged in
between requests. It's a signed cookie stored in the user's browser —
"signed" means Flask cryptographically seals it with app.secret_key so
the browser can hold it but can't forge or edit it (tampering with the
cookie invalidates the signature and Flask rejects it). The cookie
itself only contains the user's id, never their password or password
hash. This is the standard, secure way to do "stay logged in" on the
web.

Two *other* session keys are used as short-lived, server-verified
handoffs between steps of a multi-step flow -- never as proof of
identity by themselves:
  - "pending_2fa_user_id": set after a correct password when 2FA is on,
    cleared the moment the code is verified (or a new login starts).
  - "pending_reset_user_id" / "reset_verified_user_id": set while a
    password-reset code has been requested / verified, respectively.
None of these three ever get treated as "logged in" -- only
log_in_user() setting "user_id" does that.

PASSWORD RULES:
At least 9 characters, with at least one uppercase letter, one
lowercase letter, and one special (non-alphanumeric) character. Codes
for 2FA/reset are exactly 6 digits, expire 10 minutes after being
issued, and lock out after 5 wrong attempts (forcing a fresh code
rather than allowing unlimited guesses against a 1-in-a-million space).
"""

import re
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import session, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import db
import email_sender

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
PASSWORD_MIN_LENGTH = 9
_SPECIAL_CHAR = re.compile(r"[^A-Za-z0-9]")
CODE_TTL_MINUTES = 10
CODE_MAX_ATTEMPTS = 5


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
    if not _SPECIAL_CHAR.search(password):
        raise AuthError("Password must include at least one special character.")
    return password


def _generate_username_from_email(email: str) -> str:
    """
    The `username` column is still how internal lookups/uniqueness
    work, but the signup form no longer asks for one -- derived from
    the email's local part instead, with a numeric suffix appended
    until it's unique, so this never surfaces as something the user
    has to think about.
    """
    base = re.sub(r"[^A-Za-z0-9_.\-]", "", (email.split("@")[0] or "").lower())[:28] or "user"
    candidate = base
    suffix = 1
    while db.get_user_by_username(candidate):
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


def _generate_code() -> str:
    return f"{secrets.randbelow(1_000_000):06d}"


def _code_expiry_iso() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=CODE_TTL_MINUTES)).isoformat()


def signup(full_name, email, password, confirm_password):
    """
    Creates a new account with just a name, email, and password.
    Raises AuthError with a user-facing message on any problem
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
    Verifies credentials. Raises AuthError with a generic message on
    failure -- deliberately not saying WHICH part was wrong (unknown
    email vs wrong password), since that distinction would let an
    attacker enumerate which emails have accounts here.

    Returns a dict: either {"status": "ok", "user_id": ...} (already
    logged in) or {"status": "2fa_required", "user_id": ...} (correct
    password, but a code has been emailed and must be verified via
    verify_code() before log_in_user() is called).
    """
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user or not check_password_hash(user["password_hash"], password or ""):
        raise AuthError("Incorrect email or password.")

    if user.get("two_factor_enabled"):
        code = _generate_code()
        db.set_pending_code(user["id"], code, "2fa_login", _code_expiry_iso())
        email_sender.send_code_email(user["email"], code, "2fa_login")
        return {"status": "2fa_required", "user_id": user["id"]}

    log_in_user(user["id"])
    return {"status": "ok", "user_id": user["id"]}


def request_password_reset(email):
    """
    Emails a reset code if the address matches an account. Always
    returns without indicating whether the account existed (the caller
    must show the identical message either way) -- returns the user id
    on a match, or None otherwise, purely so the caller can stash it
    for the verification step without ever surfacing which case it was.
    """
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user:
        return None
    code = _generate_code()
    db.set_pending_code(user["id"], code, "password_reset", _code_expiry_iso())
    email_sender.send_code_email(user["email"], code, "password_reset")
    return user["id"]


def resend_code(user_id, purpose):
    """Re-issues a fresh code for an already-started 2FA or reset flow."""
    if not user_id:
        raise AuthError("That verification session has expired. Please start again.")
    user = db.get_user_by_id(user_id)
    if not user:
        raise AuthError("That verification session has expired. Please start again.")
    code = _generate_code()
    db.set_pending_code(user_id, code, purpose, _code_expiry_iso())
    email_sender.send_code_email(user["email"], code, purpose)


def verify_code(user_id, purpose, code):
    """
    Checks a submitted code against the pending one stored for
    user_id/purpose. Raises AuthError on any mismatch, expiry, or
    attempt-limit breach; on success, clears the pending code (one-time
    use) and returns True.
    """
    if not user_id:
        raise AuthError("Invalid or expired code. Please request a new one.")
    user = db.get_user_by_id(user_id)
    if not user or not user.get("pending_code") or user.get("pending_code_purpose") != purpose:
        raise AuthError("Invalid or expired code. Please request a new one.")

    if int(user.get("pending_code_attempts") or 0) >= CODE_MAX_ATTEMPTS:
        db.clear_pending_code(user_id)
        raise AuthError("Too many incorrect attempts. Please request a new code.")

    expires_at = user.get("pending_code_expires_at") or ""
    try:
        expired = not expires_at or datetime.now(timezone.utc) > datetime.fromisoformat(expires_at)
    except ValueError:
        expired = True
    if expired:
        db.clear_pending_code(user_id)
        raise AuthError("That code has expired. Please request a new one.")

    if (code or "").strip() != user["pending_code"]:
        db.increment_pending_code_attempts(user_id)
        raise AuthError("Incorrect code. Please try again.")

    db.clear_pending_code(user_id)
    return True


def complete_password_reset(user_id, new_password, confirm_password):
    if not user_id:
        raise AuthError("That reset session has expired. Please start again.")
    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("Passwords do not match.")
    db.update_password(user_id, generate_password_hash(new_password))


def set_two_factor_enabled(user_id, enabled: bool, current_password: str):
    """Requires the current password as confirmation before flipping this switch either way."""
    user = db.get_user_by_id(user_id)
    if not user or not check_password_hash(user["password_hash"], current_password or ""):
        raise AuthError("Incorrect password.")
    db.set_two_factor_enabled(user_id, enabled)


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
