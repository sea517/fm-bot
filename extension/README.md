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

## Timing (account safety)

| Loop | Interval |
|------|----------|
| Claim / manage outreach jobs | ~12s |
| Check inbox for replies | **45s** idle · **90s** while a DM job is active |
| Delay before sending a chat reply | **Random 5–120s per reply** |
| Delay between Contact DMs (live) | **≥6–9 min** (dashboard defaults 360–540s; extension enforces floor) |
| UI step pace | **1.8–4.8s** random between clicks |
| After failed open / missing Contact | **50–110s** cool-down; stop after 5 in a row |
| After skip (already contacted / paywall) | **25–55s** |
| Daily live send cap | **25** per Chrome profile |
| Max freelancers / job | default **5** (hard max 40) |

Dry-run can use shorter intervals. Live sends always apply the safety floors.

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
via OpenRouter) rewrites a **unique ASD-STE100 DM per freelancer** from the shared
contact-form template, grounded in their profile (title, skills, experience). Bracket
placeholders like `[specific detail from their profile]` are stripped and never sent.
All bot-written text follows **ASD-STE100** (Simplified Technical English).

## Flow

1. Dashboard on Vercel (e.g. `https://fm-bot.vercel.app`)
2. Dashboard: create job on Bot N (start with **Dry run**)
3. Extension claims the job on `/freelancer`; inbox keeps polling `/app/pobox/main`
4. Events + finish status appear in the dashboard job log

## Message template

Subject / body: `shared/templates.js` (same copy as
`src/freelancermap/outreach_templates.py`).
