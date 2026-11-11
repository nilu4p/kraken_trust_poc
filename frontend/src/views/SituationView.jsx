import { useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Activity, History, Loader2, Radio, RefreshCw, Ship, X } from "lucide-react";
import Map from "../components/Map.jsx";
import Timeline from "../components/Timeline.jsx";
import { TimeContext, formatInstant } from "../lib/timeContext.js";
import AlertDrawer from "../components/AlertDrawer.jsx";
import {
  CoverageSection,
  DEFAULT_VISUAL,
  LayersSection,
  MapStyleSection,
  SourcesSection,
  WeatherSection,
  ZonesSection,
} from "../components/ContextPanel.jsx";
import Legend from "../components/Legend.jsx";
import SidePanel from "../components/SidePanel.jsx";
import ActivityReport from "../components/ActivityReport.jsx";
import DocLink from "../components/DocLink.jsx";
import RiskBadge from "../components/RiskBadge.jsx";
import { Pill } from "../components/ui.jsx";
import { goesSlot, latestGibsDate } from "../imagery.js";
import { formatCoords, hasRole } from "../lib/format.js";
import { EVENTS, FISHING_LIMITS, MPA, QUALITY, SEVERITY, STATUS, TARGET, targetClass } from "../theme.js";
import {
  errorMessage,
  fetchAlerts,
  fetchEnvironment,
  fetchAisPicture,
  fetchCoverage,
  fetchCustomLayerData,
  fetchCustomSources,
  fetchEvents,
  fetchFishingEffort,
  fetchFishingLimits,
  fetchGfwSar,
  fetchIsobaths,
  fetchMarineProtectedAreas,
  fetchOceanLayers,
  fetchPoint,
  fetchScanStatus,
  fetchSentinel2Scenes,
  fetchZones,
  createZone,
  deleteZone,
  fetchScenes,
  fetchSources,
  fetchZee,
  triggerScan,
} from "../services/api.js";

const EMPTY = { type: "FeatureCollection", features: [] };
// "queued": accepted, waiting for the scan worker; "running": being analysed.
const ACTIVE_SCAN = new Set(["queued", "running"]);
const DEFAULT_LAYERS = {
  zones: true, limits: true, mpa: true, ais: true, tracks: true, events: true, gfwSar: false, effort: false, isobaths: false,
  scenes: false, optical: true, nightLights: false, geostationary: false, sentinel2: false, drawnZones: true,
};
const PREFS_KEY = "kraken.situation.v2";
const ZONE_LABELS = { territorial: "Mer territoriale (12 NM)", contiguous: "Zone contiguë (24 NM)", eez: "ZEE (200 NM)" };

function loadPrefs() {
  try {
    return JSON.parse(window.localStorage.getItem(PREFS_KEY) || "{}");
  } catch {
    return {};
  }
}

function savePrefs(prefs) {
  try {
    window.localStorage.setItem(PREFS_KEY, JSON.stringify(prefs));
  } catch {
    /* storage unavailable: preferences are simply not remembered */
  }
}

function sceneLabel(feature) {
  const p = feature.properties || {};
  const date = p.datetime ? String(p.datetime).slice(0, 16).replace("T", " ") : "?";
  return `${date} Z · ${p.mode || "?"} · ${(p.polarizations || []).join("/")}`;
}

function sameScan(a, b) {
  return a.status === b.status && a.message === b.message && a.step === b.step;
}

function PointDossier({ point, onClose, navigate }) {
  const { at } = useContext(TimeContext);
  const [state, setState] = useState({ status: "loading" });
  useEffect(() => {
    let alive = true;
    setState({ status: "loading" });
    fetchPoint(point.lat, point.lon, at)
      .then((data) => alive && setState({ status: "ready", data }))
      .catch((err) => alive && setState({ status: "error", message: errorMessage(err) }));
    return () => {
      alive = false;
    };
  }, [point, at]);
  const data = state.data;
  return (
    <div className="absolute left-2 top-3 z-[900] max-h-[calc(100%-1.5rem)] w-80 max-w-[calc(100%-1rem)] overflow-y-auto sm:left-14 sm:max-w-[calc(100%-4.5rem)] border border-tactical-line bg-tactical-panel/95 text-xs shadow-2xl backdrop-blur">
      <div className="flex items-center justify-between border-b border-tactical-line px-3 py-2">
        <span className="flex items-center gap-2 text-[10px] uppercase tracking-[0.2em] text-tactical-cyan">
          Dossier du point <DocLink page="situation/dossier-point/" label="dossier du point" />
        </span>
        <button type="button" onClick={onClose} aria-label="Fermer" className="text-slate-400 hover:text-white">
          <X size={14} />
        </button>
      </div>
      <div className="space-y-2 p-3">
        <p className="text-slate-300">{formatCoords(point.lat, point.lon)}</p>
        {at && <p className="text-tactical-amber">Situation au {formatInstant(at)} (rejeu)</p>}
        {state.status === "loading" && (
          <p className="flex items-center gap-2 text-slate-500">
            <Loader2 size={12} className="animate-spin" /> Interrogation des sources…
          </p>
        )}
        {state.status === "error" && <p className="text-tactical-red">{state.message}</p>}
        {data && (
          <>
            <p>
              <span className="text-slate-500">Zone : </span>
              {ZONE_LABELS[data.zone] || "Hors ZEE / terre"}
            </p>
            {data.ocean && (
              <p>
                <span className="text-slate-500">Fond : </span>
                {data.ocean.on_land ? "terre émergée" : data.ocean.depth_m != null ? (data.ocean.depth_m < 1 ? "< 1 m (vasière)" : `${Math.round(data.ocean.depth_m)} m`) : "—"}
                {data.ocean.shore_distance_m != null &&
                  ` · ${data.ocean.on_land ? "à" : "rivage à"} ${data.ocean.shore_distance_m < 1000 ? `${data.ocean.shore_distance_m} m` : `${(data.ocean.shore_distance_m / 1000).toFixed(1)} km`}${data.ocean.on_land ? " du rivage" : ""}`}
                {data.ocean.chlorophyll_mg_m3 != null && ` · chlorophylle ${data.ocean.chlorophyll_mg_m3.toFixed(1)} mg/m³`}
                {data.ocean.productive_waters && <span className="text-tactical-amber"> (eaux productives)</span>}
              </p>
            )}
            {data.ocean && (data.ocean.sst_c != null || data.ocean.current_speed_kn != null) && (
              <p>
                <span className="text-slate-500">Océan (Copernicus Marine) : </span>
                {data.ocean.sst_c != null && `SST ${data.ocean.sst_c.toFixed(1)} °C`}
                {data.ocean.thermal_front && <span className="text-tactical-amber"> · front thermique ({data.ocean.sst_gradient_c_10km} °C/10 km)</span>}
                {data.ocean.current_speed_kn != null &&
                  ` · courant ${data.ocean.current_speed_kn} nd vers ${data.ocean.current_towards_deg ?? "—"}°`}
              </p>
            )}
            {data.mpa && (
              <p style={{ color: MPA.color }}>
                <span className="text-slate-500">Aire protégée : </span>
                {data.mpa.name} — {data.mpa.designation}
              </p>
            )}
            {data.fishing_limits_km && (
              <p>
                <span className="text-slate-500">Limites de pêche (SHOM) : </span>
                {Object.entries(data.fishing_limits_km)
                  .filter(([, km]) => km != null)
                  .map(([key, km]) => `${FISHING_LIMITS[key]?.label.replace(" (SHOM)", "") || key} à ${km} km`)
                  .join(" · ") || "—"}
              </p>
            )}
            {data.sea_state?.available && (
              <p>
                <span className="text-slate-500">Mer : </span>
                vent {data.sea_state.wind_speed_ms?.toFixed?.(1) ?? "—"} m/s · houle {data.sea_state.wave_height_m ?? "—"} m
              </p>
            )}
            <p>
              <span className="text-slate-500">Événements GFW (25 km) : </span>
              {Object.keys(data.gfw_events_25km || {}).length
                ? Object.entries(data.gfw_events_25km)
                    .map(([k, n]) => `${EVENTS[k]?.label || k} ${n}`)
                    .join(" · ")
                : "aucun"}
            </p>
            <div>
              <p className="text-slate-500">Incidents à moins de 25 km :</p>
              {data.incidents.length === 0 && <p className="text-slate-600">aucun</p>}
              <ul className="mt-1 space-y-1">
                {data.incidents.slice(0, 5).map((i) => (
                  <li key={i.id}>
                    <button type="button" onClick={() => navigate("incidents", i.id)} className="flex w-full items-center justify-between gap-2 text-left hover:text-tactical-cyan">
                      <span>{i.ref}</span>
                      <span className="flex items-center gap-1">
                        <Pill color={SEVERITY[i.severity].color}>{i.severity}</Pill>
                        <Pill color={STATUS[i.status].color}>{STATUS[i.status].label}</Pill>
                        <span className="text-slate-500">{i.distance_km} km</span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

export default function SituationView({ user, navigate, onIncidentChange, focus = "" }) {
  const prefs = useMemo(loadPrefs, []);
  const [zee, setZee] = useState(null);
  const [alerts, setAlerts] = useState(EMPTY);
  const [events, setEvents] = useState(null);
  const [scenes, setScenes] = useState(EMPTY);
  const [environment, setEnvironment] = useState(null);
  const [sources, setSources] = useState(null);
  const [layers, setLayers] = useState({ ...DEFAULT_LAYERS, ...(prefs.layers || {}) });
  const [basemap, setBasemap] = useState(prefs.basemap || "osm");
  const [displayMode, setDisplayMode] = useState(prefs.displayMode || "standard");
  const [imageryDate, setImageryDate] = useState(latestGibsDate());
  // Reference instant: null = live, else epoch ms of the replayed instant. Every layer, panel
  // and computation follows it (``queryAt``, debounced while the cursor is dragged).
  const [replayAt, setReplayAt] = useState(null);
  const [spanDays, setSpanDays] = useState(prefs.timelineSpan ?? 7);
  const [queryAt, setQueryAt] = useState(null);
  const queryAtRef = useRef(null);
  queryAtRef.current = queryAt;
  const [hideClosed, setHideClosed] = useState(prefs.hideClosed ?? true);
  // Radar targets of the last N days (open incidents are always shown); 0 = all.
  const [targetDays, setTargetDays] = useState(prefs.targetDays ?? 30);
  const targetDaysRef = useRef(targetDays);
  targetDaysRef.current = targetDays;
  const [sceneId, setSceneId] = useState("");
  const [selectedKey, setSelectedKey] = useState(null);
  const [optical, setOptical] = useState(null);
  const [point, setPoint] = useState(null);
  const [fishingLimits, setFishingLimits] = useState(null);
  const [aisLive, setAisLive] = useState(null);
  const [gfwSar, setGfwSar] = useState(null);
  const [effort, setEffort] = useState(null);
  const [coverage, setCoverage] = useState(null);
  const [mpa, setMpa] = useState(null);
  const [isobaths, setIsobaths] = useState(null);
  const [gfwDays, setGfwDays] = useState(prefs.gfwDays ?? 30);
  const [oceanLayers, setOceanLayers] = useState(null);
  const [sentinel2, setSentinel2] = useState(null);
  // Surveillance zones drawn on the map (custom-rule criterion). drawing = vertices being placed.
  const [zones, setZones] = useState(null);
  const [drawing, setDrawing] = useState(null);
  const [zoneDraft, setZoneDraft] = useState(null);
  const loadZones = useCallback(() => {
    fetchZones().then(setZones).catch(() => setZones(null));
  }, []);
  useEffect(loadZones, [loadZones]);
  const onDrawPoint = useCallback((point) => setDrawing((d) => (d ? [...d, point] : d)), []);
  // Sources declared in Administration › Sources personnalisées (layer key "custom:<id>").
  const [customLayers, setCustomLayers] = useState([]);
  const [customData, setCustomData] = useState({});
  const [panelOpen, setPanelOpen] = useState(prefs.panelPinned ?? true);
  const [panelPinned, setPanelPinned] = useState(prefs.panelPinned ?? false);
  const [panelTab, setPanelTab] = useState(prefs.panelTab || "map");
  const [visual, setVisual] = useState({ ...DEFAULT_VISUAL, ...(prefs.visual || {}) });
  const [scan, setScan] = useState({ status: "idle", message: "" });
  const [error, setError] = useState("");
  const lastStatus = useRef("idle");
  const pollNow = useRef(null);
  const canScan = hasRole(user, "analyst");

  useEffect(() => {
    savePrefs({ layers, basemap, hideClosed, displayMode, panelPinned, panelTab, visual, targetDays, timelineSpan: spanDays, gfwDays });
  }, [layers, basemap, hideClosed, displayMode, panelPinned, panelTab, visual, targetDays, spanDays, gfwDays]);

  useEffect(() => {
    const iso = replayAt == null ? null : new Date(replayAt).toISOString();
    if (iso === null) {
      setQueryAt(null);
      return undefined;
    }
    const timer = setTimeout(() => setQueryAt(iso), 350);
    return () => clearTimeout(timer);
  }, [replayAt]);
  // Daily products (GFW reports, ocean maps, NASA mosaics) only change with the day / 6-hour step.
  const queryDay = queryAt ? queryAt.slice(0, 10) : null;
  const querySixHours = queryAt ? `${queryAt.slice(0, 11)}${String(Math.floor(Number(queryAt.slice(11, 13)) / 6) * 6).padStart(2, "0")}` : null;
  const effectiveImageryDate = queryDay ? (queryDay < latestGibsDate() ? queryDay : latestGibsDate()) : imageryDate;
  // Geostationary image slot (10 min): follows the instant; in live mode the clock moves it forward.
  const [clock, setClock] = useState(Date.now());
  useEffect(() => {
    if (queryAt || !layers.geostationary) return undefined;
    const timer = setInterval(() => setClock(Date.now()), 60000);
    return () => clearInterval(timer);
  }, [queryAt, layers.geostationary]);
  const geoSlot = goesSlot(queryAt, clock);


  const loadAlerts = useCallback(async () => {
    try {
      setAlerts(await fetchAlerts(targetDaysRef.current, queryAtRef.current));
      setError("");
    } catch (err) {
      setError(errorMessage(err, "Impossible de charger la situation"));
    }
  }, []);

  useEffect(() => {
    // Instant-independent context: each source is independent, a failure only hides its own layer.
    fetchZee().then(setZee).catch(() => setZee(null));
    fetchSources().then(setSources).catch(() => setSources(null));
    fetchFishingLimits().then(setFishingLimits).catch(() => setFishingLimits(null));
  }, []);

  // Everything time-dependent is (re)loaded for the reference instant: radar targets, GFW events,
  // Sentinel-1 scenes and sea state — together, so the layers never describe different moments.
  useEffect(() => {
    loadAlerts();
    fetchEvents(queryAt).then(setEvents).catch(() => setEvents(null));
    fetchScenes(queryAt).then(setScenes).catch(() => setScenes(EMPTY));
    fetchEnvironment(queryAt).then(setEnvironment).catch(() => setEnvironment(null));
  }, [queryAt, loadAlerts]);

  // Changing the time window only reloads the radar targets.
  const firstWindow = useRef(true);
  useEffect(() => {
    if (firstWindow.current) {
      firstWindow.current = false;
      return;
    }
    loadAlerts();
  }, [targetDays, loadAlerts]);

  // AIS picture at the instant: refreshed every minute in live mode, once per instant in replay.
  // Coverage describes the platform now: live only.
  useEffect(() => {
    const refresh = () => {
      fetchAisPicture(2, 6, queryAt).then(setAisLive).catch(() => setAisLive(null));
      if (!queryAt) fetchCoverage().then(setCoverage).catch(() => setCoverage(null));
    };
    refresh();
    if (queryAt) return undefined;
    const timer = setInterval(refresh, 60000);
    return () => clearInterval(timer);
  }, [queryAt]);

  // Static context layers, fetched once when first needed.
  useEffect(() => {
    if (layers.mpa && !mpa) fetchMarineProtectedAreas().then(setMpa).catch(() => setMpa({ features: [] }));
  }, [layers.mpa, mpa]);
  useEffect(() => {
    if (layers.isobaths && !isobaths) fetchIsobaths().then(setIsobaths).catch(() => setIsobaths({ features: [], available: false }));
  }, [layers.isobaths, isobaths]);
  // Ocean maps of the instant (6-hour step): reset when it changes, fetched when the overlay is on.
  useEffect(() => setOceanLayers(null), [querySixHours]);
  useEffect(() => {
    if (visual.oceanLayer !== "none" && !oceanLayers) {
      fetchOceanLayers(queryAt)
        .then((d) => setOceanLayers(d.layers))
        .catch(() => setOceanLayers({}));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visual.oceanLayer, oceanLayers]);

  useEffect(() => {
    fetchCustomSources()
      .then((d) => setCustomLayers(d.layers || []))
      .catch(() => setCustomLayers([]));
  }, []);

  // Object layers of custom sources: fetched when switched on, refreshed at the source's pace.
  const activeCustom = customLayers.filter((l) => l.kind === "features" && layers[`custom:${l.id}`]);
  const activeCustomKey = activeCustom.map((l) => `${l.id}:${l.refresh_minutes}`).join(",");
  useEffect(() => {
    const timers = activeCustom.map((layer) => {
      const load = () => {
        setCustomData((d) => ({ ...d, [layer.id]: { ...d[layer.id], loading: true } }));
        fetchCustomLayerData(layer.id)
          .then((data) => setCustomData((d) => ({ ...d, [layer.id]: { data, error: data.stale ? data.error : "" } })))
          .catch((err) => setCustomData((d) => ({ ...d, [layer.id]: { ...d[layer.id], loading: false, error: errorMessage(err) } })));
      };
      load();
      return setInterval(load, Math.max(1, layer.refresh_minutes || 15) * 60000);
    });
    return () => timers.forEach(clearInterval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeCustomKey]);

  // Sentinel-2 scenes before the instant (reloaded each new day of the instant, when the layer is on).
  useEffect(() => setSentinel2(null), [queryDay]);
  useEffect(() => {
    if (layers.sentinel2 && !sentinel2) {
      fetchSentinel2Scenes(queryAt).then(setSentinel2).catch(() => setSentinel2({ features: [], available: false }));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.sentinel2, sentinel2]);

  // Heavy GFW 4Wings layers (daily reports): fetched only when switched on, for the day of the instant.
  useEffect(() => {
    setGfwSar(null);
    setEffort(null);
  }, [queryDay, gfwDays]);
  useEffect(() => {
    if (layers.gfwSar && !gfwSar) fetchGfwSar(gfwDays, queryAt).then(setGfwSar).catch(() => setGfwSar({ features: [], available: false }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.gfwSar, gfwSar]);
  useEffect(() => {
    if (layers.effort && !effort) fetchFishingEffort(gfwDays, queryAt).then(setEffort).catch(() => setEffort({ features: [], available: false }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [layers.effort, effort]);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const status = await fetchScanStatus();
        if (cancelled) return;
        const finished = ACTIVE_SCAN.has(lastStatus.current) && !ACTIVE_SCAN.has(status.status);
        lastStatus.current = status.status;
        // Only re-render when something changed: constant re-renders made map clicks flaky.
        setScan((current) => (sameScan(current, status) ? current : status));
        if (finished) {
          loadAlerts();
          onIncidentChange?.();
        }
      } catch {
        /* keep last known state */
      }
    };
    // Fast polling only while a scan runs (3 s); otherwise every 15 s (automatic watch scans).
    let timer = null;
    const loop = async () => {
      await poll();
      if (!cancelled) timer = setTimeout(loop, ACTIVE_SCAN.has(lastStatus.current) ? 3000 : 15000);
    };
    pollNow.current = () => {
      clearTimeout(timer);
      loop();
    };
    loop();
    return () => {
      cancelled = true;
      clearTimeout(timer);
      pollNow.current = null;
    };
  }, [loadAlerts, onIncidentChange]);

  const onScan = useCallback(async () => {
    try {
      await triggerScan(sceneId || undefined);
      lastStatus.current = "running";
      setScan({ status: "running", message: "Scan Sentinel-1 lancé.", step: "init" });
      setError("");
      pollNow.current?.();
    } catch (err) {
      setError(errorMessage(err, "Échec du déclenchement du scan"));
    }
  }, [sceneId]);

  const toggleLayer = useCallback((id) => setLayers((current) => ({ ...current, [id]: !current[id] })), []);

  const { allDark, aisVessels, scannedScenes } = useMemo(() => {
    const dark = [];
    const ais = [];
    const scanned = [];
    (alerts.features || []).forEach((feature, index) => {
      const props = feature.properties || {};
      if (props.type === "ais_vessel") ais.push({ ...feature, key: `ais:${props.mmsi || index}` });
      else if (props.type === "s1_scene") scanned.push(feature);
      else dark.push({ ...feature, key: `${props.alert_id || "alert"}:${props.vessel_id || index}` });
    });
    dark.reverse();
    dark.sort((a, b) => (b.properties?.risk_score ?? -1) - (a.properties?.risk_score ?? -1));
    return { allDark: dark, aisVessels: ais, scannedScenes: scanned };
  }, [alerts]);

  const darkVessels = useMemo(
    () => (hideClosed ? allDark.filter((f) => f.properties?.incident_status !== "closed") : allDark),
    [allDark, hideClosed],
  );

  // Selection is tracked by key and resolved against fresh data, so it survives reloads.
  const selectedIndex = darkVessels.findIndex((f) => f.key === selectedKey);
  const selected = selectedIndex >= 0 ? darkVessels[selectedIndex] : null;
  const selectByOffset = useCallback(
    (offset) => {
      if (darkVessels.length === 0) return;
      const index = selectedIndex < 0 ? 0 : (selectedIndex + offset + darkVessels.length) % darkVessels.length;
      setSelectedKey(darkVessels[index].key);
    },
    [darkVessels, selectedIndex],
  );
  const onPrev = useCallback(() => selectByOffset(-1), [selectByOffset]);
  const onNext = useCallback(() => selectByOffset(1), [selectByOffset]);
  const onClose = useCallback(() => setSelectedKey(null), []);

  // "#/situation/incident:12" (from an incident file): select that incident's latest radar target once loaded.
  const focusDone = useRef("");
  useEffect(() => {
    const match = /^incident:(\d+)$/.exec(decodeURIComponent(focus || ""));
    if (!match || focusDone.current === focus) return;
    const hits = allDark.filter((f) => String(f.properties?.incident_id) === match[1]);
    if (hits.length) {
      focusDone.current = focus;
      const latest = hits.reduce((a, b) => (String(b.properties?.timestamp || "") > String(a.properties?.timestamp || "") ? b : a));
      setSelectedKey(latest.key);
    }
  }, [focus, allDark]);
  const onContextPoint = useCallback((lat, lon) => setPoint({ lat, lon }), []);
  const onIncidentUpdated = useCallback(() => {
    loadAlerts();
    onIncidentChange?.();
  }, [loadAlerts, onIncidentChange]);

  const sceneLayer = useMemo(() => {
    const byId = new globalThis.Map();
    [...(scenes.features || []), ...scannedScenes].forEach((f) => byId.set(f.properties?.scene_id, f));
    return { type: "FeatureCollection", features: [...byId.values()].filter((f) => f.geometry) };
  }, [scenes, scannedScenes]);

  const scanning = ACTIVE_SCAN.has(scan.status);
  const scanFailed = scan.status === "error";
  const priorityCount = darkVessels.filter((f) => targetClass(f.properties) === "priority").length;
  const replaying = replayAt != null;
  const timeValue = useMemo(() => ({ at: queryAt }), [queryAt]);
  // In replay, the NASA imagery date is the day of the instant: picking a date moves the instant.
  const onImageryDate = useCallback(
    (date) => (replaying ? setReplayAt(Date.parse(`${date}T12:00:00Z`)) : setImageryDate(date)),
    [replaying],
  );

  return (
    <TimeContext.Provider value={timeValue}>
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-tactical-line bg-tactical-panel/60 px-3 py-2 text-xs text-slate-400 sm:gap-3 sm:px-4">
        <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
          <span className="flex items-center gap-2">
            <Ship size={14} style={{ color: TARGET.priority.color }} />
            {darkVessels.length} cible{darkVessels.length > 1 ? "s" : ""} sans AIS
          </span>
          <span className={`flex min-w-0 max-w-md items-center gap-2 ${scanFailed ? "text-tactical-red" : ""}`}>
            <Activity size={14} className={scanning ? "animate-pulse text-tactical-amber" : scanFailed ? "" : "text-tactical-green"} />
            <span className="truncate" title={scan.message}>
              {scan.message || "Aucun scan en cours"}
            </span>
          </span>
        </div>
        {canScan && (
          <div className="flex w-full items-center gap-2 sm:w-auto">
            <select
              value={sceneId}
              onChange={(e) => setSceneId(e.target.value)}
              disabled={scanning}
              className="min-w-0 flex-1 border border-tactical-line bg-tactical-bg px-2 py-1.5 text-slate-300 sm:max-w-[18rem] sm:flex-none"
              aria-label="Scène Sentinel-1"
            >
              <option value="">Dernière scène Sentinel-1</option>
              {(scenes.features || []).map((f) => (
                <option key={f.properties.scene_id} value={f.properties.scene_id}>
                  {sceneLabel(f)}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={onScan}
              disabled={scanning || replaying}
              title={
                replaying
                  ? "Revenez au direct pour lancer un scan"
                  : "Analyse la scène Sentinel-1 choisie : détection des échos radar, tri des faux échos, comparaison avec l'AIS au moment du passage, score de risque, scellement des preuves et ouverture des incidents (quelques minutes)"
              }
              className="flex items-center gap-2 border border-tactical-cyan/40 bg-tactical-cyan/10 px-3 py-1.5 text-tactical-cyan disabled:opacity-40"
            >
              {scanning ? <RefreshCw size={14} className="animate-spin" /> : <Radio size={14} />}
              Scanner
            </button>
          </div>
        )}
      </div>

      {error && <div className="border-b border-tactical-red/40 bg-tactical-red/10 px-4 py-2 text-xs text-tactical-red">{error}</div>}

      <div className="relative flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
        <div
          className={`relative isolate min-h-0 flex-1 overflow-hidden map-mode-${displayMode} marker-scale-${visual.symbolScale} ${
            replaying ? "outline outline-2 -outline-offset-2 outline-tactical-amber/70" : ""
          }`}
          style={{ "--tile-brightness": visual.tileBrightness }}
        >
          {replaying && (
            <div className="pointer-events-auto absolute left-1/2 top-2 z-[950] flex w-max max-w-[calc(100%-1rem)] -translate-x-1/2 flex-wrap items-center gap-x-3 gap-y-1 border border-tactical-amber/70 bg-tactical-bg/90 px-3 py-1.5 text-[11px] text-tactical-amber shadow-xl">
              <History size={13} />
              <span>
                <strong>REJEU</strong> — situation reconstituée au {formatInstant(queryAt || new Date(replayAt).toISOString())} : rien de postérieur
                n&apos;est affiché, incidents au statut de cet instant
              </span>
              <button
                type="button"
                onClick={() => setReplayAt(null)}
                className="border border-tactical-green/60 px-2 py-0.5 text-tactical-green hover:bg-tactical-green/10"
              >
                Revenir au direct
              </button>
            </div>
          )}
          <Map
            zee={zee}
            darkVessels={darkVessels}
            aisVessels={aisVessels}
            events={events}
            scenes={sceneLayer}
            optical={optical}
            layers={layers}
            basemap={basemap}
            imageryDate={effectiveImageryDate}
            selectedKey={selectedKey}
            onSelect={setSelectedKey}
            onContextPoint={onContextPoint}
            fishingLimits={fishingLimits}
            aisLive={aisLive}
            gfwSar={gfwSar}
            effort={effort}
            mpa={mpa}
            isobaths={isobaths}
            oceanLayers={oceanLayers}
            visual={visual}
            customLayers={customLayers}
            customData={customData}
            geoSlot={geoSlot}
            sentinel2={sentinel2}
            zones={zones}
            drawing={drawing}
            onDrawPoint={onDrawPoint}
          />
          {aisLive?.collection?.coverage != null && aisLive.collection.coverage < 0.5 && (
            <div
              className={`pointer-events-none absolute left-1/2 z-[940] w-max max-w-[calc(100%-1rem)] -translate-x-1/2 border border-tactical-red/60 bg-tactical-bg/90 px-3 py-1 text-[11px] text-tactical-red ${
                replaying ? "top-12" : "top-2"
              }`}
              role="status"
            >
              Collecte AIS : {Math.round(aisLive.collection.coverage * 100)} % des 2 h {replaying ? "précédant cet instant" : "écoulées"} —
              une mer vide ne signifie pas l&apos;absence de navires
            </div>
          )}
          {drawing && (
            <div className="absolute left-1/2 top-2 z-[960] flex w-max max-w-[calc(100%-1rem)] -translate-x-1/2 flex-wrap items-center gap-2 border border-tactical-cyan/60 bg-tactical-bg/95 px-3 py-2 text-[11px] text-slate-200 shadow-xl">
              <span>Cliquez sur la carte pour placer les sommets de la zone ({drawing.length} placé{drawing.length > 1 ? "s" : ""}).</span>
              <button type="button" disabled={!drawing.length} onClick={() => setDrawing((d) => d.slice(0, -1))} className="text-slate-400 hover:text-white disabled:opacity-40">
                annuler le dernier point
              </button>
              <button type="button" disabled={drawing.length < 3} onClick={() => { setZoneDraft({ name: "", color: "#22d3ee", description: "", error: "" }); }}
                className="border border-tactical-green/60 px-2 py-0.5 text-tactical-green disabled:opacity-40">
                Terminer
              </button>
              <button type="button" onClick={() => { setDrawing(null); setZoneDraft(null); }} className="text-slate-400 hover:text-tactical-red">
                abandonner
              </button>
            </div>
          )}
          {zoneDraft && drawing && (
            <div className="absolute left-1/2 top-14 z-[960] w-80 max-w-[calc(100%-1rem)] -translate-x-1/2 space-y-2 border border-tactical-line bg-tactical-panel p-3 text-xs shadow-2xl">
              <p className="text-slate-200">Nouvelle zone de surveillance · {drawing.length} sommets</p>
              <input autoFocus value={zoneDraft.name} maxLength={80} onChange={(e) => setZoneDraft({ ...zoneDraft, name: e.target.value })}
                placeholder="Nom (ex. Estuaire du Maroni)" className="w-full border border-tactical-line bg-tactical-bg px-2 py-1.5 text-slate-100" />
              <input value={zoneDraft.description} maxLength={1000} onChange={(e) => setZoneDraft({ ...zoneDraft, description: e.target.value })}
                placeholder="Description (facultatif)" className="w-full border border-tactical-line bg-tactical-bg px-2 py-1.5 text-slate-100" />
              <div className="flex items-center gap-1.5">
                {["#22d3ee", "#f59e0b", "#f472b6", "#a3e635", "#e2e8f0"].map((color) => (
                  <button key={color} type="button" aria-label={`Couleur ${color}`} onClick={() => setZoneDraft({ ...zoneDraft, color })}
                    className={`h-5 w-5 border ${zoneDraft.color === color ? "border-white" : "border-transparent"}`} style={{ backgroundColor: color }} />
                ))}
              </div>
              {zoneDraft.error && <p className="text-tactical-red">{zoneDraft.error}</p>}
              <div className="flex gap-2">
                <button type="button" disabled={zoneDraft.name.trim().length < 2}
                  onClick={async () => {
                    try {
                      await createZone({ name: zoneDraft.name.trim(), description: zoneDraft.description, color: zoneDraft.color,
                        geometry: { type: "Polygon", coordinates: [[...drawing, drawing[0]]] } });
                      setDrawing(null);
                      setZoneDraft(null);
                      loadZones();
                    } catch (err) {
                      setZoneDraft((z) => ({ ...z, error: errorMessage(err, "Zone refusée") }));
                    }
                  }}
                  className="border border-tactical-green/60 px-3 py-1 text-tactical-green disabled:opacity-40">
                  Enregistrer la zone
                </button>
                <button type="button" onClick={() => setZoneDraft(null)} className="text-slate-400 hover:text-white">continuer le tracé</button>
              </div>
            </div>
          )}
          {point && <PointDossier point={point} onClose={() => setPoint(null)} navigate={navigate} />}
          {visual.showLegend && (
            <div className="absolute bottom-6 left-2 z-[500] max-w-[calc(100%-1rem)] text-[11px] sm:left-4">
              <Legend layers={layers} visual={visual} customLayers={customLayers} gfwDays={gfwDays} />
            </div>
          )}
          <AlertDrawer
            alert={selected}
            position={selectedIndex}
            total={darkVessels.length}
            onPrev={onPrev}
            onNext={onNext}
            onClose={onClose}
            user={user}
            navigate={navigate}
            onIncidentUpdated={onIncidentUpdated}
            onOpticalFootprints={setOptical}
          />
        </div>
        <Timeline
          at={replayAt}
          onChange={setReplayAt}
          spanDays={spanDays}
          onSpanChange={setSpanDays}
          retentionDays={coverage?.history_days || 90}
        />
        </div>
        <SidePanel
          open={panelOpen}
          onOpenChange={setPanelOpen}
          pinned={panelPinned}
          onPinnedChange={setPanelPinned}
          tab={panelTab}
          onTabChange={setPanelTab}
          counts={{ priority: priorityCount, check: darkVessels.length - priorityCount }}
        >
          {panelTab === "map" && (
            <div className="p-4 text-xs">
              <MapStyleSection
                basemap={basemap}
                onBasemapChange={setBasemap}
                imageryDate={effectiveImageryDate}
                onImageryDateChange={onImageryDate}
                maxImageryDate={latestGibsDate()}
                displayMode={displayMode}
                onDisplayModeChange={setDisplayMode}
                layers={layers}
                onToggleLayer={toggleLayer}
                visual={visual}
                onVisualChange={setVisual}
                geoSlot={geoSlot}
              />
            </div>
          )}
          {panelTab === "layers" && (
            <div className="p-4 text-xs">
              <LayersSection
                layers={layers}
                onToggleLayer={toggleLayer}
                events={events}
                customLayers={customLayers}
                customData={customData}
                gfwDays={gfwDays}
                onGfwDaysChange={setGfwDays}
              />
              <ZonesSection
                zones={zones}
                layers={layers}
                onToggleLayer={toggleLayer}
                canEdit={hasRole(user, "supervisor") && !replaying}
                drawing={drawing}
                onStartDraw={() => {
                  setDrawing([]);
                  setLayers((l) => ({ ...l, drawnZones: true }));
                }}
                onDelete={async (zone) => {
                  if (!window.confirm(`Supprimer la zone « ${zone.name} » ?`)) return;
                  try {
                    await deleteZone(zone.id);
                    loadZones();
                  } catch (err) {
                    setError(errorMessage(err, "Suppression impossible"));
                  }
                }}
              />
            </div>
          )}
          {panelTab === "context" && (
            <div className="space-y-5 p-4 text-xs">
              <CoverageSection coverage={coverage} aisLive={aisLive} gfwSar={gfwSar} effort={effort} />
              <WeatherSection environment={environment} />
              <SourcesSection sources={sources} onOpenSettings={hasRole(user, "admin") ? () => navigate("admin") : null} />
            </div>
          )}
          {panelTab === "activity" && <ActivityReport onShowEffort={() => setLayers((l) => ({ ...l, effort: true }))} />}
          {panelTab === "targets" && (
            <div className="flex min-h-full flex-col text-xs">
              <div className="sticky top-0 z-10 flex items-center justify-between border-b border-tactical-line bg-tactical-panel px-4 py-3 text-[10px] uppercase tracking-[0.25em] text-slate-500">
                <span>Cibles (par risque)</span>
                <label className="flex cursor-pointer items-center gap-1 normal-case tracking-normal">
                  <input type="checkbox" checked={hideClosed} onChange={() => setHideClosed((v) => !v)} className="accent-cyan-400" />
                  masquer incidents clos
                </label>
              </div>
              <label className="flex items-center justify-between gap-2 border-b border-tactical-line px-4 py-2 text-[11px] text-slate-400">
                Période des cibles radar
                <select
                  value={targetDays}
                  onChange={(e) => setTargetDays(Number(e.target.value))}
                  className="border border-tactical-line bg-tactical-bg px-2 py-1 text-slate-300"
                  title="Les cibles dont l'incident est encore ouvert restent affichées quelle que soit la période"
                >
                  <option value={7}>7 derniers jours</option>
                  <option value={30}>30 derniers jours</option>
                  <option value={90}>90 derniers jours</option>
                  <option value={0}>Tout l&apos;historique</option>
                </select>
              </label>
              {darkVessels.length === 0 && (
                <p className="px-4 py-6 text-slate-500">
                  Aucune cible radar sans AIS sur la période. Lancez un scan Sentinel-1 (bouton « Scanner » en haut à droite) ou élargissez la période.{" "}
                  <DocLink page="situation/scan/" label="lancer un scan" />
                </p>
              )}
              {["priority", "check"].map((cls) => {
                const group = darkVessels.filter((f) => targetClass(f.properties) === cls);
                if (!group.length) return null;
                return (
                  <section key={cls}>
                    <p
                      className="flex items-center gap-2 border-b border-tactical-line bg-tactical-bg/40 px-4 py-1.5 text-[10px] uppercase tracking-widest"
                      style={{ color: TARGET[cls].color }}
                    >
                      <span className="legend-dot" style={{ backgroundColor: TARGET[cls].color }} />
                      <span title={TARGET[cls].hint}>{cls === "priority" ? "Prioritaires" : "À vérifier"} · {group.length}</span>
                    </p>
                    <ul>
                      {group.map((feature) => {
                        const props = feature.properties || {};
                        const active = feature.key === selectedKey;
                        const quality = QUALITY[props.quality_level];
                        return (
                          <li key={feature.key}>
                            <button
                              type="button"
                              onClick={() => setSelectedKey(feature.key)}
                              aria-current={active}
                              className={`block w-full border-b border-tactical-line px-4 py-2.5 text-left ${
                                active ? "bg-tactical-cyan/10 text-tactical-cyan" : "text-slate-300 hover:bg-white/5"
                              }`}
                            >
                              <span className="flex items-center justify-between gap-2">
                                <span className="truncate">{props.incident_ref || `${props.vessel_id} / ${props.alert_id}`}</span>
                                <span className="flex items-center gap-1">
                                  {props.incident_severity && (
                                    <Pill color={SEVERITY[props.incident_severity]?.color}>{props.incident_severity}</Pill>
                                  )}
                                  <RiskBadge score={props.risk_score} level={props.risk_level} compact />
                                </span>
                              </span>
                              <span className="mt-1 flex flex-wrap items-center gap-x-2 text-[10px] text-slate-500">
                                {props.incident_status && (
                                  <span style={{ color: STATUS[props.incident_status]?.color }}>{STATUS[props.incident_status]?.label}</span>
                                )}
                                {props.length_m != null && (props.pixels == null || props.pixels >= 6) && <span>~{Math.round(props.length_m)} m</span>}
                                {quality && <span style={{ color: quality.color }}>détection {quality.label.toLowerCase()}</span>}
                              </span>
                            </button>
                          </li>
                        );
                      })}
                    </ul>
                  </section>
                );
              })}
            </div>
          )}
        </SidePanel>
      </div>
    </div>
    </TimeContext.Provider>
  );
}
