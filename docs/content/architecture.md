# Architecture technique

<p class="where">Pour les administrateurs et les équipes d'exploitation.</p>

```text
                 navigateur
                     │ :80 → :443 HTTPS
              ┌──────▼───────┐        ┌────────────┐
              │   console    │──/docs─▶│    docs    │
              │   (nginx)    │        └────────────┘
              └──────┬───────┘
                     │ /api
              ┌──────▼───────┐   requêtes   ┌──────────────────────────┐
              │   backend    │─────────────▶│ postgres                  │
              │ (API FastAPI)│              │ PostgreSQL 16 + TimescaleDB│
              └──┬────────┬──┘              └──────────▲───────────────┘
        tâches   │        │ état partagé               │
                 ▼        ▼                            │
              ┌──────────────┐                         │
              │    redis     │◀──────────┐             │
              └──┬────────▲──┘           │             │
                 │ files  │ état         │             │
   ┌─────────────▼──┐  ┌──┴──────────┐ ┌─┴──────────┐  │
   │ worker         │  │ worker-scan │ │ collector  │──┘
   │ règles, SLA,   │  │ Sentinel-1  │ │ AIS        │
   │ notifications  │  │ (1 à la fois)│ │ aisstream, │
   └────────▲───────┘  └──────▲──────┘ │ AISHub     │
            │   tâches périodiques     └────────────┘
         ┌──┴──────┐   │
         │  beat   │───┘
         └─────────┘
```

## Les services

| Service | Rôle | Si le service s'arrête |
| --- | --- | --- |
| **console** | Console web (nginx) : fichiers de l'interface, proxy vers l'API et la documentation, compression, en-têtes de sécurité | plus d'accès à l'interface |
| **backend** | API (authentification, incidents, preuves, carte…) ; migre la base au démarrage | plus d'accès ; les traitements d'arrière-plan continuent |
| **postgres** | Base de données (comptes, incidents, audit, positions AIS…) | tout s'arrête proprement ; `/health/ready` répond 503 |
| **redis** | État partagé entre processus (scan en cours, santé des sources, verrous, anti-répétition) et file des tâches | l'API reste consultable ; scans, règles et notifications sont suspendus jusqu'au retour |
| **worker** | Règles 24/7, contrôle des SLA, santé des sources, maintenance horaire, **envoi des notifications avec nouvelles tentatives** | les tâches s'accumulent dans Redis et sont traitées au redémarrage (les cycles de plus d'une minute sont abandonnés, pas empilés) |
| **worker-scan** | Analyses Sentinel-1, **une à la fois**, processus renouvelé après chaque scan (libère la mémoire GDAL) | les scans demandés attendent « en file » |
| **beat** | Planificateur : émet les cycles périodiques | plus de règles 24/7 ni de veille automatique |
| **collector** | Collecte AIS continue (aisstream.io, AISHub) | plus de nouvelles positions AIS |
| **docs** | Cette documentation | — |
| **notif-sink** | Messagerie de démonstration | — |
| **xview3** (facultatif, `make up-ai`) | [Avis de l'IA](moteur/ia.md) sur les cibles radar, en information seule | les scans continuent sans avis |

## Base de données : PostgreSQL + TimescaleDB

- **Une base pour tout** : comptes, sessions, incidents, chronologies, audit, listes, positions AIS, paramètres.
- **Positions AIS dans une hypertable TimescaleDB** : découpage par jour, **compression automatique après 2 jours** (environ 10 fois moins d'espace), purge par blocs entiers au-delà de la durée de conservation (90 jours par défaut, profondeur du rejeu).
- **Lectures sans attente** : les consultations ne bloquent jamais les écritures, et inversement.
- **Cohérence entre processus** : des verrous de transaction garantissent que la chaîne d'audit ne se divise jamais et qu'une même détection n'ouvre pas deux incidents, même avec plusieurs workers.
- **Migration automatique** : au premier démarrage sur PostgreSQL, l'ancienne base SQLite (`data/kraken.db`) est copiée intégralement (identifiants, références d'incident et chaîne d'audit conservés), puis renommée `kraken.db.migrated-<date>`. L'opération est tracée dans l'audit (`system.migrate`).
- **Mode SQLite** : sans `DATABASE_URL`, la plateforme fonctionne toujours sur une base SQLite locale (développement, tests, poste unique).

## Redis

| Usage | Détail |
| --- | --- |
| Scan en cours | état affiché à tous les utilisateurs, **verrou unique** (un seul scan sur toute la plateforme) qui expire si le worker meurt : le scan est alors signalé « interrompu » au lieu de rester « en cours » |
| Santé des sources | vue commune à tous les processus |
| Collecteur AIS | compteurs (messages reçus, silence) lus par l'API |
| Anti-répétition | une alerte n'est envoyée qu'une fois, quel que soit le processus |
| Cycles | un cycle de règles ou de SLA ne chevauche jamais le précédent |
| File de tâches | broker Celery (files `default`, `notify`, `scan`) |

Données Redis persistées (fichier AOF), mémoire limitée à 256 Mo sans éviction.

## Tâches (Celery)

| Tâche | File | Fréquence |
| --- | --- | --- |
| Règles 24/7 et enrichissement des navires | default | chaque minute |
| Contrôle des SLA | default | chaque minute |
| Santé des sources | default | chaque minute |
| Veille Sentinel-1 (mise en file de la prochaine scène) | default → scan | selon `auto_scan_interval_minutes` |
| Purge AIS et vérification de la chaîne d'audit | default | chaque heure |
| Scan Sentinel-1 | scan | à la demande ou par la veille |
| Envoi d'une notification | notify | à chaque alerte ; 6 nouvelles tentatives (30 s à 16 min) |

Une tâche interrompue par l'arrêt d'un worker est **redistribuée**. Les clés et paramètres modifiés dans l'interface sont rechargés au début de chaque tâche, et chaque minute par le collecteur : aucun redémarrage n'est nécessaire.

!!! info "RabbitMQ"
    Redis sert de broker par défaut : une seule dépendance, largement suffisante à cette échelle. Pour un déploiement multi-sites ou une livraison garantie des alertes, indiquez `CELERY_BROKER_URL=amqp://…` : les workers utiliseront RabbitMQ sans autre changement.

## Monter en charge

| Besoin | Action |
| --- | --- |
| Plus de règles, d'enrichissement, de notifications | `docker compose up -d --scale worker=3` |
| Plus d'utilisateurs simultanés | augmenter les processus de l'API (plusieurs workers uvicorn) : tout l'état est partagé |
| Scans plus nombreux | un worker de scan par machine (calcul et mémoire lourds) ; les scans restent séquentiels pour ne pas dépasser le quota Copernicus |
| Historique AIS plus long | augmenter `AIS_RETENTION_DAYS` : TimescaleDB compresse automatiquement |

`beat` doit rester **unique** (il émet les cycles).

## Sondes

| Adresse | Réponse |
| --- | --- |
| `/health` | vivant (le processus répond) |
| `/health/ready` | prêt : base de données **et** état partagé joignables (503 sinon) — utilisée par Docker pour démarrer les workers après l'API |

## Mots de passe des services

Générés par `make secrets` (automatiquement à `make up`) dans `secrets/` (dossier `700`, jamais versionné), transmis aux conteneurs comme secrets Docker (`/run/secrets/…`). PostgreSQL et Redis ne sont **pas exposés** hors du réseau Docker.
