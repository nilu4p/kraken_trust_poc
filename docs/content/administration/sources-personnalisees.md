# Sources personnalisées

<p class="where">Administration → <strong>Sources personnalisées</strong> (administrateurs). Les couches activées apparaissent pour tous dans <em>Panneau latéral → Couches → Sources personnalisées</em>.</p>

Brancher **n'importe quelle API ou service cartographique** sans développement : un radar côtier, les balises d'un partenaire, un export SIG, un service WMS de l'État, une mosaïque satellite… La source se décrit par un formulaire (URL, paramètres, en-têtes, authentification, lecture de la réponse), se **teste en direct** puis s'affiche sur la carte.

## Trois types de sources

| Type | Ce que renvoie le service | Affichage |
| --- | --- | --- |
| **Objets (JSON / GeoJSON / CSV)** | Une liste d'enregistrements positionnés : GeoJSON, JSON quelconque (`data.items`, `results`…), CSV avec colonnes latitude / longitude, ou un objet unique (station, prévision) | Points, lignes ou surfaces de la couleur choisie ; clic = fiche des attributs |
| **Tuiles XYZ / WMTS** | Des images 256 × 256 adressées par `{z}/{x}/{y}` (ou `{-y}` en TMS) | Couche image, opacité et mode de fusion réglables |
| **OGC WMS** | Un service `GetMap` (GeoServer, MapServer, ArcGIS WMS…) | Couche image, demandée tuile par tuile en EPSG:3857 |

## Créer une source

1. **Nouvelle source**, puis éventuellement un **modèle** : API REST JSON, fichier GeoJSON, objet unique, CSV, ArcGIS FeatureServer, tuiles NASA GIBS, OGC WMS, tuiles XYZ. Les modèles API REST, GeoJSON, objet unique, NASA GIBS et WMS pointent vers de vrais services publics : ils se testent immédiatement.
2. **Requête** : méthode (GET / POST), URL, **paramètres** (`?clé=valeur`), **en-têtes**, **corps** JSON (POST).
3. **Authentification** : aucune, clé dans un en-tête, clé dans l'URL, jeton *Bearer*, HTTP *Basic*. Le **secret** se saisit à part : il est chiffré, jamais réaffiché (seuls ses 4 derniers caractères), et peut aussi être placé à la main avec `{secret}`.
4. **Lecture de la réponse** (objets) : tout est **détecté automatiquement** ; précisez au besoin le chemin des enregistrements, les champs latitude / longitude (ou géométrie GeoJSON / WKT `POINT`), libellé, date, identifiant et les champs à afficher dans la fiche.
5. **Pagination** (objets) : numéro de page, décalage (*offset*) ou URL de la page suivante lue dans la réponse ; 20 pages au plus.
6. **Affichage** : couleur et taille des points, ou opacité, zoom natif maximal, **fusion** et crédit des tuiles ; **rafraîchissement** (durée du cache serveur) et délai maximal.
7. **Tester**, ajuster, puis **Enregistrer**. Une source peut être **désactivée** (masquée de la carte) sans être supprimée, ou **dupliquée** (sans son secret).

### Variables

Utilisables dans l'URL, les paramètres, les en-têtes et le corps. Les accolades d'un corps JSON qui ne sont pas des variables connues restent intactes.

| Variable | Valeur |
| --- | --- |
| `{secret}` | Secret de la source |
| `{bbox}` | Emprise de la ZEE `ouest,sud,est,nord` (degrés WGS84) |
| `{west}` `{south}` `{east}` `{north}` | Chaque borne de l'emprise |
| `{start}` `{end}` | Période `AAAA-MM-JJ` : aujourd'hui − *jours d'historique* → aujourd'hui |
| `{start_iso}` `{end_iso}` | Même période en ISO 8601 UTC (`2026-10-14T09:00:00Z`) |
| `{date}` | Date d'imagerie choisie sur la carte (la veille par défaut) |
| `{today}` | Date du jour (UTC) |
| `{page}` `{offset}` `{limit}` | Pagination |
| `{z}` `{x}` `{y}` `{-y}` | Tuile demandée (`{-y}` : ligne inversée des services TMS) |

## Le test en direct

**Tester** exécute la source une fois, sans rien enregistrer ni mettre en cache, et explique chaque étape :

| Étape | Échec typique |
| --- | --- |
| **URL et sécurité** | URL invalide, adresse interne bloquée, secret à envoyer en `http://` |
| **Connexion** | nom d'hôte injoignable, délai dépassé |
| **Réponse HTTP** | 401 (secret refusé), 403 (droits, quota), 404 (URL), 429 (quota) — avec le début du message du service |
| **Lecture** | réponse HTML / XML au lieu de JSON (page de connexion, erreur WMS `ServiceException`) |
| **Positions** | aucune liste d'enregistrements, aucun champ de position exploitable |

Le rapport montre la **requête réellement envoyée** (secret masqué `••••`), le statut, le type et la taille de la réponse, les **champs détectés** du premier enregistrement — un clic sur un champ l'affecte à la latitude, la longitude, au libellé, etc. —, un **aperçu des cinq premiers objets** et le nombre d'objets dans l'emprise de la ZEE, ou l'**aperçu de la tuile** sur la Guyane pour une couche image. La réponse brute (début) est consultable.

Pour une source datée (`{date}` ou WMS avec *TIME*), la date du test se choisit en haut du panneau.

## Sur la carte

- Les objets sont rechargés selon le **rafraîchissement** de la source. Si la source tombe, la **dernière copie** reste affichée avec la mention « dernière copie » sous la case à cocher.
- Les tuiles passent par le serveur KRAKEN (`/api/v1/custom-sources/{id}/tiles/…`) : la clé du service n'atteint jamais les navigateurs, et les tuiles sont mises en cache.
- **Fusion** : *Écran* rend le noir transparent (radiance nocturne, détections lumineuses), *Produit* rend le blanc transparent (cartes scannées, isolignes sur fond blanc).
- Les sources apparaissent dans la [légende](../situation/legende.md#sources-personnalisees), le [catalogue](catalogue.md) (catégorie choisie) et la [santé des sources](catalogue.md#sante-des-sources) (ligne `custom:<id>`).

## Sécurité

- Définitions et secrets sont **chiffrés au repos** (Fernet, clé maître) dans `keys/custom_sources.enc`, partagé par l'API et les workers. Les opérateurs ne reçoivent que le nom, la description et le style des couches actives : ni URL, ni paramètres, ni secret.
- **Protection SSRF** : seules les adresses **publiques** sont joignables ; chaque redirection est revérifiée et le secret n'est jamais transmis à un autre hôte. Un service interne de confiance (GeoServer sur le réseau du centre) s'autorise par son nom dans `CUSTOM_SOURCE_ALLOWED_HOSTS` (voir [Configuration](../mise-en-service/configuration.md)).
- Un secret n'est jamais envoyé en clair : `https://` obligatoire dès qu'un secret est utilisé (sauf hôte de confiance).
- Réponses bornées (`CUSTOM_SOURCE_MAX_MB`, 5 Mo par tuile, 5 000 objets affichés), en-têtes sensibles (`Host`, `Cookie`…) interdits, attributs des objets affichés comme du texte (jamais interprétés comme du HTML).
- Création, modification, suppression et tests sont tracés dans l'[audit](../audit.md) (`custom_source.*`).
