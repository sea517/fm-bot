"""Freelancermap conversation templates (ASD-STE100)."""

MSG_FM_REPLY_3 = """We reviewed your background. It is relevant to our current product.
Please contact our technical lead (oliver@kontrora.com) and send your resume.
In your email, write that Sabrina referred you after a resume review.

Our technical lead will review your background. If there is a strong fit, they will send the next steps.

Best regards"""

MSG_ASK_AVAILABILITY = (
    "Hello. Welcome.\n\n"
    "Our technical assessment on Slack runs Monday through Friday, "
    "from 10:00 a.m. to 5:00 p.m. EDT.\n\n"
    "Please send your availability in that window. "
    "Then we can start the text-based technical discussion."
)

MSG_AVAILABILITY_ACK = (
    "Thank you for your availability. We run the assessment in our team hours: "
    "Monday through Friday, 10:00 a.m. to 5:00 p.m. EDT."
)

# Short agreement when they propose a concrete later slot (time filled in at send).
MSG_AVAILABILITY_AGREE_LEADS = (
    "That works",
    "Okay",
    "Agreed",
    "Confirmed",
)

MSG_ASK_READY = (
    "Hello. This is the time we set for the technical discussion. "
    "Are you ready to start?"
)

MSG_ASK_INTRO = (
    "Okay. To start, please send a short introduction. "
    "Include your location, experience, and background."
)

MSG_ASK_RECENT_PROJECT = (
    "Thank you. What project did you work on most recently, "
    "and what did you implement?"
)

MSG_TECH_QUESTIONS = """Okay. I will ask a few questions about our current project.
Please answer within 15 minutes.
Note: Do not use AI. We will detect that.

Nearly half our route handlers use the service-role key, which bypasses RLS, and enforce tenant isolation with an application-level permission check instead. What is the security risk of that split, and how would you make sure a developer who adds a new route cannot leak one tenant's data to another? Is "RLS at the database level" a true claim if half the routes bypass it?
Our Next.js middleware only checks that an auth cookie exists, not that it is valid. Anyone can set a fake sb- cookie and pass it. Why might we have done that, where is the real security boundary in this architecture, and what breaks if a developer assumes the middleware protects a route?
We cache permissions in an in-memory Map in the API process, and we want to run multiple API instances. Walk me through what happens when an admin revokes a user's role. On which instances does that take effect, and when? How would you fix the stale data without killing performance?"""

MSG_ASK_GITHUB = (
    "Okay. Our team will review your answers.\n"
    "Next, I will send a short take-home assignment. "
    "This assignment measures your development speed and quality.\n"
    "Please send your GitHub username."
)

MSG_NEED_GITHUB_ACCOUNT = (
    "Thank you. For this step we need a GitHub account. "
    "We share a private assignment repository and review your work with a fork or pull request. "
    "Please create a free account at https://github.com/signup. "
    "Then reply here with your username. "
    "We cannot accept the assignment as a ZIP file in Slack."
)

MSG_ASSIGNMENT_INVITED = (
    "I invited you to the assignment. "
    "Check your email and follow all requirements in README.md on time. "
    "I will review your work with our team in about 3 hours."
)

MSG_ASSIGNMENT_RECEIVED = (
    "Okay. We will review your work and tell you the result in about 3 days."
)

MSG_ASSIGNMENT_FORK_ACCESS = "That is fine. You forked our repository."

TECH_QUESTIONS_TEXT = MSG_TECH_QUESTIONS
