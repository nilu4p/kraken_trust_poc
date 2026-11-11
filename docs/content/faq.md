# FAQ et dépannage

## Carte

??? question "La carte OpenStreetMap affiche « Access blocked » / tile usage policy"
    OpenStreetMap exige l'en-tête `Referer`. La plateforme l'envoie pour les tuiles (origine du site uniquement). Si le blocage persiste (usage intensif, accès depuis `localhost`), choisissez le fond **Sentinel-2 sans nuages (EOX)** dans *Panneau latéral → Carte*. Pour une démonstration publique, préférez un serveur de tuiles dédié.

??? question "« AIS continu : connecté, aucun message depuis N min »"
    Le flux aisstream fonctionne mais **aucun de ses récepteurs** (stations bénévoles) ne couvre la zone : rien n'est reçu. Toutes les règles 24/7 et la corrélation radar en dépendent. Solutions : compte **AISHub** avec une station AIS côtière en Guyane (meilleure option), import **VMS** régulier, ou AIS satellitaire commercial. Vérification : `make logs backend | grep "AIS collector"` (résumé toutes les 10 minutes).

??? question "Je ne vois aucune flèche AIS"
    Vérifiez *Contexte → Couverture temporelle → AIS continu*. Gris = clé aisstream.io absente (*Administration → Clés & intégrations*). Jaune = pas de signal récent : panne du flux (voir *Santé des sources*). Vérifiez aussi que la couche *Navires AIS* est cochée.

??? question "Les couches GFW sont vides"
    Il faut un token Global Fishing Watch. Les détections SAR GFW et l'effort de pêche sont publiés avec quelques jours de retard et peuvent mettre jusqu'à 2 minutes à charger.

??? question "La surcouche océan n'apparaît pas"
    Les données Copernicus Marine ont une résolution de plusieurs kilomètres : à fort zoom, l'image est pixelisée mais présente. Si rien n'apparaît, la version du produit a pu changer chez Copernicus Marine (voir *Santé des sources*, ligne `cmems`) : un administrateur doit mettre à jour l'identifiant de la couche.

??? question "Les lumières nocturnes VIIRS n'affichent rien"
    Vérifiez le satellite (*Carte → Imagerie satellite nocturne*) : pour **Suomi-NPP**, la console recule déjà automatiquement à la dernière mosaïque complète (J-2) ; **NOAA-20** (par défaut) est publié dès J-1. Un voile gris ou une carte vide signifie une mosaïque non publiée pour la date choisie : reculez d'un jour ou changez de satellite. Les nuages épais masquent aussi les lumières.

??? question "Brancher une nouvelle API ou un service WMS"
    *Administration → Sources personnalisées* : décrivez la source, **Tester** montre la requête envoyée, la réponse et les champs détectés, puis enregistrez. Voir [Sources personnalisées](administration/sources-personnalisees.md).

## Scans

??? question "« Clés Copernicus refusées » alors que mes scans marchaient"
    Les clés S3 Copernicus expirent ou peuvent être révoquées. Régénérez-les sur le gestionnaire de clés S3 de Copernicus et testez-les avec **Tester la saisie**. Le message indique le code exact (clé inconnue, secret erroné, accès refusé).

??? question "Que veut dire le message d'erreur du scan ?"
    | Message | Cause | Action |
    | --- | --- | --- |
    | SECRET_KEY Copernicus refusée | clé secrète erronée | ressaisir la paire de clés |
    | ACCESS_KEY Copernicus inconnue ou expirée | clé expirée ou supprimée | générer une nouvelle paire |
    | Accès S3 refusé (403) | clés révoquées ou quota mensuel atteint | nouvelle paire, ou attendre le mois suivant |
    | Copernicus S3 injoignable (réseau / DNS) | pas d'accès Internet sortant ou panne Copernicus | vérifier le réseau, réessayer |
    | Erreur TLS vers Copernicus S3 | proxy ou certificats du réseau | administrateur système |
    | Fichier SAR introuvable sur S3 | produit retiré du catalogue | choisir une autre scène |
    | Aucune scène Sentinel-1 disponible | catalogue STAC injoignable, ou aucun passage récent | réessayer ; augmenter `stac_lookback_days` |
    | Aucun asset SAR S3 sur la scène STAC | scène incomplète | choisir une autre scène |
    | La scène ne recouvre pas la ZEE | scène hors zone | choisir une autre scène |
    | rasterio/GDAL non chargeable | problème d'installation du serveur | administrateur : `make build` |
    | Un scan est déjà en cours | un seul scan à la fois | attendre la fin |
    | Erreur interne du pipeline | anomalie inattendue | `make logs`, signaler |

??? question "« La scène ne recouvre pas la ZEE »"
    La scène choisie passe à côté de la Guyane. Choisissez *Dernière scène Sentinel-1* ou une autre scène.

??? question "Le scan ne trouve rien"
    Possible et normal : peu de navires, ou des navires tous expliqués par l'AIS / VMS. Par mer forte, beaucoup d'échos sont écartés (fiabilité). Un analyste peut abaisser temporairement `cfar_k` par mer calme.

??? question "Pourquoi « AIS simulé — à confirmer » ?"
    Instance de **développement** sans aucun flux AIS : une trace fictive sert à la démonstration. Le risque est pondéré (×0,85). En production (`ENVIRONMENT=production`), aucune trace fictive n'est jamais utilisée : la cible apparaît « Aucune position AIS au moment du passage ». Configurez aisstream.io.

## Incidents

??? question "Je ne peux pas clore un incident"
    Un analyste ne peut clore que s'il est *Nouveau* ou *En triage* et avec la résolution *Activité légitime*, *Fausse alarme* ou *Doublon*. Sinon, un superviseur doit le faire. Voir [Cycle de vie](incidents/cycle-de-vie.md).

??? question "Des navires vénézuéliens (ou autres) sous licence déclenchent « pêche étrangère »"
    Ajoutez-les aux **[navires autorisés](administration/autorises.md)** (import de la liste des licences, avec MMSI, IMO ou indicatif). Ils passent en « Pêcheur sous licence » et ne déclenchent plus la règle tant que la licence est valide.

??? question "Trop de fausses alarmes"
    Classez-les en **Fausse alarme** : la plateforme apprend (−0,45 de fiabilité au même endroit). Si cela persiste, un administrateur peut relever `min_detection_quality` (0,4) ou `cfar_k` (6).

??? question "Le même navire a plusieurs incidents"
    Les détections radar sont regroupées dans un rayon de 15 km et 72 h. Un navire qui a beaucoup bougé ou réapparaît plus tard ouvre un nouvel incident : classez le second en **Doublon**, ou augmentez `incident_correlation_km`.

## Compte

??? question "Mon compte est verrouillé"
    Après 5 échecs, attendez 15 minutes, ou demandez à un administrateur de **Réinitialiser** votre mot de passe.

??? question "« Scan en file d'attente » ne démarre pas"
    Le scan attend le worker de scan. Vérifiez qu'il tourne : `make status` (service `worker-scan`) et `make workers`. Un scan déjà en cours ailleurs bloque aussi le suivant (un seul scan à la fois).

??? question "« Scan interrompu (processus arrêté) »"
    Le worker de scan s'est arrêté pendant l'analyse (redémarrage, manque de mémoire). Relancez simplement le scan ; si cela se répète, consultez `make workers`.

??? question "Où sont passées mes données après la mise à jour vers PostgreSQL ?"
    Elles ont été migrées automatiquement au premier démarrage. L'ancien fichier est conservé sous `data/kraken.db.migrated-<date>` ; l'opération figure dans l'audit (`system.migrate`). Vérifiez vos incidents et comptes, puis supprimez ce fichier.

??? question "Je suis déconnecté régulièrement"
    Les sessions expirent après 30 minutes d'inactivité et 8 heures au total : c'est voulu.

??? question "La documentation demande de se connecter"
    La base de connaissances est protégée par le portail de la plateforme : connectez-vous avec votre compte KRAKEN (tous les rôles y ont accès), vous êtes ramené à la page demandée. Une session expirée (30 min d'inactivité) redemande la connexion.
