"""
Integration test for the Task Manager module. Needs a Postgres where tasks_schema.sql and
tasks_db_setup.sql have been applied. It sends no email and calls no external service.

  TASKS_TEST_DB_PORT=5599 TASKS_TEST_DB_PASSWORD=tpass python test_task_manager.py
"""
import os, sys, re, time
if not os.getenv("TASKS_TEST_DB_PORT"):
    print("SKIP: set TASKS_TEST_DB_PORT (and TASKS_TEST_DB_PASSWORD) to run"); sys.exit(0)
os.environ.setdefault("EMAIL_USERNAME", "x"); os.environ.setdefault("EMAIL_PASSWORD", "y")
sys.path.insert(0, ".")
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from flask import Flask
from psycopg2.extras import Json
from config import Config
Config.TASKS_DB_HOST = os.getenv("TASKS_TEST_DB_HOST", "localhost")
Config.TASKS_DB_PORT = int(os.environ["TASKS_TEST_DB_PORT"])
Config.TASKS_DB_PASSWORD = os.getenv("TASKS_TEST_DB_PASSWORD", "")
Config.TASKS_SESSION_SECRET = "s" * 40
Config.TASKS_APP_PUBLIC_BASE = "https://tasks.example.com"
Config.TASKS_ADMIN_EMAILS = ["bob@x.com"]
from models.tasks_db import tasks_db
import routes.task_manager_routes as tmr
from routes.task_manager_routes import task_manager_bp
tmr.drain_in_background = lambda: None   # the test drains the outbox itself
from task_manager import auth
from services.task_manager import outbox, mailer, digest, employees, templates

sent = []
mailer.send_mail = lambda mail, to: sent.append((to, mail))
MS = {"ms-alice": "alice@x.com", "ms-bob": "bob@x.com", "ms-ghost": "ghost@x.com", "ms-carol": "carol@x.com"}
auth._email_from_ms = lambda token: MS.get(token)

# clean slate (keeps the seeded admin account)
for _t in ("notification_outbox", "email_action_tokens", "task_events", "tasks", "reminder_runs"):
    tasks_db.execute(f"DELETE FROM {_t}")
tasks_db.execute("DELETE FROM app_user_roles WHERE employee_id <> 'admin'")
tasks_db.execute("DELETE FROM employee_emails WHERE employee_id <> 'admin'")
tasks_db.execute("DELETE FROM employees WHERE id <> 'admin'")
rows = [{"id": i, "full_name": n, "email": e, "job_title": "T", "department": "D", "badge_employee_id": None,
         "is_active": True, "source_updated_at": None}
        for i, n, e in [("101", "Alice A", "alice@x.com"), ("202", "Bob B", "bob@x.com"), ("303", "Carol C", "carol@x.com")]]
tasks_db.call("SELECT fn_upsert_employees(%s::jsonb)", [Json(rows)])

app = Flask(__name__); app.register_blueprint(task_manager_bp); c = app.test_client()
ok = []
def chk(name, v): ok.append(bool(v)); print("PASS" if v else "FAIL", name)
def H(t): return {"Authorization": f"Bearer {t}"}
def post(path, t=None, **body): return c.post("/api/tasks" + path, json=body, headers=H(t) if t else {})
def get(path, t=None): return c.get("/api/tasks" + path, headers=H(t) if t else {})

chk("health", get("/health").json["db"] is True)
chk("no token -> 401", get("/tasks/mine").status_code == 401 and get("/tasks/mine").json["code"] == "UNAUTHENTICATED")
chk("bad token -> 401", get("/tasks/mine", "nope").status_code == 401)
r = get("/employees/me", "ms-ghost"); chk("MS user not in directory -> 403 NOT_IN_DIRECTORY", r.status_code == 403 and r.json["code"] == "NOT_IN_DIRECTORY")
r = get("/employees/me", "ms-alice"); chk("MS token -> alice, role user", r.status_code == 200 and r.json["id"] == "101" and r.json["role"] == "user")
r = get("/employees/me", "ms-bob"); chk("admin email list grants admin", r.json["role"] == "admin")
chk("auth/me returns email", get("/auth/me", "ms-alice").json["email"] == "alice@x.com")

# password sign-in (the admin account), as it works today
r = post("/auth/login", login="admin@gmail.com", password="adminadmin")
chk("admin login ok", r.status_code == 200 and r.json["token"].startswith("tk.") and r.json["employee"]["role"] == "admin")
adm = r.json["token"]
chk("session token works", get("/employees/me", adm).json["id"] == "admin")
chk("wrong password -> 400 VALIDATION", post("/auth/login", login="admin@gmail.com", password="x").json["code"] == "VALIDATION")
chk("tampered session token rejected", get("/employees/me", adm[:-3] + "xxx").status_code == 401)
real = time.time; time.time = lambda: real() + 8 * 86400
chk("expired session token rejected", get("/employees/me", adm).status_code == 401); time.time = real
chk("rate limit on bad logins", (lambda: [post("/auth/login", login="a", password="b") for _ in range(10)] and post("/auth/login", login="admin@gmail.com", password="adminadmin").status_code == 429)())
auth._ATTEMPTS.clear()

# admin-created user can sign in; admin-only mode blocks directory employees
r = post("/admin/test-users", adm, login_id="tester1", full_name="Tester One", email="t1@x.com", password="pw1234")
chk("admin creates test user", r.status_code == 200 and r.json["id"] == "tester1")
chk("non-admin cannot create user", post("/admin/test-users", "ms-alice", login_id="z", full_name="Z", email="z@x.com", password="pw1234").status_code == 403)
chk("test user can sign in", post("/auth/login", login="tester1", password="pw1234").status_code == 200)
tasks_db.execute("UPDATE employees SET password_hash = fn_password_hash('adminadmin') WHERE id = '101'")
chk("directory employee password login works by default", post("/auth/login", login="alice@x.com", password="adminadmin").status_code == 200)
Config.TASKS_PASSWORD_LOGIN_ADMIN_ONLY = True
chk("admin-only mode blocks directory employee", post("/auth/login", login="alice@x.com", password="adminadmin").status_code == 403)
chk("admin-only mode still allows admin", post("/auth/login", login="admin@gmail.com", password="adminadmin").status_code == 200)
Config.TASKS_PASSWORD_LOGIN_ADMIN_ONLY = False; auth._ATTEMPTS.clear()

# task workflow
due = (datetime.now() + timedelta(days=3)).date().isoformat()
r = post("/tasks", "ms-alice", assigned_to="202", due_date=due, description="Write the report", reference="REF-1", cc="carol@x.com")
chk("create task", r.status_code == 200 and r.json["status"] == "pending" and r.json["assigned_to"] == "202")
tid = r.json["id"]
chk("blank description -> VALIDATION", post("/tasks", "ms-alice", assigned_to="202", due_date=due, description="  ").json["code"] == "VALIDATION")
chk("unknown assignee -> VALIDATION/NOT_IN_DIRECTORY", post("/tasks", "ms-alice", assigned_to="999", due_date=due, description="x").status_code in (400, 403))
chk("bob sees it in my tasks", [t["id"] for t in get("/tasks/mine?filter=open", "ms-bob").json] == [tid])
chk("alice sees it as delegated", [t["id"] for t in get("/tasks/delegated?filter=open", "ms-alice").json] == [tid])
chk("counts", get("/tasks/counts", "ms-bob").json["my_pending"] == 1)
r = post(f"/tasks/{tid}/cancel", "ms-bob", reason="no"); chk("non-creator cancel -> 403 NOT_ALLOWED", r.status_code == 403 and r.json["code"] == "NOT_ALLOWED")
r = post(f"/tasks/{tid}/complete", "ms-bob"); chk("complete a pending task -> 409 INVALID_TRANSITION", r.status_code == 409 and r.json["code"] == "INVALID_TRANSITION")
chk("outsider cannot read history", get(f"/tasks/{tid}/history", "ms-carol").status_code in (403, 400))

# emails: queued rows -> sent via the (mocked) mailer
queued = tasks_db.query("SELECT kind, recipient_email, status FROM notification_outbox ORDER BY created_at")
chk("emails queued for assignee, sender, cc", {q["recipient_email"] for q in queued} >= {"bob@x.com", "alice@x.com", "carol@x.com"})
n = outbox.process_outbox(50)
chk("outbox sends and marks sent", n == len(queued) and len(sent) == n and
    tasks_db.query_one("SELECT count(*) AS n FROM notification_outbox WHERE status <> 'sent'")["n"] == 0)
bob_mail = next(m for to, m in sent if to == "bob@x.com" and "New task" in m.subject)
tok = re.search(r"token=([^&\"]+)&action=accept", bob_mail.html).group(1)
chk("assignee email has Accept/Reject links to the site", "https://tasks.example.com/email-action/?token=" in bob_mail.html and "action=deny" in bob_mail.html)
r = post("/email-action", token=tok, action="deny"); chk("deny without reason -> VALIDATION", r.status_code == 400)
r = post("/email-action", token="garbage", action="accept"); chk("bad email token -> VALIDATION", r.status_code == 400 and r.json["code"] == "VALIDATION")
r = post("/email-action", token=tok, action="accept"); chk("email accept (no login)", r.status_code == 200 and r.json["status"] == "in_progress")
r = post("/email-action", token=tok, action="accept"); chk("email accept is idempotent", r.status_code == 200)
sent.clear(); outbox.process_outbox(50)
chk("sender + cc told about acceptance", {to for to, _ in sent} >= {"alice@x.com", "carol@x.com"})

# forward, complete, deny/reassign, note
r = post(f"/tasks/{tid}/forward", "ms-bob", to_employee="303", note="you do it"); chk("forward", r.status_code == 200 and r.json["assigned_to"] == "303")
chk("note update", post(f"/tasks/{tid}/note", "ms-alice", note="fyi").status_code == 200)
chk("accept after forward", post(f"/tasks/{tid}/accept", "ms-carol").json["status"] == "in_progress")
r = post(f"/tasks/{tid}/accept-and-complete", "ms-carol", note="done")
chk("accept-and-complete (already accepted) -> completes or a clean workflow error", (r.status_code == 200 and r.json["status"] == "completed") or r.status_code in (400, 403, 409))
if r.json.get("status") != "completed": post(f"/tasks/{tid}/complete", "ms-carol", note="done")
h = get(f"/tasks/{tid}/history", "ms-alice").json
chk("history has events", len(h["events"]) >= 4 and h["task"]["status"] == "completed")
r = post("/tasks", "ms-alice", assigned_to="202", due_date=due, description="Second"); t2 = r.json["id"]
chk("deny", post(f"/tasks/{t2}/deny", "ms-bob", reason="busy").json["status"] == "denied")
chk("reassign after deny", post(f"/tasks/{t2}/reassign", "ms-alice", to_employee="303", note="try carol").json["assigned_to"] == "303")
chk("cancel", post(f"/tasks/{t2}/cancel", "ms-alice", reason="changed mind").json["status"] == "cancelled")
_nos = [t["task_no"] for t in get("/tasks/delegated?filter=all", "ms-alice").json]
chk("newest first", _nos == sorted(_nos, reverse=True))
chk("filter all lists both", len(get("/tasks/delegated?filter=all", "ms-alice").json) == 2)

# admin
chk("admin lists all tasks", len(get("/admin/tasks", adm).json) == 2)
chk("non-admin blocked from admin list", get("/admin/tasks", "ms-alice").status_code == 403)
chk("admin notifications", isinstance(get("/admin/notifications", adm).json, list))
chk("admin employees", "employees" in get("/admin/employees", adm).json)
tasks_db.execute("UPDATE notification_outbox SET status = 'failed' WHERE id = (SELECT id FROM notification_outbox LIMIT 1)")
fid = tasks_db.query_one("SELECT id FROM notification_outbox WHERE status = 'failed'")["id"]
chk("admin retry", post(f"/admin/notifications/{fid}/retry", adm).json["status"] == "queued")

# employee sync (feed mocked) and digest
feed = [{"ms_user_id": 404, "name": "Dan D", "email": "Dan@X.com", "job_title_name": "E", "department_name": "Q", "employee_id": "B1", "status": 1},
        {"ms_user_id": 101, "name": "Alice A", "email": "alice@x.com", "status": 1},
        {"ms_user_id": 202, "name": "Bob B", "email": "bob@x.com", "status": 1},
        {"ms_user_id": 303, "name": "Carol C", "email": "carol@x.com", "status": 1}]
employees._rows_from_source = lambda: feed
res = post("/admin/employees/sync", adm); chk("sync via API", res.status_code == 200)
chk("sync mapped Dan", tasks_db.query_one("SELECT email FROM v_employees WHERE id = '404'")["email"] == "dan@x.com")
chk("non-admin cannot sync", post("/admin/employees/sync", "ms-alice").status_code == 403)
chk("sync maps status/email", employees.map_rows([{"ms_user_id": 1, "name": "N", "email": "A@B.com", "status": 0}])[0]["is_active"] is False)

tz = ZoneInfo("America/New_York")
tomorrow = (datetime.now(tz) + timedelta(days=1)).date().isoformat()
tasks_db.call("SELECT fn_create_task(%s, %s, %s::date, %s, NULL, NULL)", ["101", "303", tomorrow, "Due tomorrow"])
chk("digest skipped outside 8am", digest.run_due_digest(datetime.now(tz).replace(hour=14)).get("skipped") == "not-8am")
r8 = digest.run_due_digest(datetime.now(tz).replace(hour=8)); chk("digest queues at 8am", r8.get("queued", 0) >= 1)
chk("digest runs once a day", digest.run_due_digest(datetime.now(tz).replace(hour=8)).get("skipped") == "already-ran")
sent.clear(); outbox.process_outbox(50)
chk("digest email rendered", any("Tasks due tomorrow" in m.subject for _, m in sent))

# templates
m = templates.render_mail({"kind": "task_created", "assigner_name": "A<b>", "assignee_name": "B", "description": "x" * 100,
                           "task_no": 5, "due_date": "2026-12-01", "status": "pending", "link_for": "assignee", "action_token": "t k"}, "https://s.com/")
chk("template escapes + dates + links", "A&lt;b&gt;" in m.html and "12/01/2026" in m.html and "token=t%20k&action=accept" in m.html and "…" in m.subject)

# the module's role cannot see public
try:
    tasks_db.query("SELECT 1 FROM public.users LIMIT 1"); leak = True
except Exception: leak = False
chk("module role has no access to public tables", not leak)
print(sum(ok), "/", len(ok)); sys.exit(0 if all(ok) else 1)
