# Légende de la carte

<p class="where">Carte de la vue Situation · légende repliable en bas de la carte · cases à cocher dans <em>Panneau latéral → Couches</em>.</p>

## La légende à l'écran

Le bouton **Légende** (en bas à gauche de la carte) ouvre un panneau découpé en familles, chacune repliable d'un clic sur son titre :

| Famille | Contenu |
| --- | --- |
| **Cibles radar (sans AIS)** | cible prioritaire, à vérifier, sélection — toujours affichée |
| **Navires AIS (flèche = cap)** | les six catégories, la trace 6 h |
| **Détections et comportements** | détections SAR GFW (avec / sans AIS), effort de pêche, événements GFW par type |
| **Zones et limites** | ZEE, zone contiguë, mer territoriale, limites SHOM, aires protégées, isobathes |
| **Imagerie et couches** | empreintes Sentinel-1 et optiques, lumières nocturnes VIIRS, surcouche océan active |
| **Sources personnalisées** | une ligne par source personnalisée cochée |

**Seules les couches affichées sont listées** : décocher une couche la retire aussi de la légende. Le panneau défile s'il est plus haut que l'écran. Il se masque complètement par *Carte → Affichage → Afficher la légende*.

Principe de lecture : **la forme dit d'où vient l'information, la couleur dit ce qu'elle signifie.**

| Forme | Origine |
| --- | --- |
| Rond avec halo | **Écho radar** détecté par KRAKEN (Sentinel-1) sans AIS correspondant |
| Flèche | **Navire AIS** (il déclare sa position) ; la pointe indique son cap |
| Losange | Détection radar de **Global Fishing Watch** |
| Petit rond plein | **Événement** comportemental Global Fishing Watch |
| Trait / surface | **Limite** ou **zone** (frontières, limites de pêche, aires protégées, empreintes d'images) |

## Cibles radar (navires sans AIS)

Ce sont les objets principaux : des échos radar que **aucun AIS ni VMS** n'explique au moment du passage satellite.

| Symbole | Libellé | Signification exacte |
| --- | --- | --- |
| <span class="sw" style="--c:#ff3b3b"></span> rond rouge **clignotant** | **Cible prioritaire** | Score de risque **≥ 55**, **ou** incident lié de sévérité **P1 ou P2** |
| <span class="sw" style="--c:#f5c542"></span> rond jaune fixe | **Cible à vérifier** | Score de risque **< 55** et pas d'incident P1/P2 |
| <span class="sw" style="--c:#00e5ff"></span> rond cyan agrandi | **Sélection** | Cible dont la fiche est ouverte |
| rond grisé / estompé | Cible d'un **incident clos** | Visible seulement si « masquer incidents clos » est décoché (onglet Cibles) |

!!! note "Pourquoi deux couleurs seulement ?"
    Le score de risque comporte quatre niveaux (voir ci-dessous), mais la carte n'en garde que deux pour qu'une décision se prenne d'un coup d'œil : **traiter maintenant** (rouge) ou **vérifier quand possible** (jaune). Le niveau exact apparaît dans la fiche et dans la liste des cibles.

### Niveaux du score de risque (badges)

| Badge | Niveau | Score |
| --- | --- | --- |
| <span class="sw sq" style="--c:#ff3b3b"></span> **Critique** | `critical` | 75 – 100 |
| <span class="sw sq" style="--c:#ff8c1a"></span> **Élevé** | `high` | 55 – 74 |
| <span class="sw sq" style="--c:#f5c542"></span> **Moyen** | `medium` | 35 – 54 |
| <span class="sw sq" style="--c:#94a3b8"></span> **Faible** | `low` | 0 – 34 |

### Fiabilité de détection (badge « Détection … »)

| Badge | Indice | Lecture |
| --- | --- | --- |
| <span class="sw sq" style="--c:#3dff8a"></span> **Fiable** | ≥ 70 % | Très probablement un navire |
| <span class="sw sq" style="--c:#f5c542"></span> **À vérifier** | 45 – 69 % | Plausible, à confirmer (vignette, imagerie optique) |
| <span class="sw sq" style="--c:#ff3b3b"></span> **Douteuse** | < 45 % | Fouillis de mer, objet fixe ou haut-fond possibles |

Calcul : [Indice de fiabilité](../moteur/fiabilite.md).

## Navires AIS

<p class="where">Couche « Navires AIS (temps réel) » — navires entendus dans les 2 dernières heures. Les traces sont simplifiées à une position toutes les 5 minutes.</p>

La flèche pointe dans la **direction suivie** (calculée entre les deux dernières positions) ; un **rond** remplace la flèche quand le navire n'a qu'une position. La couleur donne la **catégorie** :

| Symbole | Catégorie | Règle de classement |
| --- | --- | --- |
| <span class="sw arrow" style="--c:#3dff8a"></span> vert | **Pavillon français** | MMSI français, dont l'outre-mer (745 = Guyane, 329 Guadeloupe, 347 Martinique…) |
| <span class="sw arrow" style="--c:#93c5fd"></span> bleu clair | **Pavillon étranger** | Pavillon étranger, pas un navire de pêche |
| <span class="sw arrow" style="--c:#fb923c"></span> orange | **Pêcheur étranger** | Pavillon étranger **et** navire de pêche (type AIS 30 ou engin de pêche connu de GFW), **sans licence connue** |
| <span class="sw arrow" style="--c:#5eead4"></span> turquoise clair | **Pêcheur sous licence** | Pêcheur étranger figurant dans les [navires autorisés](../administration/autorises.md) (licence valide) ou vu au VMS |
| <span class="sw arrow" style="--c:#ff3b3b"></span> rouge cerclé | **Liste INN** | Navire reconnu dans la [liste de surveillance](../administration/surveillance.md) (MMSI, IMO, indicatif radio ou nom / alias) |
| <span class="sw arrow" style="--c:#94a3b8"></span> gris | **Pavillon inconnu** | MMSI non attribué à un pays connu, ou identité incomplète |

Le **pavillon** est déduit du MMSI (3 premiers chiffres = code MID de l'UIT), complété au besoin par le pavillon d'immatriculation connu de Global Fishing Watch, toujours affiché en clair (« Venezuela » plutôt que « VEN »). Les territoires d'outre-mer (Martinique `MTQ`, Guadeloupe `GLP`, Guyane `GUF`…) sont des **pavillons français**.

### Traces AIS

<span class="sw line solid" style="--c:#3dff8a"></span> Couche « Traces AIS (6 h) » : ligne fine de la couleur de la catégorie, reliant les positions des 6 dernières heures. Une trace qui s'interrompt brutalement près de la côte peut signaler une **coupure AIS**.

## Détections SAR Global Fishing Watch

<p class="where">Couche « Détections SAR GFW (30 j) » — désactivée par défaut (chargement lourd).</p>

| Symbole | Signification |
| --- | --- |
| <span class="sw sq diamond" style="--c:#f472b6"></span> losange rose | Navire détecté par GFW sur Sentinel-1 **sans AIS** apparié : activité non déclarée. Des losanges roses regroupés = **zone d'activité « sombre » récurrente** |
| <span class="sw sq diamond" style="--c:#64748b"></span> losange gris | Détection GFW **appariée à un AIS** (navire coopératif) |

Délai de publication par GFW : environ 5 jours. Utilisé aussi dans le calcul ([confirmation croisée](../moteur/fiabilite.md) et [points chauds](../moteur/risque.md)).

## Effort de pêche AIS

Couche « Effort de pêche AIS GFW (*N* j) », période réglée dans l'onglet Couches (7, 30 ou 90 jours) : cercles semi-transparents, d'autant plus grands que le nombre d'**heures de pêche apparente** (détectées par GFW d'après le comportement AIS) est élevé dans la maille. Montre où la flotte *déclarée* pêche.

| Symbole | Pavillon |
| --- | --- |
| <span class="sw" style="--c:#fb923c"></span> orange | étranger (hors France et outre-mer) |
| <span class="sw" style="--c:#38bdf8"></span> bleu | français |

## Événements Global Fishing Watch

<p class="where">Couche « Événements GFW » — 30 derniers jours, dans la ZEE.</p>

| Symbole | Événement | Définition |
| --- | --- | --- |
| <span class="sw" style="--c:#e879f9"></span> magenta (plus gros) | **Coupure AIS** | Le navire a cessé d'émettre pendant une longue durée ; GFW indique si la coupure paraît **volontaire** |
| <span class="sw" style="--c:#a78bfa"></span> violet | **Rencontre en mer** | Deux navires côte à côte en mer : transbordement possible |
| <span class="sw" style="--c:#2dd4bf"></span> turquoise | **Flânage** | Navire (souvent un transporteur) lent et sinueux en attente : signe d'un transbordement |
| <span class="sw" style="--c:#60a5fa"></span> bleu | **Pêche** | Activité de pêche apparente détectée par GFW |
| <span class="sw" style="--c:#facc15"></span> jaune | **Escale au port** | Navire arrêté dans un port de la zone (confiance GFW 3 ou 4) : nom du port, durée à quai. Montre où débarque la flotte qui pêche dans la ZEE. N'entre pas dans le score de risque |

Les compteurs par type s'affichent sous la case à cocher.

## Zones et limites

| Symbole | Élément | Définition |
| --- | --- | --- |
| <span class="sw line" style="--c:#00e5ff"></span> tirets cyan, voile léger | **ZEE (200 NM)** | Zone économique exclusive de Guyane (Marine Regions, MRGID 8462) : la France y a l'exclusivité de la pêche |
| <span class="sw line" style="--c:#38bdf8"></span> pointillés bleus | **Zone contiguë (24 NM)** | Contrôles douaniers, sanitaires, d'immigration |
| <span class="sw line" style="--c:#e2e8f0"></span> pointillés blancs | **Mer territoriale (12 NM)** | Souveraineté pleine de l'État côtier |
| <span class="sw line" style="--c:#c4b5fd"></span> longs tirets mauves | **Limite de pêche 100 NM (SHOM)** | Limite réglementaire de pêche des 100 milles publiée par le SHOM (référence R951-14 du jeu de données) |
| <span class="sw line" style="--c:#e2e8f0"></span> points blancs espacés | **Limite des 3 NM (SHOM)** | Bande côtière soumise à une réglementation de pêche spécifique (publiée par le SHOM) |
| <span class="sw area" style="--c:#10b981"></span> surface verte hachurée | **Aire marine protégée (WDPA)** | Réserve ou aire protégée ayant une partie marine (Grand Connétable, Amana, réserves du Suriname et du Brésil) ; infobulle : nom, statut, surface marine |
| <span class="sw line" style="--c:#7dd3fc"></span> trait fin bleu pâle | **Isobathes 20, 50, 100 m** | Profondeurs du plateau continental (grille NOAA ETOPO, ~1,8 km : contours lissés, pas une carte marine) ; infobulle : profondeur |
| <span class="sw line" style="--c:#93c5fd"></span> trait bleu clair plus épais | **Rebord du plateau (200 m)** | Rupture de pente entre plateau et talus : repère des zones de pêche au large (vivaneau notamment) |
| <span class="sw line" style="--c:#3b82f6"></span> trait fin bleu | **Isobathes 1 000 à 4 000 m** | Talus continental et grands fonds |

1 mille nautique (NM) = 1 852 m ; 12 NM ≈ 22 km, 24 NM ≈ 44 km, 100 NM ≈ 185 km, 200 NM ≈ 370 km.

## Empreintes d'images satellite

| Symbole | Élément |
| --- | --- |
| <span class="sw area" style="--c:#818cf8"></span> contour indigo pointillé | **Empreinte Sentinel-1** : surface couverte par une scène radar récente (date dans l'infobulle) |
| <span class="sw area" style="--c:#bef264"></span> contour vert-jaune pointillé | **Image optique de la cible** : Pléiades, SPOT, Sentinel-2… acquise autour de la cible sélectionnée (s'affiche avec la fiche cible) |

## Surcouches d'imagerie

| Surcouche | Lecture |
| --- | --- |
| **Lumières nocturnes VIIRS** (points lumineux sur le fond de carte) | Chaque point lumineux isolé en mer est un navire éclairé pendant la nuit précédant la date choisie : les flottilles de pêche (lamparos, palangriers) apparaissent en grappes. Les zones sombres sont transparentes : le fond de carte reste visible |
| **Océan — Température de surface** | Échelle de couleurs thermique ; une variation forte de couleur sur une courte distance = **front thermique** |
| **Océan — Chlorophylle-a** | Vert intense = eaux riches en plancton (panache de l'Amazone) |
| **Océan — Courants** | Couleur = vitesse, flèches = direction du courant de surface |

Ces surcouches se règlent dans [Panneau latéral → Carte](panneau-lateral.md#onglet-carte).

## Sources personnalisées

Couches ajoutées par un administrateur ([Sources personnalisées](../administration/sources-personnalisees.md)) :

| Symbole | Élément |
| --- | --- |
| <span class="sw" style="--c:#22d3ee"></span> rond de la couleur choisie | **Objet** d'une source (point, ligne ou surface) ; survol = libellé, clic = fiche des attributs |
| surface grise dans la légende | **Couche image** (tuiles ou WMS) : sa signification est donnée par le service d'origine (description de la source) |
