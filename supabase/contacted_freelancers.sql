-- Run in Supabase SQL Editor (or via supabase db push).
-- Stores freelancers already on freelancermap Postfach / contacted by the bot.

create table if not exists public.contacted_freelancers (
  id bigint generated always as identity primary key,
  fm_conversation_id text not null,
  display_name text,
  email text,
  project_title text,
  local_applicant_id bigint,
  stage text,
  first_seen_at timestamptz not null default now(),
  first_contacted_at timestamptz,
  last_contacted_at timestamptz,
  updated_at timestamptz not null default now(),
  constraint contacted_freelancers_fm_conversation_id_key unique (fm_conversation_id)
);

create index if not exists contacted_freelancers_email_idx
  on public.contacted_freelancers (lower(email));

create index if not exists contacted_freelancers_name_idx
  on public.contacted_freelancers (lower(display_name));

alter table public.contacted_freelancers enable row level security;

-- Service role bypasses RLS; no anon policies needed for the bot.
comment on table public.contacted_freelancers is
  'Freelancermap freelancers seen/contacted by the recruiting bot';
