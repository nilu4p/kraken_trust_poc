# Obtenir les clés des services externes

Chaque clé se saisit dans *Administration → Clés & intégrations* (bouton **Obtenir ↗** = page d'inscription). Les procédures des fournisseurs peuvent évoluer ; les étapes ci-dessous décrivent leur fonctionnement à la date de rédaction.

## Copernicus Data Space — clés S3 (obligatoire)

Permet de lire les pixels des images Sentinel-1.

1. Créez un compte gratuit sur **dataspace.copernicus.eu** (*Register*).
2. Connecté, ouvrez le **gestionnaire de clés S3** : `eodata-s3keysmanager.dataspace.copernicus.eu`.
3. **Add credentials** : une paire *Access key* / *Secret key* est générée. **Copiez la clé secrète immédiatement** : elle n'est plus réaffichée.
4. Saisissez `ACCESS_KEY` et `SECRET_KEY`, **Tester la saisie**, **Enregistrer**.

!!! warning "Expiration"
    Les clés S3 peuvent avoir une date d'expiration et un quota mensuel. Si les scans échouent avec « ACCESS_KEY inconnue ou expirée » ou « Accès S3 refusé », générez une nouvelle paire.

## aisstream.io — AIS temps réel (fortement recommandé)

1. Sur **aisstream.io**, connectez-vous (compte GitHub).
2. Menu **API Keys** → créez une clé.
3. Saisissez `AISSTREAM_API_KEY` et testez : « Clé validée (flux AIS reçu) » ou « Clé acceptée (aucun navire entendu) » sont tous deux corrects.

La couverture dépend des stations bénévoles proches de la Guyane : elle est bonne près des côtes, faible au large.

## Global Fishing Watch — token API (recommandé)

1. Créez un compte sur **globalfishingwatch.org**.
2. Ouvrez la page **API tokens** (`globalfishingwatch.org/our-apis/tokens`) et remplissez la demande (nom de l'application, usage non commercial).
3. Copiez le token (longue chaîne) dans `GFW_TOKEN` et testez.

Un seul token donne accès aux événements, aux détections SAR, à l'effort de pêche, à l'identité des navires et aux Insights. GFW limite les requêtes simultanées : la plateforme les met en file et en cache.

## Notifications — webhook

=== "Slack"

    1. *api.slack.com/apps* → **Create New App** → *From scratch*.
    2. **Incoming Webhooks** → activer → **Add New Webhook to Workspace** → choisir le canal.
    3. Copier l'URL `https://hooks.slack.com/services/…` dans `ALERT_WEBHOOK_URL`.
    4. Une URL Slack correspond à un canal : sans `{channel}`, tout arrive dans ce canal.

=== "Mattermost"

    1. *Intégrations → Webhooks entrants → Ajouter*.
    2. Copier l'URL dans `ALERT_WEBHOOK_URL`.
    3. Mattermost accepte le champ `channel` du message : créez les canaux `kraken-alertes`, `kraken-escalades`, `kraken-sla`, `kraken-surveillance`, `kraken-operations`, `kraken-securite` pour une répartition automatique.

=== "Démonstration"

    Déjà configuré par défaut : `http://notif-sink:8090/hooks/__CHANNEL__`, consultable sur la page `/notifs/` de la console (`make notifs`).

## OpenSanctions (facultatif)

1. Créez un compte sur **opensanctions.org**.
2. Demandez une clé API ; pour un usage d'intérêt public (administration, recherche), demandez l'**exemption gratuite** en décrivant la mission.
3. Saisissez `OPENSANCTIONS_API_KEY` et testez.

## VesselAPI (facultatif)

1. Créez un compte sur **vesselapi.com** (offre gratuite sans carte bancaire : 150 appels par mois).
2. Copiez la clé API dans `VESSELAPI_API_KEY` et testez (le test consomme **un** appel).
3. Réglez `vesselapi_monthly_calls` sur le quota de votre offre (*Administration → Paramètres*).
4. Avec une offre payante, activez le flux de positions en réglant `vesselapi_poll_minutes` (60 avec Basic, 10 à 15 avec Starter).

## AISHub (facultatif)

AISHub est un réseau d'échange : l'accès est gratuit **en échange du partage du flux d'une station AIS**.

1. Installez un récepteur AIS (antenne VHF + récepteur, quelques centaines d'euros) idéalement sur la côte guyanaise.
2. Inscrivez-vous sur **aishub.net** (*Join us*) et configurez l'envoi du flux de votre station.
3. Le nom d'utilisateur reçu se saisit dans `AISHUB_USERNAME`.

Une station côtière en Guyane améliore à la fois AISHub et la couverture locale de toute la plateforme.

## Sans compte

Copernicus Marine, WDPA, Marine Regions, SHOM, NOAA ERDDAP, Open-Meteo, NASA GIBS, EOX, CNES GEODES (recherche) : rien à configurer.
