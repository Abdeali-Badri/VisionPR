from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from backend.config import settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    github_id INTEGER UNIQUE,
    login TEXT NOT NULL UNIQUE,
    display_name TEXT NOT NULL,
    avatar_url TEXT,
    encrypted_token TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    repository TEXT NOT NULL UNIQUE,
    repository_url TEXT NOT NULL,
    default_branch TEXT NOT NULL DEFAULT 'main',
    language TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    title TEXT NOT NULL,
    source_type TEXT NOT NULL DEFAULT 'recording',
    source_value TEXT,
    status TEXT NOT NULL,
    current_step INTEGER NOT NULL DEFAULT 1,
    pr_number INTEGER,
    pr_url TEXT,
    head_branch TEXT,
    commit_sha TEXT,
    changed_files_json TEXT NOT NULL DEFAULT '[]',
    build_status TEXT,
    report_json TEXT,
    options_json TEXT NOT NULL DEFAULT '{}',
    error_message TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    task_number INTEGER NOT NULL,
    title TEXT NOT NULL,
    timestamp REAL,
    transcript TEXT,
    screenshot_path TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    feedback TEXT,
    changed_files_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(review_id, task_number)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id INTEGER NOT NULL REFERENCES reviews(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or settings.database_path)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(SCHEMA)
            columns = {row[1] for row in connection.execute("PRAGMA table_info(reviews)")}
            if "options_json" not in columns:
                connection.execute("ALTER TABLE reviews ADD COLUMN options_json TEXT NOT NULL DEFAULT '{}'")

    def fetch_one(self, query: str, parameters: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(query, parameters).fetchone()
            return dict(row) if row else None

    def fetch_all(self, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def execute(self, query: str, parameters: tuple[Any, ...] = ()) -> int:
        with self.connect() as connection:
            cursor = connection.execute(query, parameters)
            return int(cursor.lastrowid)


def decode_json_fields(item: dict[str, Any], *fields: str) -> dict[str, Any]:
    result = dict(item)
    for field in fields:
        result[field.removesuffix("_json")] = json.loads(result.pop(field, None) or "[]")
    return result


db = Database()
