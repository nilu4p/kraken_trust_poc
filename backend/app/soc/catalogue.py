"""Catalogue of every data source the platform uses or can plug in, with access status.

``integrated`` = connector implemented. ``key`` = env / dashboard credential.
Restricted sources are documented with the procedure to obtain access.
"""

from __future__ import annotations

from typing import Any, Dict, List

from app.config import Settings, get_settings

SOURCES: List[Dict[str, Any]] = [
    # --- Satellite imagery -------------------------------------------------------
    {"id": "copernicus_s1", "name": "Sentinel-1 GRD (Copernicus)", "category": "Imagerie radar", "access": "libre (compte)",
     "integrated": True, "key": ("access_key", "secret_key"), "use": "Détection SAR tout temps, double polarisation",
     "url": "https://dataspace.copernicus.eu/"},
    {"id": "planetary_s1", "name": "Sentinel-1 GRD — miroir Microsoft Planetary Computer", "category": "Imagerie radar",
     "access": "libre (sans compte)", "integrated": True,
     "use": "Lecture des mêmes scènes radar sans clé Copernicus, et secours automatique si Copernicus S3 échoue",
     "url": "https://planetarycomputer.microsoft.com/dataset/sentinel-1-grd"},
    {"id": "xview3", "name": "xView3 — modèle IA de détection de navires sombres (1re place, MIT)", "category": "IA (information)",
     "access": "libre (service local, make up-ai)", "integrated": True, "key": ("xview3_url",),
     "use": "Avis en % (objet, navire, pêche, longueur) sur chaque cible radar — affiché et scellé, sans effet sur les alertes",
     "url": "https://github.com/BloodAxe/xView3-The-First-Place-Solution"},
    {"id": "cnes_geodes", "name": "CNES GEODES — PEPS Sentinel-1/2, THEIA S2 L2A, WaterQual", "category": "Imagerie (CNES)",
     "access": "libre (recherche, aperçus) · compte CNES (téléchargement)", "integrated": True,
     "use": "Couverture et aperçus réels autour des cibles, redondance du catalogue Copernicus",
     "url": "https://geodes.cnes.fr/"},
    {"id": "ccm_optical", "name": "Pléiades / Pléiades Neo / SPOT / WorldView (Copernicus CCM)", "category": "Imagerie THR optique",
     "access": "recherche libre · téléchargement réservé (utilisateurs Copernicus éligibles)", "integrated": True,
     "use": "Identification visuelle (0,3–1,5 m) des cibles", "url": "https://dataspace.copernicus.eu/explore-data/data-collections/copernicus-contributing-missions"},
    {"id": "ccm_sar", "name": "TerraSAR-X / COSMO-SkyMed / PAZ / ICEYE / RADARSAT (Copernicus CCM)", "category": "Imagerie THR radar",
     "access": "recherche libre · téléchargement réservé", "integrated": True,
     "use": "Confirmation radar haute résolution, nuit et nuages", "url": "https://dataspace.copernicus.eu/"},
    {"id": "dinamis", "name": "DINAMIS — Pléiades, Pléiades Neo, SPOT 6/7 (CNES/IGN)", "category": "Imagerie THR (CNES)",
     "access": "restreint — institutions publiques françaises (convention DINAMIS)", "integrated": False,
     "use": "Programmation de prises de vue Pléiades sur une zone suspecte (tasking)", "url": "https://dinamis.data-terra.org/"},
    {"id": "co3d", "name": "CO3D (CNES / Airbus) — constellation 50 cm stéréo", "category": "Imagerie THR (CNES)",
     "access": "restreint — usages institutionnels et défense", "integrated": False,
     "use": "Revisite fréquente THR, modèles 3D des ports et navires", "url": "https://cnes.fr/projets/co3d"},
    {"id": "nasa_gibs", "name": "NASA GIBS — VIIRS couleurs vraies et Day/Night Band", "category": "Imagerie quotidienne",
     "access": "libre", "integrated": True, "use": "Lumières nocturnes des flottilles de pêche", "url": "https://nasa-gibs.github.io/gibs-api-docs/"},
    {"id": "eog_vbd", "name": "VIIRS Boat Detection (NOAA / Colorado School of Mines)", "category": "Détections de navires",
     "access": "restreint — accès programmatique réservé aux abonnés payants depuis le 1er juin 2026 (navigateur seulement pour les comptes gratuits)",
     "integrated": False,
     "use": "Détections quotidiennes de bateaux éclairés la nuit, à croiser avec le SAR", "url": "https://eogdata.mines.edu/products/vbd/"},
    # --- AIS / vessel intelligence -----------------------------------------------
    {"id": "aisstream", "name": "aisstream.io — AIS terrestre temps réel (collecte continue)", "category": "AIS", "access": "clé gratuite",
     "integrated": True, "key": ("aisstream_api_key",),
     "use": "Image AIS 24/7 historisée (classes A et B) : corrélation au moment du passage radar, règles pêche étrangère, "
            "coupures AIS, rendez-vous en mer", "url": "https://aisstream.io/"},
    {"id": "gfw_4wings_sar", "name": "Global Fishing Watch — détections SAR Sentinel-1 (4Wings)", "category": "Détections de navires",
     "access": "clé gratuite (même token GFW)", "integrated": True, "key": ("gfw_token",),
     "use": "Confirmation croisée de nos cibles, carte des détections non appariées AIS (zones d'activité non déclarée)",
     "url": "https://globalfishingwatch.org/our-apis/documentation"},
    {"id": "gfw_4wings_effort", "name": "Global Fishing Watch — effort de pêche AIS par pavillon (4Wings)", "category": "AIS",
     "access": "clé gratuite (même token GFW)", "integrated": True, "key": ("gfw_token",),
     "use": "Carte de chaleur de la pêche par pavillon (30 j)", "url": "https://globalfishingwatch.org/our-apis/documentation"},
    {"id": "gfw_identity", "name": "Global Fishing Watch — identité des navires", "category": "AIS",
     "access": "clé gratuite (même token GFW)", "integrated": True, "key": ("gfw_token",),
     "use": "Pavillon d'immatriculation et engin de pêche d'un MMSI (règle pêche étrangère)",
     "url": "https://globalfishingwatch.org/our-apis/documentation"},
    {"id": "itu_mid", "name": "Codes MID de l'UIT (pavillon d'après le MMSI)", "category": "AIS", "access": "libre (embarqué)",
     "integrated": True, "use": "Pavillon de tout navire AIS sans appel externe (745 = Guyane, 710 = Brésil, 765 = Suriname…)",
     "url": "https://www.itu.int/en/ITU-R/terrestrial/fmd/Pages/mid.aspx"},
    {"id": "global_fishing_watch", "name": "Global Fishing Watch — événements (coupures AIS, rencontres, flânage, pêche)",
     "category": "AIS", "access": "clé gratuite", "integrated": True, "key": ("gfw_token",),
     "use": "Comportements suspects dans la ZEE, score de risque", "url": "https://globalfishingwatch.org/our-apis/"},
    {"id": "aishub", "name": "AISHub — 2e flux AIS terrestre (fusionné)", "category": "AIS",
     "access": "gratuit pour les membres qui partagent le flux d'une station AIS", "integrated": True, "key": ("aishub_username",),
     "use": "Stations différentes d'aisstream : navires entendus par un seul réseau, moins de fausses coupures AIS",
     "url": "https://www.aishub.net/"},
    {"id": "gfw_insights", "name": "Global Fishing Watch — Insights (indicateurs de risque INN)", "category": "Renseignement navire",
     "access": "clé gratuite (même token GFW)", "integrated": True, "key": ("gfw_token",),
     "use": "Inscription INN des ORGP, pêche en AMP intégrale ou sans autorisation ORGP, coupures AIS, changements de pavillon (12 mois)",
     "url": "https://globalfishingwatch.org/our-apis/documentation"},
    {"id": "opensanctions", "name": "OpenSanctions — navires sous sanctions (OFAC, UE, ONU, UK…)", "category": "Renseignement navire",
     "access": "clé gratuite pour usage d'intérêt public · licence pour usage commercial", "integrated": True,
     "key": ("opensanctions_api_key",), "use": "Criblage IMO / MMSI / nom des navires présents dans la ZEE",
     "url": "https://www.opensanctions.org/api/"},
    {"id": "vesselapi", "name": "VesselAPI — registre des navires et positions AIS", "category": "Renseignement navire",
     "access": "clé gratuite (150 appels / mois) · offres payantes à partir de 1 500 appels / mois", "integrated": True,
     "key": ("vesselapi_api_key",),
     "use": "Fiche registre des navires étrangers (IMO, type, année, tonnage, port d'attache, anciens noms) avant criblage "
            "sanctions ; 3e flux AIS optionnel sur la ZEE (offre payante)", "url": "https://vesselapi.com/"},
    {"id": "kineis_cls", "name": "AIS satellitaire Kinéis / CLS (filiales CNES)", "category": "AIS satellitaire",
     "access": "restreint — contrat commercial ou institutionnel", "integrated": False,
     "use": "AIS au large, hors portée des stations côtières", "url": "https://www.kineis.com/"},
    {"id": "vms", "name": "VMS — balises des navires de pêche (DPMA / CNSP Étel)", "category": "Surveillance des pêches",
     "access": "restreint — export fourni par l'administration, import NAF / CSV (Administration › Sources)", "integrated": True,
     "use": "Positions des navires sous licence fusionnées à l'AIS : un écho SAR sans VMS ni AIS est fortement suspect, "
            "les navires sous licence ne déclenchent pas la règle pêche étrangère",
     "url": "https://www.mer.gouv.fr/"},
    {"id": "iuu_list", "name": "Combined IUU Vessel List (TMT, ORGP)", "category": "Listes de surveillance",
     "access": "libre (import Excel)", "integrated": True, "use": "Navires INN : correspondance MMSI / IMO / nom",
     "url": "https://www.iuu-vessels.org/Home/Download"},
    # --- Regulatory & ocean context ----------------------------------------------
    {"id": "marine_regions", "name": "Marine Regions — ZEE, 24 NM, 12 NM", "category": "Limites", "access": "libre",
     "integrated": True, "use": "Zonage légal, masque terre", "url": "https://www.marineregions.org/"},
    {"id": "shom", "name": "SHOM — limites de pêche 100 NM (R951-14) et 3 NM", "category": "Limites", "access": "libre (licence ouverte)",
     "integrated": True, "use": "Limites réglementaires de pêche de Guyane", "url": "https://data.shom.fr/"},
    {"id": "wdpa", "name": "WDPA — aires marines protégées (UNEP-WCMC / UICN, Protected Planet)", "category": "Limites",
     "access": "libre (service public UNEP-WCMC)", "integrated": True,
     "use": "Grand Connétable, Amana, réserves côtières voisines : règle pêche en aire protégée, score de risque",
     "url": "https://www.protectedplanet.net/"},
    {"id": "open_meteo", "name": "Open-Meteo — vent, houle, courants, SST", "category": "Océan / météo", "access": "libre",
     "integrated": True, "use": "Fiabilité de la détection SAR", "url": "https://open-meteo.com/"},
    {"id": "noaa_erddap", "name": "NOAA ERDDAP — bathymétrie ETOPO, chlorophylle VIIRS", "category": "Océan / météo",
     "access": "libre", "integrated": True, "use": "Hauts-fonds (faux positifs), eaux productives (zones de pêche)",
     "url": "https://coastwatch.pfeg.noaa.gov/erddap/"},
    {"id": "cmems", "name": "Copernicus Marine Service (Mercator Ocean, Toulouse)", "category": "Océan / météo",
     "access": "libre (WMTS public, sans compte)", "integrated": True,
     "use": "Courants, température de surface et fronts thermiques, chlorophylle quotidienne : contexte des cibles et couches carte",
     "url": "https://marine.copernicus.eu/"},
    {"id": "meteo_france", "name": "Météo-France — AROME Outre-mer Guyane", "category": "Océan / météo",
     "access": "clé gratuite (portail API) · grilles WCS/GRIB", "integrated": False,
     "use": "Vent haute résolution locale ; non branché : l'état de mer utile au SAR est déjà fourni par Open-Meteo et Copernicus Marine",
     "url": "https://portail-api.meteofrance.fr/"},
    {"id": "swot", "name": "SWOT (CNES / NASA) — altimétrie large fauchée", "category": "Océan (CNES)", "access": "libre (AVISO / PO.DAAC)",
     "integrated": False, "use": "Tourbillons et fronts océaniques favorables à la pêche", "url": "https://swot.cnes.fr/"},
]


def describe(settings: Settings | None = None) -> List[Dict[str, Any]]:
    cfg = settings or get_settings()
    result = []
    for source in SOURCES:
        keys = source.get("key") or ()
        configured = all(getattr(cfg, k, "") for k in keys) if keys else source["integrated"]
        status = "active" if source["integrated"] and configured else ("needs_key" if source["integrated"] else "available")
        result.append({**{k: v for k, v in source.items() if k != "key"}, "status": status})
    from app.sources.custom import list_sources

    kinds = {"features": "objets (API / GeoJSON / CSV)", "tiles": "tuiles XYZ / WMTS", "wms": "OGC WMS"}
    for custom in list_sources(cfg):
        result.append({
            "id": f"custom:{custom['id']}", "name": custom["name"], "category": custom.get("category") or "Source personnalisée",
            "access": "secret enregistré" if custom["has_secret"] else "libre", "integrated": True,
            "use": custom.get("description") or f"Source personnalisée — {kinds.get(custom['kind'], custom['kind'])}",
            "url": "", "status": "active" if custom.get("enabled") else "disabled", "custom": True,
        })
    return result
