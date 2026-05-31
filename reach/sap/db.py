# reach/sap/db.py — v1.0.0
# SQLite schema for SAP B1 items and customers.
# Run directly to initialize: python reach/sap/db.py

import sqlite3
from pathlib import Path

SAP_DB_PATH = str(Path(__file__).parent.parent / "data" / "sap.db")


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(SAP_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    Path(SAP_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            item_code     TEXT PRIMARY KEY,
            item_name     TEXT,
            foreign_name  TEXT,
            item_group    TEXT,
            on_hand       REAL DEFAULT 0,
            on_order      REAL DEFAULT 0,
            committed     REAL DEFAULT 0,
            available     REAL DEFAULT 0,
            price         REAL DEFAULT 0,
            currency      TEXT DEFAULT 'CAD',
            uom           TEXT,
            synced_at     TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
            item_code UNINDEXED,
            item_name,
            foreign_name,
            item_group
        );

        CREATE TABLE IF NOT EXISTS customers (
            card_code    TEXT PRIMARY KEY,
            card_name    TEXT,
            phone        TEXT,
            email        TEXT,
            bill_street  TEXT,
            bill_city    TEXT,
            bill_province TEXT,
            bill_zip     TEXT,
            ship_street  TEXT,
            ship_city    TEXT,
            ship_province TEXT,
            ship_zip     TEXT,
            synced_at    TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS customers_fts USING fts5(
            card_code UNINDEXED,
            card_name,
            phone,
            email
        );
    """)

    conn.commit()
    conn.close()
    print(f"SAP database initialized at {SAP_DB_PATH}")


def rebuild_fts(conn: sqlite3.Connection):
    c = conn.cursor()
    c.execute("DELETE FROM items_fts")
    c.execute("""
        INSERT INTO items_fts (item_code, item_name, foreign_name, item_group)
        SELECT item_code, item_name, foreign_name, item_group FROM items
    """)
    c.execute("DELETE FROM customers_fts")
    c.execute("""
        INSERT INTO customers_fts (card_code, card_name, phone, email)
        SELECT card_code, card_name, phone, email FROM customers
    """)
    conn.commit()
    print("FTS indexes rebuilt.")


if __name__ == "__main__":
    init_db()
