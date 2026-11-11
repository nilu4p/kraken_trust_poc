# Score de risque

Répond à : **ce navire sans AIS est-il suspect ?** Score de 0 à 100, **explicable** : chaque point ajouté correspond à une phrase dans la fiche cible.

## 1. Points

| Facteur | Points | Phrase affichée (exemple) |
| --- | --- | --- |
| Écho radar sans AIS | **30 × confiance** (15 à 30) | « Écho radar sans AIS (confiance 82 %) » |
| Mer territoriale (12 NM) | **+25** | « Dans la mer territoriale (12 NM) » |
| Zone contiguë (24 NM) | **+15** | |
| ZEE (200 NM) | **+10** | |
| Coupures AIS GFW à moins de 30 km (`gap_proximity_km`) sur 30 jours (coupures de plus de 7 jours ignorées : navire hors couverture ou désarmé) | **+15** pour la 1re, +2 par suivante, plafond **+22,5** | « 2 coupure(s) AIS GFW à moins de 30 km (30 j) » |
| Rencontres en mer GFW | +10, +2 / suivante, plafond +15 | |
| Flânages GFW | +8, +2 / suivant, plafond +12 | |
| Pêches GFW | +5, +2 / suivante, plafond +7,5 | |
| Grande cible (≥ 40 m), tache d'au moins 6 pixels | **+10** | « navire industriel probable » |
| Cible moyenne (15 – 40 m), tache d'au moins 6 pixels | **+5** | |
| Eaux productives (chlorophylle ≥ 1 mg/m³) **par plus de 20 m de fond** | **+5** | « zone de pêche probable » |
| Chlorophylle élevée par moins de 20 m de fond | 0 | « non interprétée (eaux côtières turbides) » : les sédiments de l'Amazone faussent la mesure satellite |
| Front thermique (gradient ≥ `sst_front_threshold`) | **+5** | « zone d'agrégation des poissons » |
| Dans une aire marine protégée | **+10** | « Dans l'aire marine protégée « Ile Du Grand-Connétable » » |
| À moins de 6 km de la limite des 3 NM | **+5** | « zone côtière réglementée » |
| Détections SAR GFW **sans AIS** à moins de 10 km sur 30 j | **+3 par détection**, plafond **+12** | « zone d'activité non déclarée récurrente » |
| Pêcheur étranger **sans licence** vu à l'AIS à moins de 15 km au moment du passage | **+5** | |
| Navire inscrit sur liste de surveillance | **+30** | |

!!! info "Activité légale exclue"
    Les événements GFW (coupures, rencontres, flânages, pêches) des **navires autorisés** (licence valide ou VMS) ne sont pas comptés : la pêche légale de la flottille sous licence ne doit pas rendre suspects les échos voisins.

## 2. Pondérations

Le total est ensuite multiplié par des facteurs inférieurs ou égaux à 1 :

| Pondération | Facteur | Pourquoi |
| --- | --- | --- |
| **Fiabilité de détection** *q* | **0,5 + 0,5 × q** (de ×0,5 à ×1) | une cible douteuse ne doit pas atteindre P1 |
| AIS **simulé** ou **aucune position AIS au moment du passage** | ×0,85 | impossible de savoir si l'écho avait un AIS |
| Météo (seulement si la fiabilité n'a pas été calculée) | ×0,7 (mer forte) à ×0,95 | déjà comptée dans la fiabilité sinon |

Le résultat est borné entre 0 et 100 et arrondi.

## 3. Niveau et sévérité

| Score | Niveau | Sévérité de l'incident |
| --- | --- | --- |
| ≥ 75 | Critique | P1 |
| 55 – 74 | Élevé | P2 |
| 35 – 54 | Moyen | P3 |
| < 35 | Faible | P4 |

!!! example "Exemple complet"
    Confiance 0,8 → 24 · mer territoriale +25 · 1 coupure AIS GFW proche +15 · cible de 45 m +10 · front thermique +5 = **79**.
    Fiabilité 0,9 → ×0,95 → **75 → Critique, P1**.

!!! info "Les règles AIS n'utilisent pas ce calcul"
    Les incidents des [règles 24/7](regles.md) ont un score fixé par la règle (ex. 90 pour un navire listé INN) : il n'y a pas d'écho radar à évaluer.
