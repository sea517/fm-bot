from dataclasses import dataclass
from xml.etree.ElementTree import Element, SubElement, tostring


@dataclass
class JobPosting:
    external_id: str
    proj_title: str
    proj_type: str  # "Remote", "Contract", or "Permanent"
    proj_duration: int  # months
    proj_country: str
    proj_city: str = ""
    proj_description: str = ""
    contact_person_forename: str = ""
    contact_person_lastname: str = ""
    contact_email: str = ""
    start_month: int = 1
    start_year: int = 2026
    phone_office: str = ""


def build_feed_xml(jobs: list[JobPosting]) -> str:
    root = Element("projects")
    for job in jobs:
        project = SubElement(root, "project")
        SubElement(project, "external_id").text = job.external_id
        SubElement(project, "proj_title").text = job.proj_title
        SubElement(project, "proj_type").text = job.proj_type
        SubElement(project, "proj_duration").text = str(job.proj_duration)
        SubElement(project, "proj_country").text = job.proj_country

        if job.proj_city:
            SubElement(project, "proj_city").text = job.proj_city
        if job.proj_description:
            SubElement(project, "proj_description").text = job.proj_description
        if job.contact_person_forename:
            SubElement(project, "contact_person_forename").text = job.contact_person_forename
        if job.contact_person_lastname:
            SubElement(project, "contact_person_lastname").text = job.contact_person_lastname
        if job.contact_email:
            SubElement(project, "contact_email").text = job.contact_email
        if job.start_month:
            SubElement(project, "start_month").text = str(job.start_month)
        if job.start_year:
            SubElement(project, "start_year").text = str(job.start_year)
        if job.phone_office:
            SubElement(project, "phone_office").text = job.phone_office

    return tostring(root, encoding="unicode", xml_declaration=True)
