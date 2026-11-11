# Référence de l'API

Pour intégrer KRAKEN-Trust à un autre système (tableau de bord, script d'export, outil de l'autorité). Toutes les routes sont préfixées par **`/api/v1`** et accessibles via la console : `https://localhost/api/v1/...`. En développement, une documentation interactive est disponible sur `http://127.0.0.1:8000/docs`.

## Authentification

1. `POST /api/v1/auth/login` avec `{"username": "...", "password": "..."}`.
2. La réponse pose le cookie de session **`kraken_session`** (HttpOnly) et renvoie `{"user": {...}, "csrf_token": "..."}`.
3. Toute requête **modifiante** (POST, PUT, PATCH, DELETE) doit porter l'en-tête **`X-CSRF-Token`** avec ce jeton.
4. `POST /api/v1/auth/logout` ferme la session.

```bash
# Exemple : lister les incidents P1 ouverts avec curl
curl -s -c jar.txt -H 'Content-Type: application/json' \
     -d '{"username":"analyste1","password":"…"}' https://localhost/api/v1/auth/login
curl -s -b jar.txt 'https://localhost/api/v1/incidents?scope=open&severity=P1'
```

!!! warning "Compte dédié"
    Créez un compte nominatif dédié à chaque intégration (rôle minimal, souvent *Observateur*) : ses accès apparaissent clairement dans l'audit. Les sessions expirent comme celles des humains (30 min d'inactivité, 8 h maximum).

## Codes de réponse

| Code | Signification |
| --- | --- |
| 200 | Succès |
| 400 | Requête invalide (ex. fichier vide) |
| 401 | Non connecté ou session expirée |
| 403 | Droits insuffisants, jeton CSRF absent, ou changement de mot de passe requis |
| 404 | Objet introuvable |
| 409 | Conflit (ex. « Un scan est déjà en cours ») |
| 422 | Données refusées (transition interdite, format de fichier non reconnu…) ; le motif est dans `detail` |
| 429 | Trop de requêtes (limitation de débit) |

## Routes

Le rôle indiqué est le minimum exigé par la route ; certaines règles métier vont plus loin (ex. l'escalade exige *Superviseur*, voir [cycle de vie](incidents/cycle-de-vie.md)).

### Session et comptes

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| POST | `/auth/login` | — | Connexion ; renvoie l'utilisateur et le jeton CSRF |
| POST | `/auth/logout` | connecté | Déconnexion |
| GET | `/auth/me` | connecté | Utilisateur courant et jeton CSRF (reprise de session) |
| POST | `/auth/password` | connecté | Changer son mot de passe (`current_password`, `new_password`) ; ferme les autres sessions |
| GET | `/auth/sessions` | connecté | Ses sessions ouvertes |
| GET | `/auth/check` | connecté | Sonde de session (204 / 401 / 403) utilisée par le proxy pour protéger la documentation |
| GET | `/users` | Admin | Liste des comptes |
| GET | `/users/assignable` | Analyste | Opérateurs à qui affecter un incident |
| POST | `/users` | Admin | Créer un compte (renvoie le mot de passe temporaire) |
| PATCH | `/users/{id}` | Admin | Modifier rôle, nom, activation |
| POST | `/users/{id}/reset-password` | Admin | Nouveau mot de passe temporaire |

### Instant de référence (temps réel et rejeu)

Les routes marquées **⏱** acceptent `at=<instant ISO 8601 UTC>` : la réponse décrit la situation **telle que la plateforme la connaissait à cet instant** (rien de postérieur, incidents au statut de l'époque). Sans `at` (ou un instant de moins de 2 minutes), c'est le temps réel. Limite : l'historique conservé (90 jours par défaut, `AIS_RETENTION_DAYS`) ; au-delà ou format invalide : **422**. Chaque réponse porte `time: {"mode": "live" | "replay", "at": …}`. Voir [Chronologie et rejeu](situation/chronologie.md).

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/timeline?start=&end=&bucket=auto` | Observateur | Activité par heure ou par jour : navires AIS, positions VMS, détections radar, observations des règles, incidents ouverts / clos, scènes analysées, et minutes d'écoute de la collecte AIS (`ais_listening_minutes`, `collection_logged`) |

### Situation et contexte

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/alerts?days=30&at=` ⏱ | Observateur | Cibles radar sans AIS des N jours précédant l'instant (0 = tout ; incidents ouverts toujours inclus), une seule fois par scène et position, avec statut C2PA et incident lié ; traces AIS, empreintes |
| GET | `/zee` | Observateur | ZEE, zone contiguë, mer territoriale (GeoJSON) |
| GET | `/environment?at=` ⏱ | Observateur | Vent, houle, courant, SST et fiabilité radar (archive horaire en rejeu ; « indisponible » plutôt que la météo actuelle) |
| GET | `/events?at=` ⏱ | Observateur | Événements GFW dans la ZEE, commencés avant l'instant |
| GET | `/fishing-limits` | Observateur | Limites SHOM 100 NM et 3 NM |
| GET | `/scenes?at=` ⏱ | Observateur | Passages Sentinel-1 des 12 jours précédant l'instant |
| GET | `/imagery?lat=&lon=&around=&days=` | Observateur | Images optiques / radar autour d'un point |
| GET | `/point?lat=&lon=&at=` ⏱ | Observateur | Dossier du point (zone, AMP, océan, météo, incidents, événements) à l'instant |
| POST | `/scan?scene_id=` | Analyste | Lance un scan (dernière scène si `scene_id` absent) |
| GET | `/scan/status` | Observateur | Étape et résultat du scan en cours |

### Image maritime continue

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/maritime/ais?hours=&track_hours=&at=` ⏱ | Observateur | Navires entendus dans les `hours` précédant l'instant, catégorie, trace ; `collection` = part des minutes écoutées par la collecte AIS et interruptions (sous 50 %, une mer vide ne prouve pas l'absence de navires) |
| GET | `/maritime/vessel/{mmsi}?at=` ⏱ | Observateur | Fiche fusionnée d'un navire (flux et trace des 24 h précédant l'instant) |
| GET | `/maritime/gfw-sar?days=&at=` ⏱ | Observateur | Détections SAR GFW |
| GET | `/maritime/fishing-effort?days=&at=` ⏱ | Observateur | Effort de pêche AIS par pavillon (`foreign` par maille) |
| GET | `/maritime/activity-report?start=&end=&at=` ⏱ | Observateur | Rapport d'activité de pêche de la ZEE sur une période (366 j au plus ; défaut : 90 j jusqu'au jour de l'instant) : heures par jour (par mois au-delà de 120 j) et par pavillon, par engin, navires les plus actifs (liste INN, licence), escales dans les ports |
| GET | `/maritime/isobaths` | Observateur | Isobathes 20 à 4 000 m de la ZEE (grille ETOPO) |
| GET | `/maritime/mpa` | Observateur | Aires marines protégées |
| GET | `/maritime/ocean-layers?at=` ⏱ | Observateur | Adresses des surcouches Copernicus Marine du jour de l'instant (`TIME`) |
| GET | `/maritime/coverage` | Observateur | Couverture temporelle de la surveillance (temps réel), positions AIS rejetées par motif |
| GET | `/imagery/sentinel2/scenes?at=&days=10&max_cloud=60` ⏱ | Observateur | Dernière image Sentinel-2 (10 m) par tuile avant l'instant (Earth Search, sans clé) |
| GET | `/imagery/sentinel2/tiles/{scene}/{z}/{x}/{y}.png` | Observateur | Tuile de carte en vraies couleurs (zooms 8 à 16), mise en cache |
| POST | `/maritime/vms/import` | Superviseur | Import VMS (corps brut NAF ou CSV, `application/octet-stream`, 10 Mo) |

### Incidents

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/incidents?scope=&severity=&status=&q=&sort=` | Observateur | Liste ; `scope` = `open` (défaut), `mine`, `unassigned`, `all` ; `q` = recherche sur la référence ou le titre ; `sort` = `recent` (défaut, ouverture la plus récente d'abord), `oldest`, `priority` |
| GET | `/incidents/meta` | Observateur | Statuts, résolutions, autorités d'escalade, playbook |
| GET | `/incidents/kpis` | Observateur | Indicateurs (ouverts, SLA, MTTA, MTTR…) |
| GET | `/incidents/{id}` | Observateur | Dossier complet : observations, playbook, chronologie |
| POST | `/incidents/{id}/transition` | Analyste* | `{"status", "note", "resolution"}` — annuler une escalade (`escalated` → `qualified`) ou une intervention (`response` → `escalated` / `qualified`) : superviseur, `note` = motif obligatoire |
| POST | `/incidents/{id}/assign` | Analyste* | `{"assignee_id"}` (*affecter à un autre : superviseur) |
| POST | `/incidents/{id}/comment` | Analyste | `{"text"}` |
| POST | `/incidents/{id}/escalate` | Superviseur* | `{"target", "reference", "note"}` — cibles dans `/incidents/meta` |
| POST | `/incidents/{id}/actions/{key}` | Analyste | `{"done": true}` — étape du playbook |

### Commandement : aide à la décision, moyens, rapports

Voir [Aide à la décision](commandement/decision.md) et [Rapports](commandement/rapports.md).

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/incidents/{id}/decision` | Observateur | Position estimée de la cible maintenant, projection de route, interception par chaque moyen, options d'action et option recommandée (rien n'est enregistré) |
| GET | `/assets` | Observateur | Moyens d'intervention, types et disponibilités possibles |
| POST | `/assets` | Superviseur | Créer un moyen (`name`, `kind`, `base_lat`, `base_lon`, `speed_kn`, `endurance_nm`, `readiness_min`, `can_board`, `status`, `notes`) |
| PUT | `/assets/{id}` | Superviseur | Modifier un moyen (champs partiels, ex. `{"status": "engaged"}`) |
| DELETE | `/assets/{id}` | Superviseur | Supprimer un moyen |
| GET | `/reports` | Observateur | Registre des rapports émis (`?kind=incident\|sitrep`, `?incident_id=`), mentions de protection disponibles |
| POST | `/reports/incident/{id}` | Analyste | Compte rendu d'incident : `issue: false` = projet (rien n'est enregistré), `issue: true` = émission signée ; `assessment`, `proposals`, `addressees`, `classification` |
| POST | `/reports/sitrep` | Analyste | SITREP sur `start` → `end` (24 h par défaut, 31 j max, dates ISO 8601 avec fuseau) ; l'émission (`issue: true`) exige *Superviseur* |
| GET | `/reports/{id}` | Observateur | Rapport émis : texte signé, sections, empreinte SHA-256 |
| POST | `/reports/{id}/verify` | Analyste | Recalcule l'empreinte, vérifie la signature et la chaîne de certificats (tracé) |
| GET | `/reports/{id}/export` | Analyste | Texte signé suivi de son bloc de scellement (`.txt`, tracé) |

### Analyse optique à la demande

Voir [Corroboration multi-capteurs](moteur/corroboration.md).

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| POST | `/sensors/optical/scan` | Analyste | Analyse optique / thermique (Sentinel-2, Landsat) autour d'un point (`lat`, `lon`, `radius_km` ≤ 25, `days` ≤ 30) ; détections gardées pour la corroboration (tracé) |

### Zones de surveillance

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/zones` | Observateur | Zones dessinées (GeoJSON) et règles actives qui les utilisent |
| POST | `/zones` | Superviseur | `{"name", "description", "color", "geometry"}` — polygone simple de 3 à 200 sommets |
| PUT | `/zones/{id}` | Superviseur | Modifier nom, description, couleur ou tracé |
| DELETE | `/zones/{id}` | Superviseur | Supprimer (refusé, 409, si une règle active l'utilise) |

### Graphe d'entités

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/graph?focus=vessel:<mmsi>&focus=incident:<id>&days=7&depth=1&at=` ⏱ | Observateur | Voisinage des entités de départ sur les `days` précédant l'instant : nœuds (navires, incidents, détections radar, liste de surveillance, sanctions) et liens horodatés (`first`, `last`) avec leurs preuves (`evidence`) ; 150 nœuds au plus (`truncated`) |
| GET | `/graph/search?q=` | Observateur | Navires (nom, MMSI, IMO, indicatif) et incidents (référence, titre) correspondant au texte |

### Règles de détection

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/rules` | Observateur | Règles intégrées (description, déclencheurs, paramètres et valeurs, sources requises, activée, bilan 30 j) + règles sur mesure (phrase, bilan) + catalogue des critères du constructeur |
| PUT | `/rules/builtin/{id}` | Superviseur | `{"enabled": bool}` — activer / désactiver une règle intégrée (sauf la règle radar) |
| POST | `/rules/preview` | Analyste | `{"definition", "at"}` — tester une règle sans l'enregistrer : navires détectés maintenant ou à un instant passé, condition par condition, et « presque détectés » |
| POST | `/rules/custom` | Superviseur | `{"name", "description", "enabled", "definition"}` — créer une règle sur mesure |
| PUT | `/rules/custom/{id}` | Superviseur | Modifier (nom, description, activation, définition) |
| DELETE | `/rules/custom/{id}` | Superviseur | Supprimer (tracé dans l'audit) |

### Preuves et audit

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/evidence` | Observateur | Dossiers de preuves et état d'intégrité |
| GET | `/evidence/files/{dossier}/{fichier}?preview=` | Observateur | Fichier d'un dossier ; tracé dans la chaîne de possession, sauf vignettes et `preview=1` (aperçus) |
| POST | `/evidence/{dossier}/verify` | Analyste | Rapport d'intégrité |
| GET | `/evidence/{dossier}/export` | Analyste | Paquet ZIP judiciaire |
| GET | `/audit?action=&actor=&limit=` | Superviseur | Journal d'audit |
| GET | `/audit/verify` | Superviseur | Vérification de la chaîne |

### Surveillance et système

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/watchlist?q=` | Observateur | Liste de surveillance |
| POST | `/watchlist` | Analyste | Ajout manuel |
| POST | `/watchlist/import?source=iuu_combined` | Admin | Import XLS / XLSX / CSV (corps brut, 10 Mo) |
| DELETE | `/watchlist/{id}` | Admin | Retrait d'une entrée |
| GET | `/authorized?q=` | Observateur | Navires autorisés (licences), validité |
| POST | `/authorized` | Superviseur | Ajouter une licence (MMSI, IMO ou indicatif obligatoire) |
| DELETE | `/authorized/{id}` | Superviseur | Retirer une licence |
| POST | `/authorized/import?replace=true` | Admin | Import CSV / XLS / XLSX (corps brut, 10 Mo) |
| GET | `/system/health` | Observateur | Santé des sources, veille automatique |
| GET | `/system/catalogue` | Observateur | Catalogue des sources et statuts |
| GET | `/system/supervision` | Admin | Supervision technique : sources (dernier succès / échec, erreurs consécutives), collecte AIS (couverture 30 min / 24 h par flux, interruptions, positions rejetées), scans (durée, issue, couverture), cycle des règles (âge, durée, blocage), temps de réponse de l'API par domaine (p50 / p95), base et disque |
| GET | `/system/ai` | Observateur | Détecteur IA : mode, modèle (URL, SHA-256), accord mesuré avec les verdicts des opérateurs (précision, rappel) |
| GET | `/sources` | Observateur | Sources configurées (sans appel réseau) |
| GET / PUT | `/settings/keys` | Admin | État des clés (jamais les valeurs) / modification |
| POST | `/settings/keys/test/{service}` | Admin | Test en direct d'un service |
| GET / PUT | `/system/settings` | Admin | Paramètres opérationnels |

### Sources personnalisées

Voir [Sources personnalisées](administration/sources-personnalisees.md). Le secret d'une source n'est jamais renvoyé : seuls `has_secret` et les 4 derniers caractères (`secret_hint`).

| Méthode | Route | Rôle | Description |
| --- | --- | --- | --- |
| GET | `/custom-sources` | Observateur | Couches activées (`layers`) ; l'administrateur reçoit aussi les définitions complètes (`sources`) |
| POST | `/custom-sources` | Admin | Créer une source : `{"definition": {...}, "secret": "..."}` |
| PUT | `/custom-sources/{id}` | Admin | Remplacer la définition ; `secret` absent = conservé, `""` = supprimé |
| DELETE | `/custom-sources/{id}` | Admin | Supprimer la source et son secret |
| POST | `/custom-sources/test` | Admin | Tester une définition non enregistrée (requête envoyée, réponse brute, champs détectés, échantillon) |
| POST | `/custom-sources/{id}/test?date=` | Admin | Tester la source enregistrée |
| GET | `/custom-sources/{id}/data` | Observateur | Objets de la source en GeoJSON (cache `refresh_minutes`, dernière copie si la source tombe) |
| GET | `/custom-sources/{id}/tiles/{z}/{x}/{y}?date=` | Observateur | Tuile image relayée par le serveur (le secret ne quitte jamais le serveur) |
| GET | `/health` (sans préfixe) | — | Sonde de vie (aucune information sensible) |
| GET | `/health/ready` (sans préfixe) | — | Sonde de disponibilité : base de données et état partagé joignables (503 sinon) |
