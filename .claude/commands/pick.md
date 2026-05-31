# /pick — Liste de prélèvement SAP B1

Génère une liste de prélèvement propre pour une succursale : filtrée par entrepôt, dédupliquée, triée par localisation.

## Usage

```
/pick [code entrepôt]
```

Exemples : `/pick WH01`, `/pick WH03`

## Steps

1. **Si aucun code fourni**, affiche les entrepôts disponibles :
   ```
   python reach/sap/pick_agent.py --list-warehouses
   ```
   Demande : "Quel entrepôt ?"

2. **Génère la liste :**
   ```
   python reach/sap/pick_agent.py --warehouse [CODE]
   ```
   En mode test : ajouter `--mock`

3. **Affiche le résultat** tel quel.

4. **Si des items sans localisation sont présents**, propose :
   "Veux-tu que je génère une liste séparée des items à localiser pour mettre à jour SAP ?"

## Notes

- Zéro coût — pas d'appel Claude API, logique pure Python.
- Trier par localisation optimise le chemin du picker dans l'entrepôt.
- Les doublons sont éliminés silencieusement mais comptés dans le header.
- Brancher SAP réel : remplir SAP_SERVICE_LAYER_URL, SAP_COMPANY_DB, SAP_USERNAME, SAP_PASSWORD dans .env
