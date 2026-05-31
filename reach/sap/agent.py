# reach/sap/agent.py — v1.0.0
# SAP B1 Quote Agent — interprets a customer request and returns a quote template.
# Usage: python reach/sap/agent.py "customer request text"
#        python reach/sap/agent.py  (prompts for input)

import sys
import io
import json
import sqlite3
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from dotenv import load_dotenv
import os

load_dotenv(Path(__file__).parent.parent.parent / ".env")

import anthropic
from db import get_conn, SAP_DB_PATH

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"
MAX_SEARCH_RESULTS = 30


# ── Search ────────────────────────────────────────────────────────────────────

def search_items(conn: sqlite3.Connection, query: str, limit: int = MAX_SEARCH_RESULTS) -> list:
    if not query.strip():
        return []
    c = conn.cursor()
    try:
        c.execute("""
            SELECT i.item_code, i.item_name, i.foreign_name, i.item_group,
                   i.on_hand, i.on_order, i.committed, i.available, i.uom
            FROM items_fts f
            JOIN items i ON i.item_code = f.item_code
            WHERE items_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit))
        return [dict(r) for r in c.fetchall()]
    except sqlite3.OperationalError:
        return []


def search_customer(conn: sqlite3.Connection, query: str) -> dict | None:
    if not query.strip():
        return None
    c = conn.cursor()
    try:
        c.execute("""
            SELECT cu.* FROM customers_fts f
            JOIN customers cu ON cu.card_code = f.card_code
            WHERE customers_fts MATCH ?
            LIMIT 1
        """, (query,))
        row = c.fetchone()
        return dict(row) if row else None
    except sqlite3.OperationalError:
        return None


# ── Claude ────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Tu es un assistant de vente interne spécialisé pour une entreprise de distribution d'outillage, quincaillerie industrielle et EPI.

Ton rôle : analyser une demande client (parfois floue, en français québécois ou en franglais) et identifier les bons produits dans le catalogue SAP B1.

Tu reçois :
1. La demande brute du client (courriel, notes d'appel, etc.)
2. Une liste de produits candidats trouvés par recherche dans le catalogue

Tu dois retourner un JSON structuré UNIQUEMENT, sans texte autour. Format exact :

{
  "customer_hint": "nom ou code client mentionné dans la demande, ou null",
  "items": [
    {
      "item_code": "CODE-SAP",
      "item_name": "Nom du produit",
      "qty_requested": 10,
      "qty_available": 450,
      "on_order": 200,
      "uom": "EA",
      "confidence": "high|medium|low",
      "note": "remarque optionnelle (ex: client a dit '3/8 inox' — j'ai choisi BOLT-375-SS)"
    }
  ],
  "items_not_found": ["description des items demandés qu'on n'a pas trouvés dans les candidats"],
  "agent_notes": "remarques générales sur la demande (ex: quantités non précisées, ambiguïtés)"
}

Règles :
- Si la quantité n'est pas précisée, mets qty_requested: null
- Si un produit est probablement bon mais pas certain, mets confidence: "medium"
- Si on_order > 0 et available < qty_requested, note-le dans agent_notes
- N'invente pas de codes — utilise seulement les codes dans la liste de candidats"""


def build_user_message(request: str, candidates: list) -> str:
    candidates_text = json.dumps(candidates, ensure_ascii=False, indent=2)
    return f"""DEMANDE CLIENT :
{request}

PRODUITS CANDIDATS (résultats de recherche dans le catalogue) :
{candidates_text}

Analyse la demande et retourne le JSON de la soumission."""


def call_claude(request: str, candidates: list) -> dict:
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model=MODEL,
        max_tokens=2048,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(request, candidates)}],
    )
    raw = message.content[0].text.strip()
    # Strip markdown code blocks if present
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


# ── Output formatter ──────────────────────────────────────────────────────────

def format_template(result: dict, customer: dict | None) -> str:
    lines = []
    lines.append("=" * 60)
    lines.append("  TEMPLATE SOUMISSION / COMMANDE")
    lines.append("=" * 60)

    if customer:
        lines.append(f"\nCLIENT")
        lines.append(f"  Code          : {customer['card_code']}")
        lines.append(f"  Nom           : {customer['card_name']}")
        if customer.get("phone"):
            lines.append(f"  Téléphone     : {customer['phone']}")
        if customer.get("email"):
            lines.append(f"  Courriel      : {customer['email']}")
        ship = " | ".join(filter(None, [
            customer.get("ship_street"), customer.get("ship_city"),
            customer.get("ship_province"), customer.get("ship_zip")
        ]))
        bill = " | ".join(filter(None, [
            customer.get("bill_street"), customer.get("bill_city"),
            customer.get("bill_province"), customer.get("bill_zip")
        ]))
        if ship:
            lines.append(f"  Livraison     : {ship}")
        if bill and bill != ship:
            lines.append(f"  Facturation   : {bill}")
    elif result.get("customer_hint"):
        lines.append(f"\nCLIENT (non trouvé dans BD)")
        lines.append(f"  Indice        : {result['customer_hint']}")
        lines.append(f"  → Chercher manuellement dans SAP")
    else:
        lines.append(f"\nCLIENT : non identifié dans la demande")

    lines.append(f"\nITEMS")
    lines.append(f"  {'CODE':<16} {'NOM':<35} {'QTÉ':>6} {'DISPO':>8} {'STATUT'}")
    lines.append(f"  {'-'*16} {'-'*35} {'-'*6} {'-'*8} {'-'*12}")

    for item in result.get("items", []):
        qty = str(item.get("qty_requested") or "?")
        avail = item.get("qty_available", 0)
        on_order = item.get("on_order", 0)

        if item.get("qty_requested") and avail >= item["qty_requested"]:
            status = "[OK]   En stock"
        elif on_order > 0:
            status = f"[BO]   {on_order} en commande"
        elif avail > 0:
            status = f"[PART] Partiel ({avail})"
        else:
            status = "[RUPTURE]"

        name = item.get("item_name", "")[:35]
        lines.append(f"  {item['item_code']:<16} {name:<35} {qty:>6} {str(avail):>8} {status}")

        if item.get("note"):
            lines.append(f"  {'':>16}   → {item['note']}")

    if result.get("items_not_found"):
        lines.append(f"\nITEMS NON TROUVÉS — à chercher manuellement :")
        for item in result["items_not_found"]:
            lines.append(f"  - {item}")

    if result.get("agent_notes"):
        lines.append(f"\nNOTES")
        lines.append(f"  {result['agent_notes']}")

    lines.append("\n" + "=" * 60)
    return "\n".join(lines)


# ── Main ──────────────────────────────────────────────────────────────────────

def run(request: str):
    if not Path(SAP_DB_PATH).exists():
        print("ERROR: SAP database not found.")
        print("  Run first: python reach/sap/sync.py --mock")
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        print("ERROR: ANTHROPIC_API_KEY not set in .env")
        sys.exit(1)

    conn = get_conn()

    # Extract search terms — use full request for FTS
    # FTS5 doesn't like special chars, clean the query
    import re
    fts_query = re.sub(r'[^\w\s]', ' ', request)
    fts_query = ' OR '.join(fts_query.split()) if fts_query.strip() else ""

    candidates = search_items(conn, fts_query) if fts_query else []

    # Call Claude
    result = call_claude(request, candidates)

    # Look up customer
    customer = None
    if result.get("customer_hint"):
        customer = search_customer(conn, result["customer_hint"])

    conn.close()

    print(format_template(result, customer))


def main():
    if len(sys.argv) > 1:
        request = " ".join(sys.argv[1:])
    else:
        print("Demande client (colle le courriel ou les notes, puis appuie sur Enter deux fois) :")
        lines = []
        while True:
            line = input()
            if line == "" and lines and lines[-1] == "":
                break
            lines.append(line)
        request = "\n".join(lines).strip()

    if not request:
        print("Aucune demande fournie.")
        sys.exit(1)

    run(request)


if __name__ == "__main__":
    main()
