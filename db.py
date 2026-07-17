import sqlite3
import os
import json
from datetime import datetime, timezone

from flask import g, has_app_context

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "employable.sqlite3")

_DATABASE_URL = os.environ.get("DATABASE_URL", "")
_USE_PG = _DATABASE_URL.startswith(("postgres://", "postgresql://"))


class _PGCursor:
    def __init__(self, cur, lastrowid=None):
        self._cur = cur
        self.lastrowid = lastrowid

    def fetchone(self):
        row = self._cur.fetchone()
        return dict(row) if row else None

    def fetchall(self):
        rows = self._cur.fetchall()
        return [dict(r) for r in rows]

    def __iter__(self):
        for row in self._cur:
            yield dict(row)


class _PGConn:
    def __init__(self):
        import psycopg2
        import psycopg2.extras
        url = _DATABASE_URL
        if url.startswith("postgres://"):
            url = "postgresql://" + url[len("postgres://"):]
        self._conn = psycopg2.connect(url, cursor_factory=psycopg2.extras.RealDictCursor)
        self._conn.autocommit = False

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        sql = sql.replace(" COLLATE NOCASE", "")
        cur = self._conn.cursor()
        last_id = None
        if sql.strip().upper().startswith("INSERT"):
            cur.execute(sql + " RETURNING id", params)
            row = cur.fetchone()
            last_id = row["id"] if row else None
        else:
            cur.execute(sql, params)
        return _PGCursor(cur, last_id)

    def executescript(self, script):
        cur = self._conn.cursor()
        for stmt in script.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        self._conn.rollback()


def _open_connection():
    if _USE_PG:
        return _PGConn()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


class _RequestConnProxy:
    """
    Every db.py function calls get_db() independently and closes what it
    gets back when done -- previously that meant a brand-new database
    connection (a fresh TCP+TLS+auth handshake against Postgres in
    production) for every single one of the 5-10 db.* calls a single
    request makes. Within a request, get_db() now hands out this proxy
    around ONE real, shared connection instead: .close() becomes a
    no-op (the real connection is closed once, by close_db(), at
    request teardown) while every other call -- execute, commit,
    rollback, executescript -- passes straight through, so no call site
    needed to change.
    """

    __slots__ = ("_real",)

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._real, name)


def get_db():
    if has_app_context():
        if "_db_conn" not in g:
            g._db_conn = _open_connection()
        return _RequestConnProxy(g._db_conn)
    # No Flask request/app context (standalone scripts, migrations, the
    # seed/test tooling) -- fall back to the original one-shot-connection
    # behavior, where the caller's own conn.close() really does close it.
    return _open_connection()


def close_db(exception=None):
    """Registered as a Flask teardown_appcontext hook (see app.py) so the
    one real connection opened per request actually gets closed once,
    after the response is built."""
    conn = g.pop("_db_conn", None)
    if conn is not None:
        if exception is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        conn.close()


def init_db():
    _PK = "BIGSERIAL PRIMARY KEY" if _USE_PG else "INTEGER PRIMARY KEY AUTOINCREMENT"
    conn = get_db()
    conn.executescript(f"""
        CREATE TABLE IF NOT EXISTS users (
            id {_PK},
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            security_question TEXT NOT NULL,
            security_answer_hash TEXT NOT NULL,
            full_name TEXT DEFAULT '',
            headline TEXT DEFAULT '',
            email TEXT DEFAULT '',
            location TEXT DEFAULT '',
            avatar_path TEXT DEFAULT '',
            phone TEXT DEFAULT '',
            target_field TEXT DEFAULT '',
            documents_confirmed INTEGER NOT NULL DEFAULT 0,
            confirmed_owner_name TEXT DEFAULT '',
            disclaimer_accepted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS documents (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            stored_path TEXT NOT NULL DEFAULT '',
            file_type TEXT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            score_delta REAL DEFAULT NULL,
            dimension_deltas TEXT DEFAULT NULL,
            insight_cache TEXT DEFAULT NULL,
            category TEXT NOT NULL DEFAULT '',
            file_size INTEGER DEFAULT NULL,
            file_bytes_b64 TEXT DEFAULT NULL,
            uploaded_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skills (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 0,
            source TEXT NOT NULL DEFAULT 'manual'
        );

        CREATE TABLE IF NOT EXISTS analyses (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS job_applications (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            job_title TEXT NOT NULL,
            company TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'applied',
            applied_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS score_history (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            overall_rating REAL NOT NULL,
            dimension_scores_json TEXT NOT NULL DEFAULT '{{}}',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_conversations (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT 'Conversation',
            kind TEXT NOT NULL DEFAULT 'chat',
            job_title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            fit_score INTEGER DEFAULT NULL,
            status_label TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id {_PK},
            conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            attachment_ids_json TEXT NOT NULL DEFAULT '[]',
            card_json TEXT DEFAULT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_attachments (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            filename TEXT NOT NULL,
            stored_path TEXT NOT NULL DEFAULT '',
            mime_type TEXT NOT NULL DEFAULT '',
            text_content TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS roadmap_completions (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            item_label TEXT NOT NULL,
            doc_id INTEGER,
            points_awarded REAL NOT NULL DEFAULT 0,
            completed_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS friend_requests (
            id {_PK},
            from_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            to_user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            responded_at TEXT DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS notes (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            color TEXT NOT NULL DEFAULT 'default',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trackers (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            job_title TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            date_applied TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'applied',
            notes TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS password_resets (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        -- A persistent, structured picture of this user (target roles,
        -- recurring blockers, inferred tone, etc.) -- one row per user,
        -- rebuilt/merged over time instead of re-derived from scratch
        -- every message. See app.py's _consolidate_user_brain.
        CREATE TABLE IF NOT EXISTS user_brain (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            profile_json TEXT NOT NULL DEFAULT '{{}}',
            updated_at TEXT NOT NULL
        );

        -- Episodic memory: short, one-line summaries of things that
        -- actually happened ("Applied to X at Y, fit 78"), separate from
        -- the structured profile above -- this is the log a real assistant
        -- would recall specific events from, not just traits.
        CREATE TABLE IF NOT EXISTS memory_log (
            id {_PK},
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
    """)
    conn.commit()

    # Previously guarded by "if not _USE_PG" -- meaning none of these ever
    # ran against a live Postgres database, only local SQLite. Harmless
    # while every column here already existed in the CREATE TABLE above
    # from day one on whatever database Postgres was first initialized
    # against, but silently wrong the moment a column is added here
    # *after* a production database already exists (CREATE TABLE IF NOT
    # EXISTS is a no-op against an existing table, so the new column
    # would just never show up in Postgres). Running for both engines
    # and swallowing "already exists" is safe either way.
    _migrations = [
        "ALTER TABLE users ADD COLUMN documents_confirmed INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN confirmed_owner_name TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN phone TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN avatar_path TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN disclaimer_accepted INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN target_field TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN two_factor_enabled INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN pending_code TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN pending_code_purpose TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN pending_code_expires_at TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN pending_code_attempts INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE documents ADD COLUMN score_delta REAL DEFAULT NULL",
        "ALTER TABLE documents ADD COLUMN dimension_deltas TEXT DEFAULT NULL",
        "ALTER TABLE documents ADD COLUMN insight_cache TEXT DEFAULT NULL",
        "ALTER TABLE documents ADD COLUMN content TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN category TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE documents ADD COLUMN file_size INTEGER DEFAULT NULL",
        "ALTER TABLE documents ADD COLUMN file_bytes_b64 TEXT DEFAULT NULL",
        "ALTER TABLE chat_messages ADD COLUMN card_json TEXT DEFAULT NULL",
        "ALTER TABLE chat_conversations ADD COLUMN kind TEXT NOT NULL DEFAULT 'chat'",
        "ALTER TABLE chat_conversations ADD COLUMN job_title TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chat_conversations ADD COLUMN company TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE chat_conversations ADD COLUMN fit_score INTEGER DEFAULT NULL",
        "ALTER TABLE chat_conversations ADD COLUMN status_label TEXT NOT NULL DEFAULT ''",
        "ALTER TABLE users ADD COLUMN security_key_hash TEXT DEFAULT ''",
        "ALTER TABLE users ADD COLUMN custom_instructions TEXT DEFAULT ''",
        "ALTER TABLE notes ADD COLUMN source TEXT NOT NULL DEFAULT 'user'",
        "ALTER TABLE users ADD COLUMN enabled_plugins TEXT NOT NULL DEFAULT '[]'",
        "ALTER TABLE users ADD COLUMN remember_all_chats INTEGER NOT NULL DEFAULT 0",
        # Chat attachments used to only keep a filesystem path -- which
        # doesn't survive on a read-only/ephemeral serverless deploy.
        # Same fix as documents.file_bytes_b64 above: keep the actual
        # bytes in the row itself.
        "ALTER TABLE chat_attachments ADD COLUMN data_b64 TEXT NOT NULL DEFAULT ''",
        # Deliberately last and best-effort, not part of the CREATE TABLE
        # block above: if any pre-existing rows already share a non-blank
        # email (e.g. two accounts that both had their email set to the
        # same address via Edit Profile, back when email wasn't required
        # or unique), this single statement failing inside an unprotected
        # executescript() would abort the whole migration and, since
        # _db_initialized never gets set to True, take down EVERY request
        # after it forever -- not just fail to add an index. Letting it
        # fail silently here just means uniqueness isn't DB-enforced;
        # signup() already rejects duplicate emails at the application
        # level regardless.
        "CREATE UNIQUE INDEX idx_users_email ON users(email) WHERE email <> ''",
    ]
    for ddl in _migrations:
        try:
            conn.execute(ddl)
            conn.commit()
        except Exception:
            conn.rollback() if _USE_PG else None

    conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- USER QUERIES ----------------

def create_user(username, password_hash, security_question, security_answer_hash, full_name="", email=""):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO users (username, password_hash, security_question,
                                   security_answer_hash, full_name, email, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (username, password_hash, security_question, security_answer_hash,
             full_name or "", email or "", now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_user_by_username(username):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_email(email):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE email = ? AND email <> ''", (email,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_user_by_id(user_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def update_password(user_id, new_password_hash):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_password_hash, user_id))
        conn.commit()
    finally:
        conn.close()


def set_security_key_hash(user_id, security_key_hash):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET security_key_hash = ? WHERE id = ?", (security_key_hash, user_id))
        conn.commit()
    finally:
        conn.close()


def update_profile_fields(user_id, **fields):
    if not fields:
        return
    allowed = {"full_name", "headline", "email", "location", "avatar_path", "phone"}
    cols = [k for k in fields.keys() if k in allowed]
    if not cols:
        return
    set_clause = ", ".join(f"{c} = ?" for c in cols)
    values = [fields[c] for c in cols] + [user_id]
    conn = get_db()
    try:
        conn.execute(f"UPDATE users SET {set_clause} WHERE id = ?", values)
        conn.commit()
    finally:
        conn.close()


def set_documents_confirmed(user_id, confirmed_owner_name):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET documents_confirmed = 1, confirmed_owner_name = ? WHERE id = ?",
            (confirmed_owner_name or "", user_id),
        )
        conn.commit()
    finally:
        conn.close()


def reset_documents_confirmed(user_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET documents_confirmed = 0, confirmed_owner_name = '' WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------- DOCUMENT QUERIES ----------------

def add_document(user_id, filename, stored_path, file_type, content="", category="", file_size=None, file_bytes_b64=None):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO documents (user_id, filename, stored_path, file_type, content, category, file_size, file_bytes_b64, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, filename, stored_path, file_type, content or "", category or "", file_size, file_bytes_b64, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_document_file_bytes(user_id, document_id):
    """
    Returns the raw original file bytes for one of this user's
    documents, or None if it has none stored (uploaded before this
    column existed) or doesn't belong to this user. Stored as base64
    text rather than a native blob/bytea column so the same code path
    works identically on SQLite and Postgres with no binary-adapter
    edge cases.
    """
    import base64
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT file_bytes_b64 FROM documents WHERE id = ? AND user_id = ?",
            (document_id, user_id),
        ).fetchone()
        if not row or not row["file_bytes_b64"]:
            return None
        return base64.b64decode(row["file_bytes_b64"])
    finally:
        conn.close()


def get_document_contents(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, filename, content FROM documents WHERE user_id = ? ORDER BY uploaded_at ASC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_document_content(user_id, document_id, content, file_bytes_b64, file_size):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE documents SET content = ?, file_bytes_b64 = ?, file_size = ? WHERE id = ? AND user_id = ?",
            (content, file_bytes_b64, file_size, document_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_document_by_id(user_id, document_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM documents WHERE id = ? AND user_id = ?", (document_id, user_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_documents_for_user(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM documents WHERE user_id = ? ORDER BY uploaded_at ASC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_roadmap_completions(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM roadmap_completions WHERE user_id = ? ORDER BY completed_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_roadmap_completion(user_id, item_label, doc_id, points_awarded):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO roadmap_completions (user_id, item_label, doc_id, points_awarded, completed_at) VALUES (?,?,?,?,?)",
            (user_id, item_label, doc_id, round(float(points_awarded), 2), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def set_document_score_delta(user_id, doc_id, delta, dimension_deltas_json=None):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE documents SET score_delta = ?, dimension_deltas = ? WHERE id = ? AND user_id = ?",
            (round(float(delta), 2), dimension_deltas_json, doc_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_document_insight_cache(user_id, doc_id, insight):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE documents SET insight_cache = ? WHERE id = ? AND user_id = ?",
            (insight, doc_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_document(user_id, document_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM documents WHERE id = ? AND user_id = ?", (document_id, user_id))
        conn.commit()
    finally:
        conn.close()


def delete_user(user_id):
    """
    Deletes the user row outright. Every other table (documents, skills,
    analyses, job_applications, score_history, chat_conversations,
    chat_attachments, roadmap_completions) references user_id with
    ON DELETE CASCADE, so this one delete removes all of it — the
    caller is still responsible for removing anything on disk (uploaded
    files, chat attachments), since cascading a DB row doesn't touch
    the filesystem.
    """
    conn = get_db()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------- SKILL QUERIES ----------------

def get_skills_for_user(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM skills WHERE user_id = ? ORDER BY sort_order ASC, id ASC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_skill(user_id, label, source="manual"):
    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT id FROM skills WHERE user_id = ? AND label = ?", (user_id, label)
        ).fetchone()
        if existing:
            return existing["id"]
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM skills WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        cur = conn.execute(
            "INSERT INTO skills (user_id, label, sort_order, source) VALUES (?, ?, ?, ?)",
            (user_id, label, max_order + 1, source),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def replace_ai_skills(user_id, labels):
    conn = get_db()
    try:
        conn.execute("DELETE FROM skills WHERE user_id = ? AND source = 'ai'", (user_id,))
        max_order = conn.execute(
            "SELECT COALESCE(MAX(sort_order), -1) AS m FROM skills WHERE user_id = ?", (user_id,)
        ).fetchone()["m"]
        order = max_order + 1
        for label in labels:
            existing = conn.execute(
                "SELECT id FROM skills WHERE user_id = ? AND label = ?", (user_id, label)
            ).fetchone()
            if existing:
                continue
            conn.execute(
                "INSERT INTO skills (user_id, label, sort_order, source) VALUES (?, ?, ?, 'ai')",
                (user_id, label, order),
            )
            order += 1
        conn.commit()
    finally:
        conn.close()


def delete_skill(user_id, skill_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM skills WHERE id = ? AND user_id = ?", (skill_id, user_id))
        conn.commit()
    finally:
        conn.close()


# ---------------- ANALYSIS QUERIES ----------------

def save_analysis(user_id, result_json):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO analyses (user_id, result_json, created_at) VALUES (?, ?, ?)",
            (user_id, result_json, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def clear_analyses(user_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM analyses WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def get_latest_analysis(user_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM analyses WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", (user_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------- JOB APPLICATION QUERIES ----------------

def get_applications_for_user(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM job_applications WHERE user_id = ? ORDER BY applied_at DESC", (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def add_application(user_id, job_title, company):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO job_applications (user_id, job_title, company, applied_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, job_title, company, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


# ---------------- SCORE HISTORY QUERIES ----------------

def save_score_history(user_id, overall_rating, dimension_scores: dict):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO score_history (user_id, overall_rating, dimension_scores_json, created_at) VALUES (?, ?, ?, ?)",
            (user_id, overall_rating, json.dumps(dimension_scores), now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_score_history(user_id, limit=20):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT overall_rating, dimension_scores_json, created_at FROM score_history WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [{"overall_rating": r["overall_rating"], "dimension_scores": json.loads(r["dimension_scores_json"] or "{}"), "created_at": r["created_at"]} for r in rows]
    finally:
        conn.close()


def set_disclaimer_accepted(user_id):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET disclaimer_accepted = 1 WHERE id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def set_target_field(user_id, target_field: str):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET target_field = ? WHERE id = ?", (target_field.strip(), user_id))
        conn.commit()
    finally:
        conn.close()


def set_custom_instructions(user_id, custom_instructions: str):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET custom_instructions = ? WHERE id = ?", (custom_instructions.strip(), user_id))
        conn.commit()
    finally:
        conn.close()


def set_remember_all_chats(user_id, enabled: bool):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET remember_all_chats = ? WHERE id = ?", (1 if enabled else 0, user_id))
        conn.commit()
    finally:
        conn.close()


# ---------------- CHAT ATTACHMENT QUERIES ----------------

def add_chat_attachment(user_id, filename, stored_path, mime_type, text_content="", data_b64=""):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO chat_attachments (user_id, filename, stored_path, mime_type, text_content, data_b64, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (user_id, filename, stored_path, mime_type, text_content, data_b64, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_chat_attachment(user_id, attachment_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM chat_attachments WHERE id = ? AND user_id = ?", (attachment_id, user_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_chat_attachments_for_user(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, filename, mime_type, created_at FROM chat_attachments WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------- CHAT CONVERSATION QUERIES ----------------

def get_conversations_for_user(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT id, title, kind, job_title, company, fit_score, status_label, created_at, updated_at
               FROM chat_conversations WHERE user_id = ?
               ORDER BY updated_at DESC""",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_previous_fit_score(user_id, job_title, company):
    """Most recent fit_score this user already has on record for the same
    job (title + company, case-insensitive), if any -- used to show a
    real "up/down from last time" trend rather than a fabricated one.
    Returns None when there's nothing to compare against yet."""
    if not job_title:
        return None
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT fit_score FROM chat_conversations
               WHERE user_id = ? AND kind = 'job' AND fit_score IS NOT NULL
                 AND lower(job_title) = lower(?) AND lower(company) = lower(?)
               ORDER BY updated_at DESC LIMIT 1""",
            (user_id, job_title, company or ""),
        ).fetchone()
        return row["fit_score"] if row else None
    finally:
        conn.close()


def create_conversation(user_id, title="Conversation"):
    conn = get_db()
    try:
        now = now_iso()
        cur = conn.execute(
            "INSERT INTO chat_conversations (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (user_id, title, now, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def conversation_belongs_to_user(conv_id, user_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT 1 FROM chat_conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def get_conversation(conv_id, user_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT * FROM chat_conversations WHERE id = ? AND user_id = ?", (conv_id, user_id)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def promote_conversation(conv_id, user_id, job_title="", company="", fit_score=None, status_label=""):
    """The core "job thread" mechanic: the moment a chat produces a
    Verdict or Document card, it's promoted out of the plain chat list
    into a tracked job thread -- this IS the tracker, there is no
    separate screen for it."""
    title = f"{job_title} · {company}" if (job_title and company) else (job_title or company or "My CV")
    conn = get_db()
    try:
        conn.execute(
            """UPDATE chat_conversations
               SET kind = 'job', title = ?, job_title = ?, company = ?, fit_score = ?, status_label = ?, updated_at = ?
               WHERE id = ? AND user_id = ?""",
            (title, job_title or "", company or "", fit_score, status_label or "", now_iso(), conv_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_conversation_status(conv_id, user_id, status_label):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE chat_conversations SET status_label = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (status_label, now_iso(), conv_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def touch_conversation(conv_id, user_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE chat_conversations SET updated_at = ? WHERE id = ? AND user_id = ?",
            (now_iso(), conv_id, user_id)
        )
        conn.commit()
    finally:
        conn.close()


def rename_conversation(conv_id, user_id, title):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE chat_conversations SET title = ? WHERE id = ? AND user_id = ?",
            (title, conv_id, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def delete_conversation(conv_id, user_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM chat_conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
        conn.commit()
    finally:
        conn.close()


def delete_all_conversations_for_user(user_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM chat_conversations WHERE user_id = ?", (user_id,))
        conn.commit()
    finally:
        conn.close()


def add_chat_message(conv_id, role, text, attachment_ids=None, card=None):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO chat_messages (conversation_id, role, text, attachment_ids_json, card_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (conv_id, role, text, json.dumps(attachment_ids or []), json.dumps(card) if card else None, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_messages_for_conversation(conv_id, user_id):
    # Joins through chat_conversations to enforce that conv_id actually
    # belongs to user_id -- without this, any logged-in user could read
    # any other user's chat history just by guessing/iterating ids.
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT m.* FROM chat_messages m
               JOIN chat_conversations c ON c.id = m.conversation_id
               WHERE m.conversation_id = ? AND c.user_id = ?
               ORDER BY m.created_at ASC""",
            (conv_id, user_id)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------- FRIEND REQUEST QUERIES ----------------

def create_friend_request(from_user_id, to_user_id):
    conn = get_db()
    try:
        cur = conn.execute(
            "INSERT INTO friend_requests (from_user_id, to_user_id, status, created_at) VALUES (?, ?, 'pending', ?)",
            (from_user_id, to_user_id, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_active_friend_request_between(user_a, user_b):
    """Any pending or accepted request between the two, either direction --
    a prior declined request never blocks a fresh invite."""
    conn = get_db()
    try:
        row = conn.execute(
            """SELECT * FROM friend_requests
               WHERE status IN ('pending', 'accepted')
                 AND ((from_user_id = ? AND to_user_id = ?) OR (from_user_id = ? AND to_user_id = ?))
               ORDER BY created_at DESC LIMIT 1""",
            (user_a, user_b, user_b, user_a),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_friend_request_by_id(request_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM friend_requests WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_pending_incoming_requests(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT fr.id, fr.created_at, u.id AS from_user_id, u.username AS from_username,
                      u.full_name AS from_full_name
               FROM friend_requests fr
               JOIN users u ON u.id = fr.from_user_id
               WHERE fr.to_user_id = ? AND fr.status = 'pending'
               ORDER BY fr.created_at DESC""",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def respond_to_friend_request(request_id, status):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE friend_requests SET status = ?, responded_at = ? WHERE id = ?",
            (status, now_iso(), request_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_friends_for_user(user_id):
    conn = get_db()
    try:
        rows = conn.execute(
            """SELECT u.id, u.username, u.full_name, fr.responded_at
               FROM friend_requests fr
               JOIN users u ON u.id = (CASE WHEN fr.from_user_id = ? THEN fr.to_user_id ELSE fr.from_user_id END)
               WHERE fr.status = 'accepted' AND (fr.from_user_id = ? OR fr.to_user_id = ?)
               ORDER BY fr.responded_at DESC""",
            (user_id, user_id, user_id),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_pending_incoming_requests(user_id):
    conn = get_db()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM friend_requests WHERE to_user_id = ? AND status = 'pending'",
            (user_id,),
        ).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()


def set_pending_code(user_id, code_hash, purpose, expires_at):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET pending_code = ?, pending_code_purpose = ?, pending_code_expires_at = ?, pending_code_attempts = 0 WHERE id = ?",
            (code_hash, purpose, expires_at, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_pending_code_attempts(user_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET pending_code_attempts = pending_code_attempts + 1 WHERE id = ?", (user_id,)
        )
        conn.commit()
    finally:
        conn.close()


def clear_pending_code(user_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET pending_code = '', pending_code_purpose = '', pending_code_expires_at = '', pending_code_attempts = 0 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------- USER BRAIN + EPISODIC MEMORY ----------------
# A persistent, structured profile (user_brain) plus a running log of
# short one-line events (memory_log) -- what turns Ploy from a stateless
# ask/respond loop into something that actually remembers who this
# person is and what's already happened, across conversations. Both are
# only ever written to when the user has "Remember all chats" switched
# on (see app.py's api_chat) -- the same consent boundary that already
# gates cross-conversation memory, just applied to this deeper layer too.

def get_user_brain(user_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT profile_json FROM user_brain WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["profile_json"] or "{}")
        except Exception:
            return {}
    finally:
        conn.close()


def get_user_brain_updated_at(user_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT updated_at FROM user_brain WHERE user_id = ?", (user_id,)).fetchone()
        return row["updated_at"] if row else None
    finally:
        conn.close()


def save_user_brain(user_id, profile: dict):
    conn = get_db()
    try:
        payload = json.dumps(profile)
        existing = conn.execute("SELECT user_id FROM user_brain WHERE user_id = ?", (user_id,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE user_brain SET profile_json = ?, updated_at = ? WHERE user_id = ?",
                (payload, now_iso(), user_id),
            )
        else:
            conn.execute(
                "INSERT INTO user_brain (user_id, profile_json, updated_at) VALUES (?, ?, ?)",
                (user_id, payload, now_iso()),
            )
        conn.commit()
    finally:
        conn.close()


def add_memory_log(user_id, summary):
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO memory_log (user_id, summary, created_at) VALUES (?, ?, ?)",
            (user_id, (summary or "").strip()[:400], now_iso()),
        )
        conn.commit()
    finally:
        conn.close()


def get_recent_memory(user_id, limit=8):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT summary, created_at FROM memory_log WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def count_memory_log(user_id):
    conn = get_db()
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM memory_log WHERE user_id = ?", (user_id,)).fetchone()
        return row["c"] if row else 0
    finally:
        conn.close()
