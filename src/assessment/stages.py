"""SPEC stage constants for freelancermap assessment chat."""

# Policy stages 0–4 (+ terminal)
STAGE_0 = "stage_0"
STAGE_1 = "stage_1"
STAGE_2 = "stage_2"
STAGE_3 = "stage_3"
STAGE_4 = "stage_4"
STAGE_HANDED_OFF = "handed_off"
STAGE_OPTED_OUT = "opted_out"
STAGE_CLOSED = "closed"

# Legacy aliases still present in older rows
LEGACY_CHAT = "assessment_chat"
LEGACY_ASK_GITHUB = "ask_github"
LEGACY_WAITING_GITHUB = "waiting_github"
LEGACY_ASSIGNMENT = "assignment_pending"
LEGACY_RECEIVED = "assignment_received"
LEGACY_REJECTION = "rejection_scheduled"
LEGACY_REJECTED = "rejected"
LEGACY_OUTREACH = "outreach_sent"

ACTIVE_CHAT_STAGES = {STAGE_0, STAGE_1, STAGE_2, STAGE_3, STAGE_4, LEGACY_CHAT, LEGACY_OUTREACH}
TERMINAL_STAGES = {STAGE_HANDED_OFF, STAGE_OPTED_OUT, STAGE_CLOSED, LEGACY_REJECTED}

HANDOFF_LINE = (
    "Let me get someone from the team to pick this up, they'll reply here. "
    "Thanks for your patience."
)

FOLLOW_UP_LINE = (
    "Just checking in — still interested in continuing for this role?"
)

OPT_OUT_ACK = "Understood. I won't message you further about this. Thanks for your time."
