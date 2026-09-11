MSG_FM_REPLY_3 = """We reviewed your background and it relevant to our current product.
Please contact to our technical lead[oliver@kontrora.com] with your resume.
In your email, please mention that Sabrina referred you after reviewing your resume.

Our tech lead will review your technical background and follow up with the next steps if there is a strong fit.

Best regards"""

MSG_EMAIL_PROCESS = """Hello,

Thank you for reaching out and for sharing your resume.

We would like to learn more about your technical experience and practical development skills. As the next step, we would like to invite you to our team's Slack workspace, where our technical team will guide you through the assessment process.

Our technical process consists of three stages:

1. Text-based technical discussion
    We will begin with a discussion-based evaluation conducted through Slack. This will help us better understand your technical background, experience, and approach to problem-solving.
2. Practical assessment
    If the initial discussion goes well, you will receive a short take-home assignment based on a simplified version of our actual technology stack. The assignment is designed to take approximately 2–3 hours to complete.
3. Technical interview
    If your assessment results are a good fit, we will schedule a technical interview, typically on the following day, to discuss your implementation, technical decisions, and overall approach.

Our team is generally available Monday through Friday, from 10:00 a.m. to 5:00 p.m. EDT.

Please reply with the email address you would like us to use for the Slack invitation, along with your availability to begin the first stage of the assessment.

Once you join Slack, our technical team will explain the process, answer any initial questions, and guide you through the next steps."""

MSG_SLACK_INVITED = "Invited. Please check your email and let me know on Slack after joining."

MSG_ASK_AVAILABILITY = (
    "Hi — glad you're here.\n\n"
    "Our technical assessment on Slack runs Monday through Friday, "
    "from 10:00 a.m. to 5:00 p.m. EDT.\n\n"
    "Please share your availability within that window so we can start the "
    "text-based technical discussion."
)

MSG_AVAILABILITY_ACK = (
    "Thanks for sharing your availability. We'll run the assessment during our "
    "team hours: Monday through Friday, 10:00 a.m. to 5:00 p.m. EDT."
)

# Short agreement when they propose a concrete later slot (time filled in at send).
MSG_AVAILABILITY_AGREE_LEADS = (
    "Sounds good",
    "That works",
    "Perfect",
    "Okay",
)

MSG_ASK_READY = (
    "Hi — it's the time we scheduled for the technical discussion. "
    "Are you ready to begin?"
)

MSG_ASK_INTRO = (
    "Okay, to begin, could you share a brief introduction including your location, "
    "experience about yourself and your background?"
)

MSG_ASK_RECENT_PROJECT = (
    "Sounds great, what kind of project did you work on most recently and what did you implement?"
)

MSG_TECH_QUESTIONS = """Okay, I will ask a few questions related to our current project.
Please answer within 15 mins.
Note : Don't use AI. We will identify.

Nearly half our route handlers use the service-role key, which bypasses RLS, and enforce tenant isolation with an application-level permission check instead. What's the security risk of that split, and how would you make sure a developer adding a new route can't accidentally leak one tenant's data to another? Is "RLS at the database level" even a true claim if half the routes bypass it?
Our Next.js middleware only checks that an auth cookie exists, not that it's valid, anyone can set a fake sb- cookie and get past it. Why might we have done that, where is the real security boundary in this architecture, and what breaks if a developer assumes the middleware protects a route?
We cache permissions in an in-memory Map in the API process, and we want to run multiple API instances. Walk me through what happens when an admin revokes a user's role, on which instances does that take effect, and when? How would you fix the staleness without killing performance?"""

MSG_TECH_TIME_PASSED = "The time was passed"

MSG_ASK_GITHUB = (
    "Okay. Our team members will review your answer.\n"
    "Next, I will share a simple take-home assignment to check your development speed and quality.\n"
    "Please share your github username."
)

MSG_NEED_GITHUB_ACCOUNT = (
    "Thanks for letting me know. For this step we need a GitHub account so we can share "
    "the private assignment repo and review your submission via a fork/PR. "
    "Please create a free account at https://github.com/signup and reply here with your "
    "username once it's ready. We can't accept the assignment as a ZIP in Slack."
)

MSG_ASSIGNMENT_INVITED = (
    "Invited you to the assignment. Check your email and follow all of requirements in "
    "readme.md on time. Will get back to you 3 hours later and review with our team."
)

MSG_ASSIGNMENT_CHECK_IN = (
    "Just checking in — have you completed the assignment yet? "
    "Please reply here once you've submitted following the readme."
)

MSG_ASSIGNMENT_RECEIVED = "Okay, let us check and let you know after 3 days later"

MSG_ASSIGNMENT_FORK_ACCESS = "That's fine because you forked our repository"

MSG_FINAL_REJECTION = """Hi,
Thank you very much for the time and effort you put into the assessment. We were impressed with your work; however, after careful consideration, we have decided to move forward with another candidate for this position.
We truly appreciate your interest in working with us and would be happy to contact you when we begin development on future products that may be a good fit for your skills.
Thank you again, and we wish you continued success."""

TECH_QUESTIONS_TEXT = MSG_TECH_QUESTIONS
