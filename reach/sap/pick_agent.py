# reach/sap/pick_agent.py — v2.0.0
# Génère une liste de prélèvement optimisée depuis SAP B1.
#   - Filtre par succursale et stock disponible
#   - Regroupe les commandes du même client (moins d'emballage)
#   - Trie par proximité physique (zone → allée → position)
#
# Usage: python reach/sap/pick_agent.py --warehouse WH01
#        python reach/sap/pick_agent.py --warehouse WH01 --mock
#        python reach/sap/pick_agent.py --list-warehouses [--mock]

import sys
import io
import re
import argparse
import sqlite3
import requests
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

SAP_URL      = os.getenv("SAP_SERVICE_LAYER_URL", "").rstrip("/")
SAP_COMPANY  = os.getenv("SAP_COMPANY_DB", "")
SAP_USER     = os.getenv("SAP_USERNAME", "")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "")
SAP_DB_PATH  = str(Path(__file__).parent.parent / "data" / "sap.db")


# ── SAP Service Layer ─────────────────────────────────────────────────────────

def sap_login() -> requests.Session:
    session = requests.Session()
    session.verify = False
    resp = session.post(f"{SAP_URL}/Login", json={
        "CompanyDB": SAP_COMPANY,
        "UserName":  SAP_USER,
        "Password":  SAP_PASSWORD,
    }, timeout=30)
    resp.raise_for_status()
    return session


def get_open_orders(session: requests.Session, warehouse_code: str) -> list:
    select = "DocNum,CardName,CardCode,DocDate,DocumentLines"
    url = f"{SAP_URL}/Orders?$select={select}&$filter=DocumentStatus eq 'bost_Open'&$top=500"
    resp = session.get(url, timeout=60)
    resp.raise_for_status()
    orders = resp.json().get("value", [])
    return _parse_order_lines(orders, warehouse_code)


def get_warehouses(session: requests.Session) -> list:
    url = f"{SAP_URL}/Warehouses?$select=WarehouseCode,WarehouseName&$top=100"
    resp = session.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json().get("value", [])


def _parse_order_lines(orders: list, warehouse_code: str) -> list:
    lines = []
    for order in orders:
        for line in order.get("DocumentLines", []):
            if line.get("WarehouseCode", "").upper() != warehouse_code.upper():
                continue
            lines.append({
                "doc_num":   order.get("DocNum"),
                "client":    order.get("CardName", ""),
                "card_code": order.get("CardCode", ""),
                "doc_date":  (order.get("DocDate") or "")[:10],
                "item_code": line.get("ItemCode", ""),
                "item_name": line.get("ItemDescription", ""),
                "qty":       float(line.get("Quantity", 0) or 0),
                "bin_loc":   str(line.get("BinEntry") or line.get("WarehouseCode") or "").strip(),
                "warehouse": line.get("WarehouseCode", ""),
            })
    return lines


# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_LINES = [
    {"doc_num": 1001, "client": "Construction Lafleur Inc.", "card_code": "C00001",
     "doc_date": "2026-05-31", "item_code": "BOLT-375-SS", "item_name": "Boulon inox 3/8-16 x 1\"",
     "qty": 200.0, "bin_loc": "A-12-3", "warehouse": "WH01"},
    {"doc_num": 1001, "client": "Construction Lafleur Inc.", "card_code": "C00001",
     "doc_date": "2026-05-31", "item_code": "WASH-375", "item_name": "Rondelle plate 3/8\"",
     "qty": 200.0, "bin_loc": "A-12-5", "warehouse": "WH01"},
    {"doc_num": 1001, "client": "Construction Lafleur Inc.", "card_code": "C00001",
     "doc_date": "2026-05-31", "item_code": "GANT-CUT5-M", "item_name": "Gants résistants coupures niv.5 M",
     "qty": 2.0, "bin_loc": "C-02-1", "warehouse": "WH01"},
    # Deuxieme commande du même client — combinable
    {"doc_num": 1004, "client": "Construction Lafleur Inc.", "card_code": "C00001",
     "doc_date": "2026-05-31", "item_code": "LUNE-SECU", "item_name": "Lunettes de sécurité claires",
     "qty": 10.0, "bin_loc": "C-02-3", "warehouse": "WH01"},
    {"doc_num": 1002, "client": "Manufacture Gagnon Ltée", "card_code": "C00002",
     "doc_date": "2026-05-31", "item_code": "MARTEAU-16", "item_name": "Marteau 16oz manche bois",
     "qty": 5.0, "bin_loc": "B-05-2", "warehouse": "WH01"},
    {"doc_num": 1002, "client": "Manufacture Gagnon Ltée", "card_code": "C00002",
     "doc_date": "2026-05-31", "item_code": "RUBAN-25", "item_name": "Ruban à mesurer 25pi",
     "qty": 3.0, "bin_loc": "", "warehouse": "WH01"},
    # Doublon intentionnel
    {"doc_num": 1001, "client": "Construction Lafleur Inc.", "card_code": "C00001",
     "doc_date": "2026-05-31", "item_code": "BOLT-375-SS", "item_name": "Boulon inox 3/8-16 x 1\"",
     "qty": 200.0, "bin_loc": "A-12-3", "warehouse": "WH01"},
    # WH02 — filtré
    {"doc_num": 1003, "client": "Entretien Tremblay", "card_code": "C00003",
     "doc_date": "2026-05-31", "item_code": "DRILL-HSS-14", "item_name": "Foret HSS 1/4\"",
     "qty": 10.0, "bin_loc": "A-01-1", "warehouse": "WH02"},
]

MOCK_WAREHOUSES = [
    {"WarehouseCode": "WH01", "WarehouseName": "Montreal — Principal"},
    {"WarehouseCode": "WH02", "WarehouseName": "Laval"},
    {"WarehouseCode": "WH03", "WarehouseName": "Quebec"},
    {"WarehouseCode": "WH04", "WarehouseName": "Sherbrooke"},
    {"WarehouseCode": "WH05", "WarehouseName": "Drummondville"},
]


# ── Stock check ───────────────────────────────────────────────────────────────

def load_stock(item_codes: list) -> dict:
    """Returns {item_code: {available, on_order}} from local sap.db."""
    if not Path(SAP_DB_PATH).exists():
        return {}
    try:
        conn = sqlite3.connect(SAP_DB_PATH)
        conn.row_factory = sqlite3.Row
        placeholders = ",".join("?" for _ in item_codes)
        rows = conn.execute(
            f"SELECT item_code, available, on_order FROM items WHERE item_code IN ({placeholders})",
            item_codes
        ).fetchall()
        conn.close()
        return {r["item_code"]: {"available": r["available"], "on_order": r["on_order"]} for r in rows}
    except Exception:
        return {}


def classify_stock(line: dict, stock: dict) -> str:
    """Returns 'PRET', 'PARTIEL', 'EN_CMD', or 'RUPTURE'."""
    info = stock.get(line["item_code"])
    if not info:
        return "PRET"  # no stock data — include in pick list
    avail = info["available"]
    qty   = line["qty"]
    if avail >= qty:
        return "PRET"
    elif avail > 0:
        return "PARTIEL"
    elif info["on_order"] > 0:
        return "EN_CMD"
    else:
        return "RUPTURE"


# ── Deduplication ─────────────────────────────────────────────────────────────

def deduplicate(lines: list) -> tuple[list, int]:
    seen, result, dupes = set(), [], 0
    for line in lines:
        key = (line["doc_num"], line["item_code"])
        if key in seen:
            dupes += 1
            continue
        seen.add(key)
        result.append(line)
    return result, dupes


# ── Location parsing & sort key ───────────────────────────────────────────────

def loc_sort_key(loc: str):
    """Parse 'A-12-3' into sortable tuple (zone, aisle_int, slot_int)."""
    if not loc:
        return ("ZZZ", 9999, 9999)
    parts = re.split(r"[-/. ]+", loc.strip().upper())
    zone  = parts[0] if parts else "ZZZ"
    try:
        aisle = int(parts[1]) if len(parts) > 1 else 9999
    except ValueError:
        aisle = 9999
    try:
        slot  = int(parts[2]) if len(parts) > 2 else 9999
    except ValueError:
        slot  = 9999
    return (zone, aisle, slot)


def loc_aisle_label(loc: str) -> str:
    """'A-12-3' → 'Zone A — Allée 12'"""
    if not loc:
        return "Sans localisation"
    parts = re.split(r"[-/. ]+", loc.strip().upper())
    zone  = parts[0] if parts else "?"
    aisle = parts[1] if len(parts) > 1 else "?"
    return f"Zone {zone} — Allée {aisle}"


# ── Client merge detection ────────────────────────────────────────────────────

def find_multi_order_clients(lines: list) -> dict:
    """Returns {card_code: [doc_nums]} for clients with 2+ orders."""
    client_orders = defaultdict(set)
    for line in lines:
        client_orders[line["card_code"]].add(line["doc_num"])
    return {cc: sorted(docs) for cc, docs in client_orders.items() if len(docs) > 1}


# ── Formatter ─────────────────────────────────────────────────────────────────

STATUS_LABEL = {
    "PRET":    "    ",
    "PARTIEL": "[PARTIEL]",
    "EN_CMD":  "[EN CMD] ",
    "RUPTURE": "[RUPTURE]",
}

def format_pick_list(lines: list, warehouse_code: str, warehouse_name: str,
                     dupes_removed: int, stock: dict) -> str:
    out = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Classify each line
    for line in lines:
        line["_status"] = classify_stock(line, stock)

    ready    = [l for l in lines if l["_status"] in ("PRET", "PARTIEL")]
    waiting  = [l for l in lines if l["_status"] in ("EN_CMD", "RUPTURE")]

    # Client merge opportunities
    multi = find_multi_order_clients(ready)

    out.append("=" * 70)
    out.append(f"  LISTE DE PRELEVEMENT — {warehouse_code} : {warehouse_name}")
    out.append(f"  Generee le : {now}")
    flags = []
    if dupes_removed:
        flags.append(f"{dupes_removed} doublon(s) elimine(s)")
    if waiting:
        flags.append(f"{len(waiting)} ligne(s) en attente de stock")
    if multi:
        flags.append(f"{len(multi)} client(s) avec commandes combinables")
    if flags:
        for f in flags:
            out.append(f"  [!] {f}")
    out.append("=" * 70)

    # ── Section 1 : Route de prélèvement ──────────────────────────────────────
    out.append(f"\n  ROUTE DE PRELEVEMENT ({len(ready)} lignes)")

    ready_sorted = sorted(ready, key=lambda l: loc_sort_key(l["bin_loc"]))

    current_aisle = None
    for line in ready_sorted:
        aisle = loc_aisle_label(line["bin_loc"])
        if aisle != current_aisle:
            current_aisle = aisle
            out.append(f"\n  >> {aisle}")
            out.append(f"  {'LOC':<10} {'CODE':<16} {'DESCRIPTION':<28} {'QTE':>6}  {'CLIENT':<22} STATUT")
            out.append(f"  {'-'*10} {'-'*16} {'-'*28} {'-'*6}  {'-'*22} {'-'*9}")

        loc   = line["bin_loc"] or "—"
        name  = (line["item_name"] or "")[:28]
        qty   = int(line["qty"])
        client_short = line["client"][:22]
        # Mark clients with multi-order combining opportunity
        multi_flag = " *" if line["card_code"] in multi else "  "
        status = STATUS_LABEL[line["_status"]]
        out.append(f"  {loc:<10} {line['item_code']:<16} {name:<28} {qty:>6}  {client_short:<22}{multi_flag} {status}")

    # ── Section 2 : Résumé emballage ──────────────────────────────────────────
    out.append(f"\n  RESUME EMBALLAGE")
    clients = defaultdict(list)
    for line in ready:
        clients[line["card_code"]].append(line)

    for card_code, clines in sorted(clients.items(), key=lambda x: x[1][0]["client"]):
        client_name = clines[0]["client"]
        orders = sorted(set(l["doc_num"] for l in clines))
        combine_note = f"  [* {len(orders)} commandes — 1 colis possible]" if len(orders) > 1 else ""
        out.append(f"\n  {client_name}")
        out.append(f"  Commandes : {', '.join('#'+str(o) for o in orders)}{combine_note}")
        out.append(f"  {'CODE':<16} {'DESCRIPTION':<30} {'QTE':>6}")
        out.append(f"  {'-'*16} {'-'*30} {'-'*6}")
        for l in sorted(clines, key=lambda x: loc_sort_key(x["bin_loc"])):
            out.append(f"  {l['item_code']:<16} {(l['item_name'] or '')[:30]:<30} {int(l['qty']):>6}")

    # ── Section 3 : En attente de stock ───────────────────────────────────────
    if waiting:
        out.append(f"\n  EN ATTENTE DE STOCK ({len(waiting)} lignes — NE PAS PRELEVER)")
        out.append(f"  {'CODE':<16} {'DESCRIPTION':<30} {'QTE':>6}  {'CLIENT':<25} STATUT")
        out.append(f"  {'-'*16} {'-'*30} {'-'*6}  {'-'*25} {'-'*9}")
        for line in waiting:
            name = (line["item_name"] or "")[:30]
            out.append(f"  {line['item_code']:<16} {name:<30} {int(line['qty']):>6}  "
                       f"{line['client'][:25]:<25} {STATUS_LABEL[line['_status']]}")

    out.append(f"\n  TOTAL : {len(ready)} pret(s) | {len(waiting)} en attente | "
               f"{len(clients)} client(s) | {len(set(l['doc_num'] for l in ready))} commande(s)")
    out.append("=" * 70)
    return "\n".join(out)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--warehouse", "-w", help="Code entrepôt SAP (ex: WH01)")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--list-warehouses", action="store_true")
    args = parser.parse_args()

    use_mock = args.mock or not all([SAP_URL, SAP_COMPANY, SAP_USER, SAP_PASSWORD])

    if args.list_warehouses:
        warehouses = MOCK_WAREHOUSES if use_mock else get_warehouses(sap_login())
        print("\nENTREPOTS DISPONIBLES:")
        for w in warehouses:
            print(f"  {w['WarehouseCode']:<10} {w['WarehouseName']}")
        return

    if not args.warehouse:
        print("Spécifie un entrepôt : --warehouse WH01")
        print("Lister les entrepôts : --list-warehouses")
        sys.exit(1)

    if use_mock:
        all_lines = [l for l in MOCK_LINES if l["warehouse"].upper() == args.warehouse.upper()]
        wh_name   = next((w["WarehouseName"] for w in MOCK_WAREHOUSES
                          if w["WarehouseCode"].upper() == args.warehouse.upper()), args.warehouse)
    else:
        import urllib3; urllib3.disable_warnings()
        session   = sap_login()
        all_lines = get_open_orders(session, args.warehouse)
        wh_name   = next((w["WarehouseName"] for w in get_warehouses(session)
                          if w["WarehouseCode"].upper() == args.warehouse.upper()), args.warehouse)

    if not all_lines:
        print(f"Aucune commande ouverte pour l'entrepôt {args.warehouse}.")
        return

    deduped, dupes = deduplicate(all_lines)

    item_codes = list(set(l["item_code"] for l in deduped))
    stock      = load_stock(item_codes)

    print(format_pick_list(deduped, args.warehouse.upper(), wh_name, dupes, stock))


if __name__ == "__main__":
    main()
