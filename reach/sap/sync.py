# reach/sap/sync.py — v1.0.0
# Pulls items and customers from SAP B1 Service Layer into local SQLite.
# Usage: python reach/sap/sync.py
# Usage (mock data for testing): python reach/sap/sync.py --mock

import sys
import json
import argparse
import requests
from pathlib import Path
from datetime import datetime

# Add workspace root to path for .env loading
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

SAP_URL      = os.getenv("SAP_SERVICE_LAYER_URL", "").rstrip("/")
SAP_COMPANY  = os.getenv("SAP_COMPANY_DB", "")
SAP_USER     = os.getenv("SAP_USERNAME", "")
SAP_PASSWORD = os.getenv("SAP_PASSWORD", "")

from db import get_conn, init_db, rebuild_fts, SAP_DB_PATH


# ── SAP Service Layer session ─────────────────────────────────────────────────

def sap_login() -> requests.Session:
    session = requests.Session()
    session.verify = False  # SAP B1 often uses self-signed certs on LAN
    resp = session.post(f"{SAP_URL}/Login", json={
        "CompanyDB": SAP_COMPANY,
        "UserName":  SAP_USER,
        "Password":  SAP_PASSWORD,
    }, timeout=30)
    resp.raise_for_status()
    return session


def sap_get_all(session: requests.Session, endpoint: str, select: str) -> list:
    results = []
    skip = 0
    page = 1000
    while True:
        url = f"{SAP_URL}/{endpoint}?$select={select}&$top={page}&$skip={skip}"
        resp = session.get(url, timeout=60)
        resp.raise_for_status()
        batch = resp.json().get("value", [])
        results.extend(batch)
        if len(batch) < page:
            break
        skip += page
    return results


# ── Item sync ─────────────────────────────────────────────────────────────────

def sync_items(conn, session: requests.Session):
    print("Syncing items from SAP B1...")
    fields = "ItemCode,ItemName,ForeignName,ItemsGroupCode,QuantityOnStock,QuantityOrderedFromVendors,QuantityOrderedByCustomers,LastPurchasePrice,SalesUnit"
    rows = sap_get_all(session, "Items", fields)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    for r in rows:
        on_hand  = r.get("QuantityOnStock", 0) or 0
        on_order = r.get("QuantityOrderedFromVendors", 0) or 0
        committed = r.get("QuantityOrderedByCustomers", 0) or 0
        available = on_hand - committed
        c.execute("""
            INSERT OR REPLACE INTO items
            (item_code, item_name, foreign_name, item_group, on_hand, on_order, committed, available, price, uom, synced_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r.get("ItemCode", ""),
            r.get("ItemName", ""),
            r.get("ForeignName", ""),
            str(r.get("ItemsGroupCode", "")),
            on_hand, on_order, committed, available,
            r.get("LastPurchasePrice", 0) or 0,
            r.get("SalesUnit", ""),
            now,
        ))
    conn.commit()
    print(f"  {len(rows)} items synced.")


# ── Customer sync ─────────────────────────────────────────────────────────────

def _extract_address(addresses: list, addr_type: str) -> dict:
    defaults = [a for a in addresses if a.get("AddressType") == addr_type and a.get("BPCode")]
    if not defaults:
        defaults = [a for a in addresses if a.get("AddressType") == addr_type]
    if not defaults:
        return {}
    a = defaults[0]
    return {
        "street":   a.get("Street", ""),
        "city":     a.get("City", ""),
        "province": a.get("State", ""),
        "zip":      a.get("ZipCode", ""),
    }


def sync_customers(conn, session: requests.Session):
    print("Syncing customers from SAP B1...")
    fields = "CardCode,CardName,Phone1,EmailAddress,BPAddresses"
    rows = sap_get_all(session, "BusinessPartners?$filter=CardType eq 'cCustomer'", fields)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    for r in rows:
        addresses = r.get("BPAddresses", []) or []
        bill = _extract_address(addresses, "bo_BillTo")
        ship = _extract_address(addresses, "bo_ShipTo")
        c.execute("""
            INSERT OR REPLACE INTO customers
            (card_code, card_name, phone, email,
             bill_street, bill_city, bill_province, bill_zip,
             ship_street, ship_city, ship_province, ship_zip,
             synced_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r.get("CardCode", ""),
            r.get("CardName", ""),
            r.get("Phone1", ""),
            r.get("EmailAddress", ""),
            bill.get("street",""), bill.get("city",""), bill.get("province",""), bill.get("zip",""),
            ship.get("street",""), ship.get("city",""), ship.get("province",""), ship.get("zip",""),
            now,
        ))
    conn.commit()
    print(f"  {len(rows)} customers synced.")


# ── Mock data for testing (no SAP needed) ─────────────────────────────────────

MOCK_ITEMS = [
    ("BOLT-375-SS",  "Boulon inox 3/8-16 x 1\"",      "SS Bolt 3/8-16x1",  "Boulonnerie", 450, 200, 50, 400, 0.85, "EA"),
    ("BOLT-375-GR5", "Boulon gr.5 3/8-16 x 1\"",      "Bolt GR5 3/8",      "Boulonnerie", 1200, 0, 100, 1100, 0.35, "EA"),
    ("WASH-375",     "Rondelle plate 3/8\"",            "Flat Washer 3/8",   "Boulonnerie", 12, 500, 0, 12, 0.12, "EA"),
    ("NUT-375",      "Écrou hex 3/8-16",                "Hex Nut 3/8-16",    "Boulonnerie", 800, 0, 0, 800, 0.18, "EA"),
    ("GANT-CUT5-L",  "Gants résistants coupures niv.5 L","Cut-5 Gloves L",  "EPI",         48, 24, 0, 48, 12.50, "PR"),
    ("GANT-CUT5-M",  "Gants résistants coupures niv.5 M","Cut-5 Gloves M",  "EPI",         32, 24, 0, 32, 12.50, "PR"),
    ("LUNE-SECU",    "Lunettes de sécurité claires",    "Safety Glasses",    "EPI",         200, 0, 50, 150, 3.25, "EA"),
    ("MARTEAU-16",   "Marteau 16oz manche bois",        "16oz Claw Hammer",  "Outillage main",180, 0, 12, 168, 18.95, "EA"),
    ("RUBAN-25",     "Ruban à mesurer 25pi",            "Tape Measure 25ft", "Outillage main",95, 0, 0, 95, 14.50, "EA"),
    ("DRILL-HSS-14", "Foret HSS 1/4\"",                 "HSS Drill 1/4",     "Forets",      300, 100, 20, 280, 4.20, "EA"),
]

MOCK_CUSTOMERS = [
    ("C00001", "Construction Lafleur Inc.",   "514-555-0101", "lafleur@example.com",
     "100 rue Principale", "Montréal", "QC", "H1A 1A1",
     "100 rue Principale", "Montréal", "QC", "H1A 1A1"),
    ("C00002", "Manufacture Gagnon Ltée",     "450-555-0202", "gagnon@example.com",
     "200 boul. Industriel", "Laval",    "QC", "H7L 2A2",
     "200 boul. Industriel", "Laval",    "QC", "H7L 2A2"),
    ("C00003", "Entretien Tremblay & Fils",   "418-555-0303", "",
     "55 chemin des Érables", "Québec", "QC", "G1R 3B3",
     "55 chemin des Érables", "Québec", "QC", "G1R 3B3"),
]


def load_mock_data(conn):
    print("Loading mock data...")
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    for item in MOCK_ITEMS:
        c.execute("""
            INSERT OR REPLACE INTO items
            (item_code, item_name, foreign_name, item_group, on_hand, on_order, committed, available, price, uom, synced_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (*item, now))
    for cust in MOCK_CUSTOMERS:
        c.execute("""
            INSERT OR REPLACE INTO customers
            (card_code, card_name, phone, email,
             bill_street, bill_city, bill_province, bill_zip,
             ship_street, ship_city, ship_province, ship_zip,
             synced_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (*cust, now))
    conn.commit()
    print(f"  {len(MOCK_ITEMS)} mock items loaded.")
    print(f"  {len(MOCK_CUSTOMERS)} mock customers loaded.")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mock", action="store_true", help="Load mock data instead of connecting to SAP")
    args = parser.parse_args()

    init_db()
    conn = get_conn()

    if args.mock:
        load_mock_data(conn)
    else:
        if not all([SAP_URL, SAP_COMPANY, SAP_USER, SAP_PASSWORD]):
            print("ERROR: SAP B1 credentials not configured in .env")
            print("  Required: SAP_SERVICE_LAYER_URL, SAP_COMPANY_DB, SAP_USERNAME, SAP_PASSWORD")
            print("  Or use --mock to load test data.")
            sys.exit(1)
        import urllib3
        urllib3.disable_warnings()
        session = sap_login()
        sync_items(conn, session)
        sync_customers(conn, session)

    rebuild_fts(conn)
    conn.close()
    print("Sync complete.")


if __name__ == "__main__":
    main()
