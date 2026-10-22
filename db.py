"""
CareLoop - Database Module (db.py)

Manages local SQLite storage for medication schedules, adherence logs,
wellness check-ins, and safety alerts.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

# Default database file path (configurable via CARELOOP_DB_PATH environment variable)
DEFAULT_DB_PATH = Path(
    os.environ.get(
        "CARELOOP_DB_PATH",
        str(Path(__file__).resolve().parent / "careloop.db"),
    )
)

# Initial seed data for scheduled medications
SEED_SCHEDULE = [
    ("BP tablet", "08:00"),
    ("Metformin", "09:00"),
    ("Vitamin D", "14:00"),
]


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Return an SQLite connection configured with dict-like row access."""
    target_path = Path(db_path) if db_path else DEFAULT_DB_PATH
    conn = sqlite3.connect(target_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str | Path | None = None) -> None:
    """
    Initialize SQLite database tables and seed baseline schedule.
    Creates:
      - schedule(id, name, time_hhmm)
      - med_logs(id, name, taken_at)
      - wellness_logs(id, mood, symptoms, created_at)
      - alerts(id, reason, severity, created_at)
    Seeds:
      - "BP tablet" 08:00
      - "Metformin" 09:00
      - "Vitamin D" 14:00
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()

        # 1. schedule table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                time_hhmm TEXT NOT NULL
            )
            """
        )

        # 2. med_logs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS med_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                taken_at TEXT NOT NULL
            )
            """
        )

        # 3. wellness_logs table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS wellness_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mood TEXT NOT NULL,
                symptoms TEXT,
                created_at TEXT NOT NULL
            )
            """
        )

        # 4. alerts table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reason TEXT NOT NULL,
                severity TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        # Seed initial medications if not already present
        for med_name, time_hhmm in SEED_SCHEDULE:
            cursor.execute(
                """
                INSERT OR IGNORE INTO schedule (name, time_hhmm)
                VALUES (?, ?)
                """,
                (med_name, time_hhmm),
            )

        conn.commit()


def reset_db(db_path: str | Path | None = None) -> None:
    """Clear logs and alerts tables while keeping schedule intact."""
    init_db(db_path)
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM med_logs")
        cursor.execute("DELETE FROM wellness_logs")
        cursor.execute("DELETE FROM alerts")
        conn.commit()


# ---------------------------------------------------------------------------
# Insert Helpers
# ---------------------------------------------------------------------------

def insert_schedule(
    name: str,
    time_hhmm: str,
    db_path: str | Path | None = None,
) -> int:
    """Insert a new medication schedule entry and return the generated ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO schedule (name, time_hhmm) VALUES (?, ?)",
            (name.strip(), time_hhmm.strip()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_med_log(
    name: str,
    taken_at: str,
    db_path: str | Path | None = None,
) -> int:
    """Insert a medication intake log and return the generated ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO med_logs (name, taken_at) VALUES (?, ?)",
            (name.strip(), taken_at.strip()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_wellness_log(
    mood: str,
    symptoms: str,
    created_at: str,
    db_path: str | Path | None = None,
) -> int:
    """Insert a wellness check-in record and return the generated ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO wellness_logs (mood, symptoms, created_at) VALUES (?, ?, ?)",
            (mood.strip(), symptoms.strip(), created_at.strip()),
        )
        conn.commit()
        return int(cursor.lastrowid)


def insert_alert(
    reason: str,
    severity: str,
    created_at: str,
    db_path: str | Path | None = None,
) -> int:
    """Insert an alert record and return the generated ID."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO alerts (reason, severity, created_at) VALUES (?, ?, ?)",
            (reason.strip(), severity.strip().lower(), created_at.strip()),
        )
        conn.commit()
        return int(cursor.lastrowid)


# ---------------------------------------------------------------------------
# Query Helpers
# ---------------------------------------------------------------------------

def get_schedule(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Retrieve all scheduled medications ordered by scheduled time."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id, name, time_hhmm FROM schedule ORDER BY time_hhmm ASC")
        return [dict(row) for row in cursor.fetchall()]


def get_med_logs(
    date_str: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve medication intake logs.
    If date_str ('YYYY-MM-DD') is specified, filters logs for that date.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if date_str:
            cursor.execute(
                "SELECT id, name, taken_at FROM med_logs WHERE taken_at LIKE ? ORDER BY taken_at ASC",
                (f"{date_str}%",),
            )
        else:
            cursor.execute("SELECT id, name, taken_at FROM med_logs ORDER BY taken_at ASC")
        return [dict(row) for row in cursor.fetchall()]


def get_wellness_logs(
    date_str: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve wellness check-in records.
    If date_str ('YYYY-MM-DD') is specified, filters logs for that date.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if date_str:
            cursor.execute(
                "SELECT id, mood, symptoms, created_at FROM wellness_logs WHERE created_at LIKE ? ORDER BY created_at ASC",
                (f"{date_str}%",),
            )
        else:
            cursor.execute("SELECT id, mood, symptoms, created_at FROM wellness_logs ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]


def get_alerts(
    date_str: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    """
    Retrieve safety alerts.
    If date_str ('YYYY-MM-DD') is specified, filters alerts for that date.
    """
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        if date_str:
            cursor.execute(
                "SELECT id, reason, severity, created_at FROM alerts WHERE created_at LIKE ? ORDER BY created_at ASC",
                (f"{date_str}%",),
            )
        else:
            cursor.execute("SELECT id, reason, severity, created_at FROM alerts ORDER BY created_at ASC")
        return [dict(row) for row in cursor.fetchall()]
