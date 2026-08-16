# Inventory & Purchase Management module

A **fully isolated** add-on to this Flask backend. It serves the DGMTS Inventory
frontend so that project no longer needs Supabase or a separate deployment.

## Isolation guarantees
- All routes under `/api/inventory/*` (blueprint `inventory_bp`).
- Own database layer `models/inventory_db.py` → the `inventory` schema in
  `dgmts_static_db`, via a dedicated role (`dgmts_inventory_user`) with **no
  access to `public`**. Imports nothing from `models/database.py` or
  `models/static_db.py`.
- Own auth (`inventory/ms_auth.py`, validates the Microsoft SSO bearer via
  `/user/me`) and own SMTP sender (`services/inventory/email.py`).
- The only edit to shared code is a guarded block in `app.py`. Set
  `INVENTORY_MODULE_ENABLED=false` (or delete the block) to disable — the block
  is also wrapped in try/except so an inventory error can't break the app.

## Files
```
config.py                      # + INVENTORY_* fields (additive)
env.example                    # + INVENTORY_* documented
app.py                         # + guarded inventory_bp registration
inventory_schema.sql           # tables in the `inventory` schema
consumable_schema.sql          # additive consumable tables (run on existing servers)
inventory_db_setup.sql         # one-time role + grants (run as superuser)
models/inventory_db.py         # isolated psycopg2 pool (search_path=inventory)
inventory/ms_auth.py           # MS bearer validation decorators
services/inventory/email.py    # SMTP sender (INVENTORY_SMTP_*)
routes/inventory_routes.py     # inventory + consumable endpoints
consumable_data_migrate.py     # Supabase -> inventory copy of consumable tables
```

## First-time setup (on the server)
1. Install deps (already in requirements.txt): `pip install -r requirements.txt`.
2. Create the role + grants:
   `psql -d dgmts_static_db -f inventory_db_setup.sql`  *(edit the password first)*.
3. Create the schema/tables:
   `psql -d dgmts_static_db -f inventory_schema.sql`.
   Existing servers that already ran the original schema: also run
   `psql -d dgmts_static_db -f consumable_schema.sql`.
4. Fill `.env` with `INVENTORY_DB_*`, `INVENTORY_SMTP_*`,
   `INVENTORY_PO_EMAIL_ACTION_SECRET`, `INVENTORY_ADMIN_EMAILS`.
5. Restart the Flask app. Verify: `GET /api/inventory/health` →
   `{"status":"ok","db":true}`.

## Endpoint groups (33 routes)
- `branches`, `departments` (GET/POST)
- `users` (GET paginated, POST/PATCH/DELETE — no Supabase Auth)
- `inventory-items` (GET with branch name, POST/PATCH/DELETE), `inventory-logs`
- `suppliers`, `consumable-items`, `inventory-stock`, `consumption-logs`
- `purchase-requests` (GET/POST/PATCH workflow/DELETE, `/action-token`)
- `po-action-permissions`, `user-tab-permissions` (GET + PUT upsert)
- `supervisor-titles` (GET/POST/DELETE)
- `ms-directory/{employees,employees/lookup,departments,meta,sync}`
- `email/send`

Auth: every route needs `Authorization: Bearer <MS token>`; admin-only actions
require an email in `INVENTORY_ADMIN_EMAILS`.

## Data migration
Export the 12 purchase-management tables from Supabase and load into the
`inventory` schema (remap `public.` → `inventory.`). Preserve `users.id`
UUIDs so existing `inventory_logs` references stay valid.

Consumable Inventory tables (`suppliers`, `consumable_items`,
`inventory_stock`, `consumption_logs`) are a separate copy:

```bash
psql -d dgmts_static_db -f consumable_schema.sql
export SOURCE_DSN="postgresql://..."   # Supabase
export TARGET_DSN="postgresql://dgmts_inventory_user:...@127.0.0.1:5432/dgmts_static_db"
python3 consumable_data_migrate.py
```
