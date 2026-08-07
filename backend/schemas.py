from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, HttpUrl


class ReviewCreate(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    repository_url: HttpUrl
    source_type: Literal["youtube", "recording", "intelligence"]
    source_value: str | None = None
    build_commands: list[str] = Field(default_factory=list, max_length=8)
    constraints: list[str] = Field(default_factory=list, max_length=12)


class FeedbackCreate(BaseModel):
    body: str = Field(min_length=3, max_length=4000)


class MergeRequest(BaseModel):
    confirmation: Literal["MERGE"]
    method: Literal["merge", "squash", "rebase"] = "squash"


class AcceptRequest(BaseModel):
    confirmation: Literal["ACCEPT"]


class TaskDraft(BaseModel):
    task_number: int = Field(ge=1)
    title: str = Field(min_length=3, max_length=220)
    timestamp: float | None = Field(default=None, ge=0)
    transcript: str | None = Field(default=None, max_length=8000)
