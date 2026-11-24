-- ======================================================================
-- Task Manager module: schema + workflow functions (generated file).
-- Source: dgmts-task-manager/db/migrations/core (run scripts/build_backend_schema.py).
-- Run as the database owner/superuser on dgmts_static_db, AFTER tasks_db_setup.sql step 1.
-- Safe to run more than once on an empty `tasks` schema; it is NOT an upgrade script.
-- ======================================================================
CREATE SCHEMA IF NOT EXISTS tasks;
SET search_path = tasks, public;

-- ---------- 20261115000000_employees.sql ----------
-- Portable employee directory. Ids are the inventory MS user_id stored as text.
-- Emails live in employee_emails. Readers use v_employees only.

create table employees (
  id text primary key,
  full_name text not null,
  badge_employee_id text,
  job_title text,
  department text,
  is_active boolean not null default true,
  source_updated_at timestamptz,
  synced_at timestamptz not null default now()
);

create table employee_emails (
  id uuid primary key default gen_random_uuid(),
  employee_id text not null references employees (id),
  email text not null unique check (email = lower(email)),
  is_primary boolean not null default true
);

create unique index employee_emails_one_primary
  on employee_emails (employee_id)
  where is_primary;

create index employee_emails_employee_id_idx on employee_emails (employee_id);

create table employee_sync_runs (
  id uuid primary key default gen_random_uuid(),
  started_at timestamptz not null default now(),
  finished_at timestamptz,
  added_count int not null default 0,
  updated_count int not null default 0,
  deactivated_count int not null default 0,
  error text
);

create view v_employees as
select
  e.id,
  e.full_name,
  em.email,
  e.job_title,
  e.department,
  e.is_active
from employees e
left join employee_emails em
  on em.employee_id = e.id
 and em.is_primary;

create or replace function fn_upsert_employees(p_rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  v_added int := 0;
  v_updated int := 0;
  v_deactivated int := 0;
  v_count int := 0;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'VALIDATION: Employee payload must be a JSON array';
  end if;

  select count(*) into v_count from jsonb_array_elements(p_rows);
  if v_count = 0 then
    raise exception 'VALIDATION: Employee sync returned no rows';
  end if;

  with incoming as (
    select *
    from jsonb_to_recordset(p_rows) as x(
      id text,
      full_name text,
      email text,
      job_title text,
      department text,
      badge_employee_id text,
      is_active boolean,
      source_updated_at timestamptz
    )
  ),
  classified as (
    select i.id, (e.id is not null) as existed
    from incoming i
    left join employees e on e.id = i.id
  )
  select
    count(*) filter (where not existed),
    count(*) filter (where existed)
  into v_added, v_updated
  from classified;

  insert into employees (
    id, full_name, badge_employee_id, job_title, department, is_active, source_updated_at, synced_at
  )
  select
    id,
    coalesce(full_name, ''),
    badge_employee_id,
    job_title,
    department,
    coalesce(is_active, true),
    source_updated_at,
    now()
  from jsonb_to_recordset(p_rows) as x(
    id text,
    full_name text,
    email text,
    job_title text,
    department text,
    badge_employee_id text,
    is_active boolean,
    source_updated_at timestamptz
  )
  on conflict (id) do update set
    full_name = excluded.full_name,
    badge_employee_id = excluded.badge_employee_id,
    job_title = excluded.job_title,
    department = excluded.department,
    is_active = excluded.is_active,
    source_updated_at = excluded.source_updated_at,
    synced_at = now();

  delete from employee_emails ee
  using (
    select id, lower(email) as email
    from jsonb_to_recordset(p_rows) as x(id text, email text)
    where email is not null and length(trim(email)) > 0
  ) i
  where ee.employee_id = i.id
    and ee.is_primary
    and ee.email is distinct from i.email;

  insert into employee_emails (employee_id, email, is_primary)
  select id, lower(trim(email)), true
  from jsonb_to_recordset(p_rows) as x(id text, email text)
  where email is not null and length(trim(email)) > 0
  on conflict (email) do update set
    employee_id = excluded.employee_id,
    is_primary = true;

  update employees e
  set is_active = false, synced_at = now()
  where e.is_active
    and not exists (
      select 1
      from jsonb_to_recordset(p_rows) as x(id text)
      where x.id = e.id
    );
  get diagnostics v_deactivated = row_count;

  insert into employee_sync_runs (finished_at, added_count, updated_count, deactivated_count)
  values (now(), v_added, v_updated, v_deactivated);

  return jsonb_build_object(
    'added', v_added,
    'updated', v_updated,
    'deactivated', v_deactivated,
    'received', v_count
  );
end;
$$;

revoke all on function fn_upsert_employees(jsonb) from public;

-- ---------- 20261115000001_employee_read.sql ----------
-- Directory reads go through this function. Table privileges stay revoked.

create or replace function fn_list_active_employees()
returns jsonb
language sql
stable
security definer
set search_path = tasks, public
as $$
  select coalesce(
    jsonb_agg(
      jsonb_build_object(
        'id', v.id,
        'full_name', v.full_name,
        'email', v.email,
        'job_title', v.job_title,
        'department', v.department,
        'is_active', v.is_active
      )
      order by v.full_name
    ),
    '[]'::jsonb
  )
  from v_employees v
  where v.is_active;
$$;

-- ---------- 20261115000100_tasks.sql ----------
-- Portable task workflow. Employee ids are text (inventory MS user_id).
-- No auth.*, pg_net, pg_cron, or Supabase-only objects in this file.

create table tasks (
  id uuid primary key default gen_random_uuid(),
  task_no bigint generated always as identity unique,
  created_date date not null,
  due_date date not null,
  description text not null check (length(trim(description)) > 0),
  reference text,
  created_by text not null references employees (id),
  created_by_email text not null,
  assigned_to text not null references employees (id),
  assigned_to_email text not null,
  original_assignee text not null references employees (id),
  forwarded_from text references employees (id),
  is_self_assigned boolean not null,
  status text not null default 'pending'
    check (status in ('pending', 'in_progress', 'denied', 'forwarded', 'completed', 'cancelled')),
  notes text,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table task_events (
  id uuid primary key default gen_random_uuid(),
  task_id uuid not null references tasks (id) on delete cascade,
  event_type text not null check (event_type in (
    'created', 'accepted', 'denied', 'forwarded', 'completed', 'cancelled', 'reassigned', 'note_added', 'edited'
  )),
  actor_id text not null references employees (id),
  actor_email text not null,
  from_user text references employees (id),
  to_user text references employees (id),
  note text,
  created_at timestamptz not null default now()
);

create table notification_outbox (
  id uuid primary key default gen_random_uuid(),
  task_event_id uuid references task_events (id) on delete cascade,
  kind text not null,
  recipient_email text not null,
  payload jsonb not null,
  status text not null default 'queued' check (status in ('queued', 'sending', 'sent', 'failed')),
  attempts int not null default 0,
  last_error text,
  send_after timestamptz not null default now(),
  sent_at timestamptz,
  created_at timestamptz not null default now()
);

create table app_user_roles (
  employee_id text primary key references employees (id),
  email text not null,
  role text not null check (role in ('user', 'admin'))
);

create table reminder_runs (
  run_date date primary key,
  ran_at timestamptz not null default now()
);

create index tasks_assigned_status_idx on tasks (assigned_to, status);
create index tasks_created_status_idx on tasks (created_by, status);
create index tasks_due_date_idx on tasks (due_date);
create index task_events_task_id_idx on task_events (task_id);
create index notification_outbox_status_send_after_idx on notification_outbox (status, send_after);

create or replace function fn_set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

create trigger tasks_set_updated_at
before update on tasks
for each row execute function fn_set_updated_at();

create or replace function fn_company_today()
returns date
language sql
stable
as $$
  select (now() at time zone 'America/New_York')::date
$$;

create or replace function fn_is_admin(p_actor text)
returns boolean
language sql
stable
security definer
set search_path = tasks, public
as $$
  select exists (
    select 1 from app_user_roles
    where employee_id = p_actor and role = 'admin'
  )
$$;

create or replace function fn_can_view_task(p_actor text, p_task_id uuid)
returns boolean
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  t tasks%rowtype;
begin
  if p_actor is null then
    return false;
  end if;
  if fn_is_admin(p_actor) then
    return true;
  end if;
  select * into t from tasks where id = p_task_id;
  if not found then
    return false;
  end if;
  if t.created_by = p_actor
     or t.assigned_to = p_actor
     or t.original_assignee = p_actor
     or t.forwarded_from is not distinct from p_actor then
    return true;
  end if;
  return exists (
    select 1 from task_events e
    where e.task_id = p_task_id
      and (e.actor_id = p_actor or e.from_user = p_actor or e.to_user = p_actor)
  );
end;
$$;

create or replace function fn_require_actor(p_actor text)
returns v_employees
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  person v_employees%rowtype;
begin
  if p_actor is null then
    raise exception 'NOT_IN_DIRECTORY: Your account is not in the employee directory';
  end if;
  select * into person from v_employees where id = p_actor;
  if not found then
    raise exception 'NOT_IN_DIRECTORY: Your account is not in the employee directory';
  end if;
  return person;
end;
$$;

create or replace function fn_email_for(p_id text, p_snapshot text)
returns text
language sql
stable
security definer
set search_path = tasks, public
as $$
  select coalesce(
    (select email from v_employees where id = p_id and email is not null),
    nullif(lower(p_snapshot), '')
  )
$$;

create or replace function fn_task_json(p_task tasks)
returns jsonb
language sql
stable
security definer
set search_path = tasks, public
as $$
  select jsonb_build_object(
    'id', p_task.id,
    'task_no', p_task.task_no,
    'created_date', p_task.created_date,
    'due_date', p_task.due_date,
    'description', p_task.description,
    'reference', p_task.reference,
    'notes', p_task.notes,
    'status', p_task.status,
    'is_self_assigned', p_task.is_self_assigned,
    'created_by', p_task.created_by,
    'created_by_email', p_task.created_by_email,
    'created_by_name', coalesce((select full_name from employees where id = p_task.created_by), p_task.created_by_email),
    'assigned_to', p_task.assigned_to,
    'assigned_to_email', p_task.assigned_to_email,
    'assigned_to_name', coalesce((select full_name from employees where id = p_task.assigned_to), p_task.assigned_to_email),
    'original_assignee', p_task.original_assignee,
    'original_assignee_name', coalesce((select full_name from employees where id = p_task.original_assignee), ''),
    'forwarded_from', p_task.forwarded_from,
    'forwarded_from_name', (select full_name from employees where id = p_task.forwarded_from),
    'completed_at', p_task.completed_at,
    'created_at', p_task.created_at,
    'updated_at', p_task.updated_at
  )
$$;

create or replace function fn_enqueue(
  p_event_id uuid,
  p_kind text,
  p_email text,
  p_task tasks,
  p_note text,
  p_link_for text
)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  if p_email is null or length(trim(p_email)) = 0 then
    return;
  end if;
  if exists (
    select 1 from notification_outbox
    where task_event_id = p_event_id
      and kind = p_kind
      and lower(recipient_email) = lower(trim(p_email))
  ) then
    return;
  end if;
  insert into notification_outbox (task_event_id, kind, recipient_email, payload)
  values (
    p_event_id,
    p_kind,
    lower(trim(p_email)),
    jsonb_build_object(
      'task_id', p_task.id,
      'task_no', p_task.task_no,
      'description', p_task.description,
      'reference', coalesce(p_task.reference, ''),
      'due_date', p_task.due_date,
      'status', p_task.status,
      'note', coalesce(p_note, ''),
      'kind', p_kind,
      'link_for', p_link_for,
      'assigner_name', coalesce((select full_name from employees where id = p_task.created_by), ''),
      'assignee_name', coalesce((select full_name from employees where id = p_task.assigned_to), ''),
      'forwarder_name', coalesce((select full_name from employees where id = p_task.forwarded_from), '')
    )
  );
end;
$$;

create or replace function fn_add_event(
  p_task_id uuid,
  p_type text,
  p_actor text,
  p_actor_email text,
  p_from text,
  p_to text,
  p_note text
)
returns uuid
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  v_id uuid;
begin
  insert into task_events (task_id, event_type, actor_id, actor_email, from_user, to_user, note)
  values (p_task_id, p_type, p_actor, p_actor_email, p_from, p_to, nullif(p_note, ''))
  returning id into v_id;
  return v_id;
end;
$$;

create or replace function fn_create_task(
  p_actor text,
  p_assigned_to text,
  p_due_date date,
  p_description text,
  p_reference text
)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  assignee v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_self boolean;
begin
  actor := fn_require_actor(p_actor);
  if not actor.is_active then
    raise exception 'NOT_ALLOWED: Inactive employees cannot create tasks';
  end if;
  if p_description is null or length(trim(p_description)) = 0 then
    raise exception 'VALIDATION: Task description is required';
  end if;
  if p_due_date is null or p_due_date < fn_company_today() then
    raise exception 'VALIDATION: Due date cannot be before today';
  end if;
  select * into assignee from v_employees where id = p_assigned_to;
  if not found or not assignee.is_active then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  if assignee.email is null then
    raise exception 'VALIDATION: That employee has no email';
  end if;

  v_self := actor.id = assignee.id;
  insert into tasks (
    created_date, due_date, description, reference,
    created_by, created_by_email, assigned_to, assigned_to_email,
    original_assignee, is_self_assigned, status
  ) values (
    fn_company_today(),
    p_due_date,
    trim(p_description),
    nullif(trim(coalesce(p_reference, '')), ''),
    actor.id,
    actor.email,
    assignee.id,
    assignee.email,
    assignee.id,
    v_self,
    case when v_self then 'in_progress' else 'pending' end
  )
  returning * into v_task;

  v_event := fn_add_event(v_task.id, 'created', actor.id, actor.email, actor.id, assignee.id, null);
  if not v_self then
    perform fn_enqueue(v_event, 'task_created', assignee.email, v_task, null, 'assignee');
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_accept_task(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can accept this task';
  end if;
  if v_task.status <> 'pending' then
    raise exception 'INVALID_TRANSITION: Only a pending task can be accepted';
  end if;
  update tasks set status = 'in_progress', notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'accepted', actor.id, actor.email, null, null, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'accepted', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
    if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
      perform fn_enqueue(v_event, 'accepted', fn_email_for(v_task.forwarded_from, null), v_task, p_note, 'creator');
    end if;
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_deny_task(p_actor text, p_task_id uuid, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_reason text := trim(coalesce(p_reason, ''));
begin
  actor := fn_require_actor(p_actor);
  if length(v_reason) = 0 then
    raise exception 'VALIDATION: A reason is required';
  end if;
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can deny this task';
  end if;
  if v_task.status <> 'pending' then
    raise exception 'INVALID_TRANSITION: Only a pending task can be denied';
  end if;
  update tasks set status = 'denied', notes = v_reason where id = v_task.id returning * into v_task;
  v_event := fn_add_event(v_task.id, 'denied', actor.id, actor.email, null, null, v_reason);
  perform fn_enqueue(v_event, 'denied', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, v_reason, 'creator');
  if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
    perform fn_enqueue(v_event, 'denied', fn_email_for(v_task.forwarded_from, null), v_task, v_reason, 'creator');
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_forward_task(p_actor text, p_task_id uuid, p_to_employee text, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  target v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can forward this task';
  end if;
  if v_task.status <> 'in_progress' then
    raise exception 'INVALID_TRANSITION: Accept the task before forwarding it';
  end if;
  if p_to_employee = actor.id then
    raise exception 'VALIDATION: Forward the task to someone else';
  end if;
  select * into target from v_employees where id = p_to_employee;
  if not found or not target.is_active or target.email is null then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  update tasks
  set assigned_to = target.id,
      assigned_to_email = target.email,
      forwarded_from = actor.id,
      status = 'pending',
      is_self_assigned = false,
      notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'forwarded', actor.id, actor.email, actor.id, target.id, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'forwarded_to_you', target.email, v_task, p_note, 'assignee');
    perform fn_enqueue(v_event, 'forwarded_fyi', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
  else
    perform fn_enqueue(v_event, 'forwarded_to_you', target.email, v_task, p_note, 'assignee');
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_complete_task(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can complete this task';
  end if;
  if v_task.status <> 'in_progress' then
    raise exception 'INVALID_TRANSITION: Accept the task before completing it';
  end if;
  update tasks
  set status = 'completed',
      completed_at = now(),
      notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'completed', actor.id, actor.email, null, null, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'completed', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
    if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
      perform fn_enqueue(v_event, 'completed', fn_email_for(v_task.forwarded_from, null), v_task, p_note, 'creator');
    end if;
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_accept_and_complete(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  -- Accept never skips ahead to completed. Completion is only allowed from in_progress.
  return fn_complete_task(p_actor, p_task_id, p_note);
end;
$$;

create or replace function fn_cancel_task(p_actor text, p_task_id uuid, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.created_by <> actor.id then
    raise exception 'NOT_ALLOWED: Only the person who created the task can cancel it';
  end if;
  if v_task.status = 'completed' then
    raise exception 'INVALID_TRANSITION: A completed task cannot be cancelled';
  end if;
  if v_task.status = 'cancelled' then
    raise exception 'INVALID_TRANSITION: This task is already cancelled';
  end if;
  update tasks
  set status = 'cancelled',
      notes = coalesce(nullif(trim(coalesce(p_reason, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'cancelled', actor.id, actor.email, null, null, p_reason);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'cancelled', fn_email_for(v_task.assigned_to, v_task.assigned_to_email), v_task, p_reason, 'assignee');
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_reassign_task(p_actor text, p_task_id uuid, p_to_employee text, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  target v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_from text;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.created_by <> actor.id then
    raise exception 'NOT_ALLOWED: Only the creator can reassign this task';
  end if;
  if v_task.status <> 'denied' then
    raise exception 'INVALID_TRANSITION: Only a denied task can be reassigned';
  end if;
  select * into target from v_employees where id = p_to_employee;
  if not found or not target.is_active or target.email is null then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  v_from := v_task.assigned_to;
  update tasks
  set assigned_to = target.id,
      assigned_to_email = target.email,
      forwarded_from = null,
      status = 'pending',
      notes = nullif(trim(coalesce(p_note, '')), '')
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'reassigned', actor.id, actor.email, v_from, target.id, p_note);
  perform fn_enqueue(v_event, 'task_created', target.email, v_task, p_note, 'assignee');
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_update_task_note(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id and v_task.created_by <> actor.id and not fn_is_admin(actor.id) then
    raise exception 'NOT_ALLOWED: You cannot edit notes on this task';
  end if;
  update tasks set notes = nullif(trim(coalesce(p_note, '')), '')
  where id = v_task.id
  returning * into v_task;
  perform fn_add_event(v_task.id, 'note_added', actor.id, actor.email, null, null, p_note);
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_list_my_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.due_date, t.task_no)
    from tasks t
    where t.assigned_to = p_actor
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_list_delegated_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.due_date, t.task_no)
    from tasks t
    where t.created_by = p_actor
      and t.is_self_assigned = false
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_task_history(p_actor text, p_task_id uuid)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  if not fn_can_view_task(p_actor, p_task_id) then
    raise exception 'NOT_ALLOWED: You cannot view this task';
  end if;
  return jsonb_build_object(
    'task', (select fn_task_json(t) from tasks t where t.id = p_task_id),
    'events', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', e.id,
        'event_type', e.event_type,
        'actor_id', e.actor_id,
        'actor_email', e.actor_email,
        'actor_name', coalesce(ae.full_name, e.actor_email),
        'from_user', e.from_user,
        'from_name', fe.full_name,
        'to_user', e.to_user,
        'to_name', te.full_name,
        'note', e.note,
        'created_at', e.created_at
      ) order by e.created_at)
      from task_events e
      left join employees ae on ae.id = e.actor_id
      left join employees fe on fe.id = e.from_user
      left join employees te on te.id = e.to_user
      where e.task_id = p_task_id
    ), '[]'::jsonb)
  );
end;
$$;

create or replace function fn_task_counts(p_actor text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  return jsonb_build_object(
    'my_pending', (
      select count(*) from tasks
      where assigned_to = p_actor and status = 'pending'
    ),
    'delegated_overdue', (
      select count(*) from tasks
      where created_by = p_actor
        and is_self_assigned = false
        and status in ('pending', 'in_progress')
        and due_date < fn_company_today()
    )
  );
end;
$$;

create or replace function fn_list_active_employees(p_actor text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id', id,
      'full_name', full_name,
      'email', email,
      'job_title', job_title,
      'department', department,
      'is_active', is_active
    ) order by full_name)
    from v_employees
    where is_active and email is not null
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_admin_list_tasks(p_actor text, p_employee text, p_status text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.created_at desc)
    from tasks t
    where (p_status is null or p_status = '' or t.status = p_status)
      and (
        p_employee is null or p_employee = ''
        or t.assigned_to = p_employee
        or t.created_by = p_employee
      )
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_admin_list_notifications(p_actor text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  return coalesce((
    select jsonb_agg(jsonb_build_object(
      'id', n.id,
      'kind', n.kind,
      'recipient_email', n.recipient_email,
      'status', n.status,
      'attempts', n.attempts,
      'last_error', n.last_error,
      'send_after', n.send_after,
      'sent_at', n.sent_at,
      'created_at', n.created_at,
      'task_id', n.payload ->> 'task_id',
      'task_no', n.payload ->> 'task_no'
    ) order by n.created_at desc)
    from notification_outbox n
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_admin_retry_notification(p_actor text, p_id uuid)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  update notification_outbox
  set status = 'queued', attempts = 0, last_error = null, send_after = now()
  where id = p_id;
  if not found then
    raise exception 'VALIDATION: Notification not found';
  end if;
  return jsonb_build_object('id', p_id, 'status', 'queued');
end;
$$;

create or replace function fn_admin_list_employees(p_actor text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  return jsonb_build_object(
    'employees', coalesce((
      select jsonb_agg(jsonb_build_object(
        'id', e.id,
        'full_name', e.full_name,
        'email', em.email,
        'job_title', e.job_title,
        'department', e.department,
        'badge_employee_id', e.badge_employee_id,
        'is_active', e.is_active,
        'synced_at', e.synced_at
      ) order by e.full_name)
      from employees e
      left join employee_emails em on em.employee_id = e.id and em.is_primary
    ), '[]'::jsonb),
    'last_sync', (
      select jsonb_build_object(
        'finished_at', finished_at,
        'added_count', added_count,
        'updated_count', updated_count,
        'deactivated_count', deactivated_count,
        'error', error
      )
      from employee_sync_runs
      order by started_at desc
      limit 1
    )
  );
end;
$$;

create or replace function fn_claim_outbox(p_limit int)
returns setof notification_outbox
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  return query
  with picked as (
    select id
    from notification_outbox
    where status = 'queued' and send_after <= now() and attempts < 5
    order by send_after, created_at
    limit greatest(coalesce(p_limit, 20), 1)
    for update skip locked
  )
  update notification_outbox n
  set status = 'sending', attempts = n.attempts + 1
  from picked
  where n.id = picked.id
  returning n.*;
end;
$$;

create or replace function fn_mark_outbox(p_id uuid, p_ok boolean, p_error text)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  v_attempts int;
begin
  select attempts into v_attempts from notification_outbox where id = p_id for update;
  if not found then
    return;
  end if;
  if p_ok then
    update notification_outbox
    set status = 'sent', sent_at = now(), last_error = null
    where id = p_id;
  elsif v_attempts >= 5 then
    update notification_outbox
    set status = 'failed', last_error = p_error
    where id = p_id;
  else
    update notification_outbox
    set status = 'queued',
        last_error = p_error,
        send_after = now() + (v_attempts * interval '2 minutes')
    where id = p_id;
  end if;
end;
$$;

create or replace function fn_try_start_reminder_run(p_date date)
returns boolean
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  insert into reminder_runs (run_date) values (p_date) on conflict do nothing;
  return found;
end;
$$;

create or replace function fn_digest_candidates(p_today date)
returns jsonb
language sql
stable
security definer
set search_path = tasks, public
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', t.id,
    'task_no', t.task_no,
    'description', t.description,
    'due_date', t.due_date,
    'status', t.status,
    'assignee_id', t.assigned_to,
    'assignee_name', e.full_name,
    'assignee_email', e.email,
    'bucket', case when t.due_date < p_today then 'overdue' else 'tomorrow' end
  ) order by e.email, t.due_date), '[]'::jsonb)
  from tasks t
  join v_employees e on e.id = t.assigned_to
  where t.status in ('pending', 'in_progress')
    and e.email is not null
    and (t.due_date < p_today or t.due_date = p_today + 1)
$$;

create or replace function fn_enqueue_digest(p_email text, p_payload jsonb)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  if p_email is null or length(trim(p_email)) = 0 then
    return;
  end if;
  insert into notification_outbox (kind, recipient_email, payload)
  values ('due_digest', lower(trim(p_email)), p_payload);
end;
$$;

revoke all on function fn_is_admin(text) from public;
revoke all on function fn_can_view_task(text, uuid) from public;
revoke all on function fn_require_actor(text) from public;
revoke all on function fn_email_for(text, text) from public;
revoke all on function fn_task_json(tasks) from public;
revoke all on function fn_enqueue(uuid, text, text, tasks, text, text) from public;
revoke all on function fn_add_event(uuid, text, text, text, text, text, text) from public;
revoke all on function fn_create_task(text, text, date, text, text) from public;
revoke all on function fn_accept_task(text, uuid, text) from public;
revoke all on function fn_deny_task(text, uuid, text) from public;
revoke all on function fn_forward_task(text, uuid, text, text) from public;
revoke all on function fn_complete_task(text, uuid, text) from public;
revoke all on function fn_accept_and_complete(text, uuid, text) from public;
revoke all on function fn_cancel_task(text, uuid, text) from public;
revoke all on function fn_reassign_task(text, uuid, text, text) from public;
revoke all on function fn_update_task_note(text, uuid, text) from public;
revoke all on function fn_list_my_tasks(text, text) from public;
revoke all on function fn_list_delegated_tasks(text, text) from public;
revoke all on function fn_task_history(text, uuid) from public;
revoke all on function fn_task_counts(text) from public;
revoke all on function fn_list_active_employees(text) from public;
revoke all on function fn_admin_list_tasks(text, text, text) from public;
revoke all on function fn_admin_list_notifications(text) from public;
revoke all on function fn_admin_retry_notification(text, uuid) from public;
revoke all on function fn_admin_list_employees(text) from public;
revoke all on function fn_claim_outbox(int) from public;
revoke all on function fn_mark_outbox(uuid, boolean, text) from public;
revoke all on function fn_try_start_reminder_run(date) from public;
revoke all on function fn_digest_candidates(date) from public;
revoke all on function fn_enqueue_digest(text, jsonb) from public;

-- ---------- 20261115000300_testing_users.sql ----------
-- Manual test users are not part of the inventory sync, so a later sync must not deactivate them.

alter table employees
  add column if not exists directory_managed boolean not null default false;

do $$
begin
  if not exists (select 1 from employees where directory_managed) then
    update employees set directory_managed = true;
  end if;
end $$;

create or replace function fn_upsert_employees(p_rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  v_added int := 0;
  v_updated int := 0;
  v_deactivated int := 0;
  v_count int := 0;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'VALIDATION: Employee payload must be a JSON array';
  end if;

  select count(*) into v_count from jsonb_array_elements(p_rows);
  if v_count = 0 then
    raise exception 'VALIDATION: Employee sync returned no rows';
  end if;

  with incoming as (
    select *
    from jsonb_to_recordset(p_rows) as x(
      id text,
      full_name text,
      email text,
      job_title text,
      department text,
      badge_employee_id text,
      is_active boolean,
      source_updated_at timestamptz
    )
  ),
  classified as (
    select i.id, (e.id is not null) as existed
    from incoming i
    left join employees e on e.id = i.id
  )
  select
    count(*) filter (where not existed),
    count(*) filter (where existed)
  into v_added, v_updated
  from classified;

  insert into employees (
    id, full_name, badge_employee_id, job_title, department, is_active, source_updated_at, synced_at, directory_managed
  )
  select
    id,
    coalesce(full_name, ''),
    badge_employee_id,
    job_title,
    department,
    coalesce(is_active, true),
    source_updated_at,
    now(),
    true
  from jsonb_to_recordset(p_rows) as x(
    id text,
    full_name text,
    email text,
    job_title text,
    department text,
    badge_employee_id text,
    is_active boolean,
    source_updated_at timestamptz
  )
  on conflict (id) do update set
    full_name = excluded.full_name,
    badge_employee_id = excluded.badge_employee_id,
    job_title = excluded.job_title,
    department = excluded.department,
    is_active = excluded.is_active,
    source_updated_at = excluded.source_updated_at,
    synced_at = now(),
    directory_managed = true;

  delete from employee_emails ee
  using (
    select id, lower(email) as email
    from jsonb_to_recordset(p_rows) as x(id text, email text)
    where email is not null and length(trim(email)) > 0
  ) i
  where ee.employee_id = i.id
    and ee.is_primary
    and ee.email is distinct from i.email;

  insert into employee_emails (employee_id, email, is_primary)
  select id, lower(trim(email)), true
  from jsonb_to_recordset(p_rows) as x(id text, email text)
  where email is not null and length(trim(email)) > 0
  on conflict (email) do update set
    employee_id = excluded.employee_id,
    is_primary = true;

  update employees e
  set is_active = false, synced_at = now()
  where e.is_active
    and e.directory_managed
    and not exists (
      select 1
      from jsonb_to_recordset(p_rows) as x(id text)
      where x.id = e.id
    );
  get diagnostics v_deactivated = row_count;

  insert into employee_sync_runs (finished_at, added_count, updated_count, deactivated_count)
  values (now(), v_added, v_updated, v_deactivated);

  return jsonb_build_object(
    'added', v_added,
    'updated', v_updated,
    'deactivated', v_deactivated,
    'received', v_count
  );
end;
$$;

create or replace function fn_testing_lookup_login(p_login text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  v_login text := btrim(coalesce(p_login, ''));
  person v_employees%rowtype;
begin
  if v_login = '' then
    raise exception 'VALIDATION: Enter an email or login id';
  end if;

  select *
  into person
  from v_employees
  where is_active
    and (
      id = v_login
      or lower(id) = lower(v_login)
      or (email is not null and lower(email) = lower(v_login))
    )
  order by case
    when id = v_login then 0
    when lower(id) = lower(v_login) then 1
    else 2
  end
  limit 1;

  if not found then
    raise exception 'NOT_IN_DIRECTORY: Your account isn''t in the employee directory. Contact admin.';
  end if;

  return jsonb_build_object(
    'id', person.id,
    'full_name', person.full_name,
    'email', person.email,
    'job_title', person.job_title,
    'department', person.department,
    'is_active', person.is_active,
    'role', coalesce((select role from app_user_roles where employee_id = person.id), 'user')
  );
end;
$$;

create or replace function fn_admin_create_test_user(
  p_actor text,
  p_login_id text,
  p_full_name text,
  p_email text
)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  v_id text := btrim(coalesce(p_login_id, ''));
  v_name text := btrim(coalesce(p_full_name, ''));
  v_email text := lower(btrim(coalesce(p_email, '')));
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  if v_id = '' or v_name = '' or v_email = '' then
    raise exception 'VALIDATION: Username, login id, and email are required';
  end if;
  if char_length(v_id) > 120 or char_length(v_name) > 200 or char_length(v_email) > 200 then
    raise exception 'VALIDATION: One of the fields is too long';
  end if;
  if v_id ~ '[[:space:][:cntrl:]]' then
    raise exception 'VALIDATION: Login id cannot contain spaces';
  end if;
  if v_email !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception 'VALIDATION: Enter a valid email address';
  end if;
  if exists (select 1 from employees where lower(id) = lower(v_id)) then
    raise exception 'VALIDATION: That login id is already in use';
  end if;
  if exists (select 1 from employee_emails where email = v_email) then
    raise exception 'VALIDATION: That email is already in use';
  end if;

  insert into employees (id, full_name, is_active, directory_managed)
  values (v_id, v_name, true, false);

  insert into employee_emails (employee_id, email, is_primary)
  values (v_id, v_email, true);

  return (
    select jsonb_build_object(
      'id', v.id,
      'full_name', v.full_name,
      'email', v.email,
      'job_title', v.job_title,
      'department', v.department,
      'is_active', v.is_active
    )
    from v_employees v
    where v.id = v_id
  );
end;
$$;

revoke all on function fn_testing_lookup_login(text) from public;
revoke all on function fn_admin_create_test_user(text, text, text, text) from public;

do $$
declare
  v_owner text;
begin
  select employee_id into v_owner from employee_emails where email = 'admin@gmail.com';
  if v_owner is not null and v_owner <> 'admin' then
    raise exception 'VALIDATION: admin@gmail.com already belongs to %', v_owner;
  end if;

  insert into employees (id, full_name, is_active, directory_managed)
  values ('admin', 'Admin', true, false)
  on conflict (id) do update
    set full_name = 'Admin',
        is_active = true,
        directory_managed = false;

  delete from employee_emails
  where employee_id = 'admin'
    and is_primary
    and email <> 'admin@gmail.com';

  insert into employee_emails (employee_id, email, is_primary)
  values ('admin', 'admin@gmail.com', true)
  on conflict (email) do update
    set employee_id = excluded.employee_id,
        is_primary = true;

  insert into app_user_roles (employee_id, email, role)
  values ('admin', 'admin@gmail.com', 'admin')
  on conflict (employee_id) do update
    set email = excluded.email,
        role = 'admin';
end $$;

-- ---------- 20261115000500_in_progress.sql ----------
-- Accept moves a task to in_progress. Open work is pending + in_progress.

do $$
declare
  v_name text;
begin
  select con.conname into v_name
  from pg_constraint con
  join pg_class rel on rel.oid = con.conrelid
  join pg_namespace nsp on nsp.oid = rel.relnamespace
  where nsp.nspname = 'public'
    and rel.relname = 'tasks'
    and con.contype = 'c'
    and pg_get_constraintdef(con.oid) ilike '%status%';
  if v_name is not null then
    execute format('alter table tasks drop constraint %I', v_name);
  end if;
end $$;

update tasks set status = 'in_progress' where status = 'accepted';

alter table tasks drop constraint if exists tasks_status_check;
alter table tasks add constraint tasks_status_check
  check (status in ('pending', 'in_progress', 'denied', 'forwarded', 'completed', 'cancelled'));

create or replace function fn_create_task(
  p_actor text,
  p_assigned_to text,
  p_due_date date,
  p_description text,
  p_reference text
)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  assignee v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_self boolean;
begin
  actor := fn_require_actor(p_actor);
  if not actor.is_active then
    raise exception 'NOT_ALLOWED: Inactive employees cannot create tasks';
  end if;
  if p_description is null or length(trim(p_description)) = 0 then
    raise exception 'VALIDATION: Task description is required';
  end if;
  if p_due_date is null or p_due_date < fn_company_today() then
    raise exception 'VALIDATION: Due date cannot be before today';
  end if;
  select * into assignee from v_employees where id = p_assigned_to;
  if not found or not assignee.is_active then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  if assignee.email is null then
    raise exception 'VALIDATION: That employee has no email';
  end if;

  v_self := actor.id = assignee.id;
  insert into tasks (
    created_date, due_date, description, reference,
    created_by, created_by_email, assigned_to, assigned_to_email,
    original_assignee, is_self_assigned, status
  ) values (
    fn_company_today(),
    p_due_date,
    trim(p_description),
    nullif(trim(coalesce(p_reference, '')), ''),
    actor.id,
    actor.email,
    assignee.id,
    assignee.email,
    assignee.id,
    v_self,
    case when v_self then 'in_progress' else 'pending' end
  )
  returning * into v_task;

  v_event := fn_add_event(v_task.id, 'created', actor.id, actor.email, actor.id, assignee.id, null);
  if not v_self then
    perform fn_enqueue(v_event, 'task_created', assignee.email, v_task, null, 'assignee');
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_accept_task(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can accept this task';
  end if;
  if v_task.status <> 'pending' then
    raise exception 'INVALID_TRANSITION: Only a pending task can be accepted';
  end if;
  update tasks set status = 'in_progress', notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'accepted', actor.id, actor.email, null, null, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'accepted', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
    if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
      perform fn_enqueue(v_event, 'accepted', fn_email_for(v_task.forwarded_from, null), v_task, p_note, 'creator');
    end if;
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_forward_task(p_actor text, p_task_id uuid, p_to_employee text, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  target v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can forward this task';
  end if;
  if v_task.status <> 'in_progress' then
    raise exception 'INVALID_TRANSITION: Accept the task before forwarding it';
  end if;
  if p_to_employee = actor.id then
    raise exception 'VALIDATION: Forward the task to someone else';
  end if;
  select * into target from v_employees where id = p_to_employee;
  if not found or not target.is_active or target.email is null then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  update tasks
  set assigned_to = target.id,
      assigned_to_email = target.email,
      forwarded_from = actor.id,
      status = 'pending',
      is_self_assigned = false,
      notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'forwarded', actor.id, actor.email, actor.id, target.id, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'forwarded_to_you', target.email, v_task, p_note, 'assignee');
    perform fn_enqueue(v_event, 'forwarded_fyi', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
  else
    perform fn_enqueue(v_event, 'forwarded_to_you', target.email, v_task, p_note, 'assignee');
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_complete_task(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can complete this task';
  end if;
  if v_task.status <> 'in_progress' then
    raise exception 'INVALID_TRANSITION: Accept the task before completing it';
  end if;
  update tasks
  set status = 'completed',
      completed_at = now(),
      notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'completed', actor.id, actor.email, null, null, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'completed', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
    if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
      perform fn_enqueue(v_event, 'completed', fn_email_for(v_task.forwarded_from, null), v_task, p_note, 'creator');
    end if;
  end if;
  return fn_task_json(v_task);
end;
$$;

create or replace function fn_accept_and_complete(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $$
begin
  -- Accept never skips ahead to completed. Completion is only allowed from in_progress.
  return fn_complete_task(p_actor, p_task_id, p_note);
end;
$$;

create or replace function fn_list_my_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.due_date, t.task_no)
    from tasks t
    where t.assigned_to = p_actor
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_list_delegated_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.due_date, t.task_no)
    from tasks t
    where t.created_by = p_actor
      and t.is_self_assigned = false
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
  ), '[]'::jsonb);
end;
$$;

create or replace function fn_task_counts(p_actor text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $$
begin
  perform fn_require_actor(p_actor);
  return jsonb_build_object(
    'my_pending', (
      select count(*) from tasks
      where assigned_to = p_actor and status = 'pending'
    ),
    'delegated_overdue', (
      select count(*) from tasks
      where created_by = p_actor
        and is_self_assigned = false
        and status in ('pending', 'in_progress')
        and due_date < fn_company_today()
    )
  );
end;
$$;

create or replace function fn_digest_candidates(p_today date)
returns jsonb
language sql
stable
security definer
set search_path = tasks, public
as $$
  select coalesce(jsonb_agg(jsonb_build_object(
    'id', t.id,
    'task_no', t.task_no,
    'description', t.description,
    'due_date', t.due_date,
    'status', t.status,
    'assignee_id', t.assigned_to,
    'assignee_name', e.full_name,
    'assignee_email', e.email,
    'bucket', case when t.due_date < p_today then 'overdue' else 'tomorrow' end
  ) order by e.email, t.due_date), '[]'::jsonb)
  from tasks t
  join v_employees e on e.id = t.assigned_to
  where t.status in ('pending', 'in_progress')
    and e.email is not null
    and (t.due_date < p_today or t.due_date = p_today + 1)
$$;

-- ---------- 20261119000600_deny_cc_email.sql ----------
-- Denied assignments return to the assigner. CC is stored on the task.
-- Email accept/reject uses a token the static app can redeem without a login session.

alter table tasks add column if not exists cc_emails text[] not null default '{}';

create table if not exists email_action_tokens (
  token text primary key,
  task_id uuid not null references tasks (id) on delete cascade,
  actor_id text not null references employees (id),
  expires_at timestamptz not null,
  created_at timestamptz not null default now()
);

create index if not exists email_action_tokens_task_idx on email_action_tokens (task_id);


create or replace function fn_normalize_cc(p_cc text, p_assignee_email text)
returns text[]
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
declare
  v_raw text;
  v_piece text;
  v_email text;
  v_out text[] := array[]::text[];
begin
  if p_cc is null or length(trim(p_cc)) = 0 then
    return v_out;
  end if;
  foreach v_raw in array regexp_split_to_array(p_cc, '[,;]+') loop
    v_piece := trim(v_raw);
    if v_piece = '' then
      continue;
    end if;
    if v_piece ~ '^[^@[:space:]]+@[^@[:space:]]+\.[^@[:space:]]+$' then
      v_email := lower(v_piece);
    else
      select lower(email) into v_email
      from v_employees
      where id = v_piece
        and email is not null
        and is_active;
      if v_email is null then
        raise exception 'VALIDATION: CC must be an employee or a valid email';
      end if;
    end if;
    if p_assignee_email is not null and v_email = lower(trim(p_assignee_email)) then
      continue;
    end if;
    if not v_email = any(v_out) then
      v_out := array_append(v_out, v_email);
    end if;
  end loop;
  if coalesce(cardinality(v_out), 0) > 20 then
    raise exception 'VALIDATION: CC is limited to 20 people';
  end if;
  return v_out;
end;
$fn$;

create or replace function fn_task_json(p_task tasks)
returns jsonb
language sql
stable
security definer
set search_path = tasks, public
as $fn$
  select jsonb_build_object(
    'id', p_task.id,
    'task_no', p_task.task_no,
    'created_date', p_task.created_date,
    'due_date', p_task.due_date,
    'description', p_task.description,
    'reference', p_task.reference,
    'notes', p_task.notes,
    'status', p_task.status,
    'is_self_assigned', p_task.is_self_assigned,
    'cc_emails', coalesce(to_jsonb(p_task.cc_emails), '[]'::jsonb),
    'created_by', p_task.created_by,
    'created_by_email', p_task.created_by_email,
    'created_by_name', coalesce((select full_name from employees where id = p_task.created_by), p_task.created_by_email),
    'assigned_to', p_task.assigned_to,
    'assigned_to_email', p_task.assigned_to_email,
    'assigned_to_name', coalesce((select full_name from employees where id = p_task.assigned_to), p_task.assigned_to_email),
    'original_assignee', p_task.original_assignee,
    'original_assignee_name', coalesce((select full_name from employees where id = p_task.original_assignee), ''),
    'forwarded_from', p_task.forwarded_from,
    'forwarded_from_name', (select full_name from employees where id = p_task.forwarded_from),
    'completed_at', p_task.completed_at,
    'created_at', p_task.created_at,
    'updated_at', p_task.updated_at
  )
$fn$;

create or replace function fn_enqueue(
  p_event_id uuid,
  p_kind text,
  p_email text,
  p_task tasks,
  p_note text,
  p_link_for text
)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_token text;
begin
  if p_email is null or length(trim(p_email)) = 0 then
    return;
  end if;
  if exists (
    select 1 from notification_outbox
    where task_event_id = p_event_id
      and kind = p_kind
      and lower(recipient_email) = lower(trim(p_email))
  ) then
    return;
  end if;
  if p_link_for = 'assignee' and p_task.status in ('pending', 'in_progress') then
    v_token := replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '');
    insert into email_action_tokens (token, task_id, actor_id, expires_at)
    values (v_token, p_task.id, p_task.assigned_to, now() + interval '90 days');
  end if;
  insert into notification_outbox (task_event_id, kind, recipient_email, payload)
  values (
    p_event_id,
    p_kind,
    lower(trim(p_email)),
    jsonb_build_object(
      'task_id', p_task.id,
      'task_no', p_task.task_no,
      'description', p_task.description,
      'reference', coalesce(p_task.reference, ''),
      'due_date', p_task.due_date,
      'status', p_task.status,
      'note', coalesce(p_note, ''),
      'kind', p_kind,
      'link_for', p_link_for,
      'action_token', v_token,
      'assigner_name', coalesce((select full_name from employees where id = p_task.created_by), ''),
      'assignee_name', coalesce((select full_name from employees where id = p_task.assigned_to), ''),
      'forwarder_name', coalesce((select full_name from employees where id = p_task.forwarded_from), '')
    )
  );
end;
$fn$;

create or replace function fn_enqueue_cc(p_event_id uuid, p_task tasks, p_note text)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_email text;
begin
  if p_task.cc_emails is null then
    return;
  end if;
  foreach v_email in array p_task.cc_emails loop
    if v_email is null or length(trim(v_email)) = 0 then
      continue;
    end if;
    if exists (
      select 1 from notification_outbox
      where task_event_id = p_event_id
        and lower(recipient_email) = lower(trim(v_email))
    ) then
      continue;
    end if;
    perform fn_enqueue(p_event_id, 'cc_notice', v_email, p_task, p_note, 'cc');
  end loop;
end;
$fn$;

drop function if exists fn_create_task(text, text, date, text, text);
drop function if exists fn_create_task(text, text, date, text, text, text);

create function fn_create_task(
  p_actor text,
  p_assigned_to text,
  p_due_date date,
  p_description text,
  p_reference text,
  p_cc text default null
)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  assignee v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_self boolean;
  v_cc text[];
begin
  actor := fn_require_actor(p_actor);
  if not actor.is_active then
    raise exception 'NOT_ALLOWED: Inactive employees cannot create tasks';
  end if;
  if p_description is null or length(trim(p_description)) = 0 then
    raise exception 'VALIDATION: Task description is required';
  end if;
  if p_due_date is null or p_due_date < fn_company_today() then
    raise exception 'VALIDATION: Due date cannot be before today';
  end if;
  select * into assignee from v_employees where id = p_assigned_to;
  if not found or not assignee.is_active then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  if assignee.email is null then
    raise exception 'VALIDATION: That employee has no email';
  end if;

  v_cc := fn_normalize_cc(p_cc, assignee.email);
  v_self := actor.id = assignee.id;
  insert into tasks (
    created_date, due_date, description, reference,
    created_by, created_by_email, assigned_to, assigned_to_email,
    original_assignee, is_self_assigned, status, cc_emails
  ) values (
    fn_company_today(),
    p_due_date,
    trim(p_description),
    nullif(trim(coalesce(p_reference, '')), ''),
    actor.id,
    actor.email,
    assignee.id,
    assignee.email,
    assignee.id,
    v_self,
    case when v_self then 'in_progress' else 'pending' end,
    v_cc
  )
  returning * into v_task;

  v_event := fn_add_event(v_task.id, 'created', actor.id, actor.email, actor.id, assignee.id, null);
  if not v_self then
    perform fn_enqueue(v_event, 'task_created', assignee.email, v_task, null, 'assignee');
    perform fn_enqueue_cc(v_event, v_task, null);
  end if;
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_accept_task(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can accept this task';
  end if;
  if v_task.status <> 'pending' then
    raise exception 'INVALID_TRANSITION: Only a pending task can be accepted';
  end if;
  update tasks set status = 'in_progress', notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'accepted', actor.id, actor.email, null, null, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'accepted', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
    if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
      perform fn_enqueue(v_event, 'accepted', fn_email_for(v_task.forwarded_from, null), v_task, p_note, 'creator');
    end if;
  end if;
  perform fn_enqueue_cc(v_event, v_task, p_note);
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_deny_task(p_actor text, p_task_id uuid, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_reason text := trim(coalesce(p_reason, ''));
begin
  actor := fn_require_actor(p_actor);
  if length(v_reason) = 0 then
    raise exception 'VALIDATION: A reason is required';
  end if;
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can deny this task';
  end if;
  if v_task.status not in ('pending', 'in_progress') then
    raise exception 'INVALID_TRANSITION: This task can no longer be denied';
  end if;
  update tasks set status = 'denied', notes = v_reason where id = v_task.id returning * into v_task;
  v_event := fn_add_event(v_task.id, 'denied', actor.id, actor.email, null, null, v_reason);
  perform fn_enqueue(v_event, 'denied', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, v_reason, 'creator');
  if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
    perform fn_enqueue(v_event, 'denied', fn_email_for(v_task.forwarded_from, null), v_task, v_reason, 'creator');
  end if;
  perform fn_enqueue_cc(v_event, v_task, v_reason);
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_forward_task(p_actor text, p_task_id uuid, p_to_employee text, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  target v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can forward this task';
  end if;
  if v_task.status <> 'in_progress' then
    raise exception 'INVALID_TRANSITION: Accept the task before forwarding it';
  end if;
  if p_to_employee = actor.id then
    raise exception 'VALIDATION: Forward the task to someone else';
  end if;
  select * into target from v_employees where id = p_to_employee;
  if not found or not target.is_active or target.email is null then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  update tasks
  set assigned_to = target.id,
      assigned_to_email = target.email,
      forwarded_from = actor.id,
      status = 'pending',
      is_self_assigned = false,
      notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'forwarded', actor.id, actor.email, actor.id, target.id, p_note);
  perform fn_enqueue(v_event, 'forwarded_to_you', target.email, v_task, p_note, 'assignee');
  if target.id is distinct from v_task.created_by then
    perform fn_enqueue(v_event, 'forwarded_fyi', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
  end if;
  perform fn_enqueue_cc(v_event, v_task, p_note);
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_complete_task(p_actor text, p_task_id uuid, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> actor.id then
    raise exception 'NOT_ALLOWED: Only the current assignee can complete this task';
  end if;
  if v_task.status <> 'in_progress' then
    raise exception 'INVALID_TRANSITION: Accept the task before completing it';
  end if;
  update tasks
  set status = 'completed',
      completed_at = now(),
      notes = coalesce(nullif(trim(coalesce(p_note, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'completed', actor.id, actor.email, null, null, p_note);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'completed', fn_email_for(v_task.created_by, v_task.created_by_email), v_task, p_note, 'creator');
    if v_task.forwarded_from is not null and v_task.forwarded_from <> v_task.created_by then
      perform fn_enqueue(v_event, 'completed', fn_email_for(v_task.forwarded_from, null), v_task, p_note, 'creator');
    end if;
  end if;
  perform fn_enqueue_cc(v_event, v_task, p_note);
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_cancel_task(p_actor text, p_task_id uuid, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.created_by <> actor.id then
    raise exception 'NOT_ALLOWED: Only the person who created the task can cancel it';
  end if;
  if v_task.status = 'completed' then
    raise exception 'INVALID_TRANSITION: A completed task cannot be cancelled';
  end if;
  if v_task.status = 'cancelled' then
    raise exception 'INVALID_TRANSITION: This task is already cancelled';
  end if;
  update tasks
  set status = 'cancelled',
      notes = coalesce(nullif(trim(coalesce(p_reason, '')), ''), notes)
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'cancelled', actor.id, actor.email, null, null, p_reason);
  if not v_task.is_self_assigned then
    perform fn_enqueue(v_event, 'cancelled', fn_email_for(v_task.assigned_to, v_task.assigned_to_email), v_task, p_reason, 'assignee');
  end if;
  perform fn_enqueue_cc(v_event, v_task, p_reason);
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_reassign_task(p_actor text, p_task_id uuid, p_to_employee text, p_note text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  target v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_from text;
  v_self boolean;
begin
  actor := fn_require_actor(p_actor);
  select * into v_task from tasks where id = p_task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.created_by <> actor.id then
    raise exception 'NOT_ALLOWED: Only the creator can reassign this task';
  end if;
  if v_task.status <> 'denied' then
    raise exception 'INVALID_TRANSITION: Only a denied task can be reassigned';
  end if;
  select * into target from v_employees where id = p_to_employee;
  if not found or not target.is_active or target.email is null then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  v_from := v_task.assigned_to;
  v_self := target.id = v_task.created_by;
  update tasks
  set assigned_to = target.id,
      assigned_to_email = target.email,
      forwarded_from = null,
      status = case when v_self then 'in_progress' else 'pending' end,
      is_self_assigned = v_self,
      notes = nullif(trim(coalesce(p_note, '')), '')
  where id = v_task.id
  returning * into v_task;
  v_event := fn_add_event(v_task.id, 'reassigned', actor.id, actor.email, v_from, target.id, p_note);
  if not v_self then
    perform fn_enqueue(v_event, 'task_created', target.email, v_task, p_note, 'assignee');
  end if;
  perform fn_enqueue_cc(v_event, v_task, p_note);
  return fn_task_json(v_task);
end;
$fn$;

create or replace function fn_list_my_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.due_date, t.task_no)
    from tasks t
    where (
      t.assigned_to = p_actor
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
    )
    or (
      t.created_by = p_actor
      and t.status = 'denied'
      and v_filter in ('open', 'denied', 'all')
    )
  ), '[]'::jsonb);
end;
$fn$;

create or replace function fn_list_delegated_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.due_date, t.task_no)
    from tasks t
    where t.created_by = p_actor
      and t.is_self_assigned = false
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress', 'denied'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
  ), '[]'::jsonb);
end;
$fn$;

create or replace function fn_task_counts(p_actor text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
begin
  perform fn_require_actor(p_actor);
  return jsonb_build_object(
    'my_pending', (
      select count(*) from tasks
      where (assigned_to = p_actor and status = 'pending')
         or (created_by = p_actor and status = 'denied')
    ),
    'delegated_overdue', (
      select count(*) from tasks
      where created_by = p_actor
        and is_self_assigned = false
        and status in ('pending', 'in_progress')
        and due_date < fn_company_today()
    )
  );
end;
$fn$;

create or replace function fn_apply_email_action(p_token text, p_action text, p_reason text)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_token email_action_tokens%rowtype;
  v_task tasks%rowtype;
  v_result jsonb;
  v_message text;
begin
  if p_action not in ('accept', 'deny', 'complete') then
    raise exception 'VALIDATION: Unknown action';
  end if;
  select * into v_token from email_action_tokens where token = nullif(trim(coalesce(p_token, '')), '');
  if not found then
    raise exception 'VALIDATION: This link is not valid';
  end if;
  if v_token.expires_at < now() then
    raise exception 'VALIDATION: This link has expired';
  end if;
  select * into v_task from tasks where id = v_token.task_id for update;
  if not found then
    raise exception 'VALIDATION: Task not found';
  end if;
  if v_task.assigned_to <> v_token.actor_id then
    raise exception 'NOT_ALLOWED: Only the current assignee can do that';
  end if;

  if p_action = 'accept' and v_task.status = 'in_progress' then
    v_message := 'Task accepted';
  elsif p_action = 'accept' and v_task.status = 'completed' then
    v_message := 'This task is already complete';
  elsif p_action = 'deny' and v_task.status = 'denied' then
    v_message := 'This task is already denied';
  elsif p_action = 'complete' and v_task.status = 'completed' then
    v_message := 'Task completed';
  elsif p_action = 'accept' then
    v_result := fn_accept_task(v_token.actor_id, v_task.id, null);
    v_message := 'Task accepted';
  elsif p_action = 'deny' then
    v_result := fn_deny_task(v_token.actor_id, v_task.id, p_reason);
    v_message := 'Task denied';
  else
    v_result := fn_complete_task(v_token.actor_id, v_task.id, null);
    v_message := 'Task completed';
  end if;

  if v_result is null then
    v_result := fn_task_json(v_task);
  end if;
  return jsonb_build_object(
    'ok', true,
    'message', v_message,
    'status', v_result->>'status',
    'task_no', v_result->'task_no',
    'description', v_result->>'description'
  );
end;
$fn$;

revoke all on function fn_normalize_cc(text, text) from public;
revoke all on function fn_enqueue_cc(uuid, tasks, text) from public;
revoke all on function fn_create_task(text, text, date, text, text, text) from public;
revoke all on function fn_apply_email_action(text, text, text) from public;

-- ---------- 20261121000700_password_cc_status_order.sql ----------
-- Every account signs in with a login id and password.
-- CC mail records the real status event, and task lists are newest first.

create extension if not exists pgcrypto schema public;

alter table employees add column if not exists password_hash text;

create or replace function fn_password_hash(p_password text)
returns text
language sql
volatile
security definer
set search_path = tasks, public
as $fn$
  select crypt(p_password, gen_salt('bf'))
$fn$;

create or replace function fn_password_matches(p_password text, p_hash text)
returns boolean
language sql
stable
security definer
set search_path = tasks, public
as $fn$
  select p_hash is not null
    and p_password is not null
    and length(p_password) > 0
    and crypt(p_password, p_hash) = p_hash
$fn$;

revoke all on function fn_password_hash(text) from public;
revoke all on function fn_password_matches(text, text) from public;

-- Temporary password for every existing account, including admin.
update employees
set password_hash = fn_password_hash('adminadmin')
where password_hash is null or id = 'admin';

create or replace function fn_upsert_employees(p_rows jsonb)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_added int := 0;
  v_updated int := 0;
  v_deactivated int := 0;
  v_count int := 0;
begin
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'VALIDATION: Employee payload must be a JSON array';
  end if;

  select count(*) into v_count from jsonb_array_elements(p_rows);
  if v_count = 0 then
    raise exception 'VALIDATION: Employee sync returned no rows';
  end if;

  with incoming as (
    select *
    from jsonb_to_recordset(p_rows) as x(
      id text,
      full_name text,
      email text,
      job_title text,
      department text,
      badge_employee_id text,
      is_active boolean,
      source_updated_at timestamptz
    )
  ),
  classified as (
    select i.id, (e.id is not null) as existed
    from incoming i
    left join employees e on e.id = i.id
  )
  select
    count(*) filter (where not existed),
    count(*) filter (where existed)
  into v_added, v_updated
  from classified;

  insert into employees (
    id, full_name, badge_employee_id, job_title, department, is_active, source_updated_at, synced_at, directory_managed
  )
  select
    id,
    coalesce(full_name, ''),
    badge_employee_id,
    job_title,
    department,
    coalesce(is_active, true),
    source_updated_at,
    now(),
    true
  from jsonb_to_recordset(p_rows) as x(
    id text,
    full_name text,
    email text,
    job_title text,
    department text,
    badge_employee_id text,
    is_active boolean,
    source_updated_at timestamptz
  )
  on conflict (id) do update set
    full_name = excluded.full_name,
    badge_employee_id = excluded.badge_employee_id,
    job_title = excluded.job_title,
    department = excluded.department,
    is_active = excluded.is_active,
    source_updated_at = excluded.source_updated_at,
    synced_at = now(),
    directory_managed = true;

  delete from employee_emails ee
  using (
    select id, lower(email) as email
    from jsonb_to_recordset(p_rows) as x(id text, email text)
    where email is not null and length(trim(email)) > 0
  ) i
  where ee.employee_id = i.id
    and ee.is_primary
    and ee.email is distinct from i.email;

  insert into employee_emails (employee_id, email, is_primary)
  select id, lower(trim(email)), true
  from jsonb_to_recordset(p_rows) as x(id text, email text)
  where email is not null and length(trim(email)) > 0
  on conflict (email) do update set
    employee_id = excluded.employee_id,
        is_primary = true;

  update employees e
  set is_active = false, synced_at = now()
  where e.is_active
    and e.directory_managed
    and not exists (
      select 1
      from jsonb_to_recordset(p_rows) as x(id text)
      where x.id = e.id
    );
  get diagnostics v_deactivated = row_count;

  update employees
  set password_hash = fn_password_hash('adminadmin')
  where password_hash is null;

  insert into employee_sync_runs (finished_at, added_count, updated_count, deactivated_count)
  values (now(), v_added, v_updated, v_deactivated);

  return jsonb_build_object(
    'added', v_added,
    'updated', v_updated,
    'deactivated', v_deactivated,
    'received', v_count
  );
end;
$fn$;

drop function if exists fn_testing_lookup_login(text);

create function fn_testing_lookup_login(p_login text, p_password text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
declare
  v_login text := btrim(coalesce(p_login, ''));
  v_password text := coalesce(p_password, '');
  person v_employees%rowtype;
  v_hash text;
begin
  if v_login = '' or v_password = '' then
    raise exception 'VALIDATION: Enter your login id and password';
  end if;

  select *
  into person
  from v_employees
  where is_active
    and (
      id = v_login
      or lower(id) = lower(v_login)
      or (email is not null and lower(email) = lower(v_login))
    )
  order by case
    when id = v_login then 0
    when lower(id) = lower(v_login) then 1
    else 2
  end
  limit 1;

  if not found then
    raise exception 'VALIDATION: Login id or password is incorrect';
  end if;

  select password_hash into v_hash from employees where id = person.id;
  if not fn_password_matches(v_password, v_hash) then
    raise exception 'VALIDATION: Login id or password is incorrect';
  end if;

  return jsonb_build_object(
    'id', person.id,
    'full_name', person.full_name,
    'email', person.email,
    'job_title', person.job_title,
    'department', person.department,
    'is_active', person.is_active,
    'role', coalesce((select role from app_user_roles where employee_id = person.id), 'user')
  );
end;
$fn$;

drop function if exists fn_admin_create_test_user(text, text, text, text) cascade;

create function fn_admin_create_test_user(
  p_actor text,
  p_login_id text,
  p_full_name text,
  p_email text,
  p_password text
)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_id text := btrim(coalesce(p_login_id, ''));
  v_name text := btrim(coalesce(p_full_name, ''));
  v_email text := lower(btrim(coalesce(p_email, '')));
  v_password text := coalesce(p_password, '');
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  if v_id = '' or v_name = '' or v_email = '' or v_password = '' then
    raise exception 'VALIDATION: Username, login id, email, and password are required';
  end if;
  if char_length(v_id) > 120 or char_length(v_name) > 200 or char_length(v_email) > 200 or char_length(v_password) > 200 then
    raise exception 'VALIDATION: One of the fields is too long';
  end if;
  if char_length(v_password) < 4 then
    raise exception 'VALIDATION: Password must be at least 4 characters';
  end if;
  if v_id ~ '[[:space:][:cntrl:]]' then
    raise exception 'VALIDATION: Login id cannot contain spaces';
  end if;
  if v_email !~ '^[^[:space:]@]+@[^[:space:]@]+\.[^[:space:]@]+$' then
    raise exception 'VALIDATION: Enter a valid email address';
  end if;
  if exists (select 1 from employees where lower(id) = lower(v_id)) then
    raise exception 'VALIDATION: That login id is already in use';
  end if;
  if exists (select 1 from employee_emails where email = v_email) then
    raise exception 'VALIDATION: That email is already in use';
  end if;

  insert into employees (id, full_name, is_active, directory_managed, password_hash)
  values (v_id, v_name, true, false, fn_password_hash(v_password));

  insert into employee_emails (employee_id, email, is_primary)
  values (v_id, v_email, true);

  return (
    select jsonb_build_object(
      'id', v.id,
      'full_name', v.full_name,
      'email', v.email,
      'job_title', v.job_title,
      'department', v.department,
      'is_active', v.is_active
    )
    from v_employees v
    where v.id = v_id
  );
end;
$fn$;

create or replace function fn_enqueue(
  p_event_id uuid,
  p_kind text,
  p_email text,
  p_task tasks,
  p_note text,
  p_link_for text
)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_token text;
begin
  if p_email is null or length(trim(p_email)) = 0 then
    return;
  end if;
  if exists (
    select 1 from notification_outbox
    where task_event_id = p_event_id
      and kind = p_kind
      and lower(recipient_email) = lower(trim(p_email))
  ) then
    return;
  end if;
  if p_link_for = 'assignee' and p_task.status in ('pending', 'in_progress') then
    v_token := replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '');
    insert into email_action_tokens (token, task_id, actor_id, expires_at)
    values (v_token, p_task.id, p_task.assigned_to, now() + interval '90 days');
  end if;
  insert into notification_outbox (task_event_id, kind, recipient_email, payload)
  values (
    p_event_id,
    p_kind,
    lower(trim(p_email)),
    jsonb_build_object(
      'task_id', p_task.id,
      'task_no', p_task.task_no,
      'description', p_task.description,
      'reference', coalesce(p_task.reference, ''),
      'due_date', p_task.due_date,
      'status', p_task.status,
      'note', coalesce(p_note, ''),
      'kind', p_kind,
      'link_for', p_link_for,
      'action_token', v_token,
      'event_type', coalesce((select event_type from task_events where id = p_event_id), ''),
      'assigner_name', coalesce((select full_name from employees where id = p_task.created_by), ''),
      'assignee_name', coalesce((select full_name from employees where id = p_task.assigned_to), ''),
      'forwarder_name', coalesce((select full_name from employees where id = p_task.forwarded_from), '')
    )
  );
end;
$fn$;

create or replace function fn_list_my_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.created_at desc, t.task_no desc)
    from tasks t
    where (
      t.assigned_to = p_actor
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
    )
    or (
      t.created_by = p_actor
      and t.status = 'denied'
      and v_filter in ('open', 'denied', 'all')
    )
  ), '[]'::jsonb);
end;
$fn$;

create or replace function fn_list_delegated_tasks(p_actor text, p_filter text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
declare
  v_filter text := coalesce(nullif(p_filter, ''), 'open');
begin
  perform fn_require_actor(p_actor);
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.created_at desc, t.task_no desc)
    from tasks t
    where t.created_by = p_actor
      and t.is_self_assigned = false
      and (
        v_filter = 'all'
        or (v_filter = 'open' and t.status in ('pending', 'in_progress', 'denied'))
        or (v_filter = 'completed' and t.status = 'completed')
        or (v_filter = 'denied' and t.status = 'denied')
      )
  ), '[]'::jsonb);
end;
$fn$;

create or replace function fn_admin_list_tasks(p_actor text, p_employee text, p_status text)
returns jsonb
language plpgsql
stable
security definer
set search_path = tasks, public
as $fn$
begin
  perform fn_require_actor(p_actor);
  if not fn_is_admin(p_actor) then
    raise exception 'NOT_ALLOWED: Admin only';
  end if;
  return coalesce((
    select jsonb_agg(fn_task_json(t) order by t.created_at desc, t.task_no desc)
    from tasks t
    where (p_status is null or p_status = '' or t.status = p_status)
      and (
        p_employee is null or p_employee = ''
        or t.assigned_to = p_employee
        or t.created_by = p_employee
      )
  ), '[]'::jsonb);
end;
$fn$;

revoke all on function fn_testing_lookup_login(text, text) from public;
revoke all on function fn_admin_create_test_user(text, text, text, text, text) from public;

-- ---------- 20261122000800_status_mail_sender_and_cc.sql ----------
-- Every status event emails the original sender and every CC address.
-- Someone who already received that event (the assignee, for example) is not mailed twice.
-- A self-assigned create still does not email the person who just saved it.

create or replace function fn_enqueue_cc(p_event_id uuid, p_task tasks, p_note text)
returns void
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  v_email text;
  v_sender text;
  v_event text;
  v_kind text;
begin
  select event_type into v_event from task_events where id = p_event_id;
  v_sender := fn_email_for(p_task.created_by, p_task.created_by_email);

  if v_sender is not null
     and not (coalesce(v_event, '') = 'created' and p_task.is_self_assigned)
     and not exists (
       select 1 from notification_outbox
       where task_event_id = p_event_id
         and lower(recipient_email) = lower(trim(v_sender))
     )
  then
    v_kind := case v_event
      when 'created' then 'assigned_by_you'
      when 'accepted' then 'accepted'
      when 'denied' then 'denied'
      when 'forwarded' then 'forwarded_fyi'
      when 'completed' then 'completed'
      when 'cancelled' then 'cancelled'
      when 'reassigned' then 'reassigned'
      else 'cc_notice'
    end;
    perform fn_enqueue(p_event_id, v_kind, v_sender, p_task, p_note, 'creator');
  end if;

  if p_task.cc_emails is null then
    return;
  end if;
  foreach v_email in array p_task.cc_emails loop
    if v_email is null or length(trim(v_email)) = 0 then
      continue;
    end if;
    if exists (
      select 1 from notification_outbox
      where task_event_id = p_event_id
        and lower(recipient_email) = lower(trim(v_email))
    ) then
      continue;
    end if;
    perform fn_enqueue(p_event_id, 'cc_notice', v_email, p_task, p_note, 'cc');
  end loop;
end;
$fn$;

create or replace function fn_create_task(
  p_actor text,
  p_assigned_to text,
  p_due_date date,
  p_description text,
  p_reference text,
  p_cc text default null
)
returns jsonb
language plpgsql
security definer
set search_path = tasks, public
as $fn$
declare
  actor v_employees%rowtype;
  assignee v_employees%rowtype;
  v_task tasks%rowtype;
  v_event uuid;
  v_self boolean;
  v_cc text[];
begin
  actor := fn_require_actor(p_actor);
  if not actor.is_active then
    raise exception 'NOT_ALLOWED: Inactive employees cannot create tasks';
  end if;
  if p_description is null or length(trim(p_description)) = 0 then
    raise exception 'VALIDATION: Task description is required';
  end if;
  if p_due_date is null or p_due_date < fn_company_today() then
    raise exception 'VALIDATION: Due date cannot be before today';
  end if;
  select * into assignee from v_employees where id = p_assigned_to;
  if not found or not assignee.is_active then
    raise exception 'VALIDATION: Choose an active employee';
  end if;
  if assignee.email is null then
    raise exception 'VALIDATION: That employee has no email';
  end if;

  v_cc := fn_normalize_cc(p_cc, assignee.email);
  v_self := actor.id = assignee.id;
  insert into tasks (
    created_date, due_date, description, reference,
    created_by, created_by_email, assigned_to, assigned_to_email,
    original_assignee, is_self_assigned, status, cc_emails
  ) values (
    fn_company_today(),
    p_due_date,
    trim(p_description),
    nullif(trim(coalesce(p_reference, '')), ''),
    actor.id,
    actor.email,
    assignee.id,
    assignee.email,
    assignee.id,
    v_self,
    case when v_self then 'in_progress' else 'pending' end,
    v_cc
  )
  returning * into v_task;

  v_event := fn_add_event(v_task.id, 'created', actor.id, actor.email, actor.id, assignee.id, null);
  if not v_self then
    perform fn_enqueue(v_event, 'task_created', assignee.email, v_task, null, 'assignee');
  end if;
  perform fn_enqueue_cc(v_event, v_task, null);
  return fn_task_json(v_task);
end;
$fn$;
