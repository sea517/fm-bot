from pathlib import Path

from src.config import (
    FREELANCERMAP_EMAIL,
    PROJECT_DESCRIPTION_FILE,
    PROJECT_DURATION_MONTHS,
    PROJECT_EXTERNAL_ID,
    PROJECT_TITLE,
    ROOT_DIR,
)
from src.feed.add_job import add_job, read_description


def post_project_from_env() -> None:
    desc_path = Path(PROJECT_DESCRIPTION_FILE)
    if not desc_path.is_absolute():
        desc_path = ROOT_DIR / desc_path
    description = read_description(None, desc_path)
    add_job(
        title=PROJECT_TITLE,
        description=description,
        proj_duration=PROJECT_DURATION_MONTHS,
        contact_email=FREELANCERMAP_EMAIL,
        contact_forename="Sabrina",
        contact_lastname="Kontrora",
        external_id=PROJECT_EXTERNAL_ID,
    )
