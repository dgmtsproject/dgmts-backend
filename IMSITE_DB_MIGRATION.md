# Imsite Supabase → VPS Postgres migration

Migration of the **main instrumentation** Supabase project into a new local
Postgres database on the VPS, following the same pattern as `dgmts_static_db`.

Flask app cutover is **not** complete yet: backend services still use
`SUPABASE_*` via `models/database.py`. A PostgREST-style CRUD API is ready so
the frontend (`C:/Work/dgmts`) can switch table access the same way the static
site used `/api/dgmts-static/data`.

---

## Source vs target

| | Source (Supabase) | Target (VPS Postgres) |
|---|---|---|
| Project ref | `xmhiocoinswgxvqokuzd` | — |
| URL | `https://xmhiocoinswgxvqokuzd.supabase.co` | `127.0.0.1:5432` |
| Region / pooler | `aws-0-ap-southeast-1.pooler.supabase.com:5432` | local |
| Database | `postgres` (schema `public`) | **`dgmts_db`** |
| DB user | `postgres` / `postgres.xmhiocoinswgxvqokuzd` (pooler) | **`dgmts_user`** |
| Schema dumped | `public` only | `public` |

**Not this migration:** static marketing DB (`fvnuefwtrkiutnomremi` → `dgmts_static_db`)
or inventory (`inventory` schema inside `dgmts_static_db`).

**Left alone on VPS:** `dgmts_prod` (older partial Django copy). Do not use it for imsite.

---

## Secrets and env var names

### Still used by the live Flask app (Supabase client)

| Env var | Purpose |
|---|---|
| `SUPABASE_URL` | `https://xmhiocoinswgxvqokuzd.supabase.co` |
| `SUPABASE_KEY` | Anon/service key for `supabase-py` |
| `SUPABASE_PASSWORD` | Postgres password for that project (dump / direct SQL) |

Frontend (`C:/Work/dgmts`) today:

| Env var | Purpose |
|---|---|
| `VITE_SUPABASE_PROJECT_URL` | Same project URL |
| `VITE_SUPABASE_ANON_KEY` | Anon key |

### New local Postgres (dump target + `/api/imsite` CRUD)

| Env var | Typical value | Notes |
|---|---|---|
| `IMSITE_DB_HOST` | `127.0.0.1` | On the VPS |
| `IMSITE_DB_PORT` | `5432` | |
| `IMSITE_DB_NAME` | `dgmts_db` | New database |
| `IMSITE_DB_USER` | `dgmts_user` | Dedicated role |
| `IMSITE_DB_PASSWORD` | *(server secret)* | Set on VPS; same style as `STATIC_DB_PASSWORD` |

Also documented on the VPS in `/root/flask-app/.env` and the local repo `.env`.

### Related (unchanged) local DBs

| Env prefix | Database | Role |
|---|---|---|
| `STATIC_DB_*` | `dgmts_static_db` | `dgmts_static_user` |
| `INVENTORY_DB_*` | `dgmts_static_db` (schema `inventory`) | `dgmts_inventory_user` |

---

## Tables restored (exact counts at migration time)

| Table | Rows |
|---|---|
| `sensor_readings` | 7375 |
| `sent_alerts` | 3074 |
| `sent_alert_logs` | 2087 |
| `instruments` | 10 |
| `Projects` | 9 |
| `ProjectUsers` | 8 |
| `users` | 7 |
| `time_based_reference_values` | 4 |
| `alarms` | 2 |
| `reference_values` | 2 |
| `alarms_new` | 0 |

Quoted identifiers: `"Projects"`, `"ProjectUsers"` (PascalCase).

---

## How the dump was done (VPS)

1. Create role + DB (idempotent):
   ```bash
   sudo -u postgres psql -c "CREATE ROLE dgmts_user LOGIN PASSWORD '...';"   # or ALTER if exists
   sudo -u postgres psql -c "CREATE DATABASE dgmts_db OWNER dgmts_user;"
   ```
2. Dump from Supabase **session pooler** (direct host is IPv6-only from this VPS):
   ```bash
   export PGPASSWORD='…'   # SUPABASE_PASSWORD
   export PGHOST=aws-0-ap-southeast-1.pooler.supabase.com
   export PGPORT=5432
   export PGDATABASE=postgres
   export PGUSER=postgres.xmhiocoinswgxvqokuzd
   export PGSSLMODE=require
   pg_dump --format=custom --no-owner --no-acl --schema=public \
     --file=/tmp/imsite_supabase_public.dump
   ```
3. Restore into empty `dgmts_db`, grant + reassign ownership to `dgmts_user`.

Helper scripts in this repo (credentials via env, not committed):

- `scripts/migrate_imsite_db.py` — SSH + dump/restore
- `scripts/verify_imsite_db_migration.py` — exact source vs target counts

---

## CRUD API (frontend cutover prep)

Mirrors `/api/dgmts-static/data`.

| Method | Path | Role |
|---|---|---|
| `GET` | `/api/imsite/health` | Pool + DB connectivity check |
| `POST` | `/api/imsite/data` | select / insert / update / delete / upsert |
| `OPTIONS` | `/api/imsite/data` | CORS preflight |

### Allowed tables

`instruments`, `Projects`, `ProjectUsers`, `users`, `sensor_readings`,
`reference_values`, `time_based_reference_values`, `sent_alerts`,
`sent_alert_logs`, `alarms`, `alarms_new`

### Request body shape (same as static)

```json
{
  "action": "select",
  "table": "instruments",
  "columns": "*",
  "filters": [{ "op": "eq", "col": "instrument_id", "val": "SMG-3" }],
  "order": [{ "col": "instrument_id", "asc": true }],
  "limit": 50,
  "offset": 0,
  "single": false,
  "maybe_single": false
}
```

Filter `op` values: `eq`, `in`, `neq`, `gt`, `gte`, `lt`, `lte`, `is` (null), `not_is`.

Mutations:

- **insert** — `{ "action":"insert", "table":"…", "rows":[…], "returning": true }`
- **update** — `{ "action":"update", "table":"…", "patch":{…}, "filters":[…], "returning": false }`
- **delete** — `{ "action":"delete", "table":"…", "filters":[…], "returning": false }`
- **upsert** — `{ "action":"upsert", "table":"…", "rows":[…], "on_conflict":"id" }`

Response: `{ "data": …, "error": null }` or `{ "data": null, "error": { "message": "…" } }`.

### Frontend shim

Copy [`frontend/imsiteDbClient.ts.example`](frontend/imsiteDbClient.ts.example) into
`C:/Work/dgmts/src/` (e.g. replace or wrap `src/supabase.ts`) so
`supabase.from(...).select/eq/update/...` keeps working but hits
`https://imsite.dullesgeotechnical.com/api/imsite/data`.

Same pattern as `dgmts-static-migrate/src/dbClient.js`.

### Known gaps vs live Supabase client (fix when updating pages)

1. **Embedded selects** such as `.select('project_id, Projects(id, name)')` are
   **not** supported by this API. Split into two queries or flatten in the page.
2. Backend Python services (`alert_service`, micromate, etc.) still use
   `SUPABASE_*` until a separate cutover.
3. No Supabase Auth / Storage on this project — auth already goes through
   Flask `/api/login`.

---

## Deploy checklist (VPS)

1. Ensure `IMSITE_DB_*` are in `/root/flask-app/.env`.
2. Deploy code with `models/imsite_db.py`, `routes/imsite_routes.py`, config fields.
3. Restart Flask / gunicorn.
4. Smoke test:
   ```bash
   curl -s https://imsite.dullesgeotechnical.com/api/imsite/health
   curl -s -X POST https://imsite.dullesgeotechnical.com/api/imsite/data \
     -H 'Content-Type: application/json' \
     -d '{"action":"select","table":"instruments","columns":"instrument_id,instrument_name","limit":5}'
   ```
5. Point `C:/Work/dgmts` at the shim when ready; remove `VITE_SUPABASE_*` after cutover.
