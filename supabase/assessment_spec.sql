-- SPEC assessment columns + global opt-out (run in Supabase SQL Editor after control_plane.sql).

alter table public.fm_applicants
  add column if not exists pending_github text;
alter table public.fm_applicants
  add column if not exists parsed_username text;
alter table public.fm_applicants
  add column if not exists raw_github_string text;
alter table public.fm_applicants
  add column if not exists validation_result text;
alter table public.fm_applicants
  add column if not exists invite_result text;
alter table public.fm_applicants
  add column if not exists handoff_reason text;
alter table public.fm_applicants
  add column if not exists last_outbound_at timestamptz;
alter table public.fm_applicants
  add column if not exists follow_up_sent boolean not null default false;
alter table public.fm_applicants
  add column if not exists opted_out boolean not null default false;
alter table public.fm_applicants
  add column if not exists screening_answers int not null default 0;
alter table public.fm_applicants
  add column if not exists github_invalid_retries int not null default 0;
alter table public.fm_applicants
  add column if not exists human_offer_sent boolean not null default false;
alter table public.fm_applicants
  add column if not exists outreach_subject text;
alter table public.fm_applicants
  add column if not exists outreach_body text;

create table if not exists public.fm_opt_outs (
  id bigint generated always as identity primary key,
  bot_id smallint references public.bots(id),
  identity_key text not null,
  display_name text,
  created_at timestamptz not null default now(),
  constraint fm_opt_outs_identity_key_key unique (identity_key)
);

create index if not exists fm_opt_outs_identity_idx
  on public.fm_opt_outs (identity_key);

create index if not exists fm_applicants_follow_up_idx
  on public.fm_applicants (bot_id, status, follow_up_sent, last_freelancer_message_at);

alter table public.fm_opt_outs enable row level security;

comment on table public.fm_opt_outs is 'Global opt-out keyed by candidate identity (not thread)';
