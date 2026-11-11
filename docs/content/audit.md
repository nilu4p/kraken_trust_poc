# Journal d'audit

<p class="where">Onglet <strong>Audit</strong> — superviseurs et administrateurs.</p>

Toutes les actions significatives sont inscrites dans un journal **en ajout seul** et **chaîné** : chaque entrée contient l'empreinte SHA-256 de l'entrée précédente plus son propre contenu. Modifier ou supprimer une entrée passée casse la chaîne à cet endroit, et cela se voit.

```text
hash(n) = SHA-256( hash(n-1) + contenu canonique de l'entrée n )
```

## Colonnes

| Colonne | Contenu |
| --- | --- |
| **Horodatage** | Date et heure UTC (prise sous verrou d'écriture : l'ordre des entrées est chronologique) |
| **Acteur** | Utilisateur, `system` (automatisme), `auto-surveillance` (veille), `cli:host` (commande `make` sur le serveur) |
| **Action** | Code de l'action (voir ci-dessous) |
| **Cible** | Objet concerné : `incident:KRK-2026-0042`, `evidence:alerte_…`, `user:alice`, `scene:…` |
| **Résultat** | `success` ou `failure` (ex. connexion refusée) |
| **Détails** | Informations complémentaires (jamais de mot de passe ni de clé) |
| **Hash** | Début de l'empreinte de l'entrée |

**Filtres par famille** : *Toutes*, *Authentification*, *Incidents*, *Preuves*, *Comptes*, *Système*, *Scans* ; champ **Acteur** pour une personne ; **Actualiser** recharge. Les 300 entrées les plus récentes correspondant au filtre sont affichées (l'API permet d'aller plus loin).

## Vérification de la chaîne

En haut de page : **Chaîne intègre · N entrées · tête abc123…** (vert) ou l'indication de la **première entrée invalide** (rouge). La plateforme vérifie aussi la chaîne **toutes les heures** et alerte le canal **#securite** si elle est rompue. Vérification en ligne de commande : `make verify-audit`.

## Actions journalisées

| Famille | Actions |
| --- | --- |
| Authentification | `auth.login`, `auth.logout`, `auth.password_change`, `auth.cli` (commande d'administration), `auth.break_glass` (accès de secours avec la clé maître) |
| Utilisateurs | `user.create`, `user.update` (rôle, activation), `user.password_reset` |
| Incidents | `incident.create`, `incident.status` (changement de statut), `incident.assign`, `incident.comment`, `incident.escalate`, `incident.action` (playbook) |
| Preuves | `evidence.view`, `evidence.verify`, `evidence.export` — forment la **chaîne de possession** |
| Capteurs | `sensor.scan` (scène optique ou nuit VIIRS analysée par la veille automatique), `sensor.optical_scan` (analyse optique à la demande depuis l'aide à la décision) |
| Commandement | `report.issue` (rapport émis, avec son empreinte), `report.verify`, `report.export`, `asset.create`, `asset.update`, `asset.delete` (moyens d'intervention) |
| Scans | `scan.start`, `scan.complete` |
| Surveillance | `watchlist.import`, `watchlist.add`, `watchlist.remove`, `vms.import`, `authorized.add`, `authorized.remove`, `authorized.import`, `custom_source.create`, `custom_source.update`, `custom_source.delete`, `custom_source.test` (sources personnalisées) |
| Système | `system.keys` (modification de clés), `system.keys_test`, `system.settings`, `system.backup`, `system.restore`, `system.factory_reset`, `system.migrate` (migration SQLite → PostgreSQL) |
