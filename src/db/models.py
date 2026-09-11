from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from src.config import DATABASE_URL, DATA_DIR


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    external_id: Mapped[str] = mapped_column(String(255), unique=True)
    proj_title: Mapped[str] = mapped_column(String(500))
    proj_description: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )


class Applicant(Base):
    __tablename__ = "applicants"

    id: Mapped[int] = mapped_column(primary_key=True)
    freelancermap_message_id: Mapped[str] = mapped_column(String(255), unique=True)
    freelancermap_project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)

    stage: Mapped[str] = mapped_column(String(50), default="new")
    bot_reply_count: Mapped[int] = mapped_column(Integer, default=0)
    gemini_followup_count: Mapped[int] = mapped_column(Integer, default=0)

    message_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_candidate_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_candidate_message_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    next_action_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    conversation_log: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_reply: Mapped[str | None] = mapped_column(Text, nullable=True)

    github_username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_channel_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_invite_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    slack_last_ts: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    reply_channel: Mapped[str | None] = mapped_column(String(50), nullable=True)
    slack_invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    github_invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    assignment_submitted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    status: Mapped[str] = mapped_column(String(50), default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def _migrate_applicants() -> None:
    """Add columns introduced after the initial applicants table was created."""
    columns = {
        "stage": "VARCHAR(50) DEFAULT 'new'",
        "bot_reply_count": "INTEGER DEFAULT 0",
        "gemini_followup_count": "INTEGER DEFAULT 0",
        "last_candidate_message": "TEXT",
        "last_candidate_message_at": "DATETIME",
        "next_action_at": "DATETIME",
        "conversation_log": "TEXT",
        "github_username": "VARCHAR(255)",
        "slack_channel_id": "VARCHAR(255)",
        "slack_invite_email": "VARCHAR(255)",
        "slack_last_ts": "VARCHAR(64)",
        "email_message_id": "VARCHAR(512)",
        "reply_channel": "VARCHAR(50)",
        "github_invited_at": "DATETIME",
        "assignment_submitted_at": "DATETIME",
    }
    with engine.connect() as conn:
        existing = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(applicants)")).fetchall()
        }
        for name, col_type in columns.items():
            if name in existing:
                continue
            conn.execute(text(f"ALTER TABLE applicants ADD COLUMN {name} {col_type}"))
            logger = __import__("logging").getLogger(__name__)
            logger.info("Migrated applicants: added column %s", name)
        conn.commit()


def init_db() -> None:
    Base.metadata.create_all(engine)
    _migrate_applicants()
