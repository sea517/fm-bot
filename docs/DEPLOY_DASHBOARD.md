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

Confirm tables `bots`, `outreach_jobs`, `job_events`, `fm_contacts`,
`fm_applicants`, `fm_messages` exist and `bots` has rows id=1,2,3 (with a
`settings` jsonb column). Re-run the `alter table public.fm_applicants …`
block at the end of `control_plane.sql` if applicants were created earlier
without `github_username` / `rejection_due_at`.

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
| `OPENROUTER_API_KEY` | OpenRouter key (assessment chat) |
| `OPENROUTER_MODEL` | `deepseek/deepseek-v4-flash-0731` |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` |
| `AI_PROVIDER` | `openrouter` |
| `GITHUB_TOKEN` | PAT with `repo` scope (invite collaborators) |
| `GITHUB_REPO` | `creativesolution999/full-stack-assignment` |

`CONTROL_HOST` / `CONTROL_PORT` are unused on Vercel.

## 3. Deploy

From the repo root (folder with `vercel.json`, `app.py`, `src/`):

```bash
npm i -g vercel   # or: npx vercel
vercel --prod
```

Or connect the GitHub repo in the Vercel dashboard (deploy on push).

**Install command** (already in `vercel.json`):

```text
pip install -r requirements-vercel.txt
```

Entrypoint is `app.py` → `app` (FastAPI). Do **not** rewrite everything to `/api` — that breaks routing.

Root `requirements.txt` includes Playwright and is too heavy; use `requirements-vercel.txt` on Vercel.

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
4. After a real DM, when the candidate replies in freelancermap messages, the
   extension polls inbox (~every 36s), calls `/api/bots/{id}/chat/turn`, and
   replies via OpenRouter (`deepseek/deepseek-v4-flash-0731`)  
5. After **N** bot messages (Settings → GitHub unlock, default **24**), bot asks
   for GitHub username → invites to `GITHUB_REPO` → fixed assignment messages  
6. Pipeline panel shows stages (`assessment_chat` → `waiting_github` → …)

## Issues to expect

| Issue | What to do |
|-------|------------|
| All routes `{"detail":"Not Found"}` | Old rewrite-to-`/api` bug — use root `app.py`, no catch-all rewrite; redeploy |
| Build installs Playwright / times out | Set install command to `pip install -r requirements-vercel.txt` |
| `500 FUNCTION_INVOCATION_FAILED` on open | Check function logs; usually missing pip dep (e.g. `requests`) in `requirements-vercel.txt` — redeploy after fix |
| `supabase: false` on `/api/health` | Missing env vars in Vercel (redeploy after adding) |
| 503 table missing | Run `control_plane.sql` |
| Extension heartbeat 401 | Wrong bot token or Bot ID |
| Cold starts / slow first claim | Normal on Hobby; first request after idle can take a few seconds |
| Function timeout | Claim/heartbeat are short; chat turns call OpenRouter — allow ~30–60s |
| Chat replies wrong / empty | Check `OPENROUTER_API_KEY` + model on Vercel; inspect extension console |
| GitHub invite fails | PAT needs `repo` scope; username must exist |

## Assessment funnel (this dashboard path)

Outreach DM → structured OpenRouter chat (screening → experience → technical →
scenario → collaboration) for ~20–30 bot turns → ask GitHub username → invite to
assignment repo → thank-you on submit → rejection message ~1–2 days later.

No Slack / private email on this path.
