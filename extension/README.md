# Freelancermap Outreach Extension

Chrome MV3 worker that claims outreach jobs from the **Control dashboard** and
drives the Contact form on [freelancermap.com](https://www.freelancermap.com/freelancer).

Full deploy (Vercel): [docs/DEPLOY_DASHBOARD.md](../docs/DEPLOY_DASHBOARD.md).

## Load unpacked

1. Chrome → `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → this `extension/` folder
4. Open https://www.freelancermap.com/freelancer (logged in)
5. Popup: set **Bot ID**, **Control API URL**, **Bot token** → Save & connect

Use a **different Chrome profile** per freelancermap account (Bot 1 / 2 / 3).

## Popup

| Control | Purpose |
|---------|---------|
| Bot ID | 1, 2, or 3 — must match dashboard tab |
| Control API URL | VPS base URL, e.g. `http://1.2.3.4:8090` |
| Bot token | `BOT_N_TOKEN` from server `.env` (not Supabase service role) |
| Poll dashboard | Heartbeat + claim queued jobs (~12s) |
| Local campaign | Optional manual start without the dashboard |

Select the **project** in the Contact form yourself — the extension fills subject + body only.

## Flow

1. VPS: `PYTHONPATH=. python3 -m src.main control-serve`
2. Dashboard: create job on Bot N (start with **Dry run**)
3. Extension on profile N claims the job and runs the campaign
4. Events + finish status appear in the dashboard job log

## Message template

Subject / body: `shared/templates.js` (same copy as
`src/freelancermap/outreach_templates.py`).
