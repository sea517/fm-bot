from pathlib import Path

from sqlalchemy import select, update

from src.config import (
    PROJECT_DESCRIPTION_FILE,
    PROJECT_EXTERNAL_ID,
    PROJECT_TITLE,
    ROOT_DIR,
)
from src.db.models import Project, SessionLocal, init_db


def _description_path() -> Path:
    path = Path(PROJECT_DESCRIPTION_FILE)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def load_env_project_brief() -> tuple[str, str, str]:
    description = _description_path().read_text(encoding="utf-8").strip()
    return PROJECT_EXTERNAL_ID, PROJECT_TITLE, description


def get_project_for_chat(project_id: str | None = None) -> Project | None:
    session = SessionLocal()
    try:
        if project_id:
            project = session.execute(
                select(Project).where(Project.external_id == project_id)
            ).scalar_one_or_none()
            if project:
                return project

        return session.execute(
            select(Project)
            .where(Project.is_active.is_(True))
            .order_by(Project.created_at.desc())
        ).scalar_one_or_none()
    finally:
        session.close()


def sync_active_project(external_id: str, title: str, description: str) -> None:
    init_db()
    session = SessionLocal()
    try:
        session.execute(update(Project).where(Project.is_active.is_(True)).values(is_active=False))

        existing = session.execute(
            select(Project).where(Project.external_id == external_id)
        ).scalar_one_or_none()

        if existing:
            existing.proj_title = title
            existing.proj_description = description
            existing.is_active = True
        else:
            session.add(
                Project(
                    external_id=external_id,
                    proj_title=title,
                    proj_description=description,
                    is_active=True,
                )
            )
        session.commit()
    finally:
        session.close()


def sync_active_project_from_env() -> None:
    external_id, title, description = load_env_project_brief()
    sync_active_project(external_id, title, description)
