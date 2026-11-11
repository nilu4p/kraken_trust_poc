# Paramètres opérationnels

<p class="where">Administration → <strong>Paramètres</strong> — administrateurs. Page générée depuis le code (<code>docs/tools/gen_parametres.py</code>).</p>

Ces réglages modifient le comportement du moteur **sans redémarrage**. Chaque modification est enregistrée, appliquée immédiatement et tracée dans l'audit (`system.settings`). La barre du haut (**Appliquer (n)**, **Annuler les modifications**) reste visible pendant le défilement. Dans l'interface, chaque paramètre affiche la même explication que ci-dessous ; **▲ Augmenter** et **▼ Diminuer** décrivent l'effet d'un changement dans chaque sens.

!!! tip "Méthode"
    Changez **un seul paramètre à la fois**, observez la file d'incidents pendant quelques jours (part de fausses alarmes dans les clôtures, incidents manqués), puis ajustez.

## Vue d'ensemble

| Paramètre | Valeur par défaut | Plage | Unité |
| --- | --- | --- | --- |
| [Veille automatique — intervalle de scan (min)](#auto-scan-interval-minutes) | 0 | 0 – 1440 | minutes |
| [Fiabilité minimale pour lever une alerte](#min-detection-quality) | 0.25 | 0.0 – 1.0 | score 0–1 |
| [Sensibilité du détecteur radar (CFAR k·σ)](#cfar-k) | 5.0 | 3.0 – 10.0 | écarts-types |
| [Rayon de corrélation des détections (km)](#incident-correlation-km) | 15.0 | 1.0 – 100.0 | km |
| [Veille multi-capteurs (optique, thermique, lumières nocturnes)](#sensor-watch-enabled) | 1 | 0 – 1 | 0 / 1 |
| [Imagerie optique — couverture nuageuse maximale de la scène (%)](#optical-max-cloud) | 40.0 | 0.0 – 100.0 | % |
| [Imagerie optique — seuil de contraste proche infrarouge (k·σ)](#optical-cfar-k) | 6.0 | 3.0 – 12.0 | écarts-types |
| [Lumières nocturnes VIIRS — seuil de contraste (k·σ)](#night-light-k) | 6.0 | 3.0 – 15.0 | écarts-types |
| [Corroboration — vitesse maximale d'association (nœuds)](#fusion-gate-speed-kn) | 15.0 | 2.0 – 40.0 | nœuds |
| [Aide à la décision — vitesse supposée d'une cible sans AIS (nœuds)](#decision-assumed-speed-kn) | 8.0 | 1.0 – 40.0 | nœuds |
| [Fenêtre de corrélation (h)](#incident-correlation-hours) | 72 | 1 – 720 | heures |
| [Recherche Sentinel-1 — jours d'historique](#stac-lookback-days) | 12 | 1 – 60 | jours |
| [Événements Global Fishing Watch — jours d'historique](#gfw-lookback-days) | 30 | 1 – 365 | jours |
| [Coupure AIS — silence avant alerte (h)](#ais-gap-hours) | 4.0 | 1.0 – 48.0 | heures |
| [Rendez-vous en mer — distance maximale (km)](#encounter-distance-km) | 0.5 | 0.1 – 5.0 | km |
| [Rendez-vous en mer — durée minimale (min)](#encounter-min-minutes) | 60 | 10 – 240 | minutes |
| [Corrélation radar/AIS — fenêtre temporelle (h)](#ais-correlation-window-h) | 3.0 | 0.5 – 12.0 | heures |
| [Front thermique — gradient minimal (°C / 10 km)](#sst-front-threshold) | 0.4 | 0.1 – 2.0 | °C par 10 km |
| [Pêche en aire protégée — vitesse maximale (nœuds)](#mpa-max-speed-knots) | 4.0 | 0.5 – 10.0 | nœuds |
| [Sanctions — score de correspondance minimal](#sanctions-min-score) | 0.75 | 0.5 – 1.0 | score 0–1 |
| [AISHub — intervalle d'interrogation (s)](#aishub-poll-seconds) | 120 | 60 – 3600 | secondes |
| [IA — détection de navires (0 arrêt, 1 observation, 2 active)](#ai-detector-mode) | 0 | 0 – 2 | mode |
| [Radar — tuiles analysées par scène](#sar-max-windows) | 24 | 0 – 2000 | tuiles de 1024 × 1024 px (≈ 105 km²) |
| [Mémoire des fausses alarmes (jours)](#false-positive-memory-days) | 60 | 1 – 3650 | jours |
| [VesselAPI — budget d'appels mensuel](#vesselapi-monthly-calls) | 150 | 0 – 1000000 | appels / mois |
| [VesselAPI — intervalle du flux de positions (min)](#vesselapi-poll-minutes) | 0 | 0 – 1440 | minutes |
| [Renseignement navire — interrogations par cycle](#intel-lookups-per-tick) | 10 | 0 – 100 | navires / minute |

## Veille automatique — intervalle de scan (min) {#auto-scan-interval-minutes}

Nom technique : `auto_scan_interval_minutes` · type entier · plage 0 – 1440 minutes · défaut **0**

Fréquence à laquelle la plateforme cherche une nouvelle scène Sentinel-1 non traitée sur la ZEE et l'analyse seule. 0 désactive la veille : seuls les scans manuels sont effectués.

- **▲ Augmenter** : Moins de requêtes vers Copernicus, mais délai plus long entre le passage du satellite et l'alerte.
- **▼ Diminuer** : Alertes plus tôt après chaque passage ; Sentinel-1 ne repasse que toutes les quelques dizaines d'heures, descendre sous 60 min n'apporte rien et consomme du quota.

!!! success "Recommandation"
    60 à 180 min en exploitation.

## Fiabilité minimale pour lever une alerte {#min-detection-quality}

Nom technique : `min_detection_quality` · type décimal · plage 0.0 – 1.0 score 0–1 · défaut **0.25**

Seuil de l'indice de fiabilité (double polarisation, contraste, forme, bathymétrie, persistance, fausses alarmes signalées). Une cible en dessous est écartée et n'ouvre pas d'incident.

- **▲ Augmenter** : Moins de fausses alarmes dans la file, au risque de manquer de petites embarcations (pirogues, tapouilles).
- **▼ Diminuer** : Remonte davantage de cibles douteuses : plus de travail de triage pour les analystes.

!!! success "Recommandation"
    0,25 par défaut ; 0,4 si la file est saturée de fausses alarmes.

## Sensibilité du détecteur radar (CFAR k·σ) {#cfar-k}

Nom technique : `cfar_k` · type décimal · plage 3.0 – 10.0 écarts-types · défaut **5.0**

Un pixel est retenu s'il dépasse la moyenne du fouillis de mer local de k écarts-types. C'est le réglage principal du compromis détection / fausses alarmes.

- **▲ Augmenter** : Seules les cibles très brillantes (coques métalliques, grands navires) restent : moins de fausses alarmes, mais les petites embarcations en bois peuvent disparaître.
- **▼ Diminuer** : Détecte des cibles plus faibles, mais le chatoiement et les vagues génèrent plus de faux échos, surtout par mer forte.

!!! success "Recommandation"
    5 par défaut ; 4 par mer calme pour les petites pêches, 6 par mer forte.

## Rayon de corrélation des détections (km) {#incident-correlation-km}

Nom technique : `incident_correlation_km` · type décimal · plage 1.0 – 100.0 km · défaut **15.0**

Une nouvelle détection à moins de cette distance d'un incident ouvert est rattachée à cet incident (nouvelle observation) au lieu d'en créer un nouveau.

- **▲ Augmenter** : Moins de doublons, mais deux navires distincts proches risquent d'être fusionnés dans le même incident.
- **▼ Diminuer** : Un même navire qui se déplace entre deux passages satellite peut ouvrir plusieurs incidents.

!!! success "Recommandation"
    10–20 km : un chalutier en pêche (3–4 nœuds) parcourt ~15 km en 2 h, mais reste souvent plusieurs jours sur le même lieu de pêche.

## Veille multi-capteurs (optique, thermique, lumières nocturnes) {#sensor-watch-enabled}

Nom technique : `sensor_watch_enabled` · type entier · plage 0 – 1 0 / 1 · défaut **1**

1 : chaque heure, la plateforme cherche les nouvelles images Sentinel-2 et Landsat 8/9 peu nuageuses sur la ZEE et les nuits VIIRS (3 satellites), y détecte les navires (contraste proche infrarouge, anomalie thermique, lumières) et garde ces détections pour corroborer les cibles radar (bloc « Corroboration multi-capteurs » de l'aide à la décision). 0 : analyses uniquement à la demande.

- **▲ Augmenter** : Davantage de cibles radar confirmées par un second capteur, sans action de l'opérateur ; quelques centaines de Mo lus par jour sur les dépôts publics.
- **▼ Diminuer** : Aucune lecture automatique : seules les analyses optiques lancées depuis l'aide à la décision d'un incident sont faites.

!!! success "Recommandation"
    1 en exploitation.

## Imagerie optique — couverture nuageuse maximale de la scène (%) {#optical-max-cloud}

Nom technique : `optical_max_cloud` · type décimal · plage 0.0 – 100.0 % · défaut **40.0**

Les scènes Sentinel-2 / Landsat plus nuageuses sont ignorées. Les nuages restants sont de toute façon masqués pixel par pixel (classification de scène / bande qualité).

- **▲ Augmenter** : Plus de scènes analysées en saison des pluies, mais moins de mer visible dans chacune.
- **▼ Diminuer** : Seules les scènes dégagées sont lues.

!!! success "Recommandation"
    40 % ; 60 % en saison des pluies.

## Imagerie optique — seuil de contraste proche infrarouge (k·σ) {#optical-cfar-k}

Nom technique : `optical_cfar_k` · type décimal · plage 3.0 – 12.0 écarts-types · défaut **6.0**

Un pixel d'eau est retenu s'il dépasse la réflectance proche infrarouge de l'eau environnante de k écarts-types (et d'au moins 0,02 en réflectance). Les eaux turbides de Guyane (panache amazonien) sont brillantes : le seuil est local.

- **▲ Augmenter** : Moins de fausses détections (écume, bancs de vase, débris), mais les petites pirogues peuvent disparaître.
- **▼ Diminuer** : Remonte des embarcations plus petites, au prix de détections parasites dans les eaux très turbides.

!!! success "Recommandation"
    6 ; 5 par eau claire au large.

## Lumières nocturnes VIIRS — seuil de contraste (k·σ) {#night-light-k}

Nom technique : `night_light_k` · type décimal · plage 3.0 – 15.0 écarts-types · défaut **6.0**

Un point lumineux isolé en mer (bande jour/nuit VIIRS, ~750 m) est retenu s'il dépasse le fond de k écarts-types. Les nuages éclairés par la lune sont écartés par la bande infrarouge thermique I5 (sommets froids).

- **▲ Augmenter** : Ne garde que les flottilles fortement éclairées (pêche au lamparo, gros navires).
- **▼ Diminuer** : Détecte des lumières plus faibles, avec davantage de reflets lunaires et d'éclairs.

!!! success "Recommandation"
    6.

## Corroboration — vitesse maximale d'association (nœuds) {#fusion-gate-speed-kn}

Nom technique : `fusion_gate_speed_kn` · type décimal · plage 2.0 – 40.0 nœuds · défaut **15.0**

Deux observations de capteurs différents (radar, optique, lumières nocturnes, GFW) sont rattachées au même navire si la distance qui les sépare peut être parcourue à cette vitesse dans l'intervalle de temps (plus les incertitudes de position de chaque capteur). Sert à la corroboration des cibles radar dans l'aide à la décision.

- **▲ Augmenter** : Associe des observations éloignées dans le temps : moins de doublons, mais risque de fusionner deux navires distincts.
- **▼ Diminuer** : Associations plus strictes : un même navire rapide peut apparaître comme plusieurs contacts.

!!! success "Recommandation"
    15 nœuds (chalutiers, tapouilles en transit) ; 25 si des embarcations rapides opèrent.

## Aide à la décision — vitesse supposée d'une cible sans AIS (nœuds) {#decision-assumed-speed-kn}

Nom technique : `decision_assumed_speed_kn` · type décimal · plage 1.0 – 40.0 nœuds · défaut **8.0**

Vitesse retenue pour une cible radar dont la route est inconnue (vue une seule fois, sans AIS) : le cercle où elle peut se trouver grandit de cette vitesse multipliée par le temps écoulé depuis l'observation. Il fixe la zone de recherche à l'arrivée d'un moyen.

- **▲ Augmenter** : Zone de recherche plus large (hypothèse prudente), options d'interception plus pessimistes.
- **▼ Diminuer** : Zone de recherche plus étroite : risque de sous-estimer le déplacement d'une embarcation rapide.

!!! success "Recommandation"
    8 nœuds (chalutier, tapouille en transit) ; 15 à 20 pour des embarcations rapides.

## Fenêtre de corrélation (h) {#incident-correlation-hours}

Nom technique : `incident_correlation_hours` · type entier · plage 1 – 720 heures · défaut **72**

Durée pendant laquelle un incident ouvert peut encore absorber de nouvelles détections proches.

- **▲ Augmenter** : Regroupe les retours d'un même navire sur plusieurs jours (campagne de pêche).
- **▼ Diminuer** : Chaque passage satellite éloigné dans le temps crée un incident distinct.

!!! success "Recommandation"
    48–96 h.

## Recherche Sentinel-1 — jours d'historique {#stac-lookback-days}

Nom technique : `stac_lookback_days` · type entier · plage 1 – 60 jours · défaut **12**

Profondeur de la recherche de scènes Sentinel-1 (liste des scènes, veille automatique).

- **▲ Augmenter** : Permet de rejouer des scènes plus anciennes (enquête a posteriori), requêtes un peu plus lourdes.
- **▼ Diminuer** : Liste plus courte, centrée sur l'actualité.

!!! success "Recommandation"
    12 jours couvre un cycle orbital complet.

## Événements Global Fishing Watch — jours d'historique {#gfw-lookback-days}

Nom technique : `gfw_lookback_days` · type entier · plage 1 – 365 jours · défaut **30**

Période des événements GFW (coupures AIS, rencontres, flânage, pêche) pris en compte sur la carte et dans le score de risque.

- **▲ Augmenter** : Contexte comportemental plus riche, mais des événements anciens peuvent surpondérer le risque.
- **▼ Diminuer** : Ne retient que les comportements récents.

!!! success "Recommandation"
    30 jours.

## Coupure AIS — silence avant alerte (h) {#ais-gap-hours}

Nom technique : `ais_gap_hours` · type décimal · plage 1.0 – 48.0 heures · défaut **4.0**

Durée de silence AIS au-delà de laquelle un navire qui émettait régulièrement (au moins 6 positions dans les 6 h précédentes) en zone côtière — mer territoriale et zone contiguë, 0 à 24 NM, à portée des récepteurs AIS terrestres — déclenche un incident « Coupure AIS ». Ignoré si notre propre flux AIS est coupé.

- **▲ Augmenter** : Moins d'alertes (pannes d'émetteur, sorties de couverture), mais une coupure volontaire courte passe inaperçue.
- **▼ Diminuer** : Repère des extinctions brèves, au prix de faux positifs liés aux trous de couverture radio.

!!! success "Recommandation"
    3 à 6 h.

## Rendez-vous en mer — distance maximale (km) {#encounter-distance-km}

Nom technique : `encounter_distance_km` · type décimal · plage 0.1 – 5.0 km · défaut **0.5**

Distance en dessous de laquelle deux navires lents (≤ 2 nœuds) sont considérés « au contact » pour la règle de transbordement.

- **▲ Augmenter** : Détecte plus de rapprochements, y compris de simples croisements lents : plus de bruit.
- **▼ Diminuer** : Ne retient que les accostages bord à bord, signature typique d'un transbordement.

!!! success "Recommandation"
    0,3 à 0,5 km.

## Rendez-vous en mer — durée minimale (min) {#encounter-min-minutes}

Nom technique : `encounter_min_minutes` · type entier · plage 10 – 240 minutes · défaut **60**

Temps cumulé (sur les 3 dernières heures) pendant lequel les deux navires doivent rester au contact pour ouvrir un incident « Rendez-vous en mer ». Un transbordement de poisson dure en général plusieurs heures.

- **▲ Augmenter** : Ne garde que les transbordements longs (cargaison importante), moins de faux positifs.
- **▼ Diminuer** : Signale des contacts brefs (échange d'équipage, de carburant), mais aussi de simples croisements lents et le chalutage en bœuf.

!!! success "Recommandation"
    60 à 120 min (Global Fishing Watch retient 2 h avec l'AIS satellite ; les trous de couverture de l'AIS côtier justifient un seuil plus bas).

## Corrélation radar/AIS — fenêtre temporelle (h) {#ais-correlation-window-h}

Nom technique : `ais_correlation_window_h` · type décimal · plage 0.5 – 12.0 heures · défaut **3.0**

Écart maximal entre une position AIS enregistrée et le passage du satellite pour qu'elle serve, extrapolée selon le cap et la vitesse, à « expliquer » un écho radar.

- **▲ Augmenter** : Plus de navires AIS utilisables, mais extrapolation moins précise : risque d'attribuer un écho sans AIS à un navire coopératif.
- **▼ Diminuer** : Corrélation plus stricte et plus fiable, mais un navire au signal espacé peut être compté à tort comme « sans AIS ».

!!! success "Recommandation"
    2 à 3 h.

## Front thermique — gradient minimal (°C / 10 km) {#sst-front-threshold}

Nom technique : `sst_front_threshold` · type décimal · plage 0.1 – 2.0 °C par 10 km · défaut **0.4**

Gradient horizontal de température de surface (Copernicus Marine OSTIA) à partir duquel un point est considéré sur un front thermique. Les fronts concentrent le plancton et les poissons : les flottilles de pêche s'y regroupent. Une cible radar sur un front gagne des points de risque.

- **▲ Augmenter** : Seuls les fronts marqués sont retenus : bonus de risque plus rare et plus significatif.
- **▼ Diminuer** : Davantage de zones qualifiées de front, le bonus perd de sa valeur discriminante.

!!! success "Recommandation"
    0,3 à 0,5 °C/10 km au large de la Guyane.

## Pêche en aire protégée — vitesse maximale (nœuds) {#mpa-max-speed-knots}

Nom technique : `mpa_max_speed_knots` · type décimal · plage 0.5 – 10.0 nœuds · défaut **4.0**

Un navire de pêche dans une aire marine protégée (WDPA) à une vitesse inférieure ou égale est considéré en action de pêche (chalutage 2–4 nd, palangre, filet) et ouvre un incident « Pêche en aire protégée ». Au-dessus, il est considéré en transit.

- **▲ Augmenter** : Inclut des navires en transit lent : plus d'alertes, plus de faux positifs.
- **▼ Diminuer** : Ne retient que les navires quasi stationnaires (filets, casiers), un chalutier peut passer inaperçu.

!!! success "Recommandation"
    4 nœuds (vitesse de chalutage).

## Sanctions — score de correspondance minimal {#sanctions-min-score}

Nom technique : `sanctions_min_score` · type décimal · plage 0.5 – 1.0 score 0–1 · défaut **0.75**

Score de similarité OpenSanctions (nom, IMO, MMSI, pavillon) au-dessus duquel un navire présent dans la ZEE est signalé comme sous sanctions internationales.

- **▲ Augmenter** : Ne garde que les correspondances quasi certaines (IMO identique) : très peu de faux positifs.
- **▼ Diminuer** : Remonte des homonymes (noms de navires très courants) : vérification manuelle nécessaire.

!!! success "Recommandation"
    0,75 ; 0,9 si les homonymes saturent la file.

## AISHub — intervalle d'interrogation (s) {#aishub-poll-seconds}

Nom technique : `aishub_poll_seconds` · type entier · plage 60 – 3600 secondes · défaut **120**

Fréquence à laquelle le second flux AIS (AISHub) est interrogé sur la ZEE. AISHub refuse plus d'un appel par minute.

- **▲ Augmenter** : Moins de charge, positions AISHub plus espacées (les règles et la corrélation radar restent alimentées par aisstream).
- **▼ Diminuer** : Image AIS plus fraîche ; ne pas descendre sous 60 s (réponse vide).

!!! success "Recommandation"
    60 à 120 s.

## IA — détection de navires (0 arrêt, 1 observation, 2 active) {#ai-detector-mode}

Nom technique : `ai_detector_mode` · type entier · plage 0 – 2 mode · défaut **0**

Avis d'un modèle open source (YOLOv8n entraîné sur des images radar, ONNX) sur chaque cible détectée par le CFAR. 1 = observation : l'avis est affiché et scellé, et son accord avec les verdicts des opérateurs est mesuré (Administration › Paramètres), sans effet sur les scores. 2 = actif : une cible confirmée par l'IA gagne en fiabilité, une cible qu'elle ne voit pas en perd, et les détections très sûres de l'IA seule deviennent des cibles signalées comme telles. Licence du modèle à valider avant activation (fiche MIT, fichier exporté sous AGPL-3.0 Ultralytics).

- **▲ Augmenter** : L'IA pèse sur les alertes : à n'activer qu'après une période d'observation montrant un bon accord avec les opérateurs (au moins 30 incidents jugés).
- **▼ Diminuer** : Aucune influence de l'IA : détection purement physique (CFAR + fiabilité).

!!! success "Recommandation"
    1 (observation) pendant quelques semaines, puis 2 si la précision mesurée dépasse 0,8.

## Radar — tuiles analysées par scène {#sar-max-windows}

Nom technique : `sar_max_windows` · type entier · plage 0 – 2000 tuiles de 1024 × 1024 px (≈ 105 km²) · défaut **24**

Nombre maximal de tuiles de la scène Sentinel-1 analysées dans la ZEE, réparties uniformément. Une scène couvre souvent 200 à 400 tuiles de ZEE : le message de fin de scan indique la part réellement analysée. 0 = toute la partie ZEE de la scène.

- **▲ Augmenter** : Couverture plus complète (moins de navires manqués), scan plus long et plus de lectures S3 (≈ 1 à 3 s par tuile).
- **▼ Diminuer** : Scan plus rapide, mais l'essentiel de la ZEE n'est pas regardé : une absence d'alerte ne prouve pas l'absence de navire.

!!! success "Recommandation"
    24 en démonstration ; 0 (tout) en exploitation si le worker de scan le permet.

## Mémoire des fausses alarmes (jours) {#false-positive-memory-days}

Nom technique : `false_positive_memory_days` · type entier · plage 1 – 3650 jours · défaut **60**

Durée pendant laquelle un incident clos en « fausse alarme » abaisse la fiabilité des nouvelles détections à moins de 250 m (−0,45). Passé ce délai, le lieu redevient surveillé normalement.

- **▲ Augmenter** : Moins de fausses alarmes répétées sur les objets fixes, mais un navire qui revient plus tard au même endroit risque d'être écarté.
- **▼ Diminuer** : Les lieux signalés à tort redeviennent vite surveillés ; les fausses alarmes récurrentes (épave, bouée) reviennent plus souvent.

!!! success "Recommandation"
    60 jours.

## VesselAPI — budget d'appels mensuel {#vesselapi-monthly-calls}

Nom technique : `vesselapi_monthly_calls` · type entier · plage 0 – 1000000 appels / mois · défaut **150**

Nombre maximal d'appels VesselAPI par mois civil (UTC), fiches registre et flux de positions confondus. À aligner sur le quota de l'offre souscrite (gratuite 150, Basic 1 500, Starter 15 000) : la plateforme s'arrête d'elle-même au budget, et plus tôt si le fournisseur annonce un quota restant inférieur. 0 désactive VesselAPI.

- **▲ Augmenter** : Plus de navires étrangers décrits par le registre et flux de positions possible, à condition que l'offre le couvre (sinon refus HTTP 429 du fournisseur).
- **▼ Diminuer** : Réserve des appels, mais les navires de passage restent sans fiche registre.

!!! success "Recommandation"
    Le quota exact de l'offre souscrite.

## VesselAPI — intervalle du flux de positions (min) {#vesselapi-poll-minutes}

Nom technique : `vesselapi_poll_minutes` · type entier · plage 0 – 1440 minutes · défaut **0**

Fréquence d'interrogation des positions AIS VesselAPI sur la ZEE (3e flux AIS, ajouté aux deux autres dans le même historique). Chaque interrogation coûte 1 à 5 appels (une page de résultats par appel) ; le flux s'interrompt quand il reste moins de 30 % du budget mensuel, réservé aux fiches registre. 0 = flux désactivé.

- **▲ Augmenter** : Économise le quota, positions VesselAPI plus espacées.
- **▼ Diminuer** : Image plus fraîche, quota consommé plus vite : à 30 min, 1 440 interrogations par mois, soit 1 440 à 7 200 appels.

!!! success "Recommandation"
    0 avec l'offre gratuite ; 120 à 240 avec Basic (1 500 appels, dont 1 050 utilisables par le flux) ; 15 à 30 avec Starter.

## Renseignement navire — interrogations par cycle {#intel-lookups-per-tick}

Nom technique : `intel_lookups_per_tick` · type entier · plage 0 – 100 navires / minute · défaut **10**

Nombre maximal de navires enrichis à chaque cycle de surveillance (GFW Insights + OpenSanctions + registre VesselAPI pour les navires étrangers). Chaque navire est revérifié au plus une fois par semaine. 0 désactive l'enrichissement.

- **▲ Augmenter** : Toute la flotte présente est criblée plus vite, au prix de quotas API consommés plus rapidement.
- **▼ Diminuer** : Économise les quotas, mais un navire sanctionné de passage peut quitter la zone avant d'être criblé.

!!! success "Recommandation"
    10 (≈ 600 navires / heure).
