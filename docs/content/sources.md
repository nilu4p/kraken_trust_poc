# Sources de données

Liste à jour avec statut en direct : *Administration → Catalogue des sources*. Les clés se gèrent dans [Clés & intégrations](administration/cles.md).

## Imagerie satellite

| Source | Accès | Utilisation dans KRAKEN |
| --- | --- | --- |
| **Sentinel-1 GRD** (Copernicus) | compte gratuit + clés S3 | Détection radar des navires, de jour comme de nuit |
| **Sentinel-1 GRD — miroir Microsoft Planetary Computer** | libre, **sans clé** | Mêmes scènes, mêmes pixels : scan radar sans compte Copernicus, et secours automatique si Copernicus S3 échoue |
| **CNES GEODES** — PEPS Sentinel-1/2, THEIA S2 L2A | libre (recherche, aperçus) | Images et aperçus autour de chaque cible, redondance du catalogue Copernicus |
| **Copernicus CCM optique** — Pléiades, Pléiades Neo, SPOT, WorldView | recherche libre · téléchargement réservé | Identification visuelle très haute résolution (0,3 – 1,5 m) |
| **Copernicus CCM radar** — TerraSAR-X, COSMO-SkyMed, PAZ, ICEYE, RADARSAT | recherche libre · téléchargement réservé | Confirmation radar haute résolution |
| **NASA GIBS** — VIIRS couleurs vraies et bande jour/nuit | libre | Fonds de carte datés, lumières nocturnes des flottilles |
| **Sentinel-2 L2A — bandes NIR, SWIR, SCL** (Earth Search, AWS) | libre, **sans clé** | [Détection optique](moteur/detection-multicapteur.md#optique) des navires en eaux côtières (10 m) |
| **Landsat 8/9 Collection 2 L2** (Planetary Computer) | libre, **sans clé** | Détection optique 30 m et **anomalie thermique** (TIRS, température en K) |
| **NASA GIBS — VIIRS DNB radiance + I5 nuit** (SNPP, NOAA-20, NOAA-21) | libre | [Lumières nocturnes](moteur/detection-multicapteur.md#lumieres-nocturnes-viirs) détectées automatiquement, écran nuageux thermique |
| **EOX Sentinel-2 cloudless** | libre | Fond de carte sans nuages |
| **GOES-Est (GOES-19) ABI** via NASA GIBS — GeoColor et infrarouge bande 13 | libre | Image géostationnaire **toutes les 10 min** (~1–2 km), historique ≥ 90 jours : nuages et météo de toute la ZEE à l'instant de la frise |
| **Sentinel-2 L2A** via Earth Search (Element 84, AWS Open Data) | libre, sans clé | Vraies couleurs à **10 m** : dernière image avant l'instant, par tuile, nuages < 60 % ; rendue en tuiles par la plateforme |

## Navires (AIS, VMS, renseignement)

| Source | Accès | Utilisation |
| --- | --- | --- |
| **aisstream.io** | clé gratuite | Flux AIS continu (classes A et B), historisé 90 jours |
| **AISHub** | gratuit pour les membres partageant une station AIS | Second flux AIS fusionné |
| **VMS** (CNSP, DM Guyane) | export de l'administration, import NAF / CSV | Positions des navires sous licence |
| **Codes MID de l'UIT** | intégré | Pavillon d'après le MMSI, sans appel externe |
| **Global Fishing Watch** — événements | token gratuit | Coupures AIS, rencontres, flânages, pêches, escales au port (30 j) |
| **GFW 4Wings** — détections SAR | même token | Confirmation croisée, zones d'activité sans AIS (délai ≈ 5 j) |
| **GFW 4Wings** — effort de pêche | même token | Carte de chaleur de la pêche par pavillon (délai ≈ 4 j) ; [rapport d'activité de la ZEE](situation/activite.md) par jour, pavillon, engin et navire |
| **GFW** — identité, Insights | même token | Pavillon, engins, indicateurs de risque INN sur 12 mois |
| **OpenSanctions** | clé gratuite (intérêt public) | Navires sous sanctions internationales |
| **VesselAPI** | clé gratuite (150 appels / mois) · offres payantes | Registre des navires étrangers (IMO, type, année, tonnage, port d'attache, anciens noms) ; 3e flux AIS optionnel (offre payante) |
| **Liste INN combinée** (TMT / ORGP) | téléchargement libre, import | Liste de surveillance |

## Limites et zones

| Source | Accès | Utilisation |
| --- | --- | --- |
| **Marine Regions** (VLIZ) | libre | ZEE, zone contiguë, mer territoriale ; masque terre |
| **SHOM** | libre (licence ouverte) | Limites de pêche 100 NM et 3 NM |
| **WDPA** (UNEP-WCMC / UICN) | libre | Aires marines protégées |

## Côte et ouvrages

| Source | Accès | Utilisation |
| --- | --- | --- |
| **OpenStreetMap** — trait de côte, îlets, appontements, digues, bouées et balises (Overpass API) | libre (ODbL), copie livrée, mise à jour tous les 30 jours | Écarter les échos radar sur la côte, les îlets, les ouvrages et le balisage ; distance au rivage. Voir [Détection radar](moteur/detection-sar.md#cote-ilets-ouvrages-et-balises) |

## Océan et météo

| Source | Accès | Utilisation |
| --- | --- | --- |
| **Copernicus Marine** (Mercator Ocean) | libre, sans compte (WMTS public) | Courants, SST et fronts thermiques, chlorophylle ; surcouches carte |
| **NOAA ERDDAP** (miroir `upwell` en priorité, `coastwatch` en secours) | libre | Bathymétrie ETOPO : grille de toute la ZEE téléchargée une fois (≈ 2 s) et conservée un an, consultée hors ligne ; chlorophylle VIIRS (secours) |
| **Open-Meteo** | libre | Vent, houle, SST, courant → fiabilité radar |

## Intelligence artificielle (information seule)

| Source | Accès | Utilisation |
| --- | --- | --- |
| **xView3** — modèle classé 1er du concours xView3-SAR (MIT) | libre, service local (`make up-ai`) | Avis en % sur chaque cible radar (objet, navire, pêche, longueur), affiché et scellé, **sans effet** sur les alertes. Voir [Avis de l'IA](moteur/ia.md) |

## Mise en cache et résilience

Chaque source est appelée avec un délai limite et ses réponses sont **mises en cache** (de 30 minutes pour la météo à 30 jours pour les frontières). Si une source tombe, la plateforme utilise la dernière réponse connue quand elle existe et continue sans la couche concernée sinon ; l'incident est visible dans [Santé des sources](administration/catalogue.md#sante-des-sources).

## Sources non branchées

| Source | Raison |
| --- | --- |
| **VIIRS Boat Detection** (EOG / Colorado School of Mines) | accès programmatique réservé aux abonnés payants depuis juin 2026 |
| **DINAMIS** (Pléiades, SPOT, CNES/IGN) | réservé aux institutions publiques conventionnées ; programmation de prises de vue |
| **CO3D** (CNES / Airbus) | usages institutionnels et défense |
| **AIS satellitaire Kinéis / CLS** | contrat commercial ou institutionnel |
| **Météo-France AROME Outre-mer** | grilles GRIB ; redondant avec Open-Meteo et Copernicus Marine pour l'état de mer |
| **SWOT** (CNES / NASA) | altimétrie : intérêt indirect (tourbillons), non prioritaire |
