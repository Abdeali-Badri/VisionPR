from __future__ import annotations

import json
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode

import httpx
from fastapi import BackgroundTasks, Cookie, Depends, FastAPI, File, HTTPException, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from backend.config import settings
from backend.database import db, utc_now
from backend.schemas import AcceptRequest, FeedbackCreate, MergeRequest, ReviewCreate, TaskDraft
from backend.security import new_oauth_state
from backend.services import (
    add_event,
    create_review,
    create_session,
    dashboard,
    github_client_for_user,
    list_reviews,
    public_user,
    review_detail,
    seed_demo_data,
    start_worker,
    upsert_user,
    user_for_session,
)


SESSION_COOKIE = "visionpr_session"
OAUTH_COOKIE = "visionpr_oauth_state"


def cookie_samesite() -> str:
    return "none" if settings.cookie_secure else "lax"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    db.initialize()
    seed_demo_data()
    yield


app = FastAPI(
    title="VisionPR API",
    description="Human-trusted meeting-to-pull-request orchestration.",
    version="0.2.0",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Requested-With"],
)


def optional_user(visionpr_session: Annotated[str | None, Cookie()] = None) -> dict[str, Any] | None:
    return user_for_session(visionpr_session)


def current_user(user: Annotated[dict[str, Any] | None, Depends(optional_user)]) -> dict[str, Any]:
    if user:
        return user
    if settings.demo_mode:
        demo = db.fetch_one("SELECT * FROM users WHERE login='alex-visionpr'")
        if demo:
            return demo
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connect GitHub to continue.")


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "visionpr-api"}


@app.get("/api/auth/config")
async def auth_config() -> dict[str, bool]:
    return {"oauth_configured": settings.oauth_configured, "demo_mode": settings.demo_mode}


@app.get("/api/auth/me")
async def auth_me(user: Annotated[dict[str, Any] | None, Depends(optional_user)]) -> dict[str, Any]:
    if user:
        return {"authenticated": True, "user": public_user(user), "mode": "github"}
    if settings.demo_mode:
        demo = db.fetch_one("SELECT * FROM users WHERE login='alex-visionpr'")
        demo_user = public_user(demo)
        if demo_user:
            demo_user.update({"login": "demo", "display_name": "Demo Workspace"})
        return {"authenticated": False, "user": demo_user, "mode": "demo"}
    return {"authenticated": False, "user": None, "mode": "github"}


@app.get("/api/auth/github/start")
async def github_start() -> Response:
    if not settings.oauth_configured:
        if settings.demo_mode:
            return RedirectResponse(f"{settings.frontend_url}/dashboard?demo=1")
        raise HTTPException(status_code=503, detail="GitHub OAuth is not configured on this server.")
    oauth_state = new_oauth_state()
    query = urlencode(
        {
            "client_id": settings.github_client_id,
            "redirect_uri": f"{settings.backend_url}/api/auth/github/callback",
            "scope": "public_repo read:user user:email",
            "state": oauth_state,
            "allow_signup": "true",
            "prompt": "select_account",
        }
    )
    response = RedirectResponse(f"https://github.com/login/oauth/authorize?{query}")
    response.set_cookie(OAUTH_COOKIE, oauth_state, httponly=True, samesite=cookie_samesite(), secure=settings.cookie_secure, max_age=600)
    return response


@app.get("/api/auth/github/callback")
async def github_callback(code: str, state: str, visionpr_oauth_state: Annotated[str | None, Cookie()] = None) -> Response:
    if not visionpr_oauth_state or not secrets.compare_digest(state, visionpr_oauth_state):
        raise HTTPException(status_code=400, detail="GitHub OAuth state did not match.")
    async with httpx.AsyncClient(timeout=20) as client:
        token_response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": settings.github_client_id,
                "client_secret": settings.github_client_secret,
                "code": code,
            },
        )
        token_payload = token_response.json()
        token = token_payload.get("access_token")
        if not token:
            raise HTTPException(status_code=400, detail="GitHub did not return an access token.")
        profile_response = await client.get(
            "https://api.github.com/user",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        )
        profile_response.raise_for_status()
    user = upsert_user(profile_response.json(), token)
    session_id = create_session(int(user["id"]))
    response = RedirectResponse(f"{settings.frontend_url}/dashboard?connected=1")
    response.set_cookie(SESSION_COOKIE, session_id, httponly=True, samesite=cookie_samesite(), secure=settings.cookie_secure, max_age=604800)
    response.delete_cookie(OAUTH_COOKIE)
    return response


@app.post("/api/auth/logout", status_code=204, response_class=Response)
async def logout(response: Response, visionpr_session: Annotated[str | None, Cookie()] = None) -> Response:
    if visionpr_session:
        db.execute("DELETE FROM sessions WHERE id=?", (visionpr_session,))
    response.delete_cookie(SESSION_COOKIE)
    response.delete_cookie(OAUTH_COOKIE)
    response.status_code = 204
    return response


@app.get("/api/dashboard")
async def dashboard_route(_: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    return dashboard()


@app.get("/api/projects")
async def projects_route(_: Annotated[dict[str, Any], Depends(current_user)]) -> list[dict[str, Any]]:
    return db.fetch_all("SELECT * FROM projects ORDER BY updated_at DESC")


@app.get("/api/reviews")
async def reviews_route(_: Annotated[dict[str, Any], Depends(current_user)]) -> list[dict[str, Any]]:
    return list_reviews()


@app.get("/api/reviews/{review_id}")
async def review_route(review_id: int, _: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found.")
    return review


@app.get("/api/reviews/{review_id}/diff")
async def review_diff(review_id: int, user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review or not review.get("pr_number"):
        return {"files": []}
    repository = github_client_for_user(user).get_repo(review["repository"])
    files = [
        {
            "filename": item.filename,
            "status": item.status,
            "additions": item.additions,
            "deletions": item.deletions,
            "changes": item.changes,
            "patch": item.patch or "Binary or oversized diff is available on GitHub.",
        }
        for item in repository.get_pull(int(review["pr_number"])).get_files()
    ]
    return {"files": files}


@app.post("/api/reviews", status_code=201)
async def create_review_route(payload: ReviewCreate, user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    data = payload.model_dump(mode="json")
    return create_review(data, int(user["id"]))


@app.post("/api/reviews/{review_id}/upload")
async def upload_evidence(
    review_id: int,
    _: Annotated[dict[str, Any], Depends(current_user)],
    file: UploadFile = File(...),
) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found.")
    suffix = Path(file.filename or "meeting.mp4").suffix.lower()
    if suffix not in {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".json"}:
        raise HTTPException(status_code=415, detail="Upload a supported recording or intelligence JSON file.")
    destination = settings.upload_dir / review["run_id"] / f"source{suffix}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    size = 0
    with destination.open("wb") as output:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > 500 * 1024 * 1024:
                output.close()
                destination.unlink(missing_ok=True)
                raise HTTPException(status_code=413, detail="Recording exceeds the 500 MB upload limit.")
            output.write(chunk)
    source_type = "intelligence" if suffix == ".json" else "recording"
    db.execute(
        "UPDATE reviews SET source_type=?,source_value=?,current_step=2,updated_at=? WHERE id=?",
        (source_type, str(destination), utc_now(), review_id),
    )
    add_event(review_id, "evidence_uploaded", f"{file.filename or 'Evidence'} uploaded and ready for analysis.")
    return {"path": str(destination), "size": size, "source_type": source_type}


@app.put("/api/reviews/{review_id}/tasks")
async def save_tasks(
    review_id: int,
    tasks: list[TaskDraft],
    _: Annotated[dict[str, Any], Depends(current_user)],
) -> dict[str, Any]:
    if not review_detail(review_id):
        raise HTTPException(status_code=404, detail="Review not found.")
    for task in tasks:
        db.execute(
            """INSERT INTO review_tasks(review_id,task_number,title,timestamp,transcript,status)
               VALUES(?,?,?,?,?,'approved') ON CONFLICT(review_id,task_number) DO UPDATE SET title=excluded.title,timestamp=excluded.timestamp,transcript=excluded.transcript,status='approved'""",
            (review_id, task.task_number, task.title, task.timestamp, task.transcript),
        )
    db.execute("UPDATE reviews SET current_step=3,status='READY',updated_at=? WHERE id=?", (utc_now(), review_id))
    add_event(review_id, "tasks_confirmed", f"{len(tasks)} extracted task(s) approved for implementation.")
    return review_detail(review_id) or {}


@app.post("/api/reviews/{review_id}/start", status_code=202)
async def start_review(review_id: int, user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found.")
    if not review.get("source_value"):
        raise HTTPException(status_code=409, detail="Add a recording, YouTube URL, or intelligence file first.")
    if not user.get("encrypted_token") and not settings.demo_mode:
        raise HTTPException(status_code=403, detail="Connect GitHub before starting repository work.")
    pid = start_worker(review_id)
    db.execute("UPDATE reviews SET status='QUEUED',current_step=3,updated_at=? WHERE id=?", (utc_now(), review_id))
    add_event(review_id, "queued", "Repository work queued in an isolated VisionPR worker.", {"worker_pid": pid})
    return {"status": "QUEUED", "worker_pid": pid}


@app.post("/api/reviews/{review_id}/feedback")
async def request_changes(review_id: int, payload: FeedbackCreate, user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review or not review.get("pr_number"):
        raise HTTPException(status_code=409, detail="This review does not have an open pull request.")
    repository = github_client_for_user(user).get_repo(review["repository"])
    comment = repository.get_pull(int(review["pr_number"])).create_issue_comment(payload.body)
    db.execute("UPDATE reviews SET status='CHANGES_REQUESTED',updated_at=? WHERE id=?", (utc_now(), review_id))
    db.execute("UPDATE review_tasks SET status='changes_requested',feedback=? WHERE review_id=?", (payload.body, review_id))
    pid = start_worker(review_id, mode="feedback")
    add_event(
        review_id,
        "changes_requested",
        "Changes requested. VisionPR will update the same pull request.",
        {"comment_url": comment.html_url, "worker_pid": pid},
    )
    return {"status": "CHANGES_REQUESTED", "comment_url": comment.html_url, "worker_pid": pid}


@app.post("/api/reviews/{review_id}/accept")
async def accept_review(review_id: int, _: AcceptRequest, user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review or review.get("status") not in {"AWAITING_HUMAN_REVIEW", "PR_OPENED", "CHANGES_REQUESTED"}:
        raise HTTPException(status_code=409, detail="This pull request is not ready to accept.")
    db.execute("UPDATE reviews SET status='ACCEPTED',updated_at=? WHERE id=?", (utc_now(), review_id))
    db.execute("UPDATE review_tasks SET status='accepted' WHERE review_id=?", (review_id,))
    add_event(review_id, "accepted", f"Changes accepted by {user['login']}. Merge remains a separate action.")
    return {"status": "ACCEPTED", "merge_available": True}


@app.post("/api/reviews/{review_id}/merge")
async def merge_review(review_id: int, payload: MergeRequest, user: Annotated[dict[str, Any], Depends(current_user)]) -> dict[str, Any]:
    review = review_detail(review_id)
    if not review or review.get("status") != "ACCEPTED" or not review.get("pr_number"):
        raise HTTPException(status_code=409, detail="Accept the reviewed changes before merging.")
    repository = github_client_for_user(user).get_repo(review["repository"])
    result = repository.get_pull(int(review["pr_number"])).merge(merge_method=payload.method)
    if not result.merged:
        raise HTTPException(status_code=409, detail=result.message or "GitHub did not merge the pull request.")
    db.execute("UPDATE reviews SET status='MERGED',updated_at=? WHERE id=?", (utc_now(), review_id))
    db.execute("UPDATE review_tasks SET status='merged' WHERE review_id=?", (review_id,))
    add_event(review_id, "merged", f"Pull request merged with {payload.method}.", {"sha": result.sha})
    return {"status": "MERGED", "sha": result.sha}
