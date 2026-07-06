import sqlite3
import os
import json
from datetime import datetime, timezone

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


def get_db():
    if _USE_PG:
        return _PGConn()
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


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
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id {_PK},
            conversation_id INTEGER NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            text TEXT NOT NULL DEFAULT '',
            attachment_ids_json TEXT NOT NULL DEFAULT '[]',
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

        CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users(email) WHERE email <> '';
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


def set_two_factor_enabled(user_id, enabled: bool):
    conn = get_db()
    try:
        conn.execute("UPDATE users SET two_factor_enabled = ? WHERE id = ?", (1 if enabled else 0, user_id))
        conn.commit()
    finally:
        conn.close()


def set_pending_code(user_id, code, purpose, expires_at_iso):
    conn = get_db()
    try:
        conn.execute(
            """UPDATE users SET pending_code = ?, pending_code_purpose = ?,
                                 pending_code_expires_at = ?, pending_code_attempts = 0
               WHERE id = ?""",
            (code, purpose, expires_at_iso, user_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_pending_code_attempts(user_id):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE users SET pending_code_attempts = pending_code_attempts + 1 WHERE id = ?",
            (user_id,),
        )
        conn.commit()
    finally:
        conn.close()


def clear_pending_code(user_id):
    conn = get_db()
    try:
        conn.execute(
            """UPDATE users SET pending_code = '', pending_code_purpose = '',
                                 pending_code_expires_at = '', pending_code_attempts = 0
               WHERE id = ?""",
            (user_id,),
        )
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

def add_document(user_id, filename, stored_path, file_type, content="", category="", file_size=None):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO documents (user_id, filename, stored_path, file_type, content, category, file_size, uploaded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (user_id, filename, stored_path, file_type, content or "", category or "", file_size, now_iso()),
        )
        conn.commit()
        return cur.lastrowid
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


# ---------------- CHAT ATTACHMENT QUERIES ----------------

def add_chat_attachment(user_id, filename, stored_path, mime_type, text_content=""):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO chat_attachments (user_id, filename, stored_path, mime_type, text_content, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, filename, stored_path, mime_type, text_content, now_iso()),
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
            "SELECT id, title, created_at, updated_at FROM chat_conversations WHERE user_id = ? ORDER BY updated_at DESC",
            (user_id,)
        ).fetchall()
        return [dict(r) for r in rows]
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


def update_conversation_title(conv_id, user_id, title):
    conn = get_db()
    try:
        conn.execute(
            "UPDATE chat_conversations SET title = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (title, now_iso(), conv_id, user_id)
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


def delete_conversation(conv_id, user_id):
    conn = get_db()
    try:
        conn.execute("DELETE FROM chat_conversations WHERE id = ? AND user_id = ?", (conv_id, user_id))
        conn.commit()
    finally:
        conn.close()


def add_chat_message(conv_id, role, text, attachment_ids=None):
    conn = get_db()
    try:
        cur = conn.execute(
            """INSERT INTO chat_messages (conversation_id, role, text, attachment_ids_json, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (conv_id, role, text, json.dumps(attachment_ids or []), now_iso()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_messages_for_conversation(conv_id):
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE conversation_id = ? ORDER BY created_at ASC",
            (conv_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
