import json
from pathlib import Path

from src.config import FEED_OUTPUT, JOBS_FILE
from src.db.projects import sync_active_project
from src.feed.xml_builder import JobPosting, build_feed_xml


def load_jobs(path: Path = JOBS_FILE) -> list[JobPosting]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    return [JobPosting(**item) for item in raw]


def save_jobs(jobs: list[JobPosting], path: Path = JOBS_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = [
        {
            "external_id": j.external_id,
            "proj_title": j.proj_title,
            "proj_type": j.proj_type,
            "proj_duration": j.proj_duration,
            "proj_country": j.proj_country,
            "proj_city": j.proj_city,
            "proj_description": j.proj_description,
            "contact_person_forename": j.contact_person_forename,
            "contact_person_lastname": j.contact_person_lastname,
            "contact_email": j.contact_email,
            "start_month": j.start_month,
            "start_year": j.start_year,
            "phone_office": j.phone_office,
        }
        for j in jobs
    ]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def generate_feed(output: Path = FEED_OUTPUT) -> Path:
    jobs = load_jobs()
    xml = build_feed_xml(jobs)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(xml, encoding="utf-8")

    if jobs:
        latest = jobs[-1]
        sync_active_project(latest.external_id, latest.proj_title, latest.proj_description)

    return output
