from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class ChatDeliverBody(BaseModel):
    """Extension confirms a Postfach send so we may persist the bot message."""

    applicant_id: int
    conversation_id: str | None = None
    body: str | None = None


class CreateJobBody(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=300)
    message_body: str = Field(min_length=1, max_length=8000)
    dry_run: bool = True
    # Account-safety defaults: ≥6–9 minutes between live DMs
    min_interval_sec: int = Field(default=360, ge=60, le=3600)
    max_interval_sec: int = Field(default=540, ge=60, le=7200)
    max_freelancers: int = Field(default=5, ge=1, le=40)

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

    @field_validator("min_interval_sec")
    @classmethod
    def floor_min_interval(cls, v: int, info) -> int:
        # Live jobs cannot go below 6 minutes even if the UI posts a lower value.
        dry = info.data.get("dry_run", True)
        if dry is False and v < 360:
            return 360
        return v

    @field_validator("max_interval_sec")
    @classmethod
    def max_ge_min(cls, v: int, info) -> int:
        mn = info.data.get("min_interval_sec", 360)
        dry = info.data.get("dry_run", True)
        if dry is False and v < 540:
            v = max(v, 540)
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


class ChatTurnBody(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=120)
    message: str = Field(min_length=1, max_length=12000)
    display_name: str | None = None
    profile_key: str | None = None

    @field_validator("conversation_id", "message", "display_name", "profile_key")
    @classmethod
    def strip_optional(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class OutreachPersonalizeBody(BaseModel):
    """Extension: personalize outreach DM from scraped profile facts."""

    display_name: str | None = None
    title: str | None = None
    location: str | None = None
    skills: str | None = None
    experience: str | None = None
    subject: str | None = None
    message_body: str = Field(default="", max_length=8000)
    project_hint: str | None = None

    @field_validator(
        "display_name",
        "title",
        "location",
        "skills",
        "experience",
        "subject",
        "message_body",
        "project_hint",
    )
    @classmethod
    def strip_fields(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


class ApplicantUpsertBody(BaseModel):
    conversation_id: str | None = None
    profile_key: str | None = None
    display_name: str | None = None
    stage: str = "outreach_sent"
    outreach_subject: str | None = None
    outreach_body: str | None = None


class FollowUpDeliverBody(BaseModel):
    delivered: bool = False


class BotSettingsBody(BaseModel):
    outreach_subject: str | None = None
    outreach_body: str | None = None
    github_unlock_after_messages: int | None = Field(default=None, ge=8, le=30)
    max_messages_per_applicant: int | None = Field(default=None, ge=10, le=40)
    automation_paused: bool | None = None


class BotSettings(BaseModel):
    outreach_subject: str = (
        "FastAPI/Next.js billing module – remote contract, about 1 month"
    )
    outreach_body: str = ""
    github_unlock_after_messages: int = 24
    max_messages_per_applicant: int = 30
    # Dashboard Stop sets this; Start clears it. Extension pauses inbox+outreach.
    automation_paused: bool = False
    updated_at: datetime | None = None
