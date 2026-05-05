# database.py — handles all task storage

import sqlite3
from datetime import datetime

DB_FILE = "tasks.db"


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row  # lets you access columns by name
    return conn


def init_db():
    """Create tables if they don't exist."""
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            slack_user  TEXT    NOT NULL,
            channel     TEXT    NOT NULL,
            thread_ts   TEXT,
            task_text   TEXT    NOT NULL,
            solution    TEXT,
            status      TEXT    DEFAULT 'pending',
            created_at  TEXT    DEFAULT (datetime('now')),
            solved_at   TEXT
        )
    """)
    # Migrate older databases that pre-date the thread_ts column
    existing_cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    if "thread_ts" not in existing_cols:
        conn.execute("ALTER TABLE tasks ADD COLUMN thread_ts TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tasks_thread_ts ON tasks(thread_ts)")
    conn.commit()
    conn.close()
    print("✅ Database ready.")


def save_task(slack_user: str, channel: str, task_text: str, thread_ts: str | None = None) -> int:
    """Insert a new task and return its ID."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tasks (slack_user, channel, thread_ts, task_text) VALUES (?, ?, ?, ?)",
        (slack_user, channel, thread_ts, task_text)
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


def get_conversation_history(
    thread_ts: str | None,
    channel: str | None = None,
    slack_user: str | None = None,
    window_minutes: int = 30,
    limit: int = 10,
) -> list[tuple[str, str]]:
    """Return recent solved (task, solution) pairs for this conversation, oldest first.

    A task counts as part of the current conversation if either:
      - it shares the same thread_ts (in-thread continuity), or
      - the same user posted it in the same channel within the last `window_minutes`
        (cross-mention continuity for top-level @mentions that are not thread replies).
    """
    if not thread_ts and not (channel and slack_user):
        return []
    conn = get_conn()
    rows = conn.execute(
        "SELECT task_text, solution FROM tasks "
        "WHERE status = 'solved' AND solution IS NOT NULL "
        "AND ("
        "    thread_ts = ? "
        "    OR (channel = ? AND slack_user = ? AND created_at >= datetime('now', ?))"
        ") "
        "ORDER BY created_at DESC LIMIT ?",
        (thread_ts, channel, slack_user, f"-{window_minutes} minutes", limit)
    ).fetchall()
    conn.close()
    return list(reversed([(r["task_text"], r["solution"]) for r in rows]))


def update_task(task_id: int, solution: str, status: str = "solved"):
    """Store Claude's solution against the task."""
    conn = get_conn()
    conn.execute(
        "UPDATE tasks SET solution=?, status=?, solved_at=? WHERE id=?",
        (solution, status, datetime.utcnow().isoformat(), task_id)
    )
    conn.commit()
    conn.close()


def get_all_tasks():
    """Return all tasks (useful for debugging)."""
    conn = get_conn()
    rows = conn.execute("SELECT * FROM tasks ORDER BY created_at DESC").fetchall()
    conn.close()
    return rows
