# Clés & intégrations

<p class="where">Administration → <strong>Clés & intégrations</strong>.</p>

Une carte par service. Les valeurs sont **chiffrées** dans le coffre de la plateforme (clé maître), **jamais réaffichées** : seuls les 4 derniers caractères servent d'indice (`••••a1b2`). La mention **coffre** (saisie dans l'interface) ou **.env** (variable d'environnement) indique l'origine. Une clé enregistrée s'applique **immédiatement**, sans redémarrage.

## Boutons

| Bouton | Effet |
| --- | --- |
| **Obtenir ↗** | Page d'inscription du service pour créer la clé |
| **Enregistrer** | Chiffre et applique les valeurs saisies (champ vide = valeur conservée) |
| **Tester la saisie** | Vérifie en direct les valeurs tapées **sans les enregistrer** |
| **Tester les clés enregistrées** | Vérifie les valeurs du coffre |
| **Supprimer** | Efface la valeur du coffre (la valeur `.env` éventuelle reprend effet) |

Les champs restent en lecture seule tant que vous ne cliquez pas dedans : cela empêche le navigateur d'y injecter votre mot de passe de connexion.

## Services

| Service | Champ(s) | Sans cette clé | Test effectué |
| --- | --- | --- | --- |
| **Copernicus S3** | `ACCESS_KEY`, `SECRET_KEY` | **pas de scan radar possible** | lecture réelle de l'en-tête d'une image Sentinel-1, puis code d'erreur S3 précis (clé inconnue, secret faux, accès refusé) |
| **Global Fishing Watch** | `GFW_TOKEN` | pas d'événements, de détections SAR GFW, d'effort de pêche, d'identité ni d'Insights | requête sur l'API événements |
| **aisstream.io** | `AISSTREAM_API_KEY` | **pas d'AIS réel** : aucune corrélation (trace simulée en développement seulement), aucune règle 24/7 | connexion au flux et attente d'un message |
| **AISHub** | `AISHUB_USERNAME` | pas de second flux AIS | requête sur la zone ; affiche le nombre de navires |
| **OpenSanctions** | `OPENSANCTIONS_API_KEY` | pas de criblage sanctions | requête de correspondance de test |
| **VesselAPI** | `VESSELAPI_API_KEY` | pas de fiche registre ni de 3e flux AIS | une fiche registre (consomme 1 appel) ; affiche le quota restant du mois |
| **Notifications** | `ALERT_WEBHOOK_URL` | aucune notification envoyée | message de test sur **chacun des 6 canaux** |

Le webhook doit être une adresse **HTTPS publique** (protection contre les requêtes vers le réseau interne), sauf les hôtes explicitement autorisés comme le serveur de notifications de démonstration `notif-sink`. Mettre `{channel}` dans l'URL envoie chaque canal vers sa propre adresse.

!!! danger "Après une remise à neuf"
    `make reset` efface le coffre : toutes les clés sont à ressaisir.
