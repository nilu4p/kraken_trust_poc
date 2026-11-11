"""Top-level pipeline: STAC → SAR → quality → AIS → risk → C2PA → incidents (BackgroundTasks / Celery-ready)."""

from __future__ import annotations

import datetime
import logging
from typing import Dict, Optional, Tuple

from app.config import Settings, get_settings
from app.core.c2pa import generate_and_seal_alert
from app.core.pki import ensure_pki
from app.core.timeframe import parse_instant
from app.pipeline.detection import SarReadError, correlate_with_ais, detect_ships_with_window, load_ais_tracks
from app.pipeline.ingestion import fetch_sentinel1_data, resolve_polarisation_hrefs, scene_feature
from app.pipeline.quality import assess
from app.pipeline.risk import score_vessel
from app.pipeline.scan_state import add_stats, mark_done, mark_error, mark_running, mark_step, snapshot
from app.sources.gfw import fetch_events
from app.sources.marine_regions import classify_zone, zone_geometries
from app.sources.open_meteo import DEFAULT_POINT, sea_state_at
from app.core import audit
from app.core.geo import haversine_km
from app.db import db
from app.soc import incidents, notify
from app.soc.watchlist import Matcher, summary
from app.sources import planetary
from app.sources.erddap import ocean_context
from app.sources.shom import distances_km

logger = logging.getLogger(__name__)


# SAR read failures that no retry can fix (vs. network / credentials / quota, which can).
PERMANENT_SAR_ERRORS = {"no_tile", "not_found"}


def _mark_processed(scene_id: str, outcome: str, cfg: Settings) -> None:
    with db(cfg) as conn:
        conn.execute(
            "INSERT INTO processed_scenes (scene_id, processed_at, outcome) VALUES (?, ?, ?) "
            "ON CONFLICT (scene_id) DO UPDATE SET processed_at = excluded.processed_at, outcome = excluded.outcome",
            (scene_id, datetime.datetime.now(datetime.timezone.utc).isoformat(), outcome),
        )


def _radar_explanation(vessel: dict, scene_time: Optional[str], scene_id: str, cfg: Settings) -> dict:
    """The full reasoning behind a radar alert, kept with the incident ("why this alert?")."""
    from app.soc.rule_catalog import explanation

    quality = vessel.get("quality") or {}
    risk = vessel.get("risk") or {}
    ais_source = vessel.get("ais_source") or "inconnue"
    checks = [
        {"condition": "Écho radar plus brillant que la mer environnante",
         "observed": f"scène {scene_id} du {str(scene_time or '')[:16].replace('T', ' ')} Z · rapport signal/bruit {vessel.get('snr')}"
                     + (f" · ~{round(vessel['length_m'])} m" if vessel.get("length_m") else ""), "ok": True},
        {"condition": "Fiabilité de la détection suffisante",
         "observed": f"{quality.get('score')} ≥ {cfg.min_detection_quality}"
                     + (f" ({'; '.join(quality.get('flags')[:4])})" if quality.get("flags") else ""), "ok": True},
        {"condition": "Aucune position AIS / VMS à proximité au passage",
         "observed": f"source AIS : {ais_source}", "ok": ais_source not in ("unavailable", "mock")},
    ]
    coast = vessel.get("coast")
    if coast:
        shore = coast.get("shore_m")
        sources = " et ".join(s for s, ok in (("image radar", coast.get("radar_checked")),
                                             ("trait de côte OpenStreetMap", coast.get("osm_known"))) if ok)
        checks.insert(1, {
            "condition": "Écho en mer, pas sur la côte (rochers, îlets, quais, mangrove écartés)",
            "observed": (("rivage à ~" + (f"{shore} m" if shore < 1000 else f"{shore / 1000:.1f} km").replace(".", ","))
                         if shore is not None else "loin de toute côte connue")
                        + (f" — contrôlé par {sources}" if sources else ""),
            "ok": shore is None or shore >= 500,
        })
    mismatch = vessel.get("size_mismatch")
    if mismatch:
        checks[-1] = {
            "condition": "Navire AIS proche, mais de taille incompatible avec l'écho",
            "observed": f"écho ~{mismatch['radar_m']} m, {mismatch.get('name') or 'MMSI ' + str(mismatch['mmsi'])} déclaré "
                        f"{mismatch['declared_m']} m, à {mismatch['distance_km']} km",
            "ok": True,
        }
    if vessel.get("ai_confidence") is not None:
        checks.append({"condition": "Avis de l'IA (second avis)",
                       "observed": f"{vessel['ai_confidence']:.0%}" + (" — détecté par l'IA seule" if vessel.get("ai_only") else ""),
                       "ok": vessel["ai_confidence"] >= 0.5})
    if vessel.get("xview3"):
        from app.sources import xview3

        # Shown for information only: not a condition of the alert (``info`` = neither passed nor reserved).
        checks.append({"condition": "Avis xView3 (information, sans effet sur l'alerte)",
                       "observed": xview3.summary(vessel["xview3"]), "ok": True, "info": True})
    return {
        **explanation("dark_vessel", checks, {"cfar_k": cfg.cfar_k, "min_detection_quality": cfg.min_detection_quality,
                                              "ais_match_km": cfg.ais_match_km}),
        "risk": {"score": risk.get("score"), "level": risk.get("level"), "reasons": risk.get("reasons") or []},
    }


def _open_incidents(dark: list, geojson_path, scene_time: Optional[str], scene_id: str, cfg: Settings) -> int:
    """Create / correlate one incident per sealed dark vessel; notify new P1/P2."""
    dossier = geojson_path.parent.name
    alert_id = dossier.rsplit("_", 1)[-1]
    created = 0
    new_refs = []
    for vessel in dark:
        incident, is_new = incidents.ingest_detection(
            vessel,
            alert_id=alert_id,
            dossier=dossier,
            scene_id=scene_id,
            seen_at=scene_time or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            explanation=_radar_explanation(vessel, scene_time, scene_id, cfg),
            settings=cfg,
        )
        if is_new:
            created += 1
            new_refs.append({"ref": incident["ref"], "severity": incident["severity"]})
            if incident["severity"] in ("P1", "P2"):
                notify.notify_incident(incident, "Nouvel incident", cfg, vessel=vessel, dossier=dossier)
        elif incident.get("upgraded_from"):
            notify.notify_incident(
                incident, f"Sévérité relevée {incident['upgraded_from']} → {incident['severity']} (nouvelle observation)",
                cfg, vessel=vessel, dossier=dossier,
            )
    add_stats(new_incidents=new_refs, correlated=len(dark) - created)
    return created


def _licensed_mmsis(cfg: Settings) -> set:
    """MMSIs with a valid licence (authorised list) or reported by VMS: legal fishing activity."""
    from app.soc.authorized import AuthorizedMatcher
    from app.sources.vms import licensed_mmsis

    try:
        return set(AuthorizedMatcher(cfg).mmsis()) | licensed_mmsis()
    except Exception as exc:
        logger.warning("Licensed vessels unavailable: %s", exc)
        return set()


def _foreign_fishing_nearby(vessel: dict, ais_tracks: list, radius_km: float = 15.0, licensed: Optional[set] = None) -> Optional[str]:
    """Foreign-flag fishing vessel without licence heard on AIS near a dark target at the pass time."""
    from app.core.geo import haversine_km
    from app.sources.mid import is_french_flag

    for track in ais_tracks or []:
        flag = track.get("flag")
        if licensed and str(track.get("mmsi")) in licensed:
            continue
        if track.get("ship_type") == 30 and flag and not is_french_flag(flag):
            if haversine_km(vessel["lat"], vessel["lon"], track["lat"], track["lon"]) <= radius_km:
                return f"{track.get('name') or track.get('mmsi')} ({flag})"
    return None


def _known_targets(scene_id: str, cfg: Settings) -> list:
    """Positions already sealed and attached to incidents for this scene (re-scan detection)."""
    from app.db import read_db

    with read_db(cfg) as conn:
        return [(r[0], r[1]) for r in conn.execute("SELECT lat, lon FROM incident_observations WHERE scene_id = ?", (scene_id,))]


def _mpa_index(cfg: Settings):
    """Marine protected areas (WDPA) lookup, or None when the service is unreachable."""
    try:
        from app.sources.wdpa import MpaIndex

        return MpaIndex(settings=cfg)
    except Exception as exc:
        logger.warning("WDPA unavailable: %s", exc)
        return None


def _gfw_sar_context(cfg: Settings, scene_dt: Optional[datetime.datetime] = None) -> list:
    """GFW Sentinel-1 detections over the EEZ in the 30 days up to the pass (empty without token / on failure)."""
    try:
        from app.sources.gfw_4wings import sar_detections

        return sar_detections(settings=cfg, end=scene_dt.date() if scene_dt else None).get("features", [])
    except Exception as exc:
        logger.warning("GFW SAR context unavailable: %s", exc)
        return []


def _apply_gfw_sar(blip: dict, gfw_sar: list, scene_time: Optional[str], cfg: Settings) -> None:
    """Same-day GFW detection nearby → confirmation; unmatched GFW detections around → hotspot."""
    from app.core.geo import haversine_km

    day = (scene_time or "")[:10]
    hotspot = 0
    for feature in gfw_sar:
        lon, lat = feature["geometry"]["coordinates"]
        props = feature["properties"]
        distance = haversine_km(blip["lat"], blip["lon"], lat, lon)
        if props.get("date") == day and distance <= cfg.gfw_sar_confirm_km:
            blip["gfw_confirmed"] = True
        if not props.get("matched_ais") and distance <= 10:
            hotspot += 1
    blip["dark_hotspot"] = hotspot


def _flag_watchlisted_ais(ais_tracks: list, zones: dict, cfg: Settings) -> list:
    """Annotate AIS tracks found on the watchlist; return those inside the EEZ."""
    matcher = Matcher(cfg)
    listed = []
    for track in ais_tracks or []:
        hit = summary(matcher.match(mmsi=track.get("mmsi"), name=track.get("name")))
        if not hit:
            continue
        track["watchlist"] = hit
        zone = classify_zone(track["lon"], track["lat"], zones)
        if zone:
            listed.append({**track, "zone": zone})
    return listed


def _open_watchlist_incidents(tracks: list, scene_id: str, scene_time: Optional[str], cfg: Settings) -> None:
    """A listed (IUU) vessel transmitting AIS inside the EEZ is an incident by itself."""
    for track in tracks:
        vessel = {
            "id": f"AIS-{track.get('mmsi', 'X')}"[:20],
            "lat": track["lat"],
            "lon": track["lon"],
            "zone": track["zone"],
            "risk": {"score": 90},
            "quality": {"score": 1.0},
        }
        incident, is_new = incidents.ingest_detection(
            vessel,
            alert_id=f"ais-{scene_id}"[:120],
            dossier="",
            scene_id=scene_id,
            seen_at=track.get("time_utc") or scene_time or datetime.datetime.now(datetime.timezone.utc).isoformat(),
            kind="watchlist_ais",
            title=f"Navire listé ({track['watchlist']['source']}) visible à l'AIS : {track.get('name') or track.get('mmsi')}",
            settings=cfg,
        )
        if is_new:
            watch = track["watchlist"]
            notify.notify_incident(
                incident,
                "Navire de la liste de surveillance dans la ZEE",
                cfg,
                channel="surveillance",
                extra=[
                    {"title": "Navire (AIS)", "value": f"{track.get('name') or '—'} · MMSI {track.get('mmsi')}", "short": False},
                    {"title": "Liste", "value": watch.get("source"), "short": True},
                    {"title": "Correspondance", "value": watch.get("matched_on"), "short": True},
                    {"title": "Motif d'inscription", "value": watch.get("reason") or "—", "short": False},
                    {"title": "Pavillon listé", "value": watch.get("flag") or "—", "short": True},
                    {"title": "Vitesse", "value": f"{track.get('sog_knots')} nd" if track.get("sog_knots") is not None else "—", "short": True},
                ],
            )
    add_stats(watchlisted=len(tracks))


def run_kraken_pipeline(
    settings: Optional[Settings] = None,
    scene_id: Optional[str] = None,
    actor: str = "system",
) -> None:
    """Ingest Sentinel-1, detect dark vessels, score them and seal C2PA proofs.

    Runs in the API process (inline mode, FastAPI BackgroundTasks) or in the
    dedicated scan worker (Celery task ``kraken.scan``). A cross-process lock
    guarantees one scan at a time. Ends with a scan report in #operations.
    """
    cfg = settings or get_settings()
    if not _run_pipeline(cfg, scene_id, actor):
        return
    state = snapshot()
    stats = state.get("stats") or {}
    notify.notify_scan(
        {
            "status": state.get("status"),
            "message": state.get("message"),
            "actor": actor,
            "scene_id": state.get("stac_id") or stats.get("scene_id") or scene_id,
            "scene_datetime": stats.get("scene_datetime"),
            "radar_targets": stats.get("radar_targets"),
            "discarded": stats.get("discarded"),
            "dark_vessels": state.get("dark_vessel_count") if state.get("status") == "done" else stats.get("dark_vessels"),
            "new_incidents": stats.get("new_incidents"),
            "correlated": stats.get("correlated"),
            "watchlisted": stats.get("watchlisted"),
            "ais_source": stats.get("ais_source"),
        },
        cfg,
    )


def _pixel_bands(item, scene_time: Optional[str], cfg: Settings) -> Tuple[Dict[str, str], str]:
    """Where the radar pixels are read: Copernicus S3 (keys) or its keyless Planetary Computer mirror."""
    if cfg.sar_source == "planetary" or (cfg.sar_source == "auto" and not (cfg.access_key and cfg.secret_key)):
        mirror = planetary.bands_for(item.id, scene_time, cfg)
        if mirror:
            return mirror, "planetary"
        if cfg.sar_source == "planetary":
            return {}, "planetary"
    return resolve_polarisation_hrefs(item), "copernicus"


def _detect(item, scene_time: Optional[str], bands: Dict[str, str], pixel_source: str, zones: dict, cfg: Settings):
    # Cross-pol (VH/HV) gives the best ship/sea contrast; co-pol confirms.
    detect_url = bands.get("cross") or bands.get("co")
    confirm_url = bands.get("co") if bands.get("cross") else None
    label = "Copernicus S3" if pixel_source == "copernicus" else "miroir Planetary Computer"
    mark_step(
        "sar",
        f"Analyse SAR de {item.id} ({'double' if confirm_url else 'simple'} polarisation, {label})",
        scene_id=item.id,
        scene_datetime=scene_time,
    )
    add_stats(pixel_source=pixel_source)
    return detect_ships_with_window(detect_url, cfg, eez=zones.get("eez"), confirm_url=confirm_url,
                                    source="copernicus_s3" if pixel_source == "copernicus" else "planetary_computer")


def _run_pipeline(cfg: Settings, scene_id: Optional[str], actor: str) -> bool:
    """False when another scan holds the lock (nothing was done)."""
    if not mark_running():
        logger.warning("Scan already running — ignoring duplicate trigger")
        return False

    try:
        ensure_pki(cfg)
        logger.info("Starting KRAKEN pipeline (EEZ Guyane)")

        mark_step("boundaries", "Chargement des frontières maritimes (Marine Regions)")
        zones = zone_geometries(cfg)

        mark_step("stac", "Recherche de la scène Sentinel-1 (Copernicus STAC)")
        item = fetch_sentinel1_data(cfg, scene_id=scene_id)
        if item is None:
            mark_error(
                f"Scène {scene_id} introuvable dans le catalogue Copernicus" if scene_id
                else "Aucune scène Sentinel-1 disponible (catalogue STAC injoignable ou vide)"
            )
            audit.record("scan.complete", actor=actor, outcome="failure", details={"reason": "stac"}, settings=cfg)
            logger.error("Pipeline stopped: no STAC item")
            return True
        scene = scene_feature(item)
        scene_time = scene["properties"]["datetime"]
        with db(cfg) as conn:
            rescan = conn.execute("SELECT 1 FROM processed_scenes WHERE scene_id = ?", (item.id,)).fetchone() is not None
        if rescan:
            logger.info("Scene %s already analysed: known targets will not be duplicated", item.id)
        add_stats(rescan=rescan)

        bands, pixel_source = _pixel_bands(item, scene_time, cfg)
        if not (bands.get("cross") or bands.get("co")):
            mark_error("Aucun asset SAR S3 sur la scène STAC")
            _mark_processed(item.id, "skipped:no_asset", cfg)  # permanent: never retried by the watch
            return True
        try:
            try:
                blips, sar_window = _detect(item, scene_time, bands, pixel_source, zones, cfg)
            except SarReadError as exc:
                # Same scene, other reading path: keys expired, quota, S3 network trouble…
                mirror = (planetary.bands_for(item.id, scene_time, cfg)
                          if pixel_source == "copernicus" and cfg.sar_source == "auto" and exc.code not in PERMANENT_SAR_ERRORS
                          else None)
                if not mirror:
                    raise
                logger.warning("Copernicus S3 read failed (%s) — reading the same scene from Planetary Computer", exc.code)
                add_stats(copernicus_error=exc.code)
                pixel_source = "planetary"
                blips, sar_window = _detect(item, scene_time, mirror, pixel_source, zones, cfg)
        except SarReadError as exc:
            mark_error(str(exc) if pixel_source == "copernicus" else f"{exc} (lecture via le miroir Planetary Computer)")
            if exc.code in PERMANENT_SAR_ERRORS:
                # The scene can never be analysed (outside the EEZ, product withdrawn): the
                # automatic watch must move on instead of retrying it forever.
                _mark_processed(item.id, f"skipped:{exc.code}", cfg)
            audit.record(
                "scan.complete", actor=actor, target=f"scene:{item.id}", outcome="failure",
                details={"reason": exc.code}, settings=cfg,
            )
            return True
        scene["properties"]["pixel_source"] = pixel_source
        if not blips:
            _mark_processed(item.id, "no_target", cfg)
            mark_done(dark_vessel_count=0, stac_id=item.id)
            logger.info("No radar blips — pipeline finished cleanly")
            return True

        mark_step("quality", "Évaluation de la fiabilité des cibles", radar_targets=len(blips))
        # Every context is read AT THE PASS, never "now": sea state, ocean, GFW detections and
        # events of that instant (a scene analysed days later must score as it would have then).
        scene_dt = parse_instant(scene_time)
        weather = sea_state_at(DEFAULT_POINT[0], DEFAULT_POINT[1], scene_time, cfg)  # scene summary (evidence)
        local_weather: dict = {}
        history = incidents.detection_history(cfg)
        kept = []
        gfw_sar = _gfw_sar_context(cfg, scene_dt)
        for blip in blips:
            # The EEZ spans ~500 km: sea state per 0.25° cell around the target, not one point for all.
            cell = (round(blip["lat"] * 4) / 4, round(blip["lon"] * 4) / 4)
            if cell not in local_weather:
                local_weather[cell] = sea_state_at(cell[0], cell[1], scene_time, cfg)
            blip["sea_state"] = local_weather[cell]
            blip["ocean"] = ocean_context(blip["lat"], blip["lon"], cfg, at=scene_dt)
            _apply_gfw_sar(blip, gfw_sar, scene_time, cfg)
            blip["quality"] = assess(blip, history=history, sea_state=blip["sea_state"], scene_id=item.id, settings=cfg)
            if blip["quality"]["score"] >= cfg.min_detection_quality:
                kept.append(blip)
        rejected = len(blips) - len(kept)
        if rejected:
            logger.info("%s low-quality target(s) discarded (fixed structures, clutter, false positives)", rejected)
        blips = kept
        if not blips:
            _mark_processed(item.id, "no_reliable_target", cfg)
            mark_done(dark_vessel_count=0, stac_id=item.id)
            return True

        mark_step("ais", "Corrélation AIS", radar_targets=len(blips), discarded=rejected)
        ais_tracks, ais_source = load_ais_tracks(cfg, scene_time)
        add_stats(ais_source=ais_source)
        dark, ais_tracks = correlate_with_ais(blips, cfg, ais_tracks=ais_tracks, scene_time=scene_time)
        for vessel in dark:
            vessel["ais_source"] = ais_source
        listed_ais = _flag_watchlisted_ais(ais_tracks, zones, cfg)

        if dark:
            mark_step("risk", "Contexte GFW + météo et score de risque", dark_vessels=len(dark))
            events = fetch_events(cfg, end=scene_dt).get("features", [])
            mpa_index = _mpa_index(cfg)
            licensed = _licensed_mmsis(cfg)
            for vessel in dark:
                vessel["zone"] = classify_zone(vessel["lon"], vessel["lat"], zones)
                vessel["mpa"] = mpa_index.find(vessel["lat"], vessel["lon"]) if mpa_index else None
                vessel["fishing_limits"] = distances_km(vessel["lat"], vessel["lon"], cfg)
                vessel["foreign_fishing_nearby"] = _foreign_fishing_nearby(vessel, ais_tracks, licensed=licensed)
                vessel["risk"] = score_vessel(
                    vessel,
                    zone=vessel["zone"],
                    events=events,
                    sea_state=vessel.get("sea_state") or weather,
                    ais_source=ais_source,
                    quality=vessel.get("quality"),
                    settings=cfg,
                    exclude_mmsi=licensed,
                )
            dark.sort(key=lambda v: v["risk"]["score"], reverse=True)

        if dark and rescan:
            known = _known_targets(item.id, cfg)
            fresh = [v for v in dark if not any(haversine_km(v["lat"], v["lon"], la, lo) <= cfg.persistence_radius_km for la, lo in known)]
            if len(fresh) < len(dark):
                logger.info("Re-scan of %s: %s known target(s) not sealed again", item.id, len(dark) - len(fresh))
            dark = fresh

        if dark:
            mark_step("seal", "Scellement C2PA des preuves")
            geojson_path = generate_and_seal_alert(
                dark,
                stac_source=str(item.id),
                settings=cfg,
                sar_window=sar_window,
                ais_tracks=ais_tracks,
                scene=scene,
                sea_state=weather,
            )
            if geojson_path is not None:
                mark_step("incidents", "Ouverture / corrélation des incidents")
                created = _open_incidents(dark, geojson_path, scene_time, item.id, cfg)
                logger.info("%s new incident(s), %s correlated", created, len(dark) - created)
        if listed_ais:
            _open_watchlist_incidents(listed_ais, item.id, scene_time, cfg)
        _mark_processed(item.id, f"{len(dark)}_dark", cfg)
        audit.record(
            "scan.complete", actor=actor, target=f"scene:{item.id}", details={"dark_vessels": len(dark)}, settings=cfg
        )
        mark_done(dark_vessel_count=len(dark), stac_id=item.id)
        logger.info("Operational summary: %s dark vessel(s)", len(dark))
    except Exception as exc:
        logger.exception("Unhandled pipeline error: %s", exc)
        audit.record("scan.complete", actor=actor, outcome="failure", details={"error": type(exc).__name__}, settings=cfg)
        mark_error("Erreur interne du pipeline (voir journaux serveur)")
    return True

