-- JARVIS ID: linked devices and shared memory facts.
-- Every row belongs to one user. Row level security makes the server refuse rows of any
-- other user, whatever a client sends; the anon key alone reads and writes nothing.
-- Run once in the Supabase SQL editor (or `supabase db push`). Safe to run again.

create table if not exists public.devices (
  id          uuid primary key,                       -- random per install, never the hostname
  user_id     uuid not null default auth.uid() references auth.users on delete cascade,
  name        text not null default '' check (length(name) <= 80),
  platform    text not null default '' check (length(platform) <= 200),
  app_version text not null default '' check (length(app_version) <= 20),
  last_seen   timestamptz not null default now(),
  created_at  timestamptz not null default now()
);

-- One row per fact of memory/long_term.json, id "category/key" (stable on every device).
-- Deletes are kept as deleted=true so other devices learn about them.
create table if not exists public.memories (
  user_id    uuid not null default auth.uid() references auth.users on delete cascade,
  id         text not null check (id ~ '^[a-z]+/[a-z0-9_]{1,120}$'),
  category   text not null check (category in ('identity', 'preferences', 'projects', 'relationships', 'wishes', 'notes')),
  key        text not null,
  value      text not null default '' check (length(value) <= 1000),
  deleted    boolean not null default false,
  version    integer not null default 1 check (version >= 1),
  device_id  uuid,                                    -- device that wrote this version
  updated_at timestamptz not null default now(),
  primary key (user_id, id),
  check (id = category || '/' || key)
);
create index if not exists memories_user_updated on public.memories (user_id, updated_at);

-- updated_at from the server clock (pulls never miss a row to a skewed client clock), and every
-- update must be exactly the next version: a client that edited an old copy gets an error, not a silent overwrite.
create or replace function public.jarvis_memories_guard() returns trigger language plpgsql
  set search_path = '' as $$
begin
  if tg_op = 'UPDATE' and new.version <> old.version + 1 then
    raise exception 'version conflict on %: expected %, got %', old.id, old.version + 1, new.version
      using errcode = '40001';
  end if;
  if tg_op = 'UPDATE' and (new.user_id <> old.user_id or new.id <> old.id) then
    raise exception 'memory owner and id cannot change';
  end if;
  new.updated_at = now();
  return new;
end $$;
drop trigger if exists memories_guard on public.memories;
create trigger memories_guard before insert or update on public.memories
  for each row execute function public.jarvis_memories_guard();

alter table public.devices  enable row level security;
alter table public.memories enable row level security;
alter table public.devices  force row level security;
alter table public.memories force row level security;

drop policy if exists "own devices" on public.devices;
create policy "own devices" on public.devices for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

drop policy if exists "own memories" on public.memories;
create policy "own memories" on public.memories for all to authenticated
  using (user_id = auth.uid()) with check (user_id = auth.uid());

revoke all on public.devices, public.memories from anon;
