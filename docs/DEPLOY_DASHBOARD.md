# Deploy Freelancermap Control Dashboard (Vercel)

The **dashboard + Control API** run on **Vercel**. The **Chrome extensions** stay on
three local machines/profiles (each logged into a different freelancermap account).
Extensions poll the Vercel URL, claim jobs, and drive the Contact form in the browser.

```
[Dashboard browser] ──Bearer DASHBOARD_API_TOKEN──► https://your-app.vercel.app
[Chrome Bot 1]      ──Bearer BOT_1_TOKEN──────────► same URL ──► Supabase
[Chrome Bot 2]      ──Bearer BOT_2_TOKEN──────────► same URL
[Chrome Bot 3]      ──Bearer BOT_3_TOKEN──────────► same URL
```

Do **not** put `SUPABASE_SERVICE_ROLE_KEY` in the extension. Only the Vercel
function talks to Supabase.

Local `control-serve` still works for development (`PYTHONPATH=. python3 -m src.main control-serve`).

## 1. Supabase schema

In the Supabase SQL Editor, run:

- `supabase/contacted_freelancers.sql` (if not already)
- `supabase/control_plane.sql` (bots, jobs, events, fm_contacts, settings, …)

Confirm tables `bots`, `outreach_jobs`, `job_events`, `fm_contacts` exist and
`bots` has rows id=1,2,3 (with a `settings` jsonb column).

## 2. Environment variables (Vercel project)

Generate tokens:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

In Vercel → Project → **Settings → Environment Variables**, set for Production
(and Preview if you want):

| Name | Value |
|------|--------|
| `SUPABASE_URL` | `https://xxxx.supabase.co` |
| `SUPABASE_SERVICE_ROLE_KEY` | service_role key |
| `DASHBOARD_API_TOKEN` | long random string |
| `BOT_1_TOKEN` | long random string |
| `BOT_2_TOKEN` | long random string |
| `BOT_3_TOKEN` | long random string |

`CONTROL_HOST` / `CONTROL_PORT` are unused on Vercel.

## 3. Deploy

From the repo root (this project):

```bash
# once
npm i -g vercel   # or: npx vercel

# install slim deps locally if you want; Vercel uses requirements-vercel.txt
vercel link
vercel env pull   # optional

# production
vercel --prod
```

Or connect the GitHub repo in the Vercel dashboard and deploy on push.

**Install command** (Project → Settings → General → Build & Development, or CLI):

```text
pip install -r requirements-vercel.txt
```

If the UI has no install override, set in `vercel.json` is not always enough for
pip — use the dashboard **Install Command**:

```text
pip install -r requirements-vercel.txt
```

Root `requirements.txt` includes Playwright and is **too heavy** for the serverless
function; always use `requirements-vercel.txt` for this project on Vercel.

## 4. Open the dashboard

1. Visit `https://YOUR_PROJECT.vercel.app/`
2. Paste **DASHBOARD_API_TOKEN** → Save
3. Health: `https://YOUR_PROJECT.vercel.app/api/health` → `{"ok":true,"supabase":true,...}`

## 5. Three Chrome profiles + extension

On each machine/profile:

1. Log into the matching freelancermap account  
2. Load unpacked `extension/`  
3. Popup: **Bot ID** = 1/2/3, **Control API URL** = `https://YOUR_PROJECT.vercel.app` (no trailing slash), **Bot token** = `BOT_N_TOKEN` → Save & connect  

## 6. Smoke test

1. Dashboard → Bot 2 → keyword + **Dry run** → Start job  
2. Extension Bot 2 claims within ~12s  
3. Job log shows events; status `busy` → `online`  

## Issues to expect

| Issue | What to do |
|-------|------------|
| Build installs Playwright / times out | Set install command to `pip install -r requirements-vercel.txt` |
| `supabase: false` on `/api/health` | Missing env vars in Vercel (redeploy after adding) |
| 503 table missing | Run `control_plane.sql` |
| Extension heartbeat 401 | Wrong bot token or Bot ID |
| Cold starts / slow first claim | Normal on Hobby; first request after idle can take a few seconds |
| Function timeout | Claim/heartbeat are short; if timeouts appear, check Pro plan limits |

## What is still scaffold

- Assessment pipeline (20–30 FM chat turns → GitHub) is UI-only for now  
- No Slack / private email on this dashboard path  
