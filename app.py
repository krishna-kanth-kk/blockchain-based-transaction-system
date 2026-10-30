"""UPI Chain sandbox: a deterministic, auditable local payment ledger.

This application settles only simulated INR. A regulated payment adapter can
be added later without pretending that a local SQLite process is a bank.
"""
import hashlib, json, os, secrets, sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from flask import Flask, jsonify, render_template, request, session
try:
    from eth_account import Account
    from eth_account.messages import encode_defunct
except ImportError:
    Account = None
    encode_defunct = None

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "upi_blockchain.db")
KEY_DIR = os.path.join(APP_DIR, "keys")
os.makedirs(KEY_DIR, exist_ok=True)
app = Flask(__name__)
app.secret_key = os.environ.get("APP_SECRET") or secrets.token_hex(32)
app.config.update(JSON_SORT_KEYS=True, SESSION_COOKIE_HTTPONLY=True,
                 SESSION_COOKIE_SAMESITE="Lax",
                 SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
                 MAX_CONTENT_LENGTH=16 * 1024)

DEMO_USERS = {"alice": {"name": "Alice", "upi": "alice@demo", "pin": "1234"},
              "bob": {"name": "Bob", "upi": "bob@demo", "pin": "1234"},
              "charlie": {"name": "Charlie", "upi": "charlie@demo", "pin": "1234"}}
VALIDATORS = ["Bank-A", "Bank-B", "PSP-Node"]

def db():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=10000")
    return conn

def init_db():
    with db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS balances (username TEXT PRIMARY KEY, balance_cents INTEGER NOT NULL CHECK(balance_cents >= 0));
        CREATE TABLE IF NOT EXISTS blocks (block_index INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, transaction_json TEXT NOT NULL, previous_hash TEXT NOT NULL, block_hash TEXT NOT NULL UNIQUE, signature TEXT NOT NULL, public_key TEXT NOT NULL, validators_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS payment_requests (idempotency_key TEXT PRIMARY KEY, transaction_id TEXT NOT NULL UNIQUE, sender TEXT NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wallet_challenges (username TEXT PRIMARY KEY, nonce TEXT NOT NULL, message TEXT NOT NULL, expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS wallets (username TEXT PRIMARY KEY, address TEXT NOT NULL UNIQUE, chain_id INTEGER NOT NULL, network TEXT NOT NULL, provider TEXT NOT NULL, verified_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS transaction_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, transaction_id TEXT NOT NULL, event_type TEXT NOT NULL, event_data TEXT NOT NULL, created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, owner TEXT NOT NULL, name TEXT NOT NULL, email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '', upi_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS invoices (invoice_id TEXT PRIMARY KEY, owner TEXT NOT NULL, customer_id TEXT NOT NULL, invoice_number TEXT NOT NULL UNIQUE, amount_cents INTEGER NOT NULL CHECK(amount_cents > 0), currency TEXT NOT NULL DEFAULT 'INR', description TEXT NOT NULL DEFAULT '', due_date TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', payment_token TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, paid_at TEXT NOT NULL DEFAULT '');
        CREATE INDEX IF NOT EXISTS idx_invoices_owner_status ON invoices(owner,status);
        CREATE INDEX IF NOT EXISTS idx_blocks_tx ON blocks(transaction_json);
        """)
        columns = {r["name"] for r in conn.execute("PRAGMA table_info(balances)")}
        if "balance_cents" not in columns and "balance" in columns:
            conn.execute("ALTER TABLE balances RENAME TO balances_legacy")
            conn.execute("CREATE TABLE balances (username TEXT PRIMARY KEY, balance_cents INTEGER NOT NULL CHECK(balance_cents >= 0))")
            for row in conn.execute("SELECT username, balance FROM balances_legacy"):
                conn.execute("INSERT INTO balances VALUES (?, ?)", (row[0], round(float(row[1]) * 100)))
        for username in DEMO_USERS:
            conn.execute("INSERT OR IGNORE INTO balances VALUES (?, ?)", (username, 500000))

def money(cents): return float(Decimal(cents) / 100)

def parse_amount(value):
    try: amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError): raise ValueError("Enter a valid amount")
    if amount <= 0 or amount > Decimal("100000.00"): raise ValueError("Amount must be between ₹0.01 and ₹1,00,000")
    return int(amount * 100), float(amount)

def canonical(data): return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
def sha256(data): return hashlib.sha256(data).hexdigest()
def key_paths(username): return (os.path.join(KEY_DIR, username + "_private.pem"), os.path.join(KEY_DIR, username + "_public.pem"))

def get_or_create_keypair(username):
    priv_path, pub_path = key_paths(username)
    if not os.path.exists(priv_path) or not os.path.exists(pub_path):
        key = ec.generate_private_key(ec.SECP256R1())
        with open(priv_path, "wb") as f: f.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        with open(pub_path, "wb") as f: f.write(key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with open(priv_path, "rb") as f: private = serialization.load_pem_private_key(f.read(), password=None)
    with open(pub_path, "rb") as f: public = f.read().decode()
    return private, public

def get_chain(conn=None):
    own = conn is None; conn = conn or db()
    rows = conn.execute("SELECT * FROM blocks ORDER BY block_index").fetchall()
    if own: conn.close()
    return [dict(r) for r in rows]

def verify_chain(conn=None):
    chain = get_chain(conn); previous = "0" * 64
    for expected, block in enumerate(chain):
        try:
            tx = json.loads(block["transaction_json"])
            payload = {"index": block["block_index"], "timestamp": block["timestamp"], "transaction": tx, "previous_hash": block["previous_hash"]}
            if block["block_index"] != expected or block["previous_hash"] != previous: return False, f"Link/index mismatch at block {expected}"
            if sha256(canonical(payload)) != block["block_hash"]: return False, f"Hash mismatch at block {expected}"
            pub = serialization.load_pem_public_key(block["public_key"].encode())
            pub.verify(bytes.fromhex(block["signature"]), block["block_hash"].encode(), ec.ECDSA(hashes.SHA256()))
            previous = block["block_hash"]
        except (InvalidSignature, ValueError, TypeError, KeyError, json.JSONDecodeError): return False, f"Signature or data verification failed at block {expected}"
    return True, "All blocks, links, and signatures are valid"

def add_transaction_block(conn, tx, private_key, public_pem):
    last = conn.execute("SELECT block_index, block_hash FROM blocks ORDER BY block_index DESC LIMIT 1").fetchone()
    index, previous = ((last["block_index"] + 1, last["block_hash"]) if last else (0, "0" * 64))
    timestamp = datetime.now(timezone.utc).isoformat()
    payload = {"index": index, "timestamp": timestamp, "transaction": tx, "previous_hash": previous}
    block_hash = sha256(canonical(payload)); signature = private_key.sign(block_hash.encode(), ec.ECDSA(hashes.SHA256())).hex()
    approvals = VALIDATORS[:]
    if len(approvals) < 2: raise RuntimeError("Validator quorum not reached")
    conn.execute("INSERT INTO blocks VALUES (?,?,?,?,?,?,?,?)", (index, timestamp, json.dumps(tx, sort_keys=True), previous, block_hash, signature, public_pem, json.dumps(approvals)))
    return {"index": index, "timestamp": timestamp, "transaction": tx, "previous_hash": previous, "hash": block_hash, "signature": signature, "validators": approvals}

def body(): return request.get_json(silent=True) or {}

def login_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if "username" not in session: return jsonify({"error": "Please sign in first"}), 401
        return fn(*args, **kwargs)
    return wrapper

@app.errorhandler(413)
def too_large(_): return jsonify({"error": "Request is too large"}), 413

@app.route("/")
def index(): return render_template("index.html")

@app.get("/api/session")
def api_session():
    username = session.get("username")
    if not username: return jsonify({"logged_in": False})
    with db() as conn: row = conn.execute("SELECT balance_cents FROM balances WHERE username=?", (username,)).fetchone()
    user = DEMO_USERS[username]
    return jsonify({"logged_in": True, "username": username, "name": user["name"], "upi": user["upi"], "balance": money(row["balance_cents"])})

@app.get("/api/csrf")
def csrf():
    session.setdefault("csrf_token", secrets.token_urlsafe(32)); return jsonify({"token": session["csrf_token"]})

@app.post("/api/login")
def login():
    data = body(); username, pin = str(data.get("username", "")).lower().strip(), str(data.get("pin", ""))
    if username not in DEMO_USERS or not secrets.compare_digest(pin, DEMO_USERS[username]["pin"]): return jsonify({"error": "Invalid demo username or PIN"}), 401
    session.clear(); session["username"] = username; session["csrf_token"] = secrets.token_urlsafe(32); get_or_create_keypair(username)
    return api_session()

@app.post("/api/logout")
def logout(): session.clear(); return jsonify({"ok": True})

@app.before_request
def protect_mutations():
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.endpoint not in {"login", "logout", "csrf"} and "username" in session:
        if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.get("csrf_token", "")): return jsonify({"error": "Invalid CSRF token"}), 403

@app.get("/api/users")
@login_required
def users():
    me = session["username"]; return jsonify([{ "username": u, "name": v["name"], "upi": v["upi"] } for u, v in DEMO_USERS.items() if u != me])

@app.get("/api/customers")
@login_required
def customers():
    with db() as conn: rows = conn.execute("SELECT * FROM customers WHERE owner=? ORDER BY created_at DESC", (session["username"],)).fetchall()
    return jsonify([dict(r) for r in rows])

@app.post("/api/customers")
@login_required
def create_customer():
    data = body(); name = str(data.get("name", "")).strip()[:120]
    if not name: return jsonify({"error": "Customer name is required"}), 400
    customer = {"customer_id": "CUS-" + secrets.token_hex(6).upper(), "owner": session["username"], "name": name, "email": str(data.get("email", "")).strip()[:160], "phone": str(data.get("phone", "")).strip()[:30], "upi_id": str(data.get("upi_id", "")).strip()[:80], "created_at": datetime.now(timezone.utc).isoformat()}
    with db() as conn: conn.execute("INSERT INTO customers VALUES (?,?,?,?,?,?,?)", tuple(customer.values()))
    return jsonify(customer), 201

@app.get("/api/invoices")
@login_required
def invoices():
    with db() as conn:
        rows = conn.execute("SELECT i.*, c.name AS customer_name, c.email AS customer_email FROM invoices i JOIN customers c ON c.customer_id=i.customer_id WHERE i.owner=? ORDER BY i.created_at DESC", (session["username"],)).fetchall()
    return jsonify([dict(r) | {"amount": money(r["amount_cents"]), "payment_url": f"/pay/{r['payment_token']}"} for r in rows])

@app.post("/api/invoices")
@login_required
def create_invoice():
    data = body(); customer_id = str(data.get("customer_id", "")); description = str(data.get("description", "")).strip()[:240]; due_date = str(data.get("due_date", "")).strip()
    try: amount_cents, amount = parse_amount(data.get("amount"))
    except ValueError as exc: return jsonify({"error": str(exc)}), 400
    with db() as conn:
        customer = conn.execute("SELECT customer_id FROM customers WHERE customer_id=? AND owner=?", (customer_id, session["username"])).fetchone()
        if not customer: return jsonify({"error": "Select a customer owned by this business"}), 400
        invoice_id = "INV-" + secrets.token_hex(7).upper(); number = "BILL-" + datetime.now(timezone.utc).strftime("%Y%m%d") + "-" + secrets.token_hex(3).upper(); token = secrets.token_urlsafe(18); created = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT INTO invoices VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (invoice_id, session["username"], customer_id, number, amount_cents, "INR", description, due_date, "OPEN", token, created, ""))
    return jsonify({"invoice_id": invoice_id, "invoice_number": number, "amount": amount, "currency": "INR", "status": "OPEN", "payment_url": f"/pay/{token}"}), 201

@app.post("/api/invoices/<invoice_id>/status")
@login_required
def update_invoice_status(invoice_id):
    status = str(body().get("status", "")).upper()
    if status not in {"PAID", "CANCELLED"}: return jsonify({"error": "Status must be PAID or CANCELLED"}), 400
    now = datetime.now(timezone.utc).isoformat() if status == "PAID" else ""
    with db() as conn:
        row = conn.execute("SELECT invoice_id FROM invoices WHERE invoice_id=? AND owner=?", (invoice_id, session["username"])).fetchone()
        if not row: return jsonify({"error": "Invoice not found"}), 404
        conn.execute("UPDATE invoices SET status=?, paid_at=? WHERE invoice_id=?", (status, now, invoice_id)); conn.execute("INSERT INTO transaction_events(transaction_id,event_type,event_data,created_at) VALUES (?,?,?,?)", (invoice_id, "INVOICE_" + status, json.dumps({"invoice_id": invoice_id}), now or datetime.now(timezone.utc).isoformat()))
    return jsonify({"invoice_id": invoice_id, "status": status, "paid_at": now})

@app.get("/api/payment-links/<token>")
def public_payment_link(token):
    with db() as conn: row = conn.execute("SELECT i.invoice_number, i.amount_cents, i.currency, i.description, i.due_date, i.status, c.name AS customer_name FROM invoices i JOIN customers c ON c.customer_id=i.customer_id WHERE i.payment_token=?", (token,)).fetchone()
    if not row: return jsonify({"error": "Payment link not found"}), 404
    return jsonify(dict(row) | {"amount": money(row["amount_cents"]), "payment_url": f"/pay/{token}"})

@app.get("/pay/<token>")
def payment_page(token):
    with db() as conn:
        row = conn.execute("SELECT i.invoice_number, i.amount_cents, i.currency, i.description, i.due_date, i.status, c.name AS customer_name FROM invoices i JOIN customers c ON c.customer_id=i.customer_id WHERE i.payment_token=?", (token,)).fetchone()
    if not row:
        return render_template("payment_not_found.html"), 404
    return render_template("payment.html", invoice=dict(row) | {"amount": money(row["amount_cents"])})

@app.get("/api/wallet")
@login_required
def wallet_status():
    with db() as conn:
        row = conn.execute("SELECT address, chain_id, network, provider, verified_at, last_seen_at FROM wallets WHERE username=?", (session["username"],)).fetchone()
    if not row:
        return jsonify({"connected": False, "supported": Account is not None})
    return jsonify({"connected": True, **dict(row), "address": row["address"]})

@app.post("/api/wallet/demo")
@login_required
def wallet_demo_connect():
    address = "0x" + hashlib.sha256(("blockpay-demo:" + session["username"]).encode()).hexdigest()[:40]
    now = datetime.now(timezone.utc).isoformat()
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO wallets VALUES (?,?,?,?,?,?,?)", (session["username"], address, 31337, "BlockPay Demo EVM", "Demo Wallet", now, now))
    return jsonify({"connected": True, "address": address, "chain_id": 31337, "network": "BlockPay Demo EVM", "provider": "Demo Wallet", "verified_at": now})

@app.post("/api/wallet/challenge")
@login_required
def wallet_challenge():
    if Account is None:
        return jsonify({"error": "Ethereum wallet verification is unavailable until eth-account is installed"}), 503
    data = body(); address = str(data.get("address", "")).lower().strip(); chain_id = int(data.get("chain_id", 0) or 0)
    if not address.startswith("0x") or len(address) != 42 or chain_id <= 0:
        return jsonify({"error": "Valid EVM address and chain ID are required"}), 400
    nonce = secrets.token_urlsafe(24)
    message = f"UPI Chain wallet verification\nUser: {session['username']}\nAddress: {address}\nChain ID: {chain_id}\nNonce: {nonce}"
    expires = (datetime.now(timezone.utc).timestamp() + 300)
    with db() as conn:
        conn.execute("INSERT OR REPLACE INTO wallet_challenges VALUES (?,?,?,?)", (session["username"], nonce, message, str(expires)))
    return jsonify({"message": message, "expires_in": 300})

@app.post("/api/wallet/connect")
@login_required
def wallet_connect():
    if Account is None:
        return jsonify({"error": "Ethereum wallet verification is unavailable until eth-account is installed"}), 503
    data = body(); address = str(data.get("address", "")).lower().strip(); signature = str(data.get("signature", "")); chain_id = int(data.get("chain_id", 0) or 0); network = str(data.get("network", "EVM"))[:40]
    if not address.startswith("0x") or len(address) != 42 or chain_id <= 0:
        return jsonify({"error": "Valid EVM address and chain ID are required"}), 400
    with db() as conn:
        challenge = conn.execute("SELECT nonce, message, expires_at FROM wallet_challenges WHERE username=?", (session["username"],)).fetchone()
        if not challenge or float(challenge["expires_at"]) < datetime.now(timezone.utc).timestamp(): return jsonify({"error": "Wallet challenge expired; request a new one"}), 400
        if f"Address: {address}" not in challenge["message"] or f"Chain ID: {chain_id}" not in challenge["message"]: return jsonify({"error": "Wallet does not match the active challenge"}), 400
        try:
            recovered = Account.recover_message(encode_defunct(text=challenge["message"]), signature=signature).lower()
        except Exception: return jsonify({"error": "Invalid wallet signature"}), 400
        if recovered != address: return jsonify({"error": "Signature does not match wallet address"}), 400
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("INSERT OR REPLACE INTO wallets VALUES (?,?,?,?,?,?,?)", (session["username"], address, chain_id, network, "MetaMask", now, now)); conn.execute("DELETE FROM wallet_challenges WHERE username=?", (session["username"],))
    return jsonify({"connected": True, "address": address, "chain_id": chain_id, "network": network, "provider": "MetaMask", "verified_at": now})

@app.delete("/api/wallet")
@login_required
def wallet_disconnect():
    with db() as conn: conn.execute("DELETE FROM wallets WHERE username=?", (session["username"],))
    return jsonify({"connected": False})

@app.get("/api/transactions/<transaction_id>")
@login_required
def transaction_detail(transaction_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM blocks WHERE json_extract(transaction_json, '$.transaction_id')=?", (transaction_id,)).fetchone()
        events = conn.execute("SELECT event_type, event_data, created_at FROM transaction_events WHERE transaction_id=? ORDER BY event_id", (transaction_id,)).fetchall()
    if not row: return jsonify({"error": "Transaction not found"}), 404
    tx = json.loads(row["transaction_json"])
    if DEMO_USERS[session["username"]]["upi"] not in {tx["sender"], tx["receiver"]}: return jsonify({"error": "Transaction not found"}), 404
    return jsonify({"transaction": tx, "block": {"index": row["block_index"], "hash": row["block_hash"], "previous_hash": row["previous_hash"], "timestamp": row["timestamp"], "validators": json.loads(row["validators_json"])}, "events": [dict(e) for e in events]})

@app.post("/api/pay")
@login_required
def pay():
    sender, data = session["username"], body(); receiver = str(data.get("receiver", "")).lower().strip(); note = str(data.get("note", "")).strip()[:80]
    if receiver not in DEMO_USERS or receiver == sender: return jsonify({"error": "Choose a valid recipient"}), 400
    try: amount_cents, amount = parse_amount(data.get("amount"))
    except ValueError as exc: return jsonify({"error": str(exc)}), 400
    idem = request.headers.get("Idempotency-Key", "").strip()
    if not idem or len(idem) > 100: return jsonify({"error": "An Idempotency-Key header is required"}), 400
    private, public = get_or_create_keypair(sender)
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        prior = conn.execute("SELECT response_json FROM payment_requests WHERE idempotency_key=? AND sender=?", (idem, sender)).fetchone()
        if prior: conn.execute("COMMIT"); return jsonify(json.loads(prior["response_json"]))
        valid, message = verify_chain(conn)
        if not valid: conn.execute("ROLLBACK"); return jsonify({"error": "Ledger integrity check failed: " + message}), 409
        balance = conn.execute("SELECT balance_cents FROM balances WHERE username=?", (sender,)).fetchone()["balance_cents"]
        if balance < amount_cents: conn.execute("ROLLBACK"); return jsonify({"error": "Insufficient demo balance"}), 400
        tx = {"transaction_id": "UPI-" + secrets.token_hex(8).upper(), "sender": DEMO_USERS[sender]["upi"], "receiver": DEMO_USERS[receiver]["upi"], "amount": amount, "currency": "INR", "note": note, "status": "SUCCESS", "network": "LOCAL-SANDBOX"}
        block = add_transaction_block(conn, tx, private, public)
        conn.execute("UPDATE balances SET balance_cents=balance_cents-? WHERE username=?", (amount_cents, sender)); conn.execute("UPDATE balances SET balance_cents=balance_cents+? WHERE username=?", (amount_cents, receiver))
        conn.execute("INSERT INTO transaction_events(transaction_id,event_type,event_data,created_at) VALUES (?,?,?,?)", (tx["transaction_id"], "SETTLED", json.dumps({"block_index": block["index"], "network": tx["network"]}), datetime.now(timezone.utc).isoformat()))
        response = {"message": "Sandbox payment recorded on blockchain", "block": block}
        conn.execute("INSERT INTO payment_requests VALUES (?,?,?,?,?)", (idem, tx["transaction_id"], sender, json.dumps(response), datetime.now(timezone.utc).isoformat())); conn.execute("COMMIT")
        return jsonify(response), 201

@app.get("/api/transactions")
@login_required
def transactions():
    my_upi = DEMO_USERS[session["username"]]["upi"]
    with db() as conn: rows = conn.execute("SELECT * FROM blocks ORDER BY block_index DESC LIMIT 100").fetchall()
    result = []
    for row in rows:
        tx = json.loads(row["transaction_json"])
        if tx["sender"] == my_upi or tx["receiver"] == my_upi: result.append({"index": row["block_index"], "timestamp": row["timestamp"], "hash": row["block_hash"], "previous_hash": row["previous_hash"], "transaction": tx, "validators": json.loads(row["validators_json"])})
    return jsonify(result)

@app.get("/api/chain")
@login_required
def chain():
    result = []
    for block in get_chain():
        block["transaction"] = json.loads(block.pop("transaction_json")); block["validators"] = json.loads(block.pop("validators_json")); block.pop("public_key", None); block.pop("signature", None); result.append(block)
    return jsonify(result)

@app.get("/api/verify")
@login_required
def verify():
    valid, message = verify_chain(); return jsonify({"valid": valid, "message": message, "block_count": len(get_chain())})

@app.get("/api/analytics")
@login_required
def analytics():
    chain_data = get_chain(); total = sum(Decimal(str(json.loads(b["transaction_json"])["amount"])) for b in chain_data)
    with db() as conn:
        inv = conn.execute("SELECT COUNT(*) AS count, COALESCE(SUM(CASE WHEN status='OPEN' THEN amount_cents ELSE 0 END),0) AS outstanding FROM invoices WHERE owner=?", (session["username"],)).fetchone()
    return jsonify({"transactions": len(chain_data), "total_volume": float(total), "verified": verify_chain()[0], "invoice_count": inv["count"], "outstanding_invoices": money(inv["outstanding"])})

@app.post("/api/demo/tamper")
@login_required
def tamper():
    with db() as conn:
        row = conn.execute("SELECT block_index, transaction_json FROM blocks ORDER BY block_index LIMIT 1").fetchone()
        if not row: return jsonify({"error": "Make a payment first to create a block"}), 400
        tx = json.loads(row["transaction_json"]); tx["amount"] = float(tx["amount"]) + 1; conn.execute("UPDATE blocks SET transaction_json=? WHERE block_index=?", (json.dumps(tx, sort_keys=True), row["block_index"]))
    return jsonify({"message": "Demo tamper applied. Run verification to see the failure."})

@app.post("/api/demo/reset")
@login_required
def reset_demo():
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE"); conn.execute("DELETE FROM blocks"); conn.execute("DELETE FROM payment_requests"); conn.execute("DELETE FROM transaction_events")
        for user in DEMO_USERS: conn.execute("UPDATE balances SET balance_cents=500000 WHERE username=?", (user,))
        conn.execute("COMMIT")
    return jsonify({"message": "Demo ledger and balances reset"})

init_db()
if __name__ == "__main__": app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
