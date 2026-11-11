# Données conservées

Ce que la plateforme stocke, où, et combien de temps.

| Donnée | Emplacement | Durée de conservation |
| --- | --- | --- |
| Comptes, sessions, incidents, chronologies, listes, paramètres | base PostgreSQL (volume Docker `pgdata`) | illimitée (sessions : jusqu'à expiration) |
| Positions AIS / VMS | PostgreSQL, hypertable TimescaleDB compressée après 2 jours | **90 jours** (`AIS_RETENTION_DAYS`) : profondeur du [rejeu](situation/chronologie.md), purge horaire par blocs journaliers |
| Journal de collecte (minutes d'écoute de chaque flux AIS) | PostgreSQL, table `feed_minutes` | 90 jours, comme les positions : distingue « aucun navire » de « aucune donnée », en direct comme en rejeu |
| Identité des navires et renseignement (GFW, sanctions) | PostgreSQL | supprimés avec le navire, 90 jours après son dernier signal |
| Journal d'audit | PostgreSQL | illimitée (ajout seul) |
| État partagé (scan en cours, santé, compteurs) | Redis (volume `redisdata`) | temporaire, reconstitué automatiquement |
| Ancienne base SQLite | `data/kraken.db.migrated-<date>` après la migration | à supprimer manuellement une fois la migration vérifiée |
| Dossiers de preuves | `preuves/alerte_*` | illimitée |
| Clé maître, PKI, coffre des clés d'API | `keys/` (droits 600) | illimitée |
| Tuiles Sentinel-2 rendues, modèle IA (fichier vérifié par SHA-256) | `cache/s2tiles/`, `cache/models/` | reconstructibles |
| Réponses des sources (cache) | `cache/` | de 30 min (météo) à 1 an (bathymétrie) ; reconstructible |
| Sauvegardes | `backups/` | jusqu'à suppression manuelle |
| Notifications de démonstration | mémoire du serveur `notif-sink` | 1 000 derniers messages, perdus au redémarrage |
| Préférences d'affichage | navigateur de chaque utilisateur | jusqu'à effacement des données du site |

## Caches de performance

Résultats identiques, calculs évités ; chaque cache est invalidé automatiquement dès que la donnée change.

| Cache | Contenu | Invalidé quand |
| --- | --- | --- |
| Frise chronologique (table `timeline_cache`) | compteurs des heures et jours terminés depuis plus de 6 h | une position arrive avec une date ancienne (import VMS…), une détection radar porte sur une période passée (scène analysée en retard) |
| Dossiers de preuves (mémoire) | contenu des `kraken_alerts.geojson` déjà lus ; les dossiers antérieurs à la période affichée sans incident ouvert ne sont plus ouverts | le fichier change (date, taille) |
| Statuts d'incident, index des détections, liste de surveillance (mémoire) | historiques reconstitués, correspondances | toute écriture dans les tables concernées (compteurs et dates de modification) |

## Contrôle des positions à l'entrée

Chaque position AIS / VMS est vérifiée avant d'entrer dans l'historique ; les positions refusées sont comptées par flux et par motif (*Panneau latéral → Contexte → Positions rejetées*) :

| Motif | Règle |
| --- | --- |
| Horodatage illisible | ni ISO 8601, ni format aisstream |
| Horodatage dans le futur | plus de 5 min d'avance sur l'horloge du serveur |
| Hors de l'historique conservé | plus vieux que `AIS_RETENTION_DAYS` |
| Position invalide | hors des bornes, ou exactement 0° / 0° |
| Saut de position impossible | plus de 2 km d'écart avec la position précédente du navire à une vitesse implicite de plus de 50 nœuds (mauvais GPS, MMSI partagé, usurpation) |

Tous les horodatages sont stockés au format **ISO 8601 UTC**. Le format d'aisstream (`2026-10-20 10:00:00.1 +0000 UTC`) était auparavant stocké tel quel : comparé sous forme de texte à des bornes ISO, il faisait sortir des fenêtres « dernières N heures » les positions du jour même. La migration v9 a corrigé les positions déjà stockées.

## Données personnelles

- Comptes : identifiant, nom affiché, rôle, dates de connexion, adresses IP dans l'audit.
- Navires : identifiants publics diffusés par les navires (AIS) ou transmis par l'administration (VMS).

Les données VMS et les incidents peuvent relever d'une procédure judiciaire : limitez les comptes et les exports aux personnes habilitées.

## Effacement

- `make reset SCOPE=data` : efface comptes, incidents, preuves, audit, positions et paramètres, garde les clés.
- `make reset` : remise à neuf complète.

Les deux commandes font une sauvegarde automatique préalable. Voir [Exploitation](exploitation.md).
