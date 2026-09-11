-- Control plane for 3 freelancermap.com outreach bots (run in Supabase SQL Editor).
-- Shared duplicate ledger + per-bot jobs. Dashboard + extensions use the Control API.

-- Existing contacted_freelancers may already exist; this extends the control plane.

create table if not exists public.bots (
  id smallint primary key check (id in (1, 2, 3)),
  label text not null,
  status text not null default 'offline'
    check (status in ('offline', 'online', 'busy', 'error')),
  last_heartbeat_at timestamptz,
  current_job_id bigint,
  last_error text,
  settings jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

insert into public.bots (id, label) values
  (1, 'Bot 1'),
  (2, 'Bot 2'),
  (3, 'Bot 3')
on conflict (id) do nothing;

create table if not exists public.outreach_jobs (
  id bigint generated always as identity primary key,
  bot_id smallint not null references public.bots(id),
  keyword text not null,
  subject text not null default '',
  message_body text not null default '',
  min_interval_sec int not null default 240 check (min_interval_sec >= 10),
  max_interval_sec int not null default 300 check (max_interval_sec >= 10),
  max_freelancers int not null default 10 check (max_freelancers >= 1 and max_freelancers <= 500),
  dry_run boolean not null default true,
  status text not null default 'queued'
    check (status in (
      'queued', 'running', 'completed', 'failed',
      'cancel_requested', 'cancelled', 'stopped'
    )),
  stats jsonb not null default '{}'::jsonb,
  error text,
  created_at timestamptz not null default now(),
  started_at timestamptz,
  finished_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint outreach_jobs_interval_ok check (max_interval_sec >= min_interval_sec)
);

create index if not exists outreach_jobs_bot_status_idx
  on public.outreach_jobs (bot_id, status, created_at desc);

create table if not exists public.job_events (
  id bigint generated always as identity primary key,
  job_id bigint not null references public.outreach_jobs(id) on delete cascade,
  bot_id smallint not null references public.bots(id),
  level text not null default 'info',
  message text not null,
  created_at timestamptz not null default now()
);

create index if not exists job_events_job_id_idx
  on public.job_events (job_id, id desc);

-- Unified contacts / duplicates (profile-level). Works alongside contacted_freelancers.
create table if not exists public.fm_contacts (
  id bigint generated always as identity primary key,
  profile_key text not null,
  display_name text,
  conversation_id text,
  source_bot_id smallint references public.bots(id),
  status text not null default 'contacted'
    check (status in (
      'contacted', 'in_assessment', 'github_shared',
      'completed', 'rejected', 'blocked'
    )),
  stage text,
  notes text,
  first_contacted_at timestamptz,
  last_contacted_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fm_contacts_profile_key_key unique (profile_key)
);

create index if not exists fm_contacts_name_idx
  on public.fm_contacts (lower(display_name));

create index if not exists fm_contacts_status_idx
  on public.fm_contacts (status);

-- Applicants pipeline (assessment later); scaffold now.
create table if not exists public.fm_applicants (
  id bigint generated always as identity primary key,
  bot_id smallint not null references public.bots(id),
  profile_key text,
  conversation_id text,
  display_name text,
  stage text not null default 'outreach_sent',
  status text not null default 'active',
  message_count int not null default 0,
  github_unlocked boolean not null default false,
  contact_id bigint references public.fm_contacts(id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists fm_applicants_bot_stage_idx
  on public.fm_applicants (bot_id, stage, updated_at desc);

create table if not exists public.fm_messages (
  id bigint generated always as identity primary key,
  applicant_id bigint not null references public.fm_applicants(id) on delete cascade,
  role text not null check (role in ('bot', 'freelancer')),
  body text not null,
  created_at timestamptz not null default now()
);

create index if not exists fm_messages_applicant_idx
  on public.fm_messages (applicant_id, id);

alter table public.bots enable row level security;
alter table public.outreach_jobs enable row level security;
alter table public.job_events enable row level security;
alter table public.fm_contacts enable row level security;
alter table public.fm_applicants enable row level security;
alter table public.fm_messages enable row level security;

comment on table public.outreach_jobs is 'Per-bot outreach campaigns created from the dashboard';
comment on table public.fm_contacts is 'Cross-bot duplicate ledger for freelancermap profiles';

-- If bots was created earlier without settings:
alter table public.bots
  add column if not exists settings jsonb not null default '{}'::jsonb;

alter table public.outreach_jobs
  add column if not exists subject text not null default '';
alter table public.outreach_jobs
  add column if not exists message_body text not null default '';

alter table public.fm_applicants
  add column if not exists github_username text;
alter table public.fm_applicants
  add column if not exists rejection_due_at timestamptz;
alter table public.fm_applicants
  add column if not exists last_freelancer_message_at timestamptz;
alter table public.fm_applicants
  add column if not exists last_bot_message_at timestamptz;
