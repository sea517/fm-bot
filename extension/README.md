# Freelancermap Outreach Extension

Chrome MV3 worker that claims outreach jobs from the **Control dashboard** and
runs **two tabs in parallel**:

| Role | URL |
|------|-----|
| Send DMs / search | https://www.freelancermap.com/freelancer |
| Assessment chat | https://www.freelancermap.com/app/pobox/main |

Full deploy (Vercel): [docs/DEPLOY_DASHBOARD.md](../docs/DEPLOY_DASHBOARD.md).

## Load unpacked

1. Chrome → `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → this `extension/` folder
4. Stay logged into freelancermap (same profile)
5. Popup: set **Bot ID**, **Control API URL**, **Bot token** → Save & connect

Use a **different Chrome profile** per freelancermap account (Bot 1 / 2 / 3).

## How DM + chat run at the same time

The service worker keeps **two dedicated tabs**:

1. **Outreach tab** — `/freelancer` for keyword search + Contact DMs  
2. **Inbox tab** — `/app/pobox/main` for reading replies and sending assessment chat  

Outreach never navigates the inbox tab, and inbox polling is **not paused** while a DM campaign is running.

## Timing

| Loop | Interval |
|------|----------|
| Claim / manage outreach jobs | ~12s |
| Check inbox for replies | **20s** |
| Delay before sending a chat reply | **Random 5–120s per reply** (longer answers bias higher; never the same wait twice in a row) |
| Delay between Contact DMs | **Random** between dashboard min/max interval (default 240–300s) |

## Popup

| Control | Purpose |
|---------|---------|
| Bot ID | 1, 2, or 3 — must match dashboard tab |
| Control API URL | Defaults to `https://fm-bot.vercel.app` (change only if needed) |
| Bot token | `BOT_N_TOKEN` from server `.env` (not Supabase service role) |
| Poll dashboard | Heartbeat + claim queued jobs (~12s) |
| Local campaign | Optional manual start without the dashboard |

The extension fills **subject + body** and clicks **Send message**. Project
selection is skipped (not required on freelancermap for DM send).

`{detail}` in the message body is optional. DeepSeek (`deepseek/deepseek-v4-flash-0731`
via OpenRouter) rewrites a **unique DM per freelancer** from the shared contact-form
template, grounded in their profile (title, skills, experience). Bracket
placeholders like `[specific detail from their profile]` are stripped and never sent.

## Flow

1. Dashboard on Vercel (e.g. `https://fm-bot.vercel.app`)
2. Dashboard: create job on Bot N (start with **Dry run**)
3. Extension claims the job on `/freelancer`; inbox keeps polling `/app/pobox/main`
4. Events + finish status appear in the dashboard job log

## Message template

Subject / body: `shared/templates.js` (same copy as
`src/freelancermap/outreach_templates.py`).
