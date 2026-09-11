"""Fixed freelancermap assessment messages (post-outreach chat)."""

MSG_ASK_GITHUB = (
    "Okay. Our team members will review your answer.\n"
    "Next, I will share a simple take-home assignment to check your development "
    "speed and quality.\n"
    "Please share your github username."
)

MSG_ASSIGNMENT_INVITED = (
    "Invited you to the assignment. Check your email and follow all of "
    "requirements in readme.md on time.\n"
    "Will get back to you 3 hours later and review with our team."
)

MSG_ASSIGNMENT_RECEIVED = (
    "Thank you for your work. Let us check and will let you know after checking"
)

MSG_FINAL_REJECTION = (
    "Hi {name},\n"
    "Thank you very much for the time and effort you put into the assessment. "
    "We were impressed with your work; however, after careful consideration, "
    "we have decided to move forward with another candidate for this position.\n"
    "We truly appreciate your interest in working with us and would be happy to "
    "contact you when we begin development on future products that may be a good "
    "fit for your skills.\n"
    "Thank you again, and we wish you continued success."
)


def rejection_message(display_name: str | None) -> str:
    first = (display_name or "").strip().split()[0] if display_name else ""
    name = first or "there"
    return MSG_FINAL_REJECTION.format(name=name)
