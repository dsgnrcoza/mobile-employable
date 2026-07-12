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

SECURITY KEY / ACCOUNT RECOVERY:
Every account gets one 25-character security key at signup, shown to
the user exactly once (right after signup, and again after any
regenerate/consume) -- like a crypto wallet's seed phrase, it is never
stored in a form the app could show them again. Only its salted hash
is kept (generate_password_hash, same primitive as the password
itself), so a leaked database can't be used to forge one. Forgetting
it while also forgetting the password means the account is
unrecoverable by design -- that tradeoff is the whole point of it being
a real bearer secret instead of a "security question" someone could
guess from a Facebook profile.

The key is single-use as a recovery credential: every time it
successfully authenticates a password reset (whether through "forgot
password" or as the current_password_or_key proof in an in-app password
change), a fresh key is generated and the old one is invalidated in the
same step, and the new one is surfaced to the caller to show once. This
stops a leaked-but-unused-yet key from being replayed after its first
use.
"""

import re
import secrets
from functools import wraps
from flask import session, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import db

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
PASSWORD_MIN_LENGTH = 8
_SPECIAL_CHAR = re.compile(r"[^A-Za-z0-9]")

# Excludes visually-ambiguous characters (0/O, 1/I/L) since this gets
# hand-copied and possibly hand-retyped -- every remaining character is
# unambiguous at a glance in most fonts.
_SECURITY_KEY_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
SECURITY_KEY_LENGTH = 25
SECURITY_KEY_GROUP_SIZE = 5


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


def generate_security_key() -> str:
    """A 25-character recovery secret, grouped for readability (e.g.
    "9ZAB3-89K56-89XYZ-Q2R7T-M4N6P"). Generated with `secrets`, the
    same CSPRNG used for the reset codes this replaced."""
    raw = "".join(secrets.choice(_SECURITY_KEY_ALPHABET) for _ in range(SECURITY_KEY_LENGTH))
    groups = [raw[i:i + SECURITY_KEY_GROUP_SIZE] for i in range(0, len(raw), SECURITY_KEY_GROUP_SIZE)]
    return "-".join(groups)


def _normalize_security_key(security_key: str) -> str:
    """Case/dash-insensitive so a hand-retyped key still matches --
    only the alphabet's characters carry entropy, the dashes are pure
    formatting."""
    return re.sub(r"[^A-Za-z0-9]", "", (security_key or "")).upper()


def signup(full_name, email, password, confirm_password):
    """
    Creates a new account with a name, email, and password -- nothing
    else. Raises AuthError with a user-facing message on any problem
    (duplicate email, weak password, mismatch, etc). On success, logs
    the new user in immediately and returns (user_id, security_key) --
    the plaintext key is returned ONLY here, for the caller to show the
    user once; it is never retrievable again after this call returns.
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
    security_key = generate_security_key()
    db.set_security_key_hash(user_id, generate_password_hash(_normalize_security_key(security_key)))
    log_in_user(user_id)
    return user_id, security_key


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


def reset_password_with_security_key(email: str, security_key: str, new_password: str, confirm_password: str) -> str:
    """
    Proves identity with the account's security key instead of a
    password, then sets the new password. Returns the freshly
    generated replacement security key (the one just used is
    invalidated in the same step) -- the caller must show this to the
    user once, the same as at signup.
    """
    generic_error = "That email and security key don't match, or don't belong to an account."
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user or not user.get("security_key_hash"):
        raise AuthError(generic_error)
    if not check_password_hash(user["security_key_hash"], _normalize_security_key(security_key)):
        raise AuthError(generic_error)

    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("Passwords do not match.")

    db.update_password(user["id"], generate_password_hash(new_password))
    new_key = generate_security_key()
    db.set_security_key_hash(user["id"], generate_password_hash(_normalize_security_key(new_key)))
    log_in_user(user["id"])
    return new_key


def regenerate_security_key(user_id) -> str:
    """Issues a brand new security key, invalidating the old one
    immediately. Used from Profile's "Regenerate" action."""
    new_key = generate_security_key()
    db.set_security_key_hash(user_id, generate_password_hash(_normalize_security_key(new_key)))
    return new_key


def change_password(user_id, current_password_or_key, new_password, confirm_password):
    """
    Password change from the Profile page -- accepts EITHER the
    current password OR the security key as proof (the key is a valid
    "current password" precisely because it's the account's ultimate
    recovery credential). If the key was the one used, it's rotated in
    the same step and the new one is returned for the caller to show
    once; returns None when the ordinary password was used instead, since
    nothing about the key changed.
    """
    user = db.get_user_by_id(user_id)
    if not user:
        raise AuthError("Current password is incorrect.")

    proof = current_password_or_key or ""
    if check_password_hash(user["password_hash"], proof):
        used_key = False
    elif user.get("security_key_hash") and check_password_hash(user["security_key_hash"], _normalize_security_key(proof)):
        used_key = True
    else:
        raise AuthError("Current password or security key is incorrect.")

    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("New passwords do not match.")
    db.update_password(user_id, generate_password_hash(new_password))

    if not used_key:
        return None
    new_key = generate_security_key()
    db.set_security_key_hash(user_id, generate_password_hash(_normalize_security_key(new_key)))
    return new_key


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
