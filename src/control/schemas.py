from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class CreateJobBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    message_body: str = Field(min_length=1, max_length=8000)
    min_interval_sec: int = Field(default=240, ge=10, le=3600)
    max_interval_sec: int = Field(default=300, ge=10, le=7200)
    max_freelancers: int = Field(default=10, ge=1, le=500)
    dry_run: bool = True

    @field_validator("keyword")
    @classmethod
    def strip_keyword(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("keyword required")
        return v

    @field_validator("subject", "message_body")
    @classmethod
    def strip_text(cls, v: str) -> str:
        v = (v or "").strip()
        if not v:
            raise ValueError("required")
        return v

    @field_validator("max_interval_sec")
    @classmethod
    def max_ge_min(cls, v: int, info) -> int:
        mn = info.data.get("min_interval_sec", 240)
        if v < mn:
            raise ValueError("max_interval_sec must be >= min_interval_sec")
        return v


class HeartbeatBody(BaseModel):
    status: str = Field(default="online")
    last_error: str | None = None


class JobEventBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    level: str = "info"


class JobStatsBody(BaseModel):
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    status: str = "completed"


class ContactUpsertBody(BaseModel):
    profile_key: str
    display_name: str | None = None
    conversation_id: str | None = None
    status: str = "contacted"
    stage: str | None = None


class BotSettingsBody(BaseModel):
    outreach_subject: str | None = None
    outreach_body: str | None = None
    github_unlock_after_messages: int | None = Field(default=None, ge=1, le=100)
    max_messages_per_applicant: int | None = Field(default=None, ge=5, le=100)


class BotSettings(BaseModel):
    outreach_subject: str = (
        "FastAPI/Next.js billing module – remote contract, ~1 month"
    )
    outreach_body: str = ""
    github_unlock_after_messages: int = 20
    max_messages_per_applicant: int = 30
    updated_at: datetime | None = None
