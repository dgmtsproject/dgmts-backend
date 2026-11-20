"""Run from dgmts-backend:  python test_consumable_min_qty.py  (fake DB, sends nothing)."""
import os, sys, re
os.environ.setdefault("EMAIL_USERNAME", "x"); os.environ.setdefault("EMAIL_PASSWORD", "y")
sys.path.insert(0, ".")
from contextlib import contextmanager
from flask import Flask
from config import Config
import routes.inventory_routes as ir
from services.inventory import consumable_alerts as ca

ITEM = {"id": "it1", "code": "GLV-1", "description": "Gloves <M>", "min_qty": 5, "supplier_name": "Acme"}
st = {"stock": {}, "cols_ok": True, "items": [], "alter": 0}
sent = []

class Cur:
    def __init__(s): s.row = None
    def execute(s, sql, p=None):
        if "FROM inventory_stock" in sql and "FOR UPDATE" in sql:
            q = st["stock"].get((p[0], p[1])); s.row = {"quantity": q} if q is not None else None
        elif "INSERT INTO inventory_stock" in sql: st["stock"][(p[0], p[1])] = p[2]
        elif "INSERT INTO consumption_logs" in sql: s.row = {"id": "l1"}
    def fetchone(s): return s.row
class FakeDB:
    @contextmanager
    def cursor(self, commit=True): yield Cur()
    def query_one(self, sql, p=None):
        if "information_schema" in sql: return {"x": 1} if st["cols_ok"] else None
        if "SELECT quantity FROM inventory_stock" in sql:
            q = st["stock"].get((p[0], p[1])); return {"quantity": q} if q is not None else None
        if "FROM consumable_items i" in sql: return dict(ITEM)
        return None
    def execute(self, sql, p=None, returning=False):
        if "ALTER TABLE" in sql: st["alter"] += 1; st["cols_ok"] = True
    def execute_one(self, sql, p=None, returning=True):
        if "INSERT INTO inventory_stock" in sql: st["stock"][(p[0], p[1])] = p[2]; return {"quantity": p[2]}
        if "INSERT INTO consumable_items" in sql:
            cols = re.search(r"\((.*?)\) VALUES", sql).group(1).split(", "); st["items"].append(dict(zip(cols, p))); return {"id": "n1"}
        if "UPDATE consumable_items" in sql: st["items"].append(("upd", sql, p)); return {"id": "n1"}
fake = FakeDB(); ir.inventory_db = fake; ca.inventory_db = fake
ca._send = lambda item, branch, q, m: sent.append((branch, q, m))
Config.INVENTORY_LOW_STOCK_NOTIFY_EMAILS = "a@x.com, b@x.com"
app = Flask(__name__); app.register_blueprint(ir.inventory_bp); c = app.test_client()
ok = []
def chk(n, v): ok.append(bool(v)); print("PASS" if v else "FAIL", n)

chk("crossing rule", ca.crossed_minimum(10,5,5) and ca.crossed_minimum(None,3,5) and not ca.crossed_minimum(4,3,5)
    and not ca.crossed_minimum(10,6,5) and not ca.crossed_minimum(10,0,0))
chk("recipients parsed", ca.recipients() == ["a@x.com", "b@x.com"])
subj, html = ca.build_email(ITEM, "DC", 4, 5)
chk("email escapes + content", "&lt;M&gt;" in html and "DC" in html and "4 left" in subj)

st["cols_ok"] = False
r = c.post("/api/inventory/consumable-items", json={"code":"A","description":"d","supplier_id":"s","min_qty":"7"})
chk("create adds column + stores min_qty", r.status_code == 201 and st["alter"] == 1 and st["items"][-1]["min_qty"] == 7)
r = c.post("/api/inventory/consumable-items", json={"code":"B","description":"d","supplier_id":"s"})
chk("create without min_qty untouched", "min_qty" not in st["items"][-1])
chk("negative rejected", c.post("/api/inventory/consumable-items", json={"code":"C","description":"d","supplier_id":"s","min_qty":-1}).status_code == 400)
chk("non-number rejected", c.post("/api/inventory/consumable-items", json={"code":"C","description":"d","supplier_id":"s","min_qty":"abc"}).status_code == 400)
chk("blank -> 0", (c.post("/api/inventory/consumable-items", json={"code":"D","description":"d","supplier_id":"s","min_qty":""}), st["items"][-1]["min_qty"] == 0)[1])
r = c.patch("/api/inventory/consumable-items/n1", json={"min_qty": 9}); chk("patch min_qty", r.status_code == 200 and "min_qty" in st["items"][-1][1])

st["stock"][("it1","DC")] = 8
r = c.post("/api/inventory/consumption-logs", json={"item_id":"it1","employee_identifier":"E1","branch":"DC","quantity_consumed":2})
chk("consume 8->6: no email", r.status_code == 201 and not sent)
r = c.post("/api/inventory/consumption-logs", json={"item_id":"it1","employee_identifier":"E1","branch":"DC","quantity_consumed":1})
chk("consume 6->5 (reaches min): one email", r.status_code == 201 and sent == [("DC", 5, 5)])
r = c.post("/api/inventory/consumption-logs", json={"item_id":"it1","employee_identifier":"E1","branch":"DC","quantity_consumed":1})
chk("consume 5->4: no repeat", len(sent) == 1)
r = c.put("/api/inventory/inventory-stock", json={"item_id":"it1","branch":"DC","quantity":50})
chk("restock: no email", r.status_code == 200 and len(sent) == 1)
r = c.put("/api/inventory/inventory-stock", json={"item_id":"it1","branch":"Hampton","quantity":3})
chk("new stock row at/below min: email", len(sent) == 2 and sent[-1] == ("Hampton", 3, 5))
ITEM["min_qty"] = 0
st["stock"][("it1","DC")] = 3
c.post("/api/inventory/consumption-logs", json={"item_id":"it1","employee_identifier":"E1","branch":"DC","quantity_consumed":2})
chk("min_qty 0: never emails", len(sent) == 2)
ITEM["min_qty"] = 5; ca.recipients = lambda: []
chk("no recipients: no crash", c.put("/api/inventory/inventory-stock", json={"item_id":"it1","branch":"Maryland","quantity":1}).status_code == 200)
ir._MIN_QTY_READY = False; ir._MIN_QTY_NEXT_TRY = 0; st["cols_ok"] = False
fake.execute = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no privilege"))
n = len(sent)
r = c.put("/api/inventory/inventory-stock", json={"item_id":"it1","branch":"DC","quantity":1})
chk("column unavailable: stock still saved, no email", r.status_code == 200 and len(sent) == n)
print(sum(ok), "/", len(ok)); sys.exit(0 if all(ok) else 1)
