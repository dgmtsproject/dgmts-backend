# Backend Redeploy Guide (dgmts-backend)

How to push backend changes live on the VPS. Follow this every time you change
the backend. The server runs one gunicorn process inside a tmux session — the
whole job is: get the new code onto the server, then stop & restart that one
process.

## Server facts
- **Host:** `192.168.1.219` (needs VPN) — `ssh root@192.168.1.219`
- **App directory:** `~/flask-app`  (i.e. `/root/flask-app`)
- **tmux session:** `flaskserver` (gunicorn runs here, foreground)
- **Run command:** `gunicorn app:app --certfile=cert.pem --keyfile=key.pem -b 0.0.0.0:5000`
- **Public URL:** https://imsite.dullesgeotechnical.com  (port 5000)
- **Local Postgres:** `dgmts_static_db` on `127.0.0.1:5432`
  - marketing/static tables → schema `public` (user `dgmts_static_user`)
  - inventory module tables → schema `inventory` (user `dgmts_inventory_user`)

---

## A. Standard redeploy (code-only change)

Use this for any normal change (bug fix, new route, logic change) that does
**not** add Python packages, change the DB schema, or add env vars.

1. **From your machine:** commit + push your change.
   ```bash
   git add -A
   git commit -m "…"
   git push origin main
   ```
2. **SSH in and attach to tmux:**
   ```bash
   ssh root@192.168.1.219
   tmux attach -t flaskserver
   ```
3. **Stop gunicorn:** press `Ctrl+C` in that pane.
   - If the port stays busy afterwards: `pkill -f "gunicorn app:app"`
4. **Pull the code and (re)activate the venv:**
   ```bash
   cd ~/flask-app
   git pull origin main
   source venv/bin/activate
   ```
5. **Restart gunicorn (same pane):**
   ```bash
   gunicorn app:app --certfile=cert.pem --keyfile=key.pem -b 0.0.0.0:5000
   ```
   Watch the startup log. You should see the scheduler start and, for the
   inventory module: `[inventory] module enabled at /api/inventory`.
6. **Detach (leave it running):** `Ctrl+b` then `d`. Then `exit` to close SSH.
7. **Verify:** see section D.

> Only ONE gunicorn may run on port 5000. Always stop/start it inside the
> `flaskserver` tmux pane — never start a second one from a separate shell
> (that causes the `Connection in use: ('0.0.0.0', 5000)` error).

---

## B. Extra step — dependencies changed

If your change added/updated anything in `requirements.txt`, after `git pull`
and `source venv/bin/activate` (step 4), run:
```bash
pip install -r requirements.txt
```
then restart gunicorn (step 5).

---

## C. Extra step — DB schema or `.env` changed (inventory module)

Do these **before** restarting gunicorn.

**New/changed inventory tables or a new `.sql` migration** — run as the
`postgres` superuser (use `<` so postgres can read the file):
```bash
cd ~/flask-app
sudo -u postgres psql -d dgmts_static_db < inventory_schema.sql        # idempotent; safe to re-run
# or a specific new migration file:
sudo -u postgres psql -d dgmts_static_db < your_new_migration.sql
```
Note: `.sql` files are force-added to git (a blanket `*.sql` ignore rule
exists), so they DO arrive with `git pull`.

**New env vars** — edit the server `.env` and add them:
```bash
nano ~/flask-app/.env
```
`.env` is NOT in git — it lives only on the server, so new variables must be
added here by hand. (See `env.example` in the repo for the documented list.)

Then restart gunicorn (section A, step 5).

---

## D. Verify after every redeploy

```bash
# Core backend still healthy:
curl -k https://localhost:5000/
# -> {"message":"DGMTS Backend API","status":"running",...}

# Inventory module healthy (DB reachable):
curl -k https://localhost:5000/api/inventory/health
# -> {"db":true,"status":"ok","module":"inventory",...}

# A public inventory read (no auth needed):
curl -k https://localhost:5000/api/inventory/ms-directory/departments
```
From anywhere (public): replace `https://localhost:5000` with
`https://imsite.dullesgeotechnical.com`.

---

## E. Turn the inventory module off (kill switch)

If the inventory module ever misbehaves, disable it without touching the rest
of the backend: set in `~/flask-app/.env`:
```
INVENTORY_MODULE_ENABLED=false
```
then restart gunicorn. The existing backend (instrumentation, alerts, payments,
static site) is unaffected — the module is fully isolated.

---

## F. Rollback

If a deploy breaks things, revert to the previous commit and restart:
```bash
cd ~/flask-app
git log --oneline -5            # find the last good commit hash
git reset --hard <good-hash>    # or: git revert <bad-hash> && … (safer, keeps history)
source venv/bin/activate
gunicorn app:app --certfile=cert.pem --keyfile=key.pem -b 0.0.0.0:5000
```
(DB schema changes are not auto-rolled-back; the inventory schema changes so
far are additive and safe to leave in place.)

---

## Quick troubleshooting

| Symptom | Fix |
|---|---|
| `Connection in use: ('0.0.0.0', 5000)` | Old gunicorn still up. Stop it in the tmux pane (`Ctrl+C`) or `pkill -f "gunicorn app:app"`, then start once. |
| Log shows `[inventory] module DISABLED (failed to load): …` | Read the error. Usually a bad `INVENTORY_DB_*` value in `.env` or the schema/role not created. Existing backend keeps running regardless. |
| `/api/inventory/health` returns `"db":false` | DB connection problem — check `INVENTORY_DB_PASSWORD` / role / that `inventory_schema.sql` ran. |
| Changes not taking effect | You must restart gunicorn; a running process holds the old code (and old `.env`). |
| Scheduler / alert changes not applied | Same — restart gunicorn (scheduler starts on import). |
