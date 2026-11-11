# Indice de fiabilité de détection

Répond à : **cet écho est-il vraiment un navire ?** Valeur de 0 à 1 (affichée en %), partant de **0,55** et corrigée par chaque constat. Les constats apparaissent en clair dans la fiche cible.

## Calcul

| Constat | Effet | Explication |
| --- | --- | --- |
| Base | **0,55** | |
| Force du signal | **+0 à +0,25** : `0,25 × min(1, max(0, (SNR − k) / 2k))` | plus la cible dépasse le seuil CFAR, plus elle est crédible |
| Confirmé en double polarisation | **+0,15** | visible dans les deux polarisations |
| Absent de la polarisation complémentaire | **−0,20** | artefact probable |
| Cible ponctuelle (1-2 pixels) | **−0,15** | chatoiement (*speckle*) possible |
| Forme allongée (≥ 6 px et allongement ≥ 2) | **+0,05** | compatible avec une coque |
| Forme compacte (≥ 6 px et allongement < 1,2) | **−0,10** | peu typique d'un navire |
| Bord de fauchée | **−0,15** | bruit élevé en bord d'image |
| Confirmé par une détection SAR GFW le même jour à moins de 1 km (`gfw_sar_confirm_km`) | **+0,15** | un second algorithme indépendant a vu la même chose |
| Au ras du rivage (< 200 m) | **−0,45** | roche, pieu, épave, lisière de mangrove : très probablement fixe |
| Proche du rivage (200 – 500 m) | **−0,25** | rochers, pieux, barrières de pêche |
| Zone côtière (500 m – 1 km) | **−0,10** | à confirmer |
| Ouvrage ou balise cartographié (OpenStreetMap) à 80 – 300 m | **−0,15** | bouée, balise, appontement, digue : écho fixe possible |
| Haut-fond (< 3 m, ou « terre » pour la grille ETOPO alors que le trait de côte place le point en mer) | **−0,15** | vase découvrante, bancs de vase de l'Amazone |
| Eaux très peu profondes (3 – 10 m) | **−0,10** | bancs de vase migrants, très présents sur la côte guyanaise |
| Point sur terre d'après la seule bathymétrie (si aucun trait de côte n'est disponible) | **−0,40** | secours : la grille ETOPO (~1,8 km) est trop grossière à la côte |

La distance au rivage est la plus courte des deux mesures : **image radar** du jour et **trait de côte OpenStreetMap**. Les échos *sur* la côte, les îlets, les ouvrages et les balises ne reçoivent pas de note : ils sont écartés dès la détection (voir [Détection radar](detection-sar.md#cote-ilets-ouvrages-et-balises)).
| Mer forte (vent > 12 m/s ou houle > 3,5 m) **au moment du passage** | **−0,15** | fouillis radar |
| Mer agitée (vent > 7 m/s ou houle > 2 m) | **−0,05** | fausses alarmes possibles. Une mer calme (vent < 3 m/s) n'est **pas** pénalisée : son fond radar sombre fait au contraire ressortir les navires |
| Fausse alarme signalée à moins de 250 m (`persistence_radius_km`) depuis moins de `false_positive_memory_days` (60 j) | **−0,45** | apprentissage par les opérateurs ; le verdict s'efface ensuite, un lieu n'est jamais aveuglé définitivement |
| Cible récurrente : vue au même endroit (250 m) sur au moins 2 autres scènes (`persistence_min_scenes`), sans navire confirmé dans le secteur | **−0,15** | objet fixe (bouée, plateforme, épave, roche) **ou** navire qui revient sur son lieu de pêche : simple signal, la cible reste à vérifier |
| Navire déjà confirmé dans le secteur | 0 (information) | |

Le résultat est borné entre 0 et 1, arrondi au centième.

## Niveaux

| Niveau | Indice |
| --- | --- |
| Fiable | ≥ 0,70 |
| À vérifier | 0,45 – 0,69 |
| Douteuse | < 0,45 |

## Seuil d'alerte

Une cible dont l'indice est **sous `min_detection_quality` (0,25 par défaut)** est écartée : elle n'ouvre pas d'incident. Les autres poursuivent vers la [corrélation AIS](correlation-ais.md).

!!! example "Exemple"
    SNR = 12, k = 5 → +0,25 × (7/10) = +0,175 ; double polarisation ✓ +0,15 ; forme allongée +0,05 ; mer favorable.
    **0,55 + 0,175 + 0,15 + 0,05 = 0,93 → Fiable (93 %)**.

!!! note "Météo au moment du passage"
    L'état de la mer utilisé est celui **de l'heure du passage satellite** (archive horaire Open-Meteo, environ 3 mois), pas celui du moment du traitement : une image de la veille au soir est jugée avec le vent de la veille au soir.
