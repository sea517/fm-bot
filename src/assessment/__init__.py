"""Freelancermap post-outreach assessment chat."""

from src.assessment.engine import process_freelancer_message
from src.assessment.templates import (
    MSG_ASK_GITHUB,
    MSG_ASSIGNMENT_INVITED,
    MSG_ASSIGNMENT_RECEIVED,
)

__all__ = [
    "process_freelancer_message",
    "MSG_ASK_GITHUB",
    "MSG_ASSIGNMENT_INVITED",
    "MSG_ASSIGNMENT_RECEIVED",
]
