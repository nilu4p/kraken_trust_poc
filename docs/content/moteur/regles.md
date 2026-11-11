# Règles 24/7

Sentinel-1 ne passe que quelques fois par semaine. **Chaque minute**, la plateforme applique des règles sur l'historique AIS / VMS et sur le renseignement navire. Chaque déclenchement ouvre (ou complète) un incident **rattaché au navire** et notifie le SOC. Une règle en erreur n'empêche pas les autres de tourner.

Toutes ces règles sont présentées, avec leurs paramètres actuels, leurs sources et leur bilan sur 30 jours, dans l'onglet **Règles** ; on peut y en désactiver et y composer des [règles sur mesure](regles-page.md).

## Pêche étrangère — `foreign_fishing`

| | |
| --- | --- |
| Condition | navire entendu dans la dernière heure, **dans la ZEE**, **de pêche** (type AIS 30, ou engin de pêche connu de GFW), **pavillon étranger** (MMSI non français, ou pavillon GFW hors France et outre-mer), **sans licence valide** dans les [navires autorisés](../administration/autorises.md) et **non vu au VMS** depuis 7 jours |
| Score | 85 mer territoriale (P1) · 70 zone contiguë (P2) · 60 ZEE (P2) |
| Canal | #alertes |
| Dédoublonnage | un incident par navire et par jour |

Avec un token GFW, l'identité (pavillon d'immatriculation, engins) de jusqu'à 10 navires par minute est vérifiée, et revérifiée chaque semaine.

## Liste INN — `watchlist_ais`

Navire entendu dans la dernière heure dans la ZEE et reconnu dans la [liste de surveillance](../administration/surveillance.md) **ou** signalé inscrit INN par GFW Insights. Score 90 (P1), canal #surveillance.

## Coupure AIS — `ais_gap`

| | |
| --- | --- |
| Condition | dernière position en **zone côtière** (mer territoriale ou contiguë, couverte par les récepteurs terrestres) ; silence entre `ais_gap_hours` (4 h) et 48 h ; au moins **6 positions** dans les 6 h précédant le silence (émetteur régulier) ; vitesse ≥ 0,5 nd au dernier signal (pas à quai) ; position non issue du VMS |
| Garde-fou | ne s'applique que si le récepteur entend **au moins 2 autres navires** dans les 30 dernières minutes : sinon c'est *notre* flux qui est coupé |
| Score | 75 si pêche ou étranger (P1), sinon 60 (P2) · canal #alertes |

## Rendez-vous en mer — `encounter`

| | |
| --- | --- |
| Condition | deux navires dans la ZEE, **≤ 2 nœuds**, à moins de `encounter_distance_km` (500 m) l'un de l'autre, dans au moins `encounter_min_minutes` / 10 tranches de 10 minutes sur les 3 dernières heures (60 min par défaut) |
| Exclusion | à moins de **10 km** d'un port ou d'un mouillage (Cayenne – Dégrad des Cannes, Kourou, îles du Salut) : des navires y attendent côte à côte pour des raisons ordinaires (poste à quai, pilotage, soutage). Même convention que Global Fishing Watch |
| Lecture | transbordement possible (poisson, carburant, équipage) |
| Score | 70 si l'un est de pêche ou étranger (P2), sinon 50 (P3) · canal #alertes |

## Navire sous sanctions — `sanctions`

Navire de la ZEE dont le criblage OpenSanctions (nom, IMO, MMSI) donne un score ≥ `sanctions_min_score` (0,75). Score 85 (P1), canal #surveillance. Les entités, scores et listes figurent dans la notification et la fiche navire.

## Pêche en aire protégée — `mpa_fishing`

Navire de **pêche** entendu dans la dernière heure, à une vitesse ≤ `mpa_max_speed_knots` (4 nd, vitesse de pêche), à l'intérieur d'une **aire marine protégée** WDPA. S'applique **aussi aux navires sous licence** : une licence de pêche ne couvre pas une réserve. Score 75 si étranger (P1), sinon 65 (P2), canal #alertes.

## Autres tâches de la boucle (chaque minute)

| Tâche | Fréquence |
| --- | --- |
| Contrôle des SLA | chaque minute |
| Santé des sources (alerte à 3 échecs consécutifs) | chaque minute |
| Purge des positions plus anciennes que 90 jours | chaque heure |
| Vérification de la chaîne d'audit | chaque heure |
| Veille Sentinel-1 automatique | selon `auto_scan_interval_minutes` |

## Surveillance du flux AIS

Toutes ces règles dépendent de l'AIS. Le collecteur compte les messages reçus : s'il est **connecté mais n'entend aucun navire pendant 60 minutes**, la source *aisstream* passe en état dégradé, une alerte part sur **#operations** (« couverture des récepteurs insuffisante ») et le panneau Couverture l'indique. Un résumé est écrit dans les journaux toutes les 10 minutes (`make logs backend | grep "AIS collector"`).
