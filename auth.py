"""
auth.py
-------
Account creation, login, and password recovery via a security
question chosen at signup (no email sending involved anywhere in this
file -- see git history if email-based 2FA/reset ever needs reviving).

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
handoffs between the steps of the password-reset flow -- never as
proof of identity by themselves:
  - "pending_reset_user_id": set once an email has been looked up (see
    get_security_question()), cleared once the security answer is
    verified.
  - "reset_verified_user_id": set once the security answer has been
    verified, cleared once the password itself has been changed.
Neither of these ever gets treated as "logged in" -- only
log_in_user() setting "user_id" does that.

PASSWORD RULES:
At least 9 characters, with at least one uppercase letter, one
lowercase letter, and one special (non-alphanumeric) character.

SECURITY QUESTION / ENUMERATION RESISTANCE:
get_security_question() always returns *some* question, even for an
email with no matching account (a decoy, picked from the same public
list every real user picks from) -- so the response looks identical
whether or not the account exists, and verify_security_answer() always
fails the same way for a decoy. Callers must never branch on which
case it was.
"""

import re
from functools import wraps
from flask import session, redirect, url_for, flash, g
from werkzeug.security import generate_password_hash, check_password_hash

import db

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]{3,32}$")
PASSWORD_MIN_LENGTH = 9
_SPECIAL_CHAR = re.compile(r"[^A-Za-z0-9]")

SECURITY_QUESTIONS = [
    "What was the name of your first pet?",
    "What is your mother's maiden name?",
    "What was the make of your first car?",
    "What elementary school did you attend?",
    "What is the name of the town where you were born?",
    "What was your childhood nickname?",
    "What is your favorite book?",
    "What was the name of your first employer?",
    "What is your favorite food?",
    "Who was your childhood best friend?",
    "What was the name of the street you grew up on?",
    "What is your favorite movie?",
]

# Always one of the real, public options above -- a decoy that happens
# to match a genuine choice is what makes it indistinguishable from a
# real account that picked the same question.
DECOY_SECURITY_QUESTION = SECURITY_QUESTIONS[0]


class AuthError(Exception):
    """Raised for any user-facing validation failure during signup/login/reset."""


def validate_username(username: str) -> str:
    username = (username or "").strip()
    if not username:
        raise AuthError("Please choose a username.")
    if not USERNAME_PATTERN.match(username):
        raise AuthError("Username must be 3-32 characters: letters, numbers, underscore, dot, or dash, no spaces.")
    return username


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


def validate_security_question(question: str) -> str:
    question = (question or "").strip()
    if question not in SECURITY_QUESTIONS:
        raise AuthError("Please choose a security question from the list.")
    return question


def validate_security_answer(answer: str) -> str:
    answer = (answer or "").strip()
    if len(answer) < 2:
        raise AuthError("Please enter an answer to your security question.")
    return answer


def _normalize_answer(answer: str) -> str:
    """Case/whitespace shouldn't matter for matching a security answer."""
    return (answer or "").strip().lower()


def signup(username, email, password, confirm_password, security_question, security_answer):
    """
    Creates a new account with a username, email, password, and a
    security question/answer pair (used later for password recovery
    instead of an emailed code). The username is chosen by the user at
    signup and is how friends find and add each other later. Raises
    AuthError with a user-facing message on any problem (duplicate
    email/username, weak password, mismatch, etc). On success, logs
    the new user in immediately and returns their id.
    """
    username = validate_username(username)
    email = validate_email(email)
    validate_password(password)
    if password != confirm_password:
        raise AuthError("Passwords do not match.")
    security_question = validate_security_question(security_question)
    security_answer = validate_security_answer(security_answer)

    if db.get_user_by_email(email):
        raise AuthError("An account with that email already exists.")
    if db.get_user_by_username(username):
        raise AuthError("That username is already taken.")

    password_hash = generate_password_hash(password)
    security_answer_hash = generate_password_hash(_normalize_answer(security_answer))
    user_id = db.create_user(username, password_hash, security_question, security_answer_hash, email=email)
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


def get_security_question(email):
    """
    Returns (user_id, question) for the given email -- user_id is None
    if no account matches, but a question is always returned (a decoy
    in that case) so the response looks identical either way. The
    caller stashes user_id in the session for the next step without
    ever surfacing which case it was.
    """
    email = (email or "").strip().lower()
    user = db.get_user_by_email(email)
    if not user:
        return None, DECOY_SECURITY_QUESTION
    return user["id"], (user.get("security_question") or DECOY_SECURITY_QUESTION)


def verify_security_answer(user_id, answer):
    """
    Checks a submitted answer against the stored hash for user_id.
    Raises AuthError on any mismatch (including a decoy's user_id of
    None, which always fails) -- the same message either way, so this
    can't be used to confirm an account exists.
    """
    if not user_id:
        raise AuthError("Incorrect answer. Please try again.")
    user = db.get_user_by_id(user_id)
    if not user or not user.get("security_answer_hash") or not check_password_hash(
        user["security_answer_hash"], _normalize_answer(answer)
    ):
        raise AuthError("Incorrect answer. Please try again.")
    return True


def complete_password_reset(user_id, new_password, confirm_password):
    if not user_id:
        raise AuthError("That reset session has expired. Please start again.")
    validate_password(new_password)
    if new_password != confirm_password:
        raise AuthError("Passwords do not match.")
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
