from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from github import Auth, Github

from backend.config import settings
from backend.database import Database, db, decode_json_fields, utc_now
from backend.security import new_session_id, token_cipher


def _repository_name(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path.strip("/").removesuffix(".git")
    if len(path.split("/")) != 2:
        raise ValueError("Repository must be a GitHub owner/repository URL.")
    return path


def upsert_user(profile: dict[str, Any], token: str, database: Database = db) -> dict[str, Any]:
    now = utc_now()
    existing = database.fetch_one("SELECT * FROM users WHERE github_id = ?", (int(profile["id"]),))
    encrypted = token_cipher.encrypt(token)
    if existing:
        database.execute(
            "UPDATE users SET login=?, display_name=?, avatar_url=?, encrypted_token=?, updated_at=? WHERE id=?",
            (profile["login"], profile.get("name") or profile["login"], profile.get("avatar_url"), encrypted, now, existing["id"]),
        )
        return database.fetch_one("SELECT * FROM users WHERE id=?", (existing["id"],)) or existing
    user_id = database.execute(
        "INSERT INTO users(github_id,login,display_name,avatar_url,encrypted_token,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (int(profile["id"]), profile["login"], profile.get("name") or profile["login"], profile.get("avatar_url"), encrypted, now, now),
    )
    return database.fetch_one("SELECT * FROM users WHERE id=?", (user_id,)) or {}


def create_session(user_id: int, database: Database = db) -> str:
    session_id = new_session_id()
    expires = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    database.execute(
        "INSERT INTO sessions(id,user_id,expires_at,created_at) VALUES(?,?,?,?)",
        (session_id, user_id, expires, utc_now()),
    )
    return session_id


def user_for_session(session_id: str | None, database: Database = db) -> dict[str, Any] | None:
    if not session_id:
        return None
    return database.fetch_one(
        "SELECT users.* FROM sessions JOIN users ON users.id=sessions.user_id WHERE sessions.id=? AND sessions.expires_at>?",
        (session_id, utc_now()),
    )


def public_user(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if not user:
        return None
    return {key: user.get(key) for key in ("id", "login", "display_name", "avatar_url")}


def add_event(review_id: int, event_type: str, message: str, metadata: dict[str, Any] | None = None, database: Database = db) -> None:
    database.execute(
        "INSERT INTO events(review_id,event_type,message,metadata_json,created_at) VALUES(?,?,?,?,?)",
        (review_id, event_type, message, json.dumps(metadata or {}), utc_now()),
    )


def seed_demo_data(database: Database = db) -> None:
    now = utc_now()
    demo = database.fetch_one("SELECT * FROM users WHERE login='alex-visionpr'")
    if not demo:
        demo_id = database.execute(
            "INSERT INTO users(github_id,login,display_name,avatar_url,created_at,updated_at) VALUES(NULL,?,?,?,?,?)",
            ("alex-visionpr", "Alex Dev", None, now, now),
        )
    else:
        demo_id = int(demo["id"])

    state_path = Path.home() / ".visionpr" / "state" / "movie-recommender-live-pr-001.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    repository = str(state.get("repository") or "Abdeali-Badri/MOVIE-RECOMMENDER")
    project = database.fetch_one("SELECT * FROM projects WHERE repository=?", (repository,))
    if not project:
        project_id = database.execute(
            "INSERT INTO projects(user_id,repository,repository_url,default_branch,language,status,updated_at) VALUES(?,?,?,?,?,?,?)",
            (demo_id, repository, f"https://github.com/{repository}", state.get("base_branch") or "main", "Python", "active", now),
        )
    else:
        project_id = int(project["id"])

    run_id = str(state.get("run_id") or "movie-recommender-live-pr-001")
    if not database.fetch_one("SELECT id FROM reviews WHERE run_id=?", (run_id,)):
        review_id = database.execute(
            """INSERT INTO reviews(run_id,user_id,project_id,title,source_type,status,current_step,pr_number,pr_url,head_branch,commit_sha,changed_files_json,build_status,options_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id, demo_id, project_id, "Make Python valid outside Google Colab", "recording",
                "AWAITING_HUMAN_REVIEW" if state.get("status") == "PR_OPENED" else state.get("status", "READY"),
                4, state.get("pr_number", 1), state.get("pr_url"), state.get("head_branch"), state.get("commit_sha"),
                json.dumps(state.get("changed_files") or ["README.md", "movie_recomender (1).py"]),
                state.get("build_status") or "success", json.dumps({"build_commands": ["python -m compileall ."]}), now, now,
            ),
        )
        database.execute(
            "INSERT INTO review_tasks(review_id,task_number,title,timestamp,transcript,status,changed_files_json) VALUES(?,?,?,?,?,?,?)",
            (review_id, 1, "Remove notebook-only dependency installation", 94.0, "Make the Python program valid outside Google Colab and document NLTK.", "awaiting_review", json.dumps(state.get("changed_files") or [])),
        )
        add_event(review_id, "pr_opened", "Pull request #1 is ready for human review.", {"pr_url": state.get("pr_url")}, database)


def dashboard(database: Database = db) -> dict[str, Any]:
    stats = database.fetch_one(
        """SELECT
            (SELECT COUNT(*) FROM projects) AS projects,
            (SELECT COUNT(*) FROM reviews) AS reviews,
            (SELECT COUNT(*) FROM reviews WHERE pr_number IS NOT NULL) AS pull_requests,
            (SELECT COUNT(*) FROM reviews WHERE status='MERGED') AS merged"""
    ) or {}
    reviews = database.fetch_all(
        """SELECT reviews.id,reviews.run_id,reviews.title,reviews.status,reviews.updated_at,reviews.pr_url,
                  projects.repository
           FROM reviews LEFT JOIN projects ON projects.id=reviews.project_id
           ORDER BY reviews.updated_at DESC LIMIT 6"""
    )
    projects = database.fetch_all("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 5")
    total_prs = max(int(stats.get("pull_requests") or 0), 1)
    stats["merge_rate"] = round((int(stats.get("merged") or 0) / total_prs) * 100)
    return {"stats": stats, "recent_reviews": reviews, "recent_projects": projects}


def list_reviews(database: Database = db) -> list[dict[str, Any]]:
    return database.fetch_all(
        """SELECT reviews.*,projects.repository FROM reviews
           LEFT JOIN projects ON projects.id=reviews.project_id ORDER BY reviews.updated_at DESC"""
    )


def review_detail(review_id: int, database: Database = db) -> dict[str, Any] | None:
    review = database.fetch_one(
        """SELECT reviews.*,projects.repository,projects.repository_url,projects.default_branch
           FROM reviews LEFT JOIN projects ON projects.id=reviews.project_id WHERE reviews.id=?""",
        (review_id,),
    )
    if not review:
        return None
    result = decode_json_fields(review, "changed_files_json", "options_json")
    result["tasks"] = [decode_json_fields(task, "changed_files_json") for task in database.fetch_all("SELECT * FROM review_tasks WHERE review_id=? ORDER BY task_number", (review_id,))]
    result["events"] = []
    for event in database.fetch_all("SELECT * FROM events WHERE review_id=? ORDER BY id DESC", (review_id,)):
        metadata = json.loads(event.pop("metadata_json", None) or "{}")
        result["events"].append({**event, "metadata": metadata})
    return result


def create_review(payload: dict[str, Any], user_id: int | None, database: Database = db) -> dict[str, Any]:
    repository = _repository_name(payload["repository_url"])
    now = utc_now()
    project = database.fetch_one("SELECT * FROM projects WHERE repository=?", (repository,))
    if project:
        project_id = int(project["id"])
    else:
        project_id = database.execute(
            "INSERT INTO projects(user_id,repository,repository_url,status,updated_at) VALUES(?,?,?,?,?)",
            (user_id, repository, payload["repository_url"], "active", now),
        )
    run_id = f"web-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
    review_id = database.execute(
        """INSERT INTO reviews(run_id,user_id,project_id,title,source_type,source_value,status,current_step,options_json,created_at,updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (run_id, user_id, project_id, payload["title"], payload["source_type"], payload.get("source_value"), "DRAFT", 1, json.dumps({"build_commands": payload.get("build_commands", []), "constraints": payload.get("constraints", [])}), now, now),
    )
    add_event(review_id, "created", "Review created. Add evidence and confirm the extracted tasks.", database=database)
    return review_detail(review_id, database) or {}


def start_worker(review_id: int, mode: str = "run") -> int:
    command = [sys.executable, "-m", "backend.worker", "--review-id", str(review_id), "--mode", mode]
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(command, cwd=settings.project_root, creationflags=flags)
    return int(process.pid)


def github_client_for_user(user: dict[str, Any]) -> Github:
    encrypted = str(user.get("encrypted_token") or "")
    if not encrypted:
        token = os.getenv("GITHUB_TOKEN", "").strip()
    else:
        token = token_cipher.decrypt(encrypted)
    if not token:
        raise PermissionError("Connect GitHub before performing repository actions.")
    return Github(auth=Auth.Token(token))
