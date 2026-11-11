# Configuration (variables d'environnement)

La plupart des réglages se font dans l'interface : **clés** (coffre chiffré) et **paramètres opérationnels** (base de données). Les variables ci-dessous se fixent avant le démarrage, dans un fichier `.env` à la racine du projet (facultatif) ou dans l'environnement, puis `make restart`.

!!! info "Ordre de priorité"
    Pour une clé : valeur saisie dans l'interface **>** `.env`. Pour un paramètre opérationnel : valeur enregistrée dans l'interface **>** `.env` **>** valeur par défaut.

## Clés et intégrations

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `ACCESS_KEY`, `SECRET_KEY` | — | Copernicus S3 |
| `SAR_SOURCE` | `auto` | Lecture des pixels radar : `auto` = Copernicus S3 si les clés sont là, sinon (et en secours) miroir sans clé Microsoft Planetary Computer ; `copernicus` ; `planetary` |
| `XVIEW3_URL` | vide (`make up-ai` : `http://xview3:8091`) | Service d'avis IA xView3 (information seule) ; vide = pas d'avis |
| `XVIEW3_THREADS` | 2 | Cœurs de calcul du service xView3 |
| `GFW_TOKEN` | — | Global Fishing Watch |
| `AISSTREAM_API_KEY` | — | aisstream.io |
| `AISHUB_USERNAME` | — | AISHub |
| `OPENSANCTIONS_API_KEY` | — | OpenSanctions |
| `VESSELAPI_API_KEY` | — | VesselAPI (registre navires, 3e flux AIS) |
| `ALERT_WEBHOOK_URL` | serveur de démonstration | Webhook des notifications (`{channel}` ou `__CHANNEL__` = nom du canal) |

## Infrastructure

Déjà réglées par `docker-compose.yml` : à modifier seulement pour une installation différente.

| Variable | Valeur dans Docker | Rôle |
| --- | --- | --- |
| `DATABASE_URL` | `postgresql://kraken@postgres:5432/kraken` | Base de données ; vide = SQLite locale (`DATA_DIR/kraken.db`) |
| `DATABASE_PASSWORD_FILE` | `/run/secrets/postgres_password` | Mot de passe PostgreSQL (secret Docker) |
| `DATABASE_POOL_SIZE` | 10 | Connexions par processus |
| `REDIS_URL` | `redis://redis:6379/0` | État partagé et file des tâches ; vide = état en mémoire (un seul processus) |
| `REDIS_PASSWORD_FILE` | `/run/secrets/redis_password` | Mot de passe Redis (secret Docker) |
| `CELERY_BROKER_URL` | vide (= Redis) | `amqp://…` pour utiliser RabbitMQ comme file de tâches |
| `BACKGROUND_MODE` | `workers` | `workers` : traitements par Celery et le collecteur ; `inline` : tout dans l'API (un seul conteneur) |

## Sécurité

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `MASTER_KEY` | vide | Clé maître Fernet fournie par un gestionnaire de secrets (prioritaire) |
| `MASTER_KEY_FILE` | `/run/secrets/master_key` | Clé maître en secret Docker (`secrets/master_key`, créé par `make secrets`), hors du volume `keys/` et des sauvegardes. Sans `MASTER_KEY` ni fichier (hors Docker) : `keys/master.key` est généré |
| `CONSOLE_BIND` | `127.0.0.1` | Adresse de publication de la console (ports 80 et 443). `0.0.0.0` pour un serveur joignable depuis internet |
| `DOMAIN` | *(vide)* | Nom de domaine du serveur : certificat Let's Encrypt obtenu et renouvelé automatiquement (service `certbot`). Vide = certificat auto-signé |
| `LETSENCRYPT_EMAIL` | *(vide)* | Adresse des avis d'expiration Let's Encrypt |
| `LETSENCRYPT_STAGING` | 0 | 1 = certificats de test Let's Encrypt (non reconnus), pour essayer la configuration |
| `HTTP_PORT` / `HTTPS_PORT` | 80 / 443 | Ports publiés de la console (Let's Encrypt exige le 80) |
| `HTTP_ALT_PORT` | 8080 | Second port HTTP (ancienne adresse de la console), redirigé vers HTTPS comme le 80 |
| `C2PA_TSA_URL` | vide | Autorité d'horodatage RFC 3161 des signatures C2PA (ex. `http://timestamp.digicert.com`) ; vide = heure du serveur seule |
| `BOOTSTRAP_ADMIN_USER` | `admin` | Identifiant du premier administrateur |
| `BOOTSTRAP_ADMIN_PASSWORD` | aléatoire | Mot de passe initial (sinon affiché une fois dans les journaux). S'il ne respecte pas la politique (12 caractères, 3 familles, pas un mot courant…), il est ignoré et un mot de passe aléatoire est généré et affiché dans les journaux |
| `SESSION_TTL_MINUTES` | 480 | Durée maximale d'une session |
| `SESSION_IDLE_MINUTES` | 30 | Déconnexion après inactivité |
| `COOKIE_SECURE` | true (Docker) | Cookies `Secure` : la console Docker n'est servie qu'en HTTPS |
| `PASSWORD_MIN_LENGTH` | 12 | Longueur minimale des mots de passe |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_MINUTES` | 5 / 15 | Verrouillage après échecs |
| `RATE_LIMIT_PER_MINUTE` | 300 | Requêtes par minute et par adresse |
| `TRUSTED_PROXIES` | réseau Docker | Proxys dont l'en-tête `X-Forwarded-For` est cru |
| `WEBHOOK_ALLOWED_HOSTS` | `["notif-sink"]` | Hôtes internes autorisés en HTTP pour le webhook |
| `ALLOW_INSECURE_WEBHOOKS` | false | Autorise tout webhook HTTP (déconseillé) |
| `CUSTOM_SOURCE_ALLOWED_HOSTS` | `[]` | Hôtes internes de confiance joignables par les [sources personnalisées](../administration/sources-personnalisees.md) (sinon adresses publiques uniquement), ex. `["geoserver.cmc.local"]` |
| `CUSTOM_SOURCE_MAX_MB` | 20 | Taille maximale d'une réponse de source personnalisée (Mo) |
| `PUBLIC_URL` | `https://$DOMAIN` (`https://localhost` sans domaine) | Adresse de la console dans les liens des notifications |
| `OPTICAL_MAX_WINDOWS` | `24` | Fenêtres de 1024 px lues par scène optique en veille automatique (charge réseau) |
| `REPORT_ORIGINATOR` | `Centre de surveillance maritime KRAKEN — Guyane` | Ligne *ORIGINE* des [rapports](../commandement/rapports.md) (compte rendu, SITREP) |
| `REPORT_CLASSIFICATION` | `DIFFUSION RESTREINTE` | Mention de protection proposée par défaut sur les rapports (*NON PROTÉGÉ* reste toujours proposé) |
| `CORS_ORIGINS` | adresses locales | Origines autorisées pour l'API |
| `ENVIRONMENT` | `production` (Docker) | `development` active la documentation interactive de l'API |

## Zone surveillée

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `EEZ_MRGID` | 8462 | Identifiant Marine Regions de la ZEE (Guyane) |
| `EEZ_BBOX` | `[-54.6, 3.5, -49.4, 9.0]` | Rectangle ouest, sud, est, nord (abonnements AIS, requêtes) |
| `EEZ_POLYGON` | contour simplifié | Contour de secours hors ligne |

Changer de zone (autre territoire) demande de modifier ces trois valeurs ; certaines sources propres à la Guyane (limites SHOM, codes MID locaux) devraient alors être adaptées.

## Moteur (avancé)

Les paramètres modifiables dans l'interface sont décrits dans [Paramètres opérationnels](../administration/parametres.md). Autres valeurs :

| Variable | Défaut | Rôle |
| --- | --- | --- |
| `MAX_DETECTIONS` | 25 | Nombre maximal de cibles par scène |
| `SAR_WINDOW_SIZE` / `SAR_MAX_WINDOWS` | 1024 / 24 | Taille et nombre maximal de tuiles analysées par scène (0 = toute la ZEE de la scène ; réglable dans Paramètres). La part couverte est affichée en fin de scan |
| `BLOB_MIN_PIXELS` / `BLOB_MAX_PIXELS` | 2 / 600 | Taille admise d'une tache radar |
| `PERSISTENCE_RADIUS_KM` / `PERSISTENCE_MIN_SCENES` | 0,25 / 2 | Détection des objets fixes |
| `AIS_MATCH_KM` / `AIS_MAX_MATCH_KM` | 1,5 / 25 | Rayon de corrélation AIS |
| `AIS_RETENTION_DAYS` | 14 | Conservation des positions |
| `AIS_STORE_INTERVAL_S` | 60 | Au plus une position stockée par navire et par intervalle |
| `AIS_COLLECTOR_ENABLED` / `RULES_ENABLED` | true / true | Collecte AIS et règles 24/7 |
| `CMEMS_ENABLED` | true | Contexte Copernicus Marine |
| `GAP_PROXIMITY_KM` | 30 | Rayon des événements GFW pris en compte dans le risque |
| `INTEL_REFRESH_DAYS` | 7 | Fréquence de recriblage d'un navire |
| `SLA_TRIAGE_MINUTES` / `SLA_RESOLVE_MINUTES` | voir [SLA](../incidents/severite-sla.md) | Délais par sévérité (JSON) |
| `HTTP_TIMEOUT` | 20 | Délai maximal d'un appel externe (s) |
| `WEATHER_TTL_MINUTES` | 30 | Cache météo |
| `LOG_LEVEL` | INFO | Verbosité des journaux |
