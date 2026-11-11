# KRAKEN-Trust — SOC maritime de la ZEE de Guyane

Plateforme souveraine de détection et de traitement de la pêche illégale (INN / *dark vessels*) dans la ZEE de Guyane française, organisée comme un **SOC** : détecter → qualifier → escalader → répondre → clore, avec des preuves scellées vérifiables (C2PA signé par la PKI de l'instance, chaîne de possession tracée).

| Brique | Rôle |
| --- | --- |
| **kraken-engine** (`backend/`) | Pipeline Sentinel-1 (STAC + S3), détection SAR, fiabilité, corrélation AIS, score de risque, scellement C2PA, gestion d'incidents, coffre de preuves, IAM, audit |
| **Console KRAKEN** (`frontend/`) | Console opérateur React : situation tactique, file d'incidents, preuves, audit, administration |

## Démarrage

```bash
cp backend/.env.example .env      # renseigner au minimum ACCESS_KEY / SECRET_KEY (ou plus tard via l'UI)
make up                           # build + démarrage (backend non exposé, console sur 127.0.0.1:8080)
make admin-password               # mot de passe du tout premier administrateur (affiché une seule fois)
```

Ouvrir https://localhost (ou https://<votre domaine>, voir « Sécurité »), se connecter avec `admin` et le mot de passe temporaire : un nouveau mot de passe est exigé à la première connexion. Créer ensuite les comptes opérateurs dans **Administration → Utilisateurs**.

Développement local : `make test` (tests backend, dans l'image de test si aucun `.venv` ; l'image de production ne contient pas les outils de test), `make venv` puis `make dev` (uvicorn :8000 + Vite :5173 hors Docker).

> Les volumes `keys/ preuves/ cache/ data/` sont montés dans un conteneur non-root (UID 1000 par défaut, `APP_UID=…` sinon). Si un ancien conteneur root a créé des fichiers : `sudo chown -R $(id -u):$(id -g) keys preuves cache data`.

## Architecture

| Service | Rôle |
| --- | --- |
| `backend` | API FastAPI (migre la base au démarrage, sonde `/health/ready`) |
| `postgres` | PostgreSQL 16 + **TimescaleDB** (positions AIS en hypertable compressée) |
| `redis` | État partagé entre processus (scan, santé des sources, verrous, anti-répétition, limitation de débit) et broker Celery |
| `worker` | Celery : règles 24/7, SLA, santé des sources, maintenance, notifications avec nouvelles tentatives |
| `worker-scan` | Celery : analyses Sentinel-1, une à la fois, processus renouvelé après chaque scan |
| `beat` | Planificateur Celery (cycles périodiques) |
| `collector` | Collecte AIS continue (aisstream.io, AISHub, VesselAPI) |
| `console`, `docs`, `notif-sink` | Console nginx, documentation, messagerie de démonstration |

- **Migration automatique** : au premier démarrage, l'ancienne base SQLite `data/kraken.db` est copiée dans PostgreSQL (identifiants, références et chaîne d'audit conservés) puis renommée `kraken.db.migrated-<date>`.
- **Secrets** : `make up` génère les mots de passe PostgreSQL / Redis dans `secrets/` (non versionné, secrets Docker) ; ces services ne sont pas exposés.
- **Montée en charge** : `docker compose up -d --scale worker=3` ; `CELERY_BROKER_URL=amqp://…` pour RabbitMQ.
- **Mode simple** : sans `DATABASE_URL` ni `REDIS_URL` (`BACKGROUND_MODE=inline`), la plateforme tourne dans un seul processus sur SQLite (développement, tests).
- **Tests** : `make test` (SQLite) et `make test-pg` (même suite sur une base PostgreSQL de test dédiée).

Détails : documentation, page *Architecture technique*.

## Documentation (base de connaissances)

Toute la plateforme est expliquée pour les utilisateurs sur **/docs/** de la console (onglet *Documentation* du bandeau, et liens « ? » contextuels) : légende symbole par symbole, panneau latéral et options d'affichage, fiche cible, incidents (statuts, transitions, sévérités, SLA), preuves C2PA, audit, administration, formules exactes (CFAR, fiabilité, corrélation AIS, score de risque, règles 24/7, fusion), sources, notifications, sécurité, commandes `make`, glossaire et FAQ. Recherche plein texte et infobulles sur les sigles.

- Service Docker `docs` séparé (MkDocs Material construit en site statique, nginx non-root), servi par la console sous `/docs/` : même origine, pas de port supplémentaire.
- **Protégée par le portail de connexion** : nginx vérifie la session KRAKEN sur chaque page et fichier (`auth_request` → `/api/v1/auth/check`) ; sans session, renvoi vers la connexion puis retour à la page demandée.
- Pages Markdown dans `docs/content/` ; `make docs` reconstruit et redéploie. La construction est stricte : un lien ou une ancre cassés la font échouer.
- La page *Paramètres opérationnels* est générée depuis le code (`make docs-params`) ; `backend/tests/test_docs.py` échoue si un paramètre, un type d'incident ou un canal de notification n'est pas documenté.

## Chaîne SOC

| Étape | Dans l'application | Rôle minimum |
| --- | --- | --- |
| **Détecter** | Scan manuel ou **veille automatique** des nouvelles scènes Sentinel-1 ; chaque cible fiable ouvre un incident (ou s'ajoute à un incident proche : corrélation 15 km / 72 h) | — |
| **Trier** | File d'incidents par sévérité P1-P4 et SLA ; « Prendre en charge » (accusé + auto-affectation) ; fausse alarme / légitime / doublon classables dès le triage | Analyste |
| **Qualifier** | Playbook (vignette SAR, AIS/GFW, imagerie Pléiades/S2, lumières VIIRS, identification), notes, observations corrélées | Analyste |
| **Escalader** | Saisine d'une autorité de l'Action de l'État en Mer (CROSS AG, Préfet AEM, Marine nationale, DM Guyane, Gendarmerie maritime, Douanes, Parquet) avec référence | Superviseur |
| **Décider** | **Aide à la décision** : position estimée de la cible maintenant, sortie de ZEE prévue, délai de chaque moyen pour être sur zone et lieu d'interception, option recommandée et cadre juridique (CNUDM) | Observateur |
| **Répondre** | « Intervention engagée », suivi, export du dossier de preuves | Superviseur |
| **Rendre compte** | **Compte rendu d'incident** et **SITREP** au format message (GDH, destinataires, mention de protection), signés ECDSA par la PKI de l'instance, registre, vérification, impression / PDF | Analyste (SITREP : superviseur) |
| **Clore** | Résolution obligatoire (INN confirmée, légitime, fausse alarme, doublon, sans suite) ; réouverture possible | Superviseur |

- **SLA** par sévérité (triage P1 30 min → P4 24 h ; résolution P1 24 h → P4 14 j), dépassements signalés une fois dans la chronologie et notifiés.
- **KPI** : ouverts par sévérité, hors SLA, non assignés, MTTA / MTTR 30 j, détections 7 j (bandeau supérieur).
- **Notifications par canal** (format Slack / Mattermost : fiche détaillée, couleur par sévérité, boutons « Ouvrir l'incident », « Situation tactique », « Dossier de preuves ») :

  | Canal | Contenu |
  | --- | --- |
  | `#alertes` | nouveaux incidents P1/P2 (risque et ses raisons, fiabilité et ses signaux, position en DMS, taille, polarisation, échéance), sévérité relevée par une nouvelle observation |
  | `#escalades` | saisine d'une autorité AEM (référence, éléments transmis), intervention engagée, clôture avec résolution, réouverture |
  | `#sla` | échéance de triage ou de résolution dépassée (une seule fois par étape) |
  | `#surveillance` | navire de la liste INN visible à l'AIS dans la ZEE (MMSI, motif d'inscription, pavillon, vitesse) |
  | `#operations` | bilan de chaque scan (scène, cibles, écartées, navires sans AIS, nouveaux incidents) ou cause de l'échec ; panne / rétablissement d'une source |
  | `#securite` | compte verrouillé, admin créé ou élevé, clés modifiées, mode secours, réinitialisation, restauration, journal d'audit altéré (contrôle horaire) |

  Routage : `{channel}` (ou `__CHANNEL__`) dans `ALERT_WEBHOOK_URL` → une URL par canal ; sinon URL unique et champ `channel` (`kraken-<canal>`) pour Mattermost. Anti-rafale sur les événements répétitifs. Les images de preuve ne sont jamais envoyées (elles restent derrière l'authentification).
- **Serveur de notifications local (PoC)** : `notif-sink` imite Slack / Mattermost sur **/notifs/** (via la console, connexion exigée) (canaux, messages non lus, recherche, boutons d'action). Branché par défaut (`http://notif-sink:8090/hooks/__CHANNEL__`). Bouton « Tester » du webhook (Administration) : un message de test par canal.
- **Boucle de fiabilité** : une fausse alarme close par un opérateur dégrade automatiquement toute détection future au même endroit.

## Corroboration multi-capteurs

Dans l'aide à la décision de chaque incident, bloc **Corroboration multi-capteurs** : un autre capteur a-t-il vu la cible radar ? (détails : documentation, *Moteur et calculs › Corroboration multi-capteurs* et *Détection optique et nocturne*).

- **Radar** : ambiguïtés azimutales et lobes secondaires écartés à l'échelle de la scène (deux sources classiques de faux échos Sentinel-1).
- **Optique** : Sentinel-2 10 m (Earth Search) et Landsat 8/9 30 m (Planetary Computer), sans clé. CFAR proche infrarouge sur l'eau dégagée, puis filtres physiques : SWIR (coque contre front de turbidité / écume), ombre de cumulus, marge nuageuse 150 m, coques classées « nuage » récupérées, jetées et structures OSM, doublons entre tuiles. Validé sur images réelles (mouillage de Gibraltar, approches de Cayenne).
- **Thermique** : température de brillance Landsat TIRS (K) ; anomalie chaude = indice, sommet froid = nuage écarté.
- **Lumières nocturnes** : VIIRS jour/nuit des 3 satellites (NASA GIBS), palette décodée en radiance physique, écran nuageux par la bande thermique I5.
- **Rapprochement** : pistes AIS/VMS qui « expliquent » les détections, puis pistage des contacts sans AIS (porte de vitesse réglable), confiance P = 1 − Π(1 − p) par capteur ; statuts *sans AIS corroboré*, *à confirmer*, *identifié*. Un écho faible corroboré n'est plus douteux pour l'aide à la décision ; le compte rendu reprend chaque observation.
- **Veille automatique horaire** des nouvelles scènes et nuits (`sensor_watch_enabled`), et analyse optique à la demande autour d'une cible (bouton de l'aide à la décision).

## Commandement : aide à la décision et rapports

Onglet **Commandement** et panneau **Aide à la décision** de chaque incident (détails : documentation, section *Commandement*).

- **Cible maintenant** : dernière position projetée à cap et vitesse constants — AIS du navire relu en direct pour les incidents ouverts sur un MMSI, cinématique estimée entre deux observations radar, sinon *datum* dont le rayon croît à la vitesse supposée (paramètre `decision_assumed_speed_kn`).
- **Projection** : délai et point de sortie de la ZEE (précision ~1 min), ou approche de la côte.
- **Moyens** (patrouilleurs, vedettes, aéronefs : point de départ en mer, vitesse, distance franchissable, délai d'appareillage, capacité d'arraisonnement, disponibilité) : interception résolue exactement contre la route de la cible, réalisable ou non avec ses réserves (rayon d'action, arrivée après la sortie de ZEE, point hors eaux françaises).
- **Options** (interception, levée de doute aérienne, surveillance, signalement / coopération) avec l'option recommandée et son motif, rappel du régime juridique de la zone et du droit de poursuite.
- **Rapports** : projet recalculé à la demande, puis émission qui fige le texte, le signe (ECDSA, certificat de l'instance), l'enregistre au registre (`CR-…`, `SITREP-…`), l'inscrit dans l'audit et la chronologie de l'incident ; export texte avec bloc de scellement, impression A4, vérification d'intégrité.

## Surveillance continue (entre deux passages satellite)

Sentinel-1 ne survole la ZEE que quelques fois par semaine. Entre deux passages, la plateforme surveille en continu à partir de l'AIS et de Global Fishing Watch :

| Flux | Apport |
| --- | --- |
| **Collecteur AIS continu** (aisstream.io, classes A et B) | Image AIS 24/7 historisée (`ais_positions`, rétention 90 j) : la corrélation radar/AIS utilise la position **au moment exact du passage** (extrapolée selon cap et vitesse), et non un instantané pris au moment du scan |
| **Pavillon par MMSI** (codes MID de l'UIT, sans appel externe) | 745 = Guyane, 710 = Brésil, 765 = Suriname, 775 = Venezuela… |
| **GFW identité** | Pavillon d'immatriculation et engin de pêche |
| **GFW 4Wings SAR** | Détections Sentinel-1 de GFW : confirmation croisée de nos cibles, zones de détections récurrentes sans AIS |
| **GFW 4Wings effort** | Heures de pêche AIS par pavillon sur 7, 30 ou 90 j (carte de chaleur, orange = pavillon étranger) |
| **Rapport d'activité de la ZEE** (onglet *Activité*) | Comme le rapport de zone de la carte GFW : sur 30 j à 12 mois, heures par jour et pavillon, engins, navires les plus actifs (liste INN, licence), escales dans les ports |
| **GFW escales au port** | Navires arrêtés dans les ports de la zone (événement « Escale au port ») |
| **Isobathes** (ETOPO, hors ligne) | Courbes 20 à 4 000 m, rebord du plateau à 200 m |
| **AISHub** (2e flux AIS) | Autres stations côtières, fusionnées dans le même historique (une position entendue par les deux flux n'est stockée qu'une fois ; chaque position garde sa source) |
| **VMS importé** (NAF ou CSV) | Balises des navires sous licence : expliquent des échos radar, exclues de la règle pêche étrangère |
| **GFW Insights** | Par navire, sur 12 mois : inscription INN des ORGP, pêche en AMP intégrale ou sans autorisation ORGP, coupures AIS, changements de pavillon |
| **OpenSanctions** | Criblage sanctions (OFAC, UE, ONU, UK…) par IMO / MMSI / nom |
| **VesselAPI** | Fiche registre des navires étrangers (IMO, type, année, tonnage, anciens noms) avant criblage ; 3e flux AIS optionnel. Budget d'appels mensuel strict (offre gratuite : 150 / mois) |
| **WDPA** (aires marines protégées) | Grand Connétable, Amana, réserves côtières voisines |
| **Copernicus Marine** | Courants, température de surface (fronts thermiques), chlorophylle quotidienne |

**Règles 24/7** (chaque minute, incidents corrélés par navire, notifiés au SOC) :

| Règle | Déclencheur | Sévérité |
| --- | --- | --- |
| Pêche étrangère | navire de pêche (type AIS 30 ou engin GFW) sous pavillon étranger dans la ZEE | P1 en mer territoriale, P2 au-delà |
| Liste INN | navire de la liste de surveillance émettant dans la ZEE | P1 |
| Coupure AIS | navire émettant régulièrement en zone côtière puis silencieux > 4 h, alors que le récepteur capte toujours les autres navires | P2 (P1 si pêche / étranger) |
| Rendez-vous en mer | deux navires ≤ 2 nœuds à moins de 500 m pendant ≥ 60 min, à plus de 10 km des ports et mouillages (transbordement possible) | P2 si pêche / étranger, sinon P3 |
| Navire sous sanctions | correspondance OpenSanctions ≥ 0,75 pour un navire émettant dans la ZEE | P1 |
| Liste INN (GFW) | GFW Insights signale une inscription sur une liste INN d'ORGP | P1 |
| Pêche en aire protégée | navire de pêche à ≤ 4 nœuds dans une aire marine protégée (WDPA) | P2 (P1 si étranger) |

**Graphe d'entités dans le temps** : onglet *Graphe* — navires, incidents, échos radar, liste de surveillance et sanctions reliés par des liens datés et prouvés (proximité AIS réelle, navires proches d'un écho au passage radar, rendez-vous, observations) ; fenêtre jusqu'à 90 jours terminée maintenant ou à un instant passé, extension depuis n'importe quelle entité, lecture animée des liens dans l'ordre où ils sont apparus.

**Règles de détection explicables** : onglet *Règles* — chaque règle intégrée décrite en clair (pourquoi, déclencheurs, effet, limites), ses paramètres actuels, ses sources et son bilan 30 j (part de fausses alarmes) ; activation par un superviseur. **Règles sur mesure** sans programmation (zone, pavillon, type, vitesse, silence AIS, aire protégée, cercle, nuit, licence, listes…), lues comme une phrase et **testées avant activation**, y compris à un instant passé. Chaque incident garde **« Pourquoi cette alerte ? »** : règle, conditions avec valeurs observées, paramètres en vigueur.

**Supervision technique** (Administration) : sources, couverture de la collecte AIS, positions rejetées, durée des scans, cycle des règles, temps de réponse de l'API par domaine, base et disque. Alerte **#operations** quand la collecte AIS tombe sous 50 % des 30 dernières minutes. Contrôle de **taille** : un écho radar bien plus grand que le navire AIS qui l'« explique » reste suspect. Règles sur mesure avec **« ou »** et **zones de surveillance dessinées** sur la carte.

**Fiabilité des données** : journal de collecte (minutes d'écoute de chaque flux AIS) — un écho n'est déclaré « sans AIS » que si l'AIS était écouté au passage, et les périodes sans collecte sont hachurées sur la frise ; horodatages normalisés en ISO 8601 UTC ; positions aberrantes (horloge, saut impossible > 50 nd, 0°/0°) refusées et comptées.

**Imagerie synchronisée** : GOES-Est toutes les 10 min (nuages, toute la ZEE) et Sentinel-2 à 10 m (dernière image avant l'instant), au même instant que le reste de la situation.

**IA open source (second avis, optionnel)** : modèle ONNX de détection de navires radar (Hugging Face), en mode observation mesuré contre les verdicts des opérateurs avant toute influence sur les alertes — voir `docs/content/moteur/ia.md`.

**Chronologie et rejeu** : un instant de référence unique pilote toute la situation — **direct** ou **rejeu** jusqu'à 90 jours (frise d'activité par source, lecture accélérée). Au rejeu de T : AIS avant T, cibles déjà scellées à T, incidents au statut de T, événements GFW, océan et météo de T. Le calcul des alertes applique la même règle : une scène est évaluée avec le contexte de l'heure de son passage (météo par cible, océan du jour, événements GFW antérieurs).

**Fusion multi-sources** : chaque navire présent dans la ZEE est enrichi automatiquement (10 navires par minute, revérifiés chaque semaine) par GFW Insights, le registre VesselAPI (navires étrangers de pêche ou de type non déclaré) et OpenSanctions ; le résultat apparaît dans la **fiche fusion** (clic sur un navire AIS) avec les flux qui l'entendent, sa licence VMS et ses incidents. Les cibles radar gagnent des points de risque sur un **front thermique** ou dans une **aire protégée**.

Les seuils se règlent dans Administration → Paramètres. Le panneau **Couverture temporelle** de la vue Situation montre la fraîcheur de chaque flux (dernier scan radar, signaux AIS par heure, dernière exécution des règles, GFW).

Prérequis : `AISSTREAM_API_KEY` et `GFW_TOKEN` (gratuits) renseignés dans Administration → Clés & intégrations. Sans eux, la plateforme reste limitée aux passages radar. `AISHUB_USERNAME`, `OPENSANCTIONS_API_KEY` et `VESSELAPI_API_KEY` sont optionnels ; Copernicus Marine et WDPA fonctionnent sans clé.

## Moteur de détection et de fiabilité

1. **STAC** — scène Sentinel-1 la plus récente (ou choisie), requêtes mises en cache avec réessais (limite 429).
2. **SAR** — géoréférencement GCP, tuiles dans la ZEE, **CA-CFAR local** sur la polarisation croisée, **confirmation en co-polarisation**, extraction de blobs (taille, allongement, SNR local), masquage terre / hors ZEE.
3. **Fiabilité 0-1** — double polarisation, SNR, forme, bord de fauchée, état de la mer, **bathymétrie** (terre émergée, hauts-fonds), **cible persistante** (bouée, plateforme, épave), **fausses alarmes signalées**. Sous le seuil (réglable) : pas d'alerte.
4. **AIS** — corrélation 1-pour-1, rayon élargi selon la vitesse et l'écart temporel AIS ↔ passage satellite.
5. **Risque 0-100 explicable** — zone légale, limites de pêche SHOM, événements GFW, liste INN, eaux productives, taille, source AIS, pondéré par la fiabilité → sévérité P1-P4.
6. **Preuve** — planche + vignette par cible, manifeste **C2PA signé X.509** (hash de chaque vignette), sidecar SHA-256.

## Sécurité

| Domaine | Mesure |
| --- | --- |
| Identité | Comptes nominatifs, rôles cumulatifs (observateur < analyste < superviseur < administrateur), admin initial à mot de passe aléatoire à changer |
| Mots de passe | scrypt (N=2¹⁵, sel aléatoire), politique 12+ caractères / 3 classes / pas d'identifiant, réinitialisation par mot de passe temporaire |
| Connexion | Message d'erreur générique, temps constant (pas d'énumération), verrouillage après 5 échecs (15 min), limitation 10/min par IP et par compte |
| Sessions | Jeton opaque 256 bits en cookie `HttpOnly; SameSite=Strict` (+ `Secure` avec `COOKIE_SECURE=true`), seul le SHA-256 est stocké, expiration absolue 8 h + inactivité 30 min, révocation au changement de mot de passe / de rôle |
| CSRF | Jeton par session exigé (`X-CSRF-Token`) sur toute requête modifiante |
| Autorisation | RBAC sur chaque endpoint, règles métier (escalade / clôture réservées superviseur) |
| Secrets | API keys et webhook **chiffrés** (Fernet) sous une clé maître tenue hors du volume `keys/` (secret Docker `secrets/master_key`, ou `MASTER_KEY`) ; clés privées PKI chiffrées PKCS#8 ; jamais renvoyés ni journalisés |
| Preuves | Plus de partage statique public : accès authentifié, chemins validés (anti-traversal), chaque consultation / vérification / export tracé = **chaîne de possession** |
| Audit | Journal append-only **chaîné SHA-256** (toute altération détectée, vérifiable dans l'UI ou `make verify-audit`) |
| Sortant | Webhook : HTTPS uniquement, hôtes publics (anti-SSRF), sans redirection |
| HTTP | CSP stricte (front et API), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, HSTS (si TLS), CORS liste blanche sans `*`, docs API désactivées en production, rate-limit global |
| Conteneurs | Utilisateurs non-root, système de fichiers en lecture seule, `no-new-privileges`, `cap_drop: ALL`, API liée à 127.0.0.1, nginx sans version |

La console est servie en **HTTPS uniquement** (nginx : les ports 80 et 8080 ne font que répondre au défi Let's Encrypt et rediriger vers 443, HSTS, cookies `Secure`). Par défaut elle n'est publiée que sur cette machine (`https://localhost`, certificat auto-signé). **Hébergement sur un serveur** : faire pointer un nom de domaine (enregistrement A) vers l'IP publique, ouvrir les ports 80 et 443, et 8080 si d'anciens liens y pointent (pare-feu du fournisseur, rien d'autre), copier `.env.example` en `.env` (`DOMAIN`, `LETSENCRYPT_EMAIL`, `CONSOLE_BIND=0.0.0.0`) puis `make up` : le service `certbot` obtient le certificat (≈ 1 min, `docker compose logs certbot`) et le renouvelle automatiquement, nginx le recharge sans coupure. Le serveur de notifications PoC est consultable sur `/notifs/` (connexion à la plateforme exigée) ; son port 8090 reste local. La clé maître est un secret Docker (`secrets/master_key`, généré par `make secrets`, ou `MASTER_KEY` depuis un gestionnaire de secrets) : elle n'est **pas** dans les sauvegardes. **Copiez `secrets/master_key` hors de la machine** : sans elle, les secrets chiffrés et les sauvegardes sont illisibles.

## Sources de données

Le catalogue complet (sources actives, à clé, ou à accès restreint avec leur procédure) est consultable dans **Administration → Catalogue des sources**.

| Source | Accès | Usage |
| --- | --- | --- |
| Copernicus Sentinel-1 (STAC + S3) | compte gratuit | Détection SAR double polarisation |
| **CNES GEODES** (PEPS Sentinel-1/2, THEIA S2 L2A, WaterQual) | libre (recherche + aperçus) | Imagerie et **aperçus réels** autour de chaque cible |
| Copernicus CCM optique (**Pléiades / Pléiades Neo / SPOT** / WorldView) | recherche libre, téléchargement réservé | Identification visuelle THR |
| Copernicus CCM radar (TerraSAR-X, COSMO-SkyMed, PAZ, ICEYE, RADARSAT) | recherche libre, téléchargement réservé | Confirmation radar THR, nuit et nuages |
| NASA GIBS (VIIRS, Day/Night Band) | libre | Lumières nocturnes des flottilles |
| **SHOM** — limites de pêche 100 NM (R951-14) et 3 NM | libre (licence ouverte) | Couche réglementaire, distances dans le risque |
| Marine Regions — ZEE, 24 NM, 12 NM | libre | Zonage légal, masque terre |
| **NOAA ERDDAP** — bathymétrie ETOPO, chlorophylle VIIRS (secours) | libre | Hauts-fonds (faux positifs), eaux productives |
| **Copernicus Marine** (Mercator Ocean) — WMTS public | libre, sans compte | Courants, SST et fronts thermiques, chlorophylle : contexte des cibles et surcouches carte |
| **WDPA** (UNEP-WCMC / UICN) — aires marines protégées | libre | Couche carte, règle pêche en aire protégée, score de risque |
| Open-Meteo | libre | État de mer → fiabilité SAR |
| Global Fishing Watch | clé gratuite | Coupures AIS, rencontres, flânage, pêche |
| Global Fishing Watch Insights | même token | Indicateurs de risque INN par navire |
| aisstream.io | clé gratuite | AIS temps réel |
| **AISHub** | gratuit si partage d'une station AIS | 2e flux AIS fusionné |
| **OpenSanctions** | clé gratuite (intérêt public) | Navires sous sanctions |
| **VesselAPI** | clé gratuite (150 appels / mois) · offres payantes | Registre navires, 3e flux AIS optionnel |
| **VMS** | export de l'administration (NAF / CSV) | Navires sous licence (Administration → Catalogue → Import VMS) |
| **Liste INN combinée** (TMT / ORGP) | import XLS, XLSX ou CSV | Liste de surveillance : navires listés signalés, incident automatique si AIS dans la ZEE |

Sources restreintes recensées (à brancher selon habilitations) : **DINAMIS** (programmation Pléiades, CNES/IGN), **CO3D** (CNES/Airbus), **AIS satellitaire Kinéis / CLS** (filiales CNES), VIIRS Boat Detection (accès programmatique réservé aux abonnés payants EOG depuis juin 2026), Météo-France AROME Outre-mer, SWOT (CNES/NASA).

Les clés se gèrent dans **Administration → Clés & intégrations** (test en direct, chiffrement, audit) ; l'état de chaque source dans **Santé des sources**.

**Sources personnalisées** (*Administration → Sources personnalisées*) : n'importe quelle API JSON / GeoJSON / CSV, tuiles XYZ / WMTS ou service OGC WMS se branche depuis la console, sans code : URL à variables (`{bbox}`, `{start}`, `{date}`, `{secret}`…), paramètres, en-têtes, corps POST, authentification (en-tête, paramètre, Bearer, Basic), pagination, lecture de la réponse détectée automatiquement, **test en direct** étape par étape (requête envoyée, réponse, champs détectés, aperçu). Les appels passent par le serveur (secret chiffré, jamais envoyé au navigateur, protection SSRF).

## Exploitation

```bash
make admin                            # crée un administrateur (sans validation, tracé et notifié au SOC)
make admin NAME=alice                 # idem, sans question
make users                            # liste des comptes et de leur état
make user NAME=alice ROLE=analyst     # compte + mot de passe temporaire
make reset-password NAME=alice
make reset-password NAME=alice RECOVERY=1   # secours : plus aucun admin ne peut se connecter (clé maître)
make verify-audit                     # contrôle de la chaîne d'audit (code retour ≠ 0 si altérée)
make logs
```

`make admin` crée un administrateur **sans validation préalable** : l'accès à l'hôte est le contrôle, et chaque création est tracée (`cli:host`) et notifiée au SOC. Les autres commandes de comptes (`user`, `reset-password`, `users`) exigent l'**authentification d'un administrateur existant** (identifiant + mot de passe saisis sans écho, mêmes règles de verrouillage et d'audit que l'interface ; un compte au mot de passe temporaire doit d'abord le changer dans l'interface). En secours, `RECOVERY=1` (ou `--recovery` en CLI) remplace l'authentification par la **clé maître** — 3 essais par 30 min, tracé (`auth.break_glass`) et notifié au SOC.

> Limite assumée : un utilisateur root ou membre du groupe `docker` sur l'hôte peut lire directement les volumes. Ces contrôles empêchent l'usage opportuniste et laissent une trace infalsifiable ; restreignez l'accès à l'hôte et au groupe `docker`, et conservez la clé maître hors de la machine.

## Sauvegarde, remise à neuf, restauration

```bash
make backup                       # archive data/ preuves/ keys/ dans backups/ (0600)
make backups-list
make reset                        # remise à neuf COMPLÈTE : comptes, incidents, preuves, audit, clés → installation neuve
make reset SCOPE=data             # efface les données, garde la PKI, la clé maître et les clés API
make restore FILE=backups/kraken-backup-….tar.gz
```

- Uniquement en ligne de commande sur l'hôte (aucune route web : impossible à distance avec un compte volé).
- Authentification d'un administrateur (ou `RECOVERY=1` : clé maître), puis phrase à taper : `REINITIALISER KRAKEN`.
- Le récapitulatif de ce qui sera effacé est affiché avant confirmation ; **une sauvegarde est créée automatiquement** avant tout effacement (désactivable avec `--no-backup` en CLI directe). La restauration crée aussi une sauvegarde de l'état courant.
- Après une remise à neuf complète, un nouvel administrateur `admin` est créé et son mot de passe temporaire affiché une seule fois.
- La nouvelle chaîne d'audit commence par `system.factory_reset`, avec l'empreinte de la sauvegarde et la tête de l'ancienne chaîne : la réinitialisation reste traçable.
- Les archives sont contrôlées avant restauration (pas de chemins absolus, de `..`, de liens symboliques ni de dossiers inattendus).
- **Les sauvegardes contiennent la clé maître et les secrets chiffrés** : stockez-les hors de la machine, avec le même niveau de protection que la plateforme.

## API (extrait)

Toutes les routes `/api/v1/*` exigent une session (sauf `/auth/login`). Documentation interactive : http://localhost:8000/docs en `ENVIRONMENT=development`.

| Route | Rôle |
| --- | --- |
| `POST /auth/login` · `POST /auth/logout` · `GET /auth/me` · `POST /auth/password` | tous |
| `GET /alerts` · `/zee` · `/environment` · `/events` · `/scenes` · `/imagery` · `/point` | observateur |
| `POST /scan` | analyste |
| `GET /incidents` · `/incidents/{id}` · `/incidents/kpis` | observateur |
| `POST /incidents/{id}/transition · assign · comment · actions/{key}` | analyste |
| `POST /incidents/{id}/escalate` | superviseur |
| `GET /evidence` · `/evidence/files/{dossier}/{fichier}` | observateur |
| `POST /evidence/{dossier}/verify` · `GET /evidence/{dossier}/export` | analyste |
| `GET /audit` · `/audit/verify` | superviseur |
| `GET /maritime/ais` · `/maritime/vessel/{mmsi}` · `/maritime/mpa` · `/maritime/ocean-layers` · `/maritime/coverage` | observateur |
| `POST /maritime/vms/import` | superviseur |
| `GET /incidents/{id}/decision` · `GET /assets` · `GET /reports` · `GET /reports/{id}` | observateur |
| `POST /reports/incident/{id}` · `POST /reports/sitrep` (projet) · `POST /reports/{id}/verify` · `GET /reports/{id}/export` | analyste |
| `POST /reports/sitrep` (émission) · `POST/PUT/DELETE /assets` | superviseur |
| `/users` · `/settings/keys` · `/system/settings` | administrateur |

## Licence

Distribué sous licence [MIT](LICENSE). Le code tiers garde sa propre licence : solution xView3 (`xview3/vendor/`, MIT, © 2021 Eugene Khvedchenya) et polices IBM Plex (`frontend/public/fonts/`, SIL Open Font License 1.1).
