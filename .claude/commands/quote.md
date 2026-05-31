# /quote — SAP B1 Quote Agent

Prend une demande client (courriel collé, notes d'appel, description floue) et retourne un template de soumission structuré avec les codes SAP, disponibilités, et infos client.

## Usage

```
/quote [texte optionnel]
```

Si le texte est fourni après `/quote`, utilise-le comme demande.
Sinon, demande à l'utilisateur de coller ou décrire la demande client.

## Steps

1. **Récupère la demande client**
   - Si l'utilisateur a fourni du texte avec la commande, utilise-le directement.
   - Sinon, dis : "Colle le courriel ou les notes de la demande client :"

2. **Vérifie que la base SAP est prête**
   Run: `python reach/sap/db.py` (silently, to ensure tables exist)

3. **Lance l'agent**
   Run: `python reach/sap/agent.py "[demande]"`
   - Si la commande échoue avec "SAP database not found" : dis "La base SAP n'est pas encore synchronisée. Lance d'abord : `python reach/sap/sync.py --mock` (données test) ou `python reach/sap/sync.py` (SAP réel)."
   - Si la commande échoue avec "ANTHROPIC_API_KEY" : dis "Ajoute ta clé Anthropic dans .env : ANTHROPIC_API_KEY=..."

4. **Affiche le résultat** tel quel — le script formate déjà la sortie.

5. **Propose les suivis** :
   - "Veux-tu créer cette soumission directement dans SAP ?" (Phase 2 — disponible quand l'API SAP est branchée)
   - "Veux-tu rédiger le courriel de soumission au client ?"

## Notes

- Le script `reach/sap/agent.py` peut prendre 10–20 secondes (appel Claude API).
- Pour des demandes longues avec beaucoup d'items, les résultats sont plus précis.
- La base SAP doit être synchronisée régulièrement (`python reach/sap/sync.py`).
- En mode test : `python reach/sap/sync.py --mock` charge 10 items fictifs pour valider le flux.
