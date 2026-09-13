"""Fixed freelancermap assessment messages (ASD-STE100)."""

MSG_ASK_GITHUB = (
    "Thank you. Our team will review your answers.\n"
    "Next, I will send a short take-home assignment. "
    "This assignment measures your development speed and quality.\n"
    "Please send your GitHub username."
)

MSG_ASSIGNMENT_INVITED = (
    "I invited you to the assignment repository. "
    "Open your email and read all requirements in README.md.\n"
    "I will review your work with our team in about 3 hours."
)

MSG_ASSIGNMENT_RECEIVED = (
    "Thank you for your work. We will review it and tell you the result."
)

MSG_FINAL_REJECTION = (
    "Hello {name},\n"
    "Thank you for your time on this assessment. "
    "We reviewed your work carefully. "
    "We will continue with another candidate for this role.\n"
    "We value your interest. "
    "We may contact you again for a future product that fits your skills.\n"
    "Thank you again. We wish you success."
)


def rejection_message(display_name: str | None) -> str:
    first = (display_name or "").strip().split()[0] if display_name else ""
    name = first or "there"
    return MSG_FINAL_REJECTION.format(name=name)
