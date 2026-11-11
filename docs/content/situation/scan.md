# Lancer un scan

<p class="where">Barre du haut de la vue Situation. Réservé aux analystes, superviseurs et administrateurs.</p>

## Commandes

- **Liste des scènes** : *Dernière scène Sentinel-1* (par défaut) ou une scène précise parmi les passages récents sur la ZEE (date et heure UTC).
- **Scanner** : lance l'analyse. Un seul scan à la fois sur toute la plateforme ; le bouton est inactif pendant un scan.
- **Message d'état** : étape en cours, puis résultat (« 3 navire(s) sans AIS ») ou cause précise d'échec (clés Copernicus refusées, scène hors ZEE…).

En parallèle, la **veille automatique** peut scanner seule chaque nouvelle scène (paramètre `auto_scan_interval_minutes`, désactivé par défaut).

## Étapes d'un scan

| # | Étape affichée | Ce qui se passe |
| --- | --- | --- |
| 1 | Chargement des frontières maritimes | ZEE, 24 NM et 12 NM (Marine Regions, mis en cache 30 jours) |
| 2 | Recherche de la scène Sentinel-1 | Catalogue STAC Copernicus : scène la plus récente ou celle choisie (jusqu'à 12 jours en arrière) |
| 3 | Analyse SAR | Lecture des pixels sur Copernicus S3 par tuiles de 1 024 px situées dans la ZEE (6 au maximum), détection CFAR, confirmation en double polarisation |
| 4 | Évaluation de la fiabilité des cibles | Bathymétrie, état de la mer, persistance, fausses alarmes passées, confirmation GFW ; les cibles sous le seuil sont écartées |
| 5 | Corrélation AIS | Les positions AIS / VMS au moment du passage expliquent une partie des cibles ; les autres sont des **navires sans AIS** |
| 6 | Contexte GFW + météo et score de risque | Score de risque de chaque cible sans AIS |
| 7 | Scellement C2PA des preuves | Planche, vignettes, manifeste signé |
| 8 | Ouverture / corrélation des incidents | Nouvel incident ou observation ajoutée à un incident proche |

À la fin, un rapport est publié sur le canal **#operations** et les nouveaux P1/P2 sur **#alertes**. Une scène déjà analysée est retenue pour que la veille automatique ne la retraite pas.

## Veille automatique et échecs

| Cas | Comportement |
| --- | --- |
| Scène hors ZEE (« ne recouvre pas la ZEE ») ou produit retiré | marquée **ignorée** : jamais retentée, la veille passe à la scène suivante |
| Échec passager (réseau, quota, clés) | retentée à l'intervalle suivant, **abandonnée après 3 échecs** |
| Scan en cours | la veille tourne en arrière-plan : les règles 24/7 et la surveillance des délais continuent pendant le scan |

L'état du scan est rafraîchi toutes les 3 s pendant un scan, toutes les 15 s sinon.

Détail des algorithmes : [Moteur et calculs](../moteur/index.md).
