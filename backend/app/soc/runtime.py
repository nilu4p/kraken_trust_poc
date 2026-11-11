"""Operational settings editable by administrators at runtime (persisted, audited)."""

from __future__ import annotations

import datetime
import json
import logging
from typing import Any, Dict, Optional

from app.config import Settings, get_settings
from app.core import audit
from app.db import db, read_db

logger = logging.getLogger(__name__)

# name -> (type, min, max, label, explanation shown in the administration UI)
EDITABLE: Dict[str, Any] = {
    "auto_scan_interval_minutes": (
        int, 0, 1440, "Veille automatique — intervalle de scan (min)",
        {
            "unit": "minutes",
            "default": 0,
            "what": "Fréquence à laquelle la plateforme cherche une nouvelle scène Sentinel-1 non traitée sur la ZEE et l'analyse seule. 0 désactive la veille : seuls les scans manuels sont effectués.",
            "higher": "Moins de requêtes vers Copernicus, mais délai plus long entre le passage du satellite et l'alerte.",
            "lower": "Alertes plus tôt après chaque passage ; Sentinel-1 ne repasse que toutes les quelques dizaines d'heures, descendre sous 60 min n'apporte rien et consomme du quota.",
            "advice": "60 à 180 min en exploitation.",
        },
    ),
    "min_detection_quality": (
        float, 0.0, 1.0, "Fiabilité minimale pour lever une alerte",
        {
            "unit": "score 0–1",
            "default": 0.25,
            "what": "Seuil de l'indice de fiabilité (double polarisation, contraste, forme, bathymétrie, persistance, fausses alarmes signalées). Une cible en dessous est écartée et n'ouvre pas d'incident.",
            "higher": "Moins de fausses alarmes dans la file, au risque de manquer de petites embarcations (pirogues, tapouilles).",
            "lower": "Remonte davantage de cibles douteuses : plus de travail de triage pour les analystes.",
            "advice": "0,25 par défaut ; 0,4 si la file est saturée de fausses alarmes.",
        },
    ),
    "cfar_k": (
        float, 3.0, 10.0, "Sensibilité du détecteur radar (CFAR k·σ)",
        {
            "unit": "écarts-types",
            "default": 5.0,
            "what": "Un pixel est retenu s'il dépasse la moyenne du fouillis de mer local de k écarts-types. C'est le réglage principal du compromis détection / fausses alarmes.",
            "higher": "Seules les cibles très brillantes (coques métalliques, grands navires) restent : moins de fausses alarmes, mais les petites embarcations en bois peuvent disparaître.",
            "lower": "Détecte des cibles plus faibles, mais le chatoiement et les vagues génèrent plus de faux échos, surtout par mer forte.",
            "advice": "5 par défaut ; 4 par mer calme pour les petites pêches, 6 par mer forte.",
        },
    ),
    "incident_correlation_km": (
        float, 1.0, 100.0, "Rayon de corrélation des détections (km)",
        {
            "unit": "km",
            "default": 15.0,
            "what": "Une nouvelle détection à moins de cette distance d'un incident ouvert est rattachée à cet incident (nouvelle observation) au lieu d'en créer un nouveau.",
            "higher": "Moins de doublons, mais deux navires distincts proches risquent d'être fusionnés dans le même incident.",
            "lower": "Un même navire qui se déplace entre deux passages satellite peut ouvrir plusieurs incidents.",
            "advice": "10–20 km : un chalutier en pêche (3–4 nœuds) parcourt ~15 km en 2 h, mais reste souvent plusieurs jours sur le même lieu de pêche.",
        },
    ),
    "sensor_watch_enabled": (
        int, 0, 1, "Veille multi-capteurs (optique, thermique, lumières nocturnes)",
        {
            "unit": "0 / 1",
            "default": 1,
            "what": "1 : chaque heure, la plateforme cherche les nouvelles images Sentinel-2 et Landsat 8/9 peu nuageuses sur la ZEE et les nuits VIIRS (3 satellites), y détecte les navires (contraste proche infrarouge, anomalie thermique, lumières) et garde ces détections pour corroborer les cibles radar (bloc « Corroboration multi-capteurs » de l'aide à la décision). 0 : analyses uniquement à la demande.",
            "higher": "Davantage de cibles radar confirmées par un second capteur, sans action de l'opérateur ; quelques centaines de Mo lus par jour sur les dépôts publics.",
            "lower": "Aucune lecture automatique : seules les analyses optiques lancées depuis l'aide à la décision d'un incident sont faites.",
            "advice": "1 en exploitation.",
        },
    ),
    "optical_max_cloud": (
        float, 0.0, 100.0, "Imagerie optique — couverture nuageuse maximale de la scène (%)",
        {
            "unit": "%",
            "default": 40.0,
            "what": "Les scènes Sentinel-2 / Landsat plus nuageuses sont ignorées. Les nuages restants sont de toute façon masqués pixel par pixel (classification de scène / bande qualité).",
            "higher": "Plus de scènes analysées en saison des pluies, mais moins de mer visible dans chacune.",
            "lower": "Seules les scènes dégagées sont lues.",
            "advice": "40 % ; 60 % en saison des pluies.",
        },
    ),
    "optical_cfar_k": (
        float, 3.0, 12.0, "Imagerie optique — seuil de contraste proche infrarouge (k·σ)",
        {
            "unit": "écarts-types",
            "default": 6.0,
            "what": "Un pixel d'eau est retenu s'il dépasse la réflectance proche infrarouge de l'eau environnante de k écarts-types (et d'au moins 0,02 en réflectance). Les eaux turbides de Guyane (panache amazonien) sont brillantes : le seuil est local.",
            "higher": "Moins de fausses détections (écume, bancs de vase, débris), mais les petites pirogues peuvent disparaître.",
            "lower": "Remonte des embarcations plus petites, au prix de détections parasites dans les eaux très turbides.",
            "advice": "6 ; 5 par eau claire au large.",
        },
    ),
    "night_light_k": (
        float, 3.0, 15.0, "Lumières nocturnes VIIRS — seuil de contraste (k·σ)",
        {
            "unit": "écarts-types",
            "default": 6.0,
            "what": "Un point lumineux isolé en mer (bande jour/nuit VIIRS, ~750 m) est retenu s'il dépasse le fond de k écarts-types. Les nuages éclairés par la lune sont écartés par la bande infrarouge thermique I5 (sommets froids).",
            "higher": "Ne garde que les flottilles fortement éclairées (pêche au lamparo, gros navires).",
            "lower": "Détecte des lumières plus faibles, avec davantage de reflets lunaires et d'éclairs.",
            "advice": "6.",
        },
    ),
    "fusion_gate_speed_kn": (
        float, 2.0, 40.0, "Corroboration — vitesse maximale d'association (nœuds)",
        {
            "unit": "nœuds",
            "default": 15.0,
            "what": "Deux observations de capteurs différents (radar, optique, lumières nocturnes, GFW) sont rattachées au même navire si la distance qui les sépare peut être parcourue à cette vitesse dans l'intervalle de temps (plus les incertitudes de position de chaque capteur). Sert à la corroboration des cibles radar dans l'aide à la décision.",
            "higher": "Associe des observations éloignées dans le temps : moins de doublons, mais risque de fusionner deux navires distincts.",
            "lower": "Associations plus strictes : un même navire rapide peut apparaître comme plusieurs contacts.",
            "advice": "15 nœuds (chalutiers, tapouilles en transit) ; 25 si des embarcations rapides opèrent.",
        },
    ),
    "decision_assumed_speed_kn": (
        float, 1.0, 40.0, "Aide à la décision — vitesse supposée d'une cible sans AIS (nœuds)",
        {
            "unit": "nœuds",
            "default": 8.0,
            "what": "Vitesse retenue pour une cible radar dont la route est inconnue (vue une seule fois, sans AIS) : le cercle où elle peut se trouver grandit de cette vitesse multipliée par le temps écoulé depuis l'observation. Il fixe la zone de recherche à l'arrivée d'un moyen.",
            "higher": "Zone de recherche plus large (hypothèse prudente), options d'interception plus pessimistes.",
            "lower": "Zone de recherche plus étroite : risque de sous-estimer le déplacement d'une embarcation rapide.",
            "advice": "8 nœuds (chalutier, tapouille en transit) ; 15 à 20 pour des embarcations rapides.",
        },
    ),
    "incident_correlation_hours": (
        int, 1, 720, "Fenêtre de corrélation (h)",
        {
            "unit": "heures",
            "default": 72,
            "what": "Durée pendant laquelle un incident ouvert peut encore absorber de nouvelles détections proches.",
            "higher": "Regroupe les retours d'un même navire sur plusieurs jours (campagne de pêche).",
            "lower": "Chaque passage satellite éloigné dans le temps crée un incident distinct.",
            "advice": "48–96 h.",
        },
    ),
    "stac_lookback_days": (
        int, 1, 60, "Recherche Sentinel-1 — jours d'historique",
        {
            "unit": "jours",
            "default": 12,
            "what": "Profondeur de la recherche de scènes Sentinel-1 (liste des scènes, veille automatique).",
            "higher": "Permet de rejouer des scènes plus anciennes (enquête a posteriori), requêtes un peu plus lourdes.",
            "lower": "Liste plus courte, centrée sur l'actualité.",
            "advice": "12 jours couvre un cycle orbital complet.",
        },
    ),
    "gfw_lookback_days": (
        int, 1, 365, "Événements Global Fishing Watch — jours d'historique",
        {
            "unit": "jours",
            "default": 30,
            "what": "Période des événements GFW (coupures AIS, rencontres, flânage, pêche) pris en compte sur la carte et dans le score de risque.",
            "higher": "Contexte comportemental plus riche, mais des événements anciens peuvent surpondérer le risque.",
            "lower": "Ne retient que les comportements récents.",
            "advice": "30 jours.",
        },
    ),
    "ais_gap_hours": (
        float, 1.0, 48.0, "Coupure AIS — silence avant alerte (h)",
        {
            "unit": "heures",
            "default": 4.0,
            "what": "Durée de silence AIS au-delà de laquelle un navire qui émettait régulièrement (au moins 6 positions dans les 6 h précédentes) en zone côtière — mer territoriale et zone contiguë, 0 à 24 NM, à portée des récepteurs AIS terrestres — déclenche un incident « Coupure AIS ». Ignoré si notre propre flux AIS est coupé.",
            "higher": "Moins d'alertes (pannes d'émetteur, sorties de couverture), mais une coupure volontaire courte passe inaperçue.",
            "lower": "Repère des extinctions brèves, au prix de faux positifs liés aux trous de couverture radio.",
            "advice": "3 à 6 h.",
        },
    ),
    "encounter_distance_km": (
        float, 0.1, 5.0, "Rendez-vous en mer — distance maximale (km)",
        {
            "unit": "km",
            "default": 0.5,
            "what": "Distance en dessous de laquelle deux navires lents (≤ 2 nœuds) sont considérés « au contact » pour la règle de transbordement.",
            "higher": "Détecte plus de rapprochements, y compris de simples croisements lents : plus de bruit.",
            "lower": "Ne retient que les accostages bord à bord, signature typique d'un transbordement.",
            "advice": "0,3 à 0,5 km.",
        },
    ),
    "encounter_min_minutes": (
        int, 10, 240, "Rendez-vous en mer — durée minimale (min)",
        {
            "unit": "minutes",
            "default": 60,
            "what": "Temps cumulé (sur les 3 dernières heures) pendant lequel les deux navires doivent rester au contact pour ouvrir un incident « Rendez-vous en mer ». Un transbordement de poisson dure en général plusieurs heures.",
            "higher": "Ne garde que les transbordements longs (cargaison importante), moins de faux positifs.",
            "lower": "Signale des contacts brefs (échange d'équipage, de carburant), mais aussi de simples croisements lents et le chalutage en bœuf.",
            "advice": "60 à 120 min (Global Fishing Watch retient 2 h avec l'AIS satellite ; les trous de couverture de l'AIS côtier justifient un seuil plus bas).",
        },
    ),
    "ais_correlation_window_h": (
        float, 0.5, 12.0, "Corrélation radar/AIS — fenêtre temporelle (h)",
        {
            "unit": "heures",
            "default": 3.0,
            "what": "Écart maximal entre une position AIS enregistrée et le passage du satellite pour qu'elle serve, extrapolée selon le cap et la vitesse, à « expliquer » un écho radar.",
            "higher": "Plus de navires AIS utilisables, mais extrapolation moins précise : risque d'attribuer un écho sans AIS à un navire coopératif.",
            "lower": "Corrélation plus stricte et plus fiable, mais un navire au signal espacé peut être compté à tort comme « sans AIS ».",
            "advice": "2 à 3 h.",
        },
    ),
    "sst_front_threshold": (
        float, 0.1, 2.0, "Front thermique — gradient minimal (°C / 10 km)",
        {
            "unit": "°C par 10 km",
            "default": 0.4,
            "what": "Gradient horizontal de température de surface (Copernicus Marine OSTIA) à partir duquel un point est considéré sur un front thermique. Les fronts concentrent le plancton et les poissons : les flottilles de pêche s'y regroupent. Une cible radar sur un front gagne des points de risque.",
            "higher": "Seuls les fronts marqués sont retenus : bonus de risque plus rare et plus significatif.",
            "lower": "Davantage de zones qualifiées de front, le bonus perd de sa valeur discriminante.",
            "advice": "0,3 à 0,5 °C/10 km au large de la Guyane.",
        },
    ),
    "mpa_max_speed_knots": (
        float, 0.5, 10.0, "Pêche en aire protégée — vitesse maximale (nœuds)",
        {
            "unit": "nœuds",
            "default": 4.0,
            "what": "Un navire de pêche dans une aire marine protégée (WDPA) à une vitesse inférieure ou égale est considéré en action de pêche (chalutage 2–4 nd, palangre, filet) et ouvre un incident « Pêche en aire protégée ». Au-dessus, il est considéré en transit.",
            "higher": "Inclut des navires en transit lent : plus d'alertes, plus de faux positifs.",
            "lower": "Ne retient que les navires quasi stationnaires (filets, casiers), un chalutier peut passer inaperçu.",
            "advice": "4 nœuds (vitesse de chalutage).",
        },
    ),
    "sanctions_min_score": (
        float, 0.5, 1.0, "Sanctions — score de correspondance minimal",
        {
            "unit": "score 0–1",
            "default": 0.75,
            "what": "Score de similarité OpenSanctions (nom, IMO, MMSI, pavillon) au-dessus duquel un navire présent dans la ZEE est signalé comme sous sanctions internationales.",
            "higher": "Ne garde que les correspondances quasi certaines (IMO identique) : très peu de faux positifs.",
            "lower": "Remonte des homonymes (noms de navires très courants) : vérification manuelle nécessaire.",
            "advice": "0,75 ; 0,9 si les homonymes saturent la file.",
        },
    ),
    "aishub_poll_seconds": (
        int, 60, 3600, "AISHub — intervalle d'interrogation (s)",
        {
            "unit": "secondes",
            "default": 120,
            "what": "Fréquence à laquelle le second flux AIS (AISHub) est interrogé sur la ZEE. AISHub refuse plus d'un appel par minute.",
            "higher": "Moins de charge, positions AISHub plus espacées (les règles et la corrélation radar restent alimentées par aisstream).",
            "lower": "Image AIS plus fraîche ; ne pas descendre sous 60 s (réponse vide).",
            "advice": "60 à 120 s.",
        },
    ),
    "ai_detector_mode": (
        int, 0, 2, "IA — détection de navires (0 arrêt, 1 observation, 2 active)",
        {
            "unit": "mode",
            "default": 0,
            "what": "Avis d'un modèle open source (YOLOv8n entraîné sur des images radar, ONNX) sur chaque cible détectée par le CFAR. 1 = observation : l'avis est affiché et scellé, et son accord avec les verdicts des opérateurs est mesuré (Administration › Paramètres), sans effet sur les scores. 2 = actif : une cible confirmée par l'IA gagne en fiabilité, une cible qu'elle ne voit pas en perd, et les détections très sûres de l'IA seule deviennent des cibles signalées comme telles. Licence du modèle à valider avant activation (fiche MIT, fichier exporté sous AGPL-3.0 Ultralytics).",
            "higher": "L'IA pèse sur les alertes : à n'activer qu'après une période d'observation montrant un bon accord avec les opérateurs (au moins 30 incidents jugés).",
            "lower": "Aucune influence de l'IA : détection purement physique (CFAR + fiabilité).",
            "advice": "1 (observation) pendant quelques semaines, puis 2 si la précision mesurée dépasse 0,8.",
        },
    ),
    "sar_max_windows": (
        int, 0, 2000, "Radar — tuiles analysées par scène",
        {
            "unit": "tuiles de 1024 × 1024 px (≈ 105 km²)",
            "default": 24,
            "what": "Nombre maximal de tuiles de la scène Sentinel-1 analysées dans la ZEE, réparties uniformément. Une scène couvre souvent 200 à 400 tuiles de ZEE : le message de fin de scan indique la part réellement analysée. 0 = toute la partie ZEE de la scène.",
            "higher": "Couverture plus complète (moins de navires manqués), scan plus long et plus de lectures S3 (≈ 1 à 3 s par tuile).",
            "lower": "Scan plus rapide, mais l'essentiel de la ZEE n'est pas regardé : une absence d'alerte ne prouve pas l'absence de navire.",
            "advice": "24 en démonstration ; 0 (tout) en exploitation si le worker de scan le permet.",
        },
    ),
    "false_positive_memory_days": (
        int, 1, 3650, "Mémoire des fausses alarmes (jours)",
        {
            "unit": "jours",
            "default": 60,
            "what": "Durée pendant laquelle un incident clos en « fausse alarme » abaisse la fiabilité des nouvelles détections à moins de 250 m (−0,45). Passé ce délai, le lieu redevient surveillé normalement.",
            "higher": "Moins de fausses alarmes répétées sur les objets fixes, mais un navire qui revient plus tard au même endroit risque d'être écarté.",
            "lower": "Les lieux signalés à tort redeviennent vite surveillés ; les fausses alarmes récurrentes (épave, bouée) reviennent plus souvent.",
            "advice": "60 jours.",
        },
    ),
    "vesselapi_monthly_calls": (
        int, 0, 1_000_000, "VesselAPI — budget d'appels mensuel",
        {
            "unit": "appels / mois",
            "default": 150,
            "what": "Nombre maximal d'appels VesselAPI par mois civil (UTC), fiches registre et flux de positions confondus. À aligner sur le quota de l'offre souscrite (gratuite 150, Basic 1 500, Starter 15 000) : la plateforme s'arrête d'elle-même au budget, et plus tôt si le fournisseur annonce un quota restant inférieur. 0 désactive VesselAPI.",
            "higher": "Plus de navires étrangers décrits par le registre et flux de positions possible, à condition que l'offre le couvre (sinon refus HTTP 429 du fournisseur).",
            "lower": "Réserve des appels, mais les navires de passage restent sans fiche registre.",
            "advice": "Le quota exact de l'offre souscrite.",
        },
    ),
    "vesselapi_poll_minutes": (
        int, 0, 1440, "VesselAPI — intervalle du flux de positions (min)",
        {
            "unit": "minutes",
            "default": 0,
            "what": "Fréquence d'interrogation des positions AIS VesselAPI sur la ZEE (3e flux AIS, ajouté aux deux autres dans le même historique). Chaque interrogation coûte 1 à 5 appels (une page de résultats par appel) ; le flux s'interrompt quand il reste moins de 30 % du budget mensuel, réservé aux fiches registre. 0 = flux désactivé.",
            "higher": "Économise le quota, positions VesselAPI plus espacées.",
            "lower": "Image plus fraîche, quota consommé plus vite : à 30 min, 1 440 interrogations par mois, soit 1 440 à 7 200 appels.",
            "advice": "0 avec l'offre gratuite ; 120 à 240 avec Basic (1 500 appels, dont 1 050 utilisables par le flux) ; 15 à 30 avec Starter.",
        },
    ),
    "intel_lookups_per_tick": (
        int, 0, 100, "Renseignement navire — interrogations par cycle",
        {
            "unit": "navires / minute",
            "default": 10,
            "what": "Nombre maximal de navires enrichis à chaque cycle de surveillance (GFW Insights + OpenSanctions + registre VesselAPI pour les navires étrangers). Chaque navire est revérifié au plus une fois par semaine. 0 désactive l'enrichissement.",
            "higher": "Toute la flotte présente est criblée plus vite, au prix de quotas API consommés plus rapidement.",
            "lower": "Économise les quotas, mais un navire sanctionné de passage peut quitter la zone avant d'être criblé.",
            "advice": "10 (≈ 600 navires / heure).",
        },
    ),
}


def describe(settings: Optional[Settings] = None) -> Dict[str, Any]:
    cfg = settings or get_settings()
    return {
        name: {"value": getattr(cfg, name), "type": kind.__name__, "min": low, "max": high, "label": label, **explanation}
        for name, (kind, low, high, label, explanation) in EDITABLE.items()
    }


def apply_stored(settings: Optional[Settings] = None) -> None:
    cfg = settings or get_settings()
    with read_db(cfg) as conn:
        stored = conn.execute("SELECT key, value FROM app_settings").fetchall()
    for record in stored:
        if record["key"] in EDITABLE:
            kind = EDITABLE[record["key"]][0]
            setattr(cfg, record["key"], kind(json.loads(record["value"])))


def update(changes: Dict[str, Any], actor: str, settings: Optional[Settings] = None) -> Dict[str, Any]:
    cfg = settings or get_settings()
    validated: Dict[str, Any] = {}
    for name, value in changes.items():
        if name not in EDITABLE:
            raise ValueError(f"Paramètre non modifiable : {name}")
        kind, low, high, _label, _explanation = EDITABLE[name]
        try:
            typed = kind(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Valeur invalide pour {name}") from exc
        if not low <= typed <= high:
            raise ValueError(f"{name} doit être entre {low} et {high}")
        validated[name] = typed
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with db(cfg) as conn:
        for name, value in validated.items():
            conn.execute(
                "INSERT INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at, "
                "updated_by = excluded.updated_by",
                (name, json.dumps(value), now, actor),
            )
    for name, value in validated.items():
        setattr(cfg, name, value)
    audit.record("system.settings", actor=actor, target="settings", details=validated, settings=cfg)
    return describe(cfg)
