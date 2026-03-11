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
            task_text   TEXT    NOT NULL,
            solution    TEXT,
            status      TEXT    DEFAULT 'pending',
            created_at  TEXT    DEFAULT (datetime('now')),
            solved_at   TEXT
        )
    """)
    conn.commit()
    conn.close()
    print("✅ Database ready.")


def save_task(slack_user: str, channel: str, task_text: str) -> int:
    """Insert a new task and return its ID."""
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO tasks (slack_user, channel, task_text) VALUES (?, ?, ?)",
        (slack_user, channel, task_text)
    )
    task_id = cur.lastrowid
    conn.commit()
    conn.close()
    return task_id


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
