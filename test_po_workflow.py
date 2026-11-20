import os, sys, json, re, time
os.environ.update(EMAIL_USERNAME="x", EMAIL_PASSWORD="y")
sys.path.insert(0, "/Users/xperia/Workspace/Personal/Z/dgmts-imsite/dgmts-backend")
from flask import Flask
from config import Config
Config.INVENTORY_PO_EMAIL_ACTION_SECRET = "test-secret-0123456789abcdef"
Config.INVENTORY_APP_PUBLIC_BASE = "https://ui.example.com"
Config.INVENTORY_API_PUBLIC_BASE = "https://api.example.com"
Config.INVENTORY_INTERNAL_SECRET = "internal-secret-xyz"
from routes.inventory_routes import inventory_bp
from services.inventory import po_workflow as wf

PO = {"po_number": "PO-1", "requested_by": "101", "supervisor_id": "202",
      "item_description": "Gloves", "status": "Open", "quantity": 5, "rejection_reason": None}
state = {"po": dict(PO)}; emails = []; updates = []; deleted = []
class FakeDB:
    def query_one(self, sql, params=None):
        if "FROM purchase_requests" in sql: return state["po"]
        if "ms_directory_employees" in sql:
            e = {"101": "req@x.com", "202": "sup@x.com"}.get(params[0]); return {"email": e} if e else None
        return None
    def execute_one(self, sql, params=None, returning=True):
        cols = [c.split(" = ")[0] for c in re.search(r"SET (.*) WHERE", sql).group(1).split(", ")]
        updates.append(dict(zip(cols, params[:-1]))); state["po"].update(dict(zip(cols, params[:-1]))); return {}
    def execute(self, sql, params=None, returning=False): deleted.append(params); return 1
wf.inventory_db = FakeDB()
import routes.inventory_routes as ir; ir.inventory_db = FakeDB()
wf.send_inventory_email = lambda to, s, h: (emails.append((to, s)) or (True, None))
app = Flask(__name__); app.register_blueprint(inventory_bp)
c = app.test_client()
P = "/api/inventory"
res = []
def check(name, cond, extra=""):
    res.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name, extra if not cond else "")

routes = {r.rule for r in app.url_map.iter_rules()}
for r in ["/po/workflow-notify","/po/send-supervisor-pending-email","/po/email-action","/po/email-reject-preview","/email/low-stock"]:
    check(f"route registered {r}", P + r in routes)
check("existing routes still registered", all(P + r in routes for r in ["/consumable-items","/purchase-requests","/users","/ms-directory/sync"]), "")

# --- workflow-notify
r = c.post(P+"/po/workflow-notify", json={"poNumber":"PO-1","event":"approved"})
check("notify without bearer -> 401", r.status_code == 401)
H = {"Authorization": "Bearer tok"}
r = c.post(P+"/po/workflow-notify", json={"event":"approved"}, headers=H)
check("notify missing poNumber -> 400", r.status_code == 400)
r = c.post(P+"/po/workflow-notify", json={"poNumber":"PO-1","event":"nope"}, headers=H)
check("notify unknown event -> 400", r.status_code == 400 and "Unknown event" in r.json["error"])
r = c.post(P+"/po/workflow-notify", json={"poNumber":"PO-1","event":"edited"}, headers=H)
check("notify edited w/o editor -> 400", r.status_code == 400)
emails.clear()
r = c.post(P+"/po/workflow-notify?dry_run=1", json={"poNumber":"PO-1","event":"approved"}, headers=H)
check("dry run returns wouldSend, sends nothing", r.status_code == 200 and r.json["dryRun"] and len(r.json["wouldSend"]) == 2 and not emails, r.json)
check("dry run recipients resolved", r.json["wouldSend"][0]["to"] == ["req@x.com"])
check("accounts link points at backend", "https://api.example.com/api/inventory/po/email-action?token=" in r.json["wouldSend"][1]["html"])
r = c.post(P+"/po/workflow-notify", json={"poNumber":"PO-1","event":"approved"}, headers=H)
check("real send -> 200 and 2 emails", r.status_code == 200 and len(emails) == 2, emails)

# --- supervisor pending
r = c.post(P+"/po/send-supervisor-pending-email", json={"supervisorId":"202"})
check("pending w/o items -> 400", r.status_code == 400)
r = c.post(P+"/po/send-supervisor-pending-email?dry_run=1", json={"supervisorId":"202","requesteeName":"A","items":[{"poNumber":"PO-1","quantity":5,"itemDescription":"Gloves"}]})
check("pending dry run ok", r.status_code == 200 and r.json["wouldSend"][0]["to"][0] == "sup@x.com")
r = c.post(P+"/po/send-supervisor-pending-email?dry_run=1", json={"supervisorId":"999","items":[{"poNumber":"PO-1"}]})
check("pending unknown supervisor -> 500 w/ message", r.status_code == 500 and "Supervisor email" in r.json["error"])

# --- email action GET
exp = int(time.time()) + 3600
tA = wf.sign_po_email_action_token({"poNumber":"PO-1","action":"approve","supervisorId":"202","exp":exp})
tR = wf.sign_po_email_action_token({"poNumber":"PO-1","action":"reject","supervisorId":"202","exp":exp})
tO = wf.sign_po_email_action_token({"poNumber":"PO-1","action":"order_placed","exp":exp})
tX = wf.sign_po_email_action_token({"poNumber":"PO-1","action":"approve","supervisorId":"202","exp":int(time.time())-10})
r = c.get(P+"/po/email-action")
check("no token -> redirect to result error", r.status_code == 302 and "email-result" in r.headers["Location"] and "status=error" in r.headers["Location"])
r = c.get(P+"/po/email-action?token="+tX)
check("expired token -> error redirect", "invalid+or+has+expired" in r.headers["Location"] or "invalid%20or%20has%20expired" in r.headers["Location"] or "expired" in r.headers["Location"])
r = c.get(P+"/po/email-action?token="+tR)
check("reject token -> reject form on UI", r.headers["Location"].startswith("https://ui.example.com/purchase-request/email-reject?token="))
r = c.get(P+"/po/email-reject-preview?token="+tR)
check("reject preview JSON", r.status_code == 200 and r.json == {"poNumber":"PO-1","itemDescription":"Gloves","quantity":"5"}, r.json)
r = c.get(P+"/po/email-reject-preview?token="+tA)
check("preview rejects non-reject token", r.status_code == 400)
emails.clear(); updates.clear(); state["po"] = dict(PO)
r = c.get(P+"/po/email-action?token="+tA)
check("approve -> 302 to UI sso entry", r.status_code == 302 and r.headers["Location"].startswith("https://ui.example.com/?next="), r.headers.get("Location"))
check("approve updated DB to Approved", state["po"]["status"] == "Approved")
check("approve sent requester + accounts emails", len(emails) == 2)
r = c.get(P+"/po/email-action?token="+tA)
check("approve again -> 'no longer open' error", "no+longer+open" in r.headers["Location"] or "no%20longer%20open" in r.headers["Location"])
emails.clear(); r = c.get(P+"/po/email-action?token="+tO)
check("order placed -> status Order Placed", state["po"]["status"] == "Order Placed" and r.status_code == 302)

# --- reject POST (form), 303
state["po"] = dict(PO)
r = c.post(P+"/po/email-action", data={"token": tR, "rejectionReason": ""})
check("reject POST empty reason -> 303 back to form w/ formError", r.status_code == 303 and "formError=" in r.headers["Location"])
r = c.post(P+"/po/email-action", data={"token": tR, "rejectionReason": "Too pricey"})
check("reject POST -> 303 + status Rejected + reason saved", r.status_code == 303 and state["po"]["status"] == "Rejected" and state["po"]["rejection_reason"] == "Too pricey")
r = c.post(P+"/po/email-action", data={"token": tA, "rejectionReason": "x"})
check("POST with non-reject token -> 303 error", r.status_code == 303 and "status=error" in r.headers["Location"])
r = c.post(P+"/po/email-action", json={"token": tR, "rejectionReason": "x"})
check("POST JSON accepted", r.status_code == 303)

# --- low stock
r = c.post(P+"/email/low-stock", json={"itemName":"A","quantity":1,"threshold":5})
check("low-stock -> success", r.status_code == 200 and r.json["success"])

# --- delete protection
r = c.delete(P+"/purchase-requests/PO-1")
check("DELETE without token -> 401", r.status_code == 401, r.status_code)
r = c.delete(P+"/purchase-requests/PO-1", headers={"X-Inventory-Internal-Secret": "internal-secret-xyz"})
check("DELETE with internal secret -> allowed", r.status_code == 200 and deleted)
print("\nTOTAL", sum(res), "/", len(res), "passed")
sys.exit(0 if all(res) else 1)
