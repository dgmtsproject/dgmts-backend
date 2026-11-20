import os, sys, time
os.environ.update(EMAIL_USERNAME="x", EMAIL_PASSWORD="y")
sys.path.insert(0, ".")
from werkzeug.security import generate_password_hash
from flask import Flask
from config import Config
Config.INVENTORY_SUPERUSER_PASSWORD_HASH = generate_password_hash("pw-123", method="pbkdf2:sha256")
Config.INVENTORY_SUPERUSER_SECRET = "s"*40
from routes.inventory_routes import inventory_bp
from inventory import superuser
app = Flask(__name__); app.register_blueprint(inventory_bp); c = app.test_client()
ok=[]
def chk(n,v): ok.append(bool(v)); print("PASS" if v else "FAIL", n)
chk("status enabled", c.get("/api/inventory/superuser/status").json["enabled"] is True)
chk("wrong pw 401", c.post("/api/inventory/superuser/login", json={"username":"superuser","password":"no"}).status_code==401)
r=c.post("/api/inventory/superuser/login", json={"username":"SuperUser","password":"pw-123"}); chk("login 200", r.status_code==200)
t=r.json["token"]; chk("email admin", r.json["email"]=="admin@gmail.com")
p=superuser.verify_token(t); chk("verify", p and p["email"]=="admin@gmail.com")
chk("tampered rejected", superuser.verify_token(t[:-2]+"xx") is None)
real=time.time; time.time=lambda: real()+9*3600
chk("expired rejected", superuser.verify_token(t) is None); time.time=real
from inventory.ms_auth import validate_ms_token, is_inventory_admin
chk("validate_ms_token su", validate_ms_token(t)["email"]=="admin@gmail.com" and is_inventory_admin(validate_ms_token(t)["email"]))
chk("forged su.x.y rejected", validate_ms_token("su.abc.def") is None)
superuser._attempts.clear()
for _ in range(5): c.post("/api/inventory/superuser/login", json={"username":"a","password":"b"})
chk("rate limit 429", c.post("/api/inventory/superuser/login", json={"username":"superuser","password":"pw-123"}).status_code==429)
Config.INVENTORY_SUPERUSER_PASSWORD_HASH=None
chk("disabled 404", c.post("/api/inventory/superuser/login", json={}).status_code==404 and superuser.verify_token(t) is None)
print(sum(ok),"/",len(ok))
