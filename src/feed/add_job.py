import time
from datetime import datetime
from pathlib import Path

from src.feed.generator import generate_feed, load_jobs, save_jobs
from src.feed.xml_builder import JobPosting


def read_description(description: str | None, description_file: Path | None) -> str:
    if description_file:
        return description_file.read_text(encoding="utf-8").strip()
    if description:
        return description.strip()
    raise ValueError("Provide --description or --description-file")


def add_job(
    title: str,
    description: str,
    proj_type: str = "Remote",
    proj_duration: int = 6,
    proj_country: str = "Germany",
    proj_city: str = "",
    contact_email: str = "",
    contact_forename: str = "",
    contact_lastname: str = "",
    start_month: int | None = None,
    start_year: int | None = None,
    external_id: str | None = None,
) -> JobPosting:
    now = datetime.now()
    job_id = external_id or f"job-{int(time.time())}"

    job = JobPosting(
        external_id=job_id,
        proj_title=title,
        proj_type=proj_type,
        proj_duration=proj_duration,
        proj_country=proj_country,
        proj_city=proj_city,
        proj_description=description,
        contact_person_forename=contact_forename,
        contact_person_lastname=contact_lastname,
        contact_email=contact_email,
        start_month=start_month or now.month,
        start_year=start_year or now.year,
    )

    jobs = load_jobs()
    if external_id and any(j.external_id == external_id for j in jobs):
        jobs = [j for j in jobs if j.external_id != external_id]
    jobs.append(job)
    save_jobs(jobs)
    generate_feed()
    return job
