"""Conversation stage constants."""

# freelancermap
STAGE_NEW = "new"
STAGE_WAITING_REPLY_1 = "waiting_reply_1"
STAGE_WAITING_REPLY_2 = "waiting_reply_2"
STAGE_WAITING_REPLY_3 = "waiting_reply_3"

# Oliver inbox
STAGE_WAITING_EMAIL_RESUME = "waiting_email_resume"
STAGE_WAITING_EMAIL_PROCESS = "waiting_email_process"
STAGE_WAITING_SLACK_EMAIL = "waiting_slack_email"

# Slack assessment
STAGE_WAITING_JOIN = "waiting_join"
STAGE_WAITING_AVAILABILITY = "waiting_availability"
STAGE_WAITING_START = "waiting_start"  # availability booked for a later slot
STAGE_WAITING_READY = "waiting_ready"  # at promised time — confirm they can start
STAGE_WAITING_INTRO = "waiting_intro"
STAGE_WAITING_RECENT_PROJECT = "waiting_recent_project"
STAGE_WAITING_TECH_ANSWERS = "waiting_tech_answers"
STAGE_GEMINI_FOLLOWUP_1 = "gemini_followup_1"
STAGE_GEMINI_FOLLOWUP_2 = "gemini_followup_2"
STAGE_GEMINI_FOLLOWUP_3 = "gemini_followup_3"
STAGE_WAITING_GITHUB_ASK = "waiting_github_ask"
STAGE_WAITING_GITHUB = "waiting_github"
STAGE_GITHUB_INVITED = "github_invited"
STAGE_ASSIGNMENT_NOTIFIED = "assignment_notified"
STAGE_ASSIGNMENT_SUBMITTED = "assignment_submitted"
STAGE_WAITING_FINAL_REJECTION = "waiting_final_rejection"
STAGE_WAITING_CHANNEL_CLEANUP = "waiting_channel_cleanup"
STAGE_COMPLETED = "completed"

STAGE_FAILED = "failed"

CHANNEL_FREELANCERMAP = "freelancermap"
CHANNEL_EMAIL = "email"
CHANNEL_SLACK = "slack"
CHANNEL_NONE = "none"
