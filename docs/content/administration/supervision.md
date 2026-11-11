# Supervision technique

<p class="where">Administration → <strong>Supervision technique</strong> — administrateurs. Rafraîchie toutes les 30 secondes.</p>

Cet écran répond à : **la plateforme elle-même fonctionne-t-elle, et ses données sont-elles complètes ?** Il ne surveille pas la mer, mais l'outil qui la surveille. Il est calculé localement, sans appel externe : il reste disponible quand les sources ne le sont pas.

## Les quatre indicateurs du haut

| Indicateur | OK | À surveiller | Problème |
| --- | --- | --- | --- |
| **Sources externes** | toutes répondent | une dégradée (1 ou 2 erreurs d'affilée) | une indisponible (3 erreurs d'affilée) |
| **Collecte AIS (30 min)** | ≥ 80 % des minutes écoutées | 50 – 80 % | < 50 % : alerte envoyée sur #operations |
| **Cycle des règles 24/7** | dernier passage il y a moins de 3 min | — | plus de 3 min : boucle, worker ou planificateur bloqué |
| **Espace disque libre** | ≥ 20 % du volume | 10 – 20 % | < 10 % |

## Détails

| Bloc | Contenu | À quoi ça sert |
| --- | --- | --- |
| **Sources externes** | dernier succès, dernier échec, erreurs consécutives, dernière erreur, pour chaque source appelée par ce processus | repérer une clé expirée, un quota atteint, un service tombé |
| **Collecte AIS** | écoute sur 24 h par flux (aisstream, AISHub, VesselAPI), positions reçues dans l'heure, état du flux aisstream, **positions rejetées** par motif, interruptions de plus de 10 min, alerte en cours | savoir si une mer vide est fiable ; voir un flux qui renvoie des positions aberrantes |
| **Scans radar** | 10 derniers scans : fin, issue, **durée**, part de la ZEE analysée, cibles ; durée moyenne et échecs récents | dimensionner `sar_max_windows`, détecter des échecs répétés (clés, S3, scène) |
| **Temps de réponse de l'API** | par domaine (incidents, maritime, alertes, graphe…) : nombre de requêtes, médiane, 95e centile, maximum, erreurs serveur | trouver ce qui ralentit la console (au-delà de 1 s, l'opérateur le ressent) |
| **Base de données et stockage** | moteur (PostgreSQL / TimescaleDB ou SQLite), taille, nombre de lignes des tables principales, taille de `preuves/`, `cache/`, `backups/`, `data/` | anticiper la croissance de l'historique de 90 jours |

!!! info "Par processus"
    Les temps de réponse et la santé des sources sont ceux vus par le processus de l'API. En déploiement à plusieurs conteneurs, les workers et le collecteur publient leur santé dans l'état partagé (Redis), visible aussi dans *Santé des sources*.
