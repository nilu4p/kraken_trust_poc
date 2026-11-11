# Notifications

La plateforme pousse les événements importants vers une messagerie d'équipe (Slack, Mattermost, Teams…) par **webhook**, sur **6 canaux** :

| Canal | Contenu |
| --- | --- |
| **#alertes** | Nouveaux incidents prioritaires (P1, P2) et sévérités relevées ; pêche étrangère, coupures, rendez-vous, aires protégées |
| **#escalades** | Saisines des autorités AEM, interventions et clôtures |
| **#sla** | Échéances de triage ou de résolution dépassées |
| **#surveillance** | Navires de la liste de surveillance (INN) et navires sous sanctions dans la ZEE |
| **#operations** | Rapports de scan Sentinel-1, échecs, santé des sources (panne et rétablissement), **collecte AIS dégradée** (moins de 50 % des 30 dernières minutes écoutées) puis rétablie |
| **#securite** | Événements de sécurité : verrouillage de compte, création d'administrateur en ligne de commande, clés modifiées, journal d'audit altéré |

## Contenu d'une notification d'incident

Pastille de sévérité (🔴 P1, 🟠 P2, 🟡 P3, ⚪ P4), titre, référence et lien vers le dossier, puis des champs : risque, fiabilité, position, zone, statut, assigné ; selon le type : navire, pavillon, type AIS, engin, motif d'inscription, second navire d'un rendez-vous, indicateurs GFW Insights, correspondances de sanctions, aire protégée.

## Anti-répétition

Une même alerte n'est pas renvoyée en boucle : chaque message a une clé et un délai de répétition (par exemple, une source en panne n'est signalée qu'au passage à *hors service*, un SLA dépassé une seule fois par étape).

## Serveur de démonstration

Pour la démonstration, un **serveur de notifications local** imite Slack / Mattermost : `make notifs` ouvre la page `/notifs/` de la console (connexion à la plateforme exigée, rôle **superviseur** ou administrateur : le canal #securite montre des noms de comptes et des adresses IP).

| Élément | Usage |
| --- | --- |
| Colonne de gauche | les 6 canaux avec leur rôle et le nombre de **messages non lus** |
| Fil central | messages du canal, mis en forme comme dans Slack (pastille de couleur, champs, lien vers l'incident) |
| **Rechercher** | filtre sur la référence, le navire, l'autorité… |
| **Vider** | efface les messages du canal affiché |

Il garde les **1 000 derniers messages en mémoire** : tout est perdu au redémarrage. Ce n'est pas un outil de production.

## Configuration

*Administration → Clés & intégrations → Notifications* : URL du webhook (HTTPS public). Avec `{channel}` dans l'URL, chaque canal a sa propre adresse ; sinon, une seule URL reçoit tout et le message précise `"channel": "kraken-<nom>"`. Le bouton **Tester** envoie un message sur chacun des 6 canaux.

## Alerte « Collecte AIS dégradée »

Chaque minute, la plateforme calcule la part des **30 dernières minutes** pendant lesquelles au moins un flux AIS (aisstream, AISHub, VesselAPI) écoutait réellement, d'après le [journal de collecte](donnees.md).

| Situation | Message sur #operations |
| --- | --- |
| Moins de **50 %** | « Collecte AIS dégradée : 20 % des 30 dernières minutes écoutées » + écoute par flux, interruptions des 6 dernières heures, conséquences, points à vérifier (conteneur collecteur, clé aisstream, réseau, couverture) |
| Remontée au-dessus de **80 %** | « Collecte AIS rétablie après 42 min » |

Pourquoi c'est important : tant que la collecte est dégradée, **une mer vide ne prouve rien**, les échos radar de cette période sont signalés « non identifiés » (risque minoré) au lieu de « sans AIS », et la règle « coupure AIS » ne peut pas conclure. L'écart entre 50 % et 80 % (hystérésis) évite une alerte à chaque minute quand la collecte oscille. Pas d'alerte juste après un premier démarrage (pas encore 30 minutes d'historique) ni sans flux AIS configuré. L'état est aussi visible dans *Administration › Supervision technique*.
