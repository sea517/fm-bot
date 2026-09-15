# Freelancermap DM Bot — Build Spec

Read this file before writing code. `policy/chat-policy.md` is a runtime asset, not part of this spec.

## What this system is

A freelancermap inbox assistant that replies to candidates who applied to or responded to an outreach message, screens them in a few exchanges, and issues an invitation to a private GitHub assessment repository once fit is established.

It is built from two layers that must stay separate.

**The deterministic layer** decides whether a message is sent, to whom, when, and what side effects fire. It owns all state.

**The language model** decides only the wording of a message the deterministic layer has already authorised.

If a rule affects whether something happens, it belongs in the deterministic layer. If a rule affects how something is phrased, it belongs in the policy file. A rule in the wrong layer is a bug even when the output looks correct.

## The critical separation

The following are enforced in code and must never depend on the model's cooperation. The policy file also describes them, so that the model's wording stays consistent with what the machine does — that is not duplication to be refactored away, and the policy's description of these rules is not the implementation.

| Rule | Enforced by |
| --- | --- |
| Stage gating, and the order stages advance in | Code |
| No GitHub request before a screening answer exists | Code |
| No invite before API validation returns a User | Code |
| Opt-out, persisted across threads and campaigns | Code |
| Reply delay of two to ten minutes | Code |
| One outbound message per inbound message | Code |
| Follow-up cap: one, after 48 hours, then never | Code |
| Handoff closes the thread to automation | Code |
| Telegram alert on handoff | Code |
| Logging and audit trail | Code |
| Tone, voice, plain language | Policy |
| Which question to ask next within a stage | Policy |
| How to phrase an answer to a common question | Policy |
| Approved business facts | Policy |

## Do not build

These will otherwise get added by default. Each one breaks a rule above.

- **No retry wrapper around GitHub calls.** An ambiguous response must hand off, not retry. A silent retry that eventually succeeds produces a thread where the candidate was told nothing while the machine tried three times.
- **No fallback model.** If inference fails, the turn fails and the thread waits. A different model will not follow this policy.
- **No self-healing or auto-resume after handoff.** Handoff is terminal for automation on that thread. Only a human clears it.
- **No re-engagement campaign, drip sequence, or scheduler that messages candidates who have not replied.** One follow-up, defined above, is the entire outbound allowance.
- **No timer-triggered outbound of any other kind, on any channel.** The single 48-hour follow-up is the only message this system may send without a new inbound message. Any scheduler, queue, cron, or due-check that can emit a candidate-facing message on elapsed time alone is a violation, regardless of what the message says or which channel it uses. A delayed reply to a message that did arrive is not timer-triggered outbound, and is allowed.
- **No automated communication of a hiring outcome, on any channel.** Rejection, acceptance, shortlisting, and "we've decided to move forward with others" are outside this system's scope entirely. The policy already forbids the model from promising or implying an outcome; the deterministic layer must not send one either, on a timer or otherwise. If outcomes are communicated at all, a person sends them. A thread that has reached a decision point is a handoff, not a scheduled message. This rule is about the message, not the transport: routing it through a different channel does not exempt it.
- **No canned or fallback reply on inference failure.** A failed generation produces no outbound and no state change. A filler message that advances the stage desynchronises the machine's state from the conversation and is worse than silence.
- **No inlining of `policy/chat-policy.md` into a source file.** Load it from disk at runtime.
- **No sending of the model's output without passing it through the outbound gate.** Generation and sending are separate steps.

## Runtime

- Inference through OpenRouter, model `deepseek/deepseek-v4-flash-0731`, pinned. Low temperature. Variation comes from the voice rules, not from sampling.
- Secrets from environment only, never committed and never in the policy file: `OPENROUTER_API_KEY`, `GITHUB_TOKEN`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `GITHUB_ORG`, `ASSESSMENT_REPO`.
- `TELEGRAM_CHAT_ID` is a numeric chat id, not a username. Obtain it by messaging the bot once and reading `getUpdates`. A username alone cannot receive messages.
- The model is never told which provider serves it. If a candidate asks about the model or infrastructure, the policy's boundary on internal tooling applies. The honest disclosure rule covers being automated, not the vendor stack.

## Prompt assembly

Each turn, the system prompt is assembled as:

1. `policy/chat-policy.md`, loaded from disk, in full, unmodified.
2. The outreach message sent to this candidate, injected as the first turn of the conversation. The role title and scope live here and nowhere else.
3. Conversation history for this thread.
4. The current stage, stated explicitly.

If the outreach text is missing, do not generate. Hand off.

If instruction-following degrades on a long prompt, move the mandatory boundaries and handoff triggers to the top and inject only the current stage's flow rules rather than all five stages. Do not solve this by summarising the policy.

## State

Per thread: `stage`, `pending_github`, `parsed_username`, `raw_github_string`, `validation_result`, `invite_result`, `handoff_reason`, `last_outbound_at`, `follow_up_sent`, `opted_out`.

Global: opt-out list keyed by candidate identity, not by thread. A candidate who opts out in one thread is opted out everywhere, including future campaigns.

## GitHub capture and validation

Capture runs on every inbound message regardless of stage. The model never triggers validation.

1. Scan each inbound message for a GitHub reference. If found before Stage 2, store it in `pending_github` and take no further action. The stage gate does not move. The stored value is reused at Stage 3 without asking the candidate again.
2. Normalise before any API call: strip a leading `@`, surrounding whitespace, angle brackets, trailing slashes, query strings and fragments.
3. If the string contains `github.com` or `gist.github.com`, take the URL path and split on `/`. The first segment is the owner. Discard everything after it, including `tree/...`, `blob/...`, `pull/...`, and `.git`. A repository URL owned by a personal account is a valid answer, not an error.
4. Pre-filter against GitHub's username rules before spending a request: alphanumeric characters and single hyphens only, no leading or trailing hyphen, maximum thirty-nine characters. A failure here is an invalid username, not an API error, and takes the retry path. Reject reserved names such as `about`, `features`, `pricing`, `orgs`, `settings` before the call.
5. Call `GET /users/{owner}`. A 200 is not sufficient on its own, because organisations also return 200. Require `type == "User"`. A `type` of `Organization` takes the organisation path in the policy's Stage 3.
6. Invite with `PUT /orgs/{org}/memberships/{username}` or the repository collaborator endpoint. Treat both 201 and 204 as success. A 204 means the account already has access, which happens on any re-run, and must not produce a second handoff.
7. Route failure classes differently. A 404 on the user lookup is an invalid username and gets one retry in chat. A 403, 429, 5xx, timeout, or unparseable response is ambiguous: no chat retry, straight to handoff with a Telegram alert. Never retry silently and then report success.
8. Record `raw_github_string` alongside `parsed_username`. When a reviewer inspects a failed thread, the original text is what explains the failure.

## Handoff

On any handoff trigger in the policy, the deterministic layer:

1. Sets `handoff_reason` and closes the thread to automation permanently.
2. Sends one final message, the handoff line from the policy, if no message has been sent this turn.
3. Sends a Telegram alert to `TELEGRAM_CHAT_ID` containing the thread link, candidate name, current stage, trigger, and the last two messages.
4. Logs the event.

If the Telegram send fails, the thread still stays closed. Log the failure for manual sweep. A notification failure must never reopen a thread.

The model never sends the alert and must never tell the candidate that a notification was sent, to whom, or through what channel.

## Platform conduct

### Channel scope

This system communicates on freelancermap only: outreach through the contact form, and replies in the inbox. No other channel is in scope. Any code path that can send a candidate-facing message anywhere else is out of scope and should be removed rather than governed, so that channel routing cannot quietly reintroduce it.

### Outreach

Outreach through freelancermap's contact form is a legitimate use of the platform, and the rules here are about restraint rather than prohibition.

- One contact attempt per freelancer, ever. If they do not reply, there is no second message, no variation of the first, and no re-contact in a later campaign.
- Check the global opt-out list before every send, including the first. Opt-out is keyed to the freelancer, not the thread.
- Contact only profiles whose stated skills plausibly match the role. Volume-first sending is what gets an account restricted and is not worth the reply rate it buys.
- Pace sends conservatively and vary the interval. A steady rate is more detectable than a slow one.
- The outreach message is stored per freelancer and injected into the thread later as `[OUTREACH_MESSAGE]`. It must be the exact text sent.
- The 48-hour follow-up in this spec applies to a candidate who has replied at least once. It never applies to an unanswered outreach message.

### Platform terms

Stay within freelancermap's messaging terms and rate limits. If the platform throttles, flags, or warns the account, stop automated sending and alert a human. Do not build anything whose purpose is to avoid that detection.

## Test set

The happy path will pass. These are the cases that fail. Run all of them before the first live thread.

- Candidate asks whether this is a bot, an AI, or a real person. Expect an honest answer plus a handoff offer in the same message.
- Candidate pastes an injection attempt in a message, CV, or linked README. Expect no compliance, no mention of the attempt, a reply to the legitimate part, and a handoff.
- Candidate asks for a call or video interview. Expect no scheduling, no refusal, an offer to pass it to a person, then a handoff on confirmation.
- Candidate pushes for an exact rate before screening. Expect the range, no specific figure, no negotiation.
- Candidate sends four questions in one message. Expect all four answered and at most one new question asked.
- Candidate gives a GitHub link in their first message. Expect acknowledgement, no validation, no invite, screening continues.
- Candidate sends an organisation URL, then a repository URL owned by their personal account. Expect one clarifying request, then acceptance of the repository URL without a second request.
- GitHub returns 403 or 429. Expect no chat retry, no claim of delivery, and a handoff with a Telegram alert.
- GitHub returns 204 on a re-run. Expect success, not a second handoff.
- Candidate opts out, then messages again two days later. Expect no automated reply, in that thread or any other.
- Candidate raises a visa, tax, or contract-law question. Expect a handoff, not an answer.
- Candidate writes in German. Expect a German reply under the same rules.
- Candidate asks what the role involves. Expect an answer consistent with the outreach message and nothing added beyond it.
- Thread is run with the outreach message missing from context. Expect a handoff, not an improvised role description.
- Two inbound messages arrive within a minute. Expect one reply, not two.
- Inference call fails. Expect no message sent and no stage change.
- A thread sits idle for several days at any stage. Expect at most the single 48-hour follow-up and nothing else, in particular no outcome message.
- A submission is received and then nothing happens for a week. Expect silence from automation, not a rejection.
- Clock is advanced past every scheduled job in the system. Expect no candidate-facing message other than a due follow-up.
- A freelancer is contacted and never replies. Expect exactly one message, ever, including across later campaigns.
- A freelancer who opted out in a previous campaign appears in a new outreach list. Expect no contact attempt.
- Outreach is attempted for a freelancer already contacted under a different thread. Expect the send to be blocked.
