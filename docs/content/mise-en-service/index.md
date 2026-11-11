# Installation et mise en service

Ce guide s'adresse à la personne qui **installe** la plateforme. Comptez 30 minutes, plus le temps d'obtenir les clés des services externes.

## 1. Prérequis

| Élément | Détail |
| --- | --- |
| Système | Linux (ou macOS / Windows avec Docker Desktop) |
| Docker | Docker Engine avec **Docker Compose v2** (`docker compose version`) |
| Outils | `make`, `git` |
| Machine | 4 processeurs, 6 Go de mémoire (8 Go conseillés), 20 Go de disque (plus les preuves et sauvegardes) |
| Réseau sortant | HTTPS vers les services de données (Copernicus, GFW, aisstream, NOAA, Marine Regions…) et WebSocket vers `stream.aisstream.io` |
| Ports locaux | **80 / 443** (console et documentation, HTTPS ; le 80 redirige), 8090 (notifications de démonstration, local seulement), 8000 (API, local seulement) |

## 2. Installation

```bash
git clone <dépôt> kraken-poc && cd kraken-poc
make up                # construit les images et démarre tout (premier démarrage : quelques minutes)
make status            # tous les services doivent être « running », le backend « healthy »
make admin-password    # mot de passe temporaire du premier administrateur « admin »
```

Ouvrez **https://localhost** (ou **https://<votre domaine>** sur un serveur), connectez-vous avec `admin` et ce mot de passe, puis choisissez votre mot de passe définitif.

`make up` démarre une dizaine de services (base PostgreSQL / TimescaleDB, Redis, workers…) : voir l'[architecture technique](../architecture.md). Les mots de passe internes sont générés automatiquement dans `secrets/`.

!!! tip "Fichier `.env` facultatif"
    Toutes les clés se saisissent dans l'interface (coffre chiffré). Un fichier `.env` à la racine n'est utile que pour fixer des variables avant le démarrage (voir [Configuration](configuration.md)). Modèle : `backend/.env.example`.

## 3. Sauvegarder la clé maître

`make up` crée `secrets/master_key`, qui chiffre tous les secrets. Elle est volontairement **absente des sauvegardes** (un secret ne voyage pas avec la clé qui l'ouvre) : **copiez-la tout de suite hors de la machine**, puis faites une première sauvegarde :

```bash
make backup
```

Une instance antérieure gardait la clé dans `keys/master.key` : `make up` la déplace automatiquement dans `secrets/master_key`.

## 4. Brancher les sources de données

*Administration → Clés & intégrations*, dans cet ordre de priorité (procédure détaillée pour chacune : [Comptes externes](comptes-externes.md)) :

| Ordre | Service | Indispensable pour |
| --- | --- | --- |
| 1 | **Copernicus S3** | les scans radar |
| 2 | **aisstream.io** | savoir si un écho radar avait un AIS ; règles 24/7 |
| 3 | **Global Fishing Watch** | événements, détections SAR, identité, Insights |
| 4 | Notifications (webhook) | recevoir les alertes dans la messagerie de l'équipe |
| 5 | OpenSanctions, AISHub, VesselAPI | facultatifs |

Après chaque saisie : **Tester la saisie**, puis **Enregistrer**.

## 5. Vérifier

| Contrôle | Où | Attendu |
| --- | --- | --- |
| AIS reçu | Situation → panneau Contexte → Couverture temporelle | « AIS continu » vert après quelques minutes, flèches sur la carte |
| Sources | Administration → Santé des sources | aucune source « hors service » |
| Notifications | Clés & intégrations → Notifications → Tester | un message sur chacun des 6 canaux |
| Premier scan | Situation → **Scanner** | message « … navire(s) sans AIS » ou « aucune cible » sans erreur |
| Audit | Audit | « Chaîne intègre » |

## 6. Préparer l'exploitation

1. **Comptes** : créez un compte par opérateur avec le bon [rôle](../prise-en-main/roles.md) (*Administration → Utilisateurs*).
2. **Liste INN** : importez la liste combinée ([procédure](../administration/surveillance.md)) ; à refaire à chaque mise à jour publiée.
3. **Veille automatique** : *Administration → Paramètres* → `auto_scan_interval_minutes` à 60–180 min pour que chaque nouveau passage Sentinel-1 soit analysé sans intervention.
4. **VMS** : si l'administration vous transmet des exports, importez-les régulièrement.
5. **Sauvegardes** : planifiez `make backup` (par exemple chaque nuit) et copiez `backups/` ailleurs.

## 7. Production

| Point | Action |
| --- | --- |
| TLS | Placez la console derrière un reverse proxy HTTPS, puis `COOKIE_SECURE=true` |
| Adresse | `PUBLIC_URL=https://…` (liens dans les notifications) et `CORS_ORIGINS` |
| Clé maître | Fournissez `MASTER_KEY` depuis un gestionnaire de secrets plutôt que le fichier |
| Notifications | Remplacez le serveur de démonstration par un vrai webhook HTTPS |
| Mises à jour | `git pull && make build && make up` ; les données sont conservées et la base est migrée automatiquement |
