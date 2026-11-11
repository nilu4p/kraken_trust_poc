# Enquêter sur un navire AIS

Incidents **pêche étrangère**, **liste INN**, **sanctions**, **coupure AIS**, **rendez-vous en mer**, **pêche en aire protégée** : le navire est identifié par son MMSI.

## 1. Retrouver le navire

Depuis le dossier : *Voir la situation tactique*, puis cliquez sa flèche (ou repérez-le par sa couleur : orange = pêcheur étranger, rouge cerclé = liste INN). La [fiche fusion](../situation/fiche-navire.md) s'affiche dans la bulle.

## 2. Vérifier l'identité

| Question | Où regarder | Attention |
| --- | --- | --- |
| Pavillon | bulle (déduit du MMSI) et GFW | un MMSI peut être falsifié ; comparez avec le pavillon d'immatriculation GFW |
| Type et engin | bulle, GFW | type AIS 30 = pêche déclarée |
| IMO, indicatif | fiche fusion | l'IMO est le meilleur identifiant (ne change jamais) |
| Liste INN | ⚠ dans la bulle, *Correspondance : …* | une correspondance **par nom seul** doit être confirmée |
| Sanctions | ⚠ Sanctions, score | lisez la fiche OpenSanctions (lien) : homonymes fréquents sous 0,9 |
| Licence | « sous licence (VMS) » | un navire sous licence n'est pas en infraction du seul fait de sa présence |

## 3. Comprendre le comportement

| Indice | Où | Lecture |
| --- | --- | --- |
| Trace 6 h | couche *Traces AIS* | allers-retours lents = pêche ; ligne droite rapide = transit |
| Vitesse | bulle | 2–4 nd typique du chalutage ; < 1 nd = dérive ou filet |
| Coupures | GFW Insights, événements GFW | coupures répétées = dissimulation probable |
| Couverture AIS | GFW Insights | < 50 % pour un navire de pêche = suspect |
| Rencontres | événements GFW, incidents rendez-vous | transbordement |
| Changements de pavillon | GFW Insights | caractéristique des navires INN |

## 4. Décider

- Comportement normal et autorisé → **Activité légitime** avec la justification.
- Suspicion confirmée → **Qualifier** puis escalade (superviseur) avec : identité complète, position et heure UTC, trace, indicateurs.
- Coupure AIS en cours → prévenir le superviseur : la dernière position connue et le courant (dossier du point) donnent la zone de recherche.
