# Glossaire

## A – C

AEM — Action de l'État en mer
:   Organisation par laquelle l'État exerce ses missions en mer (police des pêches, sauvetage, sûreté), coordonnée en Guyane par le Préfet, délégué du gouvernement.

AIS — Automatic Identification System
:   Émetteur VHF qui diffuse toutes les quelques secondes l'identité (MMSI, nom, indicatif, IMO, type), la position, la route et la vitesse d'un navire. Obligatoire pour les grands navires et beaucoup de navires de pêche, mais **il peut être éteint** : c'est tout l'enjeu.

Ambiguïté azimutale
:   Faux écho radar : réplique d'un écho très brillant, décalée de quelques kilomètres dans le sens du vol du satellite. Écartée automatiquement.

AIS classe A / classe B
:   Classe A : émetteurs des navires professionnels (fréquents, complets). Classe B : émetteurs simplifiés (plaisance, petite pêche), moins fréquents.

Allongement
:   Rapport entre la longueur et la largeur d'une tache radar. Un navire est allongé (≥ 2), le bruit est compact (≈ 1).

AMP — Aire marine protégée
:   Espace maritime protégé (réserve naturelle, parc) où la pêche peut être limitée ou interdite. Source : WDPA.

Bathymétrie
:   Profondeur de la mer. Utilisée pour écarter les échos sur terre émergée ou hauts-fonds.

C2PA
:   Standard de manifeste signé attaché à une image, qui prouve son origine et son intégrité. Voir [Preuves](preuves.md).

CA-CFAR
:   Détecteur radar qui compare chaque pixel à la moyenne de la mer qui l'entoure. Voir [Détection radar](moteur/detection-sar.md).

Chatoiement (speckle)
:   Grain aléatoire propre aux images radar ; peut créer de faux points brillants isolés.

Chlorophylle-a
:   Pigment du phytoplancton ; une forte concentration (≥ 1 mg/m³) signale des eaux riches, donc poissonneuses.

CFR
:   Numéro d'immatriculation communautaire d'un navire de pêche (registre de la flotte de l'UE), présent dans les messages VMS.

Chronologie
:   Historique horodaté de tout ce qui arrive à un incident.

Clé maître
:   Clé de chiffrement (`secrets/master_key` ou `MASTER_KEY`, hors des sauvegardes) qui protège toutes les clés d'API et les clés de signature. Sa perte rend ces secrets illisibles.

Coffre
:   Stockage chiffré des clés d'API et du webhook, alimenté depuis *Administration → Clés & intégrations*.

COG — Cloud Optimized GeoTIFF
:   Format d'image lisible par morceaux à distance, sans tout télécharger.

Corrélation
:   (1) Appariement d'un écho radar avec une position AIS ([Corrélation radar / AIS](moteur/correlation-ais.md)). (2) Regroupement de détections proches dans un même incident ([Cycle de vie](incidents/cycle-de-vie.md)).

Corroboration multi-capteurs
:   Confirmation d'une cible radar par un autre capteur indépendant (optique Sentinel-2 / Landsat, lumières nocturnes VIIRS, détections radar GFW) au même endroit et au même moment. Voir [Corroboration multi-capteurs](moteur/corroboration.md).

Couverture AIS
:   Part du temps où un navire est effectivement entendu. Faible = souvent silencieux.

CROSS
:   Centre régional opérationnel de surveillance et de sauvetage ; en Guyane, CROSS Antilles-Guyane.

## D – I

DNB — Day/Night Band
:   Bande « jour/nuit » des radiomètres VIIRS, assez sensible pour voir de nuit l'éclairage d'un navire depuis l'espace (~750 m).

Dark vessel — navire sans AIS
:   Navire détecté par une autre source (radar) mais qui ne déclare pas sa position.

Dead reckoning — estime
:   Calcul de la position actuelle d'un navire à partir d'une position passée, de sa route et de sa vitesse.

Double polarisation
:   Image radar acquise en deux polarisations (VV et VH). Un navire réel apparaît dans les deux.

Empreinte (hash) SHA-256
:   Valeur de 64 caractères calculée à partir d'un fichier ; change complètement si le fichier change d'un seul bit.

Escalade
:   Transmission d'un incident qualifié à une autorité de l'AEM.

Estime
:   Voir *Dead reckoning*.

Fiabilité de détection
:   Probabilité qu'un écho radar soit un navire (0 – 100 %). Voir [Indice de fiabilité](moteur/fiabilite.md).

Flânage (loitering)
:   Navire qui traîne lentement de façon sinueuse ; typique d'un transporteur qui attend un transbordement.

Front thermique
:   Zone où la température de surface change rapidement sur une courte distance ; les poissons s'y concentrent.

GCP — Ground Control Points
:   Points qui relient les pixels de l'image radar à des coordonnées géographiques.

GFW — Global Fishing Watch
:   ONG qui analyse l'AIS et l'imagerie satellite mondiale pour rendre visible l'activité de pêche.

GRD — Ground Range Detected
:   Produit Sentinel-1 d'amplitude projetée au sol, utilisé pour la détection de navires.

Haut-fond
:   Zone peu profonde ; rochers et bancs de vase peuvent y créer des échos radar.

IMO — numéro OMI
:   Identifiant à 7 chiffres attribué à vie à la coque d'un navire : il ne change pas avec le nom ou le pavillon.

Incident
:   Anomalie à traiter dans la file : navire sans AIS, comportement suspect, navire listé…

Indicatif radio (IRCS)
:   Indicatif d'appel international du navire.

Indice de confiance
:   Force du signal radar seule (50 – 99 %), à distinguer de la fiabilité.

INN — pêche illicite, non déclarée et non réglementée
:   En anglais IUU. Pêche sans licence, dans une zone interdite, au-delà des quotas, ou non déclarée.

IW — Interferometric Wide swath
:   Mode d'acquisition Sentinel-1 : bande de 250 km, pixel de 10 m.

## K – P

KPI
:   Indicateur clé : incidents ouverts, SLA dépassés, MTTA, MTTR.

Liste INN combinée
:   Fusion, par TMT, des listes de navires INN publiées par les ORGP.

MID — Maritime Identification Digits
:   3 premiers chiffres du MMSI, qui identifient le pays (745 = Guyane, 710 = Brésil, 765 = Suriname, 775 = Venezuela).

Mille nautique (NM)
:   1 852 m. Les limites maritimes s'expriment en milles.

Mise en cache
:   Conservation locale des réponses des sources pour aller plus vite, limiter les quotas et continuer à fonctionner si une source tombe.

MMSI
:   Identifiant AIS à 9 chiffres d'une station radio de navire.

MTTA / MTTR
:   Délai moyen de prise en charge / de clôture des incidents sur 30 jours.

NAF — North Atlantic Format
:   Format texte standard des messages VMS (`//SR// … //ER//`).

Nœud (nd)
:   Vitesse d'un mille nautique par heure = 1,852 km/h.

Observation
:   Une détection rattachée à un incident (passage radar ou déclenchement de règle). Un incident peut en compter plusieurs.

ORGP — Organisation régionale de gestion des pêches
:   Organisme international qui gère une pêcherie (CICTA/ICCAT pour les thonidés de l'Atlantique…) et publie une liste INN.

OSTIA
:   Produit d'analyse quotidienne de la température de surface (Met Office, diffusé par Copernicus Marine).

Pavillon
:   État d'immatriculation d'un navire, dont il relève juridiquement.

Pavillon de complaisance
:   Immatriculation dans un État aux contrôles faibles (Panama, Libéria…), fréquente chez les navires INN.

Persistance
:   Écho présent au même endroit sur plusieurs scènes : objet fixe probable.

PKI
:   Infrastructure de clés : l'autorité de certification de la plateforme qui signe les preuves.

Playbook
:   Liste d'étapes de traitement d'un incident.

Polarisation (VV, VH, HH, HV)
:   Orientation de l'onde radar à l'émission et à la réception. VH / HV (croisée) : meilleur contraste navire / mer.

## Q – Z

SWIR — infrarouge à ondes courtes
:   Bande 1,6 µm de Sentinel-2 et Landsat. L'eau l'absorbe même chargée de vase ; une coque le réfléchit. Sert à distinguer un navire d'un front de turbidité.

Qualification
:   Étape où l'analyste conclut qu'un incident concerne un navire suspect.

Résolution
:   Motif de clôture d'un incident (Pêche INN confirmée, Légitime, Fausse alarme, Doublon, Sans suite).

Scène
:   Une acquisition Sentinel-1 : image radar d'environ 250 × 170 km prise lors d'un passage du satellite.

Score de risque
:   Niveau de suspicion d'un navire sans AIS (0 – 100). Voir [Score de risque](moteur/risque.md).

Sévérité (P1 – P4)
:   Priorité d'un incident, déduite du score de risque.

SAR — radar à synthèse d'ouverture
:   Radar imageur satellite, indépendant de la lumière et des nuages.

SLA
:   Délai maximal de prise en charge et de résolution, fixé par sévérité.

SNR — rapport signal / bruit
:   Écart entre la brillance d'une cible et la mer locale, en écarts-types.

SOC
:   Centre opérationnel qui détecte, qualifie, escalade et traite les incidents.

SST — Sea Surface Temperature
:   Température de surface de la mer.

STAC
:   Catalogue standard d'images satellite, interrogé pour trouver les scènes Sentinel-1.

Transbordement
:   Transfert de poisson, carburant ou équipage d'un navire à l'autre en mer ; permet de blanchir des captures INN.

Type AIS
:   Code déclaré par le navire : 30 = pêche, 31-32 = remorquage, 37 = plaisance, 52 = remorqueur, 60-69 = passagers, 70-79 = cargo, 80-89 = pétrolier.

UTC / Zulu
:   Temps universel coordonné, référence de toutes les heures de la plateforme (suffixe « Z »).

Veille automatique
:   Analyse automatique de chaque nouvelle scène Sentinel-1 sur la ZEE (paramètre `auto_scan_interval_minutes`).

Vignette (chip)
:   Petit extrait d'image radar centré sur une cible.

VMS — Vessel Monitoring System
:   Balise satellitaire obligatoire des navires de pêche sous licence, suivie par l'administration.

Webhook
:   Adresse web à laquelle la plateforme envoie ses notifications ; fournie par Slack, Mattermost, Teams…

WDPA
:   Base mondiale des aires protégées (UNEP-WCMC / UICN).

WMTS
:   Protocole standard de diffusion de tuiles cartographiques (utilisé par Copernicus Marine).

ZEE — zone économique exclusive
:   Jusqu'à 200 milles des côtes ; l'État côtier y a l'exclusivité de l'exploitation des ressources, dont la pêche.

Zone contiguë
:   De 12 à 24 milles ; contrôles douaniers, sanitaires et d'immigration.

Mer territoriale
:   Jusqu'à 12 milles ; souveraineté de l'État côtier.
