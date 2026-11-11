# Corrélation radar / AIS

Répond à : **cet écho radar correspond-il à un navire qui déclarait sa position ?** Si oui, il est « expliqué » et n'ouvre pas d'incident. Sinon, c'est un **navire sans AIS** (*dark vessel*).

## Quelles positions ?

L'historique des positions est alimenté en continu par **aisstream.io**, **AISHub** et les **imports VMS** (au plus une position par navire et par minute, conservée 90 jours). Pour une scène radar acquise à l'instant *T* :

1. pour chaque navire, on prend sa position **la plus proche de T** dans une fenêtre de ± `ais_correlation_window_h` (3 h) ;
2. on la **projette à l'instant T** selon sa route et sa vitesse (*estime*, *dead reckoning*) :

```text
distance (km)  = vitesse (nœuds) × 1,852 × écart (h)
Δlatitude (°)  = distance × cos(route) / 110,57
Δlongitude (°) = distance × sin(route) / (111,32 × cos(latitude))
```

!!! warning "Une absence d'AIS ne vaut que si l'on écoutait"
    Le [journal de collecte](../donnees.md) enregistre chaque minute où un flux AIS (aisstream, AISHub, VesselAPI) était à l'écoute. Si moins de **50 %** des minutes entre T − 30 min et T + 30 min ont été écoutées, les positions trouvées expliquent encore les échos qu'elles touchent, mais les autres échos ne sont **pas** déclarés « sans AIS » : source *Aucune position AIS au moment du passage*, risque ×0,85. Une panne du collecteur ne fabrique donc plus de faux navires sombres.

Sans historique au moment du passage :

| Situation | Comportement | Source affichée |
| --- | --- | --- |
| Scène de moins de `ais_correlation_window_h` (3 h) | écoute du flux AIS en direct pendant 20 s | *aisstream.io (temps réel, passage récent)* |
| Scène plus ancienne | **aucune comparaison** : l'AIS d'aujourd'hui ne dit rien d'un passage d'hier | *Aucune position AIS au moment du passage* — risque ×0,85 |
| Aucune clé AIS configurée, développement | AIS simulé (démonstration) | *Simulé — à confirmer* — risque ×0,85 |
| Aucune clé AIS configurée, production | aucune trace (jamais de navire fictif, qui pourrait masquer un vrai écho) | *Aucune position AIS au moment du passage* — risque ×0,85 |

## Contrôle de taille

Un écho apparié à un navire AIS n'est « expliqué » que si sa taille est **compatible** avec ce navire. Il reste **suspect** quand l'écho mesure au moins **2,5 fois** la longueur déclarée **et** au moins **50 m de plus** (marge de la mesure radar à 10 m par pixel), avec un écho mesurable (6 pixels au moins).

| Longueur déclarée | Source |
| --- | --- |
| Dimensions AIS A + B (messages statiques aisstream, AISHub) | déclaration du navire lui-même |
| Registre VesselAPI | fiche registre (navires étrangers de pêche, ou à l'ouverture de leur fiche) |

Exemple : écho de ~120 m « expliqué » par une pirogue AIS déclarée 15 m → incident « Écho incohérent avec le navire AIS déclaré », risque +10, avec dans « Pourquoi cette alerte ? » la taille de l'écho, le navire AIS, sa longueur déclarée et la distance. Deux explications à vérifier : un **second navire sans AIS** collé au navire déclaré (transbordement, escorte), ou une **identité AIS douteuse** (MMSI emprunté).

## Rayon d'appariement

```text
position projetée à T (historique) :
rayon = min( ais_max_match_km , ais_match_km + 0,25 × vitesse × 1,852 × âge de la position )
         (25 km)                 (1,5 km)

position brute non projetée (instantané en direct) :
rayon = min( ais_max_match_km , ais_match_km + vitesse × 1,852 × |écart entre la position et T| )
```

Une projection à cap et vitesse constants n'est exacte que si le navire n'a pas changé de route. Un chalutier en pêche tourne sans cesse : on garde donc une marge égale au **quart de la distance parcourue** depuis la dernière position reçue (même modèle d'erreur que la [corroboration](corroboration.md)). Exemple : une position reçue 2 h avant le passage, à 8 nœuds, donne 1,5 + 0,25 × 8 × 1,852 × 2 ≈ **8,9 km** au lieu de 1,5 km. Sans cette marge, un navire AIS qui a viré entre-temps n'était pas reconnu, et son écho apparaissait à tort « sans AIS ». Une position reçue à l'instant du passage garde le rayon de base (1,5 km).

## Appariement un pour un

Toutes les paires (écho, navire) à distance inférieure au rayon sont triées de la plus proche à la plus lointaine, puis attribuées dans cet ordre : **un navire AIS n'explique qu'un seul écho** et un écho n'est expliqué qu'une fois. Deux échos proches d'un seul navire AIS : le plus proche est expliqué, l'autre reste **sans AIS** (navire accompagnant, transbordement…).

## Autres usages

- Navires **listés INN** vus à l'AIS au moment du passage → incident *liste INN*.
- Navire de **pêche étranger** à moins de 15 km d'un écho sans AIS → raison de risque supplémentaire (flottille).
