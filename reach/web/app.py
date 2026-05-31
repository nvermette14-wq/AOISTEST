# reach/web/app.py — v1.0.0
# Interface web entrepôt — pick, confirm, ship, pack.
# Usage: python reach/web/app.py
# Accès: http://localhost:5000

import sys
import sqlite3
import uuid
import json
import requests
from pathlib import Path
from datetime import datetime
from flask import Flask, render_template, request, jsonify, redirect, url_for, make_response

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

# SAP config
SAP_URL      = os.getenv("SAP_SERVICE_LAYER_URL", "").rstrip("/")
SAP_COMPANY  = os.getenv("SAP_COMPANY_DB", "")
SAP_USER     = os.getenv("SAP_USERNAME", "")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "")

SAP_DB_PATH    = str(Path(__file__).parent.parent / "data" / "sap.db")
STATE_DB_PATH  = str(Path(__file__).parent.parent / "data" / "pick_state.db")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change-me-in-production")

USE_MOCK = not all([SAP_URL, SAP_COMPANY, SAP_USER, SAP_PASSWORD])

# ── State database ────────────────────────────────────────────────────────────

def init_state_db():
    Path(STATE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id          TEXT PRIMARY KEY,
            warehouse   TEXT NOT NULL,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at TEXT,
            status      TEXT DEFAULT 'active'
        );
        CREATE TABLE IF NOT EXISTS confirmations (
            session_id  TEXT NOT NULL,
            doc_num     INTEGER NOT NULL,
            item_code   TEXT NOT NULL,
            qty_req     REAL,
            qty_picked  REAL DEFAULT 0,
            confirmed   INTEGER DEFAULT 0,
            confirmed_at TEXT,
            PRIMARY KEY (session_id, doc_num, item_code)
        );
        CREATE TABLE IF NOT EXISTS delivery_notes (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            card_code   TEXT,
            client_name TEXT,
            warehouse   TEXT,
            sap_doc_num INTEGER,
            status      TEXT DEFAULT 'pending',
            error       TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS staged_orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            warehouse   TEXT,
            doc_num     INTEGER,
            card_code   TEXT,
            client_name TEXT,
            staged_at   TEXT DEFAULT CURRENT_TIMESTAMP,
            released    INTEGER DEFAULT 0,
            released_at TEXT
        );
        CREATE TABLE IF NOT EXISTS staged_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            staged_order_id INTEGER,
            item_code   TEXT,
            item_name   TEXT,
            qty_staged  REAL,
            FOREIGN KEY (staged_order_id) REFERENCES staged_orders(id)
        );
        CREATE TABLE IF NOT EXISTS flags (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id  TEXT,
            warehouse   TEXT,
            doc_num     INTEGER,
            item_code   TEXT,
            item_name   TEXT,
            bin_loc     TEXT,
            reason      TEXT,
            note        TEXT,
            resolved    INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    """)
    conn.commit()
    conn.close()


def get_state_conn():
    conn = sqlite3.connect(STATE_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── SAP helpers ───────────────────────────────────────────────────────────────

def sap_login():
    session = requests.Session()
    session.verify = False
    resp = session.post(f"{SAP_URL}/Login", json={
        "CompanyDB": SAP_COMPANY, "UserName": SAP_USER, "Password": SAP_PASSWORD,
    }, timeout=30)
    resp.raise_for_status()
    return session


MOCK_WAREHOUSES = [
    {"WarehouseCode": "WH01", "WarehouseName": "Montreal — Principal"},
    {"WarehouseCode": "WH02", "WarehouseName": "Laval"},
    {"WarehouseCode": "WH03", "WarehouseName": "Quebec"},
    {"WarehouseCode": "WH04", "WarehouseName": "Sherbrooke"},
    {"WarehouseCode": "WH05", "WarehouseName": "Drummondville"},
]

MOCK_ORDERS = [
    {"doc_num": 1001, "doc_entry": 1001, "client": "Construction Lafleur Inc.",
     "card_code": "C00001", "doc_date": "2026-05-31", "warehouse": "WH01",
     "lines": [
        {"item_code": "BOLT-375-SS", "item_name": "Boulon inox 3/8-16 x 1\"",   "qty": 200, "bin_loc": "A-12-3", "line_num": 0},
        {"item_code": "WASH-375",    "item_name": "Rondelle plate 3/8\"",         "qty": 200, "bin_loc": "A-12-5", "line_num": 1},
        {"item_code": "GANT-CUT5-M","item_name": "Gants résistants coupures M",  "qty": 2,   "bin_loc": "C-02-1", "line_num": 2},
     ]},
    {"doc_num": 1002, "doc_entry": 1002, "client": "Manufacture Gagnon Ltée",
     "card_code": "C00002", "doc_date": "2026-05-31", "warehouse": "WH01",
     "lines": [
        {"item_code": "MARTEAU-16", "item_name": "Marteau 16oz manche bois",    "qty": 5, "bin_loc": "B-05-2", "line_num": 0},
        {"item_code": "RUBAN-25",   "item_name": "Ruban à mesurer 25pi",         "qty": 3, "bin_loc": "",       "line_num": 1},
     ]},
    {"doc_num": 1004, "doc_entry": 1004, "client": "Construction Lafleur Inc.",
     "card_code": "C00001", "doc_date": "2026-05-31", "warehouse": "WH01",
     "lines": [
        {"item_code": "LUNE-SECU", "item_name": "Lunettes de sécurité claires", "qty": 10, "bin_loc": "C-02-3", "line_num": 0},
     ]},
]


def get_warehouses():
    if USE_MOCK:
        return MOCK_WAREHOUSES
    s = sap_login()
    resp = s.get(f"{SAP_URL}/Warehouses?$select=WarehouseCode,WarehouseName&$top=100", timeout=30)
    return resp.json().get("value", [])


def get_orders_for_warehouse(warehouse_code: str) -> list:
    if USE_MOCK:
        return [o for o in MOCK_ORDERS if o["warehouse"].upper() == warehouse_code.upper()]
    s = sap_login()
    select = "DocNum,DocEntry,CardCode,CardName,DocDate,DocumentLines"
    resp = s.get(
        f"{SAP_URL}/Orders?$select={select}&$filter=DocumentStatus eq 'bost_Open'&$top=500",
        timeout=60
    )
    raw = resp.json().get("value", [])
    orders = []
    for o in raw:
        lines = []
        for i, l in enumerate(o.get("DocumentLines", [])):
            if l.get("WarehouseCode", "").upper() != warehouse_code.upper():
                continue
            lines.append({
                "item_code": l.get("ItemCode", ""),
                "item_name": l.get("ItemDescription", ""),
                "qty":       float(l.get("Quantity", 0) or 0),
                "bin_loc":   str(l.get("BinEntry") or l.get("WarehouseCode") or "").strip(),
                "line_num":  i,
            })
        if lines:
            orders.append({
                "doc_num":   o.get("DocNum"),
                "doc_entry": o.get("DocEntry"),
                "client":    o.get("CardName", ""),
                "card_code": o.get("CardCode", ""),
                "doc_date":  (o.get("DocDate") or "")[:10],
                "warehouse": warehouse_code,
                "lines":     lines,
            })
    return orders


def load_complete_delivery_flags() -> set:
    """Returns set of card_codes requiring complete delivery before shipping."""
    if not Path(SAP_DB_PATH).exists():
        return set()
    try:
        conn = sqlite3.connect(SAP_DB_PATH)
        rows = conn.execute(
            "SELECT card_code FROM customers WHERE complete_delivery = 1"
        ).fetchall()
        conn.close()
        return {r[0] for r in rows}
    except Exception:
        return set()


def get_stock(item_codes: list) -> dict:
    """Returns {item_code: available_qty} from local sap.db."""
    if not Path(SAP_DB_PATH).exists() or not item_codes:
        return {}
    try:
        conn = sqlite3.connect(SAP_DB_PATH)
        placeholders = ",".join("?" for _ in item_codes)
        rows = conn.execute(
            f"SELECT item_code, available FROM items WHERE item_code IN ({placeholders})",
            item_codes
        ).fetchall()
        conn.close()
        return {r[0]: r[1] for r in rows}
    except Exception:
        return {}


def toggle_complete_delivery(card_code: str) -> bool:
    """Toggles complete_delivery flag. Returns new value."""
    if not Path(SAP_DB_PATH).exists():
        return False
    conn = sqlite3.connect(SAP_DB_PATH)
    current = conn.execute(
        "SELECT complete_delivery FROM customers WHERE card_code=?", (card_code,)
    ).fetchone()
    new_val = 0 if (current and current[0]) else 1
    conn.execute(
        "UPDATE customers SET complete_delivery=? WHERE card_code=?", (new_val, card_code)
    )
    conn.commit()
    conn.close()
    return bool(new_val)


def create_sap_delivery(card_code: str, warehouse: str, lines: list) -> int | None:
    """POST /DeliveryNotes to SAP B1. Returns doc number or None on error."""
    if USE_MOCK:
        # Simulate SAP — return a fake doc number
        return int(datetime.now().strftime("%H%M%S"))
    try:
        s = sap_login()
        body = {
            "CardCode": card_code,
            "DocDate":  datetime.now().strftime("%Y-%m-%dT00:00:00"),
            "DocumentLines": [
                {
                    "ItemCode":      l["item_code"],
                    "Quantity":      l["qty_picked"],
                    "WarehouseCode": warehouse,
                    "BaseType":      17,
                    "BaseEntry":     l["doc_entry"],
                    "BaseLine":      l["line_num"],
                }
                for l in lines if l["qty_picked"] > 0
            ]
        }
        resp = s.post(f"{SAP_URL}/DeliveryNotes", json=body, timeout=30)
        resp.raise_for_status()
        return resp.json().get("DocNum")
    except Exception as e:
        return None


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    warehouse = request.cookies.get("wh")
    if warehouse:
        return redirect(url_for("pick", warehouse=warehouse))
    warehouses = get_warehouses()
    return render_template("index.html", warehouses=warehouses, mock=USE_MOCK)


@app.route("/set-warehouse", methods=["POST"])
def set_warehouse():
    warehouse = request.form.get("warehouse")
    resp = make_response(redirect(url_for("pick", warehouse=warehouse)))
    resp.set_cookie("wh", warehouse, max_age=60*60*24*365)
    return resp


@app.route("/change-warehouse")
def change_warehouse():
    resp = make_response(redirect(url_for("index")))
    resp.delete_cookie("wh")
    return resp


@app.route("/pick/<warehouse>")
def pick(warehouse):
    orders = get_orders_for_warehouse(warehouse)
    wh_name = next((w["WarehouseName"] for w in get_warehouses()
                    if w["WarehouseCode"].upper() == warehouse.upper()), warehouse)

    # Create a pick session
    session_id = str(uuid.uuid4())[:8]
    conn = get_state_conn()
    conn.execute("INSERT INTO sessions (id, warehouse) VALUES (?,?)", (session_id, warehouse))

    # Pre-load confirmations
    for order in orders:
        for line in order["lines"]:
            conn.execute("""
                INSERT OR IGNORE INTO confirmations
                (session_id, doc_num, item_code, qty_req, qty_picked, confirmed)
                VALUES (?,?,?,?,0,0)
            """, (session_id, order["doc_num"], line["item_code"], line["qty"]))

    conn.commit()
    conn.close()

    import re
    def loc_key(loc):
        if not loc: return ("ZZZ", 9999, 9999)
        parts = re.split(r"[-/. ]+", loc.strip().upper())
        z = parts[0] if parts else "ZZZ"
        try: a = int(parts[1]) if len(parts) > 1 else 9999
        except: a = 9999
        try: s = int(parts[2]) if len(parts) > 2 else 9999
        except: s = 9999
        return (z, a, s)

    # Load complete-delivery flags and stock for filtering
    complete_delivery_clients = load_complete_delivery_flags()
    all_item_codes = list({l["item_code"] for o in orders for l in o["lines"]})
    stock = get_stock(all_item_codes)

    active_orders  = []
    waiting_orders = []  # complete-delivery orders with missing stock

    for order in orders:
        if order["card_code"] in complete_delivery_clients:
            missing = [l for l in order["lines"]
                       if stock.get(l["item_code"], 999) < l["qty"]]
            if missing:
                order["missing_items"] = missing
                waiting_orders.append(order)
                continue
        active_orders.append(order)

    # Sort lines by location within each order — keep orders intact
    for order in active_orders:
        order["lines"].sort(key=lambda l: loc_key(l["bin_loc"]))

    active_orders.sort(key=lambda o: loc_key(o["lines"][0]["bin_loc"] if o["lines"] else ""))

    total_items = sum(len(o["lines"]) for o in active_orders)

    return render_template("pick.html", orders=active_orders,
                           waiting_orders=waiting_orders,
                           session_id=session_id,
                           warehouse=warehouse, wh_name=wh_name,
                           total_items=total_items, mock=USE_MOCK)


@app.route("/api/confirm", methods=["POST"])
def confirm_item():
    data       = request.json
    session_id = data.get("session_id")
    doc_num    = data.get("doc_num")
    item_code  = data.get("item_code")
    qty_picked = float(data.get("qty_picked", 0))

    conn = get_state_conn()
    conn.execute("""
        UPDATE confirmations
        SET qty_picked=?, confirmed=1, confirmed_at=CURRENT_TIMESTAMP
        WHERE session_id=? AND doc_num=? AND item_code=?
    """, (qty_picked, session_id, doc_num, item_code))
    conn.commit()

    total   = conn.execute("SELECT COUNT(*) FROM confirmations WHERE session_id=?", (session_id,)).fetchone()[0]
    done    = conn.execute("SELECT COUNT(*) FROM confirmations WHERE session_id=? AND confirmed=1", (session_id,)).fetchone()[0]
    conn.close()

    # Count flagged items (excluded from pick)
    flagged = conn.execute(
        "SELECT COUNT(*) FROM flags WHERE session_id=? AND doc_num=? AND item_code=?",
        (session_id, doc_num, item_code)
    ).fetchone()[0]

    return jsonify({"ok": True, "done": done, "total": total, "flagged": flagged > 0})


@app.route("/api/flag", methods=["POST"])
def flag_item():
    data       = request.json
    session_id = data.get("session_id")
    warehouse  = data.get("warehouse")
    doc_num    = data.get("doc_num")
    item_code  = data.get("item_code")
    item_name  = data.get("item_name", "")
    bin_loc    = data.get("bin_loc", "")
    reason     = data.get("reason", "")
    note       = data.get("note", "")

    conn = get_state_conn()
    conn.execute("""
        INSERT OR REPLACE INTO flags
        (session_id, warehouse, doc_num, item_code, item_name, bin_loc, reason, note)
        VALUES (?,?,?,?,?,?,?,?)
    """, (session_id, warehouse, doc_num, item_code, item_name, bin_loc, reason, note))

    # Mark as confirmed with qty 0 so it doesn't block session close
    conn.execute("""
        UPDATE confirmations SET confirmed=1, qty_picked=0
        WHERE session_id=? AND doc_num=? AND item_code=?
    """, (session_id, doc_num, item_code))

    total  = conn.execute("SELECT COUNT(*) FROM confirmations WHERE session_id=?", (session_id,)).fetchone()[0]
    done   = conn.execute("SELECT COUNT(*) FROM confirmations WHERE session_id=? AND confirmed=1", (session_id,)).fetchone()[0]
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "done": done, "total": total})


@app.route("/supervisor")
def supervisor():
    conn   = get_state_conn()
    flags  = [dict(r) for r in conn.execute(
        "SELECT * FROM flags WHERE resolved=0 ORDER BY created_at DESC"
    ).fetchall()]
    staged = [dict(r) for r in conn.execute("""
        SELECT so.*, GROUP_CONCAT(si.item_code || ' x' || CAST(si.qty_staged AS INTEGER), ', ') as items_summary
        FROM staged_orders so
        LEFT JOIN staged_items si ON si.staged_order_id = so.id
        WHERE so.released = 0
        GROUP BY so.id
        ORDER BY so.staged_at DESC
    """).fetchall()]
    conn.close()

    # Load customers with complete_delivery flag for management
    customers = []
    if Path(SAP_DB_PATH).exists():
        try:
            sap = sqlite3.connect(SAP_DB_PATH)
            sap.row_factory = sqlite3.Row
            customers = [dict(r) for r in sap.execute(
                "SELECT card_code, card_name, complete_delivery FROM customers ORDER BY card_name LIMIT 100"
            ).fetchall()]
            sap.close()
        except Exception:
            pass

    return render_template("supervisor.html", flags=flags, staged=staged, customers=customers)


@app.route("/api/flag/resolve", methods=["POST"])
def resolve_flag():
    flag_id = request.json.get("flag_id")
    conn    = get_state_conn()
    conn.execute("UPDATE flags SET resolved=1 WHERE id=?", (flag_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/complete", methods=["POST"])
def complete_session():
    data       = request.json
    session_id = data.get("session_id")
    warehouse  = data.get("warehouse")

    conn      = get_state_conn()
    confs     = conn.execute(
        "SELECT * FROM confirmations WHERE session_id=? AND confirmed=1", (session_id,)
    ).fetchall()

    # Group by client (card_code) — need to join with order info
    orders    = get_orders_for_warehouse(warehouse)
    order_map = {o["doc_num"]: o for o in orders}

    # Group confirmed lines by client
    from collections import defaultdict
    by_client = defaultdict(list)
    for c in confs:
        order = order_map.get(c["doc_num"], {})
        by_client[order.get("card_code", "UNKNOWN")].append({
            "item_code":  c["item_code"],
            "qty_picked": c["qty_picked"],
            "doc_entry":  order.get("doc_entry", c["doc_num"]),
            "line_num":   next((l["line_num"] for l in order.get("lines", [])
                                if l["item_code"] == c["item_code"]), 0),
        })

    results = []
    for card_code, lines in by_client.items():
        client_name = next((o["client"] for o in orders if o["card_code"] == card_code), card_code)
        doc_num = create_sap_delivery(card_code, warehouse, lines)
        status  = "created" if doc_num else "error"
        conn.execute("""
            INSERT INTO delivery_notes (session_id, card_code, client_name, warehouse, sap_doc_num, status)
            VALUES (?,?,?,?,?,?)
        """, (session_id, card_code, client_name, warehouse, doc_num, status))
        results.append({"client": client_name, "doc_num": doc_num, "status": status})

    conn.execute("UPDATE sessions SET status='completed', completed_at=CURRENT_TIMESTAMP WHERE id=?",
                 (session_id,))
    conn.commit()
    conn.close()

    return jsonify({"ok": True, "deliveries": results})


@app.route("/pack")
@app.route("/pack/<warehouse>")
def pack(warehouse=None):
    warehouses = get_warehouses()
    conn       = get_state_conn()

    query = """
        SELECT d.*, s.warehouse FROM delivery_notes d
        JOIN sessions s ON s.id = d.session_id
        WHERE d.status = 'created'
    """
    params = []
    if warehouse:
        query += " AND s.warehouse = ?"
        params.append(warehouse)

    query += " ORDER BY d.created_at DESC LIMIT 50"
    notes = [dict(r) for r in conn.execute(query, params).fetchall()]
    conn.close()

    return render_template("pack.html", notes=notes, warehouses=warehouses,
                           active_warehouse=warehouse, mock=USE_MOCK)


@app.route("/api/stage", methods=["POST"])
def stage_order():
    """Move confirmed items to staging zone without creating SAP delivery."""
    data       = request.json
    session_id = data.get("session_id")
    warehouse  = data.get("warehouse")

    conn   = get_state_conn()
    confs  = conn.execute(
        "SELECT * FROM confirmations WHERE session_id=? AND confirmed=1 AND qty_picked>0",
        (session_id,)
    ).fetchall()

    orders = get_orders_for_warehouse(warehouse)
    order_map = {o["doc_num"]: o for o in orders}

    from collections import defaultdict
    by_order = defaultdict(list)
    for c in confs:
        by_order[c["doc_num"]].append(c)

    staged = []
    for doc_num, items in by_order.items():
        order = order_map.get(doc_num, {})
        client_name = order.get("client", str(doc_num))
        card_code   = order.get("card_code", "")
        s_id = conn.execute("""
            INSERT INTO staged_orders (session_id, warehouse, doc_num, card_code, client_name)
            VALUES (?,?,?,?,?)
        """, (session_id, warehouse, doc_num, card_code, client_name)).lastrowid
        for item in items:
            order_lines = order.get("lines", [])
            item_name = next((l["item_name"] for l in order_lines
                              if l["item_code"] == item["item_code"]), item["item_code"])
            conn.execute("""
                INSERT INTO staged_items (staged_order_id, item_code, item_name, qty_staged)
                VALUES (?,?,?,?)
            """, (s_id, item["item_code"], item_name, item["qty_picked"]))
        staged.append({"client": client_name, "doc_num": doc_num, "items": len(items)})

    conn.execute("UPDATE sessions SET status='staged', completed_at=CURRENT_TIMESTAMP WHERE id=?",
                 (session_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "staged": staged})


@app.route("/api/supervisor/toggle-complete-delivery", methods=["POST"])
def toggle_complete_delivery_route():
    card_code = request.json.get("card_code")
    new_val   = toggle_complete_delivery(card_code)
    return jsonify({"ok": True, "complete_delivery": new_val})


@app.route("/api/pack/done", methods=["POST"])
def pack_done():
    note_id = request.json.get("note_id")
    conn    = get_state_conn()
    conn.execute("UPDATE delivery_notes SET status='packed' WHERE id=?", (note_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


# ── Run ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_state_db()
    mode = "MOCK" if USE_MOCK else "SAP REEL"
    print(f"[WMS] Demarrage en mode {mode}")
    print(f"[WMS] Interface: http://localhost:5000")
    app.run(host="0.0.0.0", port=5000, debug=True)
