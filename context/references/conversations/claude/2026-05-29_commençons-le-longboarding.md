# Commençons le longboarding

*Imported from Claude export — 2026-05-29*

---

**HUMAN:** bon allons y pour l'lonboarding

**ASSISTANT:** 
```
This block is not supported on your current device yet.
```


```
This block is not supported on your current device yet.
```

Bon, j'ai ce qu'il me faut. Je connais ton design system — la palette, les tokens, le style épuré. L'onboarding va matcher parfaitement.

Je construis un flow en 5 étapes : bienvenue → rôle (passager/conducteur/les deux) → code postal habituel → horaire type → permission notifications. Tout ça avant d'accéder à l'app principale.
```
This block is not supported on your current device yet.
```


```
This block is not supported on your current device yet.
```

Voilà l'onboarding complet en 5 étapes :

**Écran 1 — Bienvenue** : logo, les 3 piliers (corridor, plafond légal, non lucratif), et la note de confidentialité.

**Écran 2 — Rôle** : passager / conducteur / les deux. Le bouton "Continuer" reste désactivé tant qu'aucun choix n'est fait.

**Écran 3 — Corridor habituel** : mêmes codes postaux FSA que dans l'app principale, avec géoloc, validation et la liste des corridors pilotes actifs. "Continuer" se déverrouille seulement quand les deux codes sont valides.

**Écran 4 — Horaire** : jours cliquables, heure de départ + retour, flexibilité. C'est ce qui alimentera le matching.

**Écran 5 — Notifications** : 4 toggle nets, sans bullshit. La note confirme qu'il n'y a rien de marketing.

**Écran 6 — Récap + succès** : résumé du profil, nudge pour la vérification SAAQ si conducteur, puis bouton vers les trajets.

La barre de progression en haut suit chaque étape. Tout est dans le même design system que ton app principale — même vert, même police, mêmes tokens.

Pour intégrer ça dans le HTML principal : je peux soit l'ajouter comme un panel qui se montre avant les tabs (si `isNewUser = true`), soit comme un écran overlay qui disparaît après l'onboarding. Quelle approche tu préfères?