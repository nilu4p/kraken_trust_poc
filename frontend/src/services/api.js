// Platform endpoints. The transport (session cookie, CSRF, error messages) and the endpoints
// common to every screen (auth, audit, accounts, administration, custom sources) live
// in client.js, re-exported here so that every view keeps importing from "services/api.js".
import { assetUrl, client, download, get, post, put } from "./client.js";

export * from "./client.js";

// --- Situation --------------------------------------------------------------
// Time-aware endpoints take ``at`` (ISO UTC instant of a replay; omitted = live): every layer
// of the situation then describes the same instant.
const atParams = (at, params = {}) => (at ? { ...params, at } : params);

export const fetchAlerts = (days = 30, at = null) => get("/api/v1/alerts", { params: atParams(at, { days }) });
export const fetchZee = () => get("/api/v1/zee");
export const fetchEnvironment = (at = null) => get("/api/v1/environment", { params: atParams(at) });
export const fetchEvents = (at = null) => get("/api/v1/events", { params: atParams(at) });
export const fetchScenes = (at = null) => get("/api/v1/scenes", { params: atParams(at) });
export const fetchSentinel2Scenes = (at = null, maxCloud = 60) =>
  get("/api/v1/imagery/sentinel2/scenes", { params: atParams(at, { max_cloud: maxCloud }), timeout: 60000 });
export const sentinel2TileUrl = (itemId) => assetUrl(`/api/v1/imagery/sentinel2/tiles/${itemId}/{z}/{x}/{y}.png`);
export const fetchTimeline = (start, end, bucket = "auto") =>
  get("/api/v1/timeline", { params: { start, ...(end ? { end } : {}), bucket } });
export const fetchSources = () => get("/api/v1/sources");
export const fetchScanStatus = () => get("/api/v1/scan/status");
export const triggerScan = (sceneId) => post("/api/v1/scan", null, { params: sceneId ? { scene_id: sceneId } : {} });
export const fetchPoint = (lat, lon, at = null) => get("/api/v1/point", { params: atParams(at, { lat, lon }) });
export const fetchImagery = (lat, lon, around, signal) =>
  get("/api/v1/imagery", { params: { lat, lon, around: around || undefined, days: 3 }, signal, timeout: 60000 });

// --- Incidents --------------------------------------------------------------
export const fetchIncidents = (params) => get("/api/v1/incidents", { params });
export const fetchIncident = (id) => get(`/api/v1/incidents/${id}`);
export const fetchIncidentMeta = () => get("/api/v1/incidents/meta");
export const fetchKpis = () => get("/api/v1/incidents/kpis");
export const transitionIncident = (id, status, extra = {}) =>
  post(`/api/v1/incidents/${id}/transition`, { status, ...extra });
export const assignIncident = (id, assignee_id) => post(`/api/v1/incidents/${id}/assign`, { assignee_id });
export const commentIncident = (id, text) => post(`/api/v1/incidents/${id}/comment`, { text });
export const escalateIncident = (id, body) => post(`/api/v1/incidents/${id}/escalate`, body);
export const setIncidentAction = (id, key, done) => post(`/api/v1/incidents/${id}/actions/${key}`, { done });

// --- Analyse optique à la demande (aide à la décision) -------------------------
// Remote imagery reads: up to ~2 min.
export const scanOptical = (body) => post("/api/v1/sensors/optical/scan", body, { timeout: 150000 });

// --- Commandement : aide à la décision, moyens, rapports ----------------------
export const fetchDecision = (id) => get(`/api/v1/incidents/${id}/decision`, { timeout: 60000 });
export const fetchAssets = () => get("/api/v1/assets");
export const createAsset = (body) => post("/api/v1/assets", body);
export const updateAsset = (id, body) => put(`/api/v1/assets/${id}`, body);
export const deleteAsset = (id) => client.delete(`/api/v1/assets/${id}`).then((r) => r.data);
export const fetchReports = (params = {}) => get("/api/v1/reports", { params });
export const fetchReport = (id) => get(`/api/v1/reports/${id}`);
// issue: false = preview (nothing stored), true = signed and registered.
export const draftIncidentReport = (id, body) => post(`/api/v1/reports/incident/${id}`, body, { timeout: 60000 });
export const draftSitrep = (body) => post("/api/v1/reports/sitrep", body, { timeout: 60000 });
export const verifyReport = (id) => post(`/api/v1/reports/${id}/verify`);
export const reportExportUrl = (id) => assetUrl(`/api/v1/reports/${id}/export`);
export const fetchAssignable = () => get("/api/v1/users/assignable");

// --- Evidence ---------------------------------------------------------------
export const fetchEvidence = () => get("/api/v1/evidence");
export const verifyEvidence = (dossier) => post(`/api/v1/evidence/${encodeURIComponent(dossier)}/verify`);
export const downloadEvidence = (dossier) =>
  download(`/api/v1/evidence/${encodeURIComponent(dossier)}/export`, `${dossier}_preuves.zip`);

// --- Administration -----------------------------------------------------------
export const testKeys = (service, values) =>
  post(`/api/v1/settings/keys/test/${service}`, values || {}, { timeout: 60000 });

/** Same evidence file, displayed as a preview: not counted as a chain-of-custody consultation. */
export function previewUrl(path) {
  const url = assetUrl(path);
  return url ? `${url}${url.includes("?") ? "&" : "?"}preview=1` : "";
}

// --- Regulatory limits, watchlist, catalogue ----------------------------------
export const fetchFishingLimits = () => get("/api/v1/fishing-limits");
export const fetchWatchlist = (q) => get("/api/v1/watchlist", { params: q ? { q } : {} });
export const importWatchlist = (file, source = "iuu_combined") =>
  client
    .post("/api/v1/watchlist/import", file, {
      params: { source },
      headers: { "Content-Type": "application/octet-stream" },
      timeout: 120000,
    })
    .then((r) => r.data);
export const addWatchlistEntry = (entry) => post("/api/v1/watchlist", entry);
export const deleteWatchlistEntry = (id) => client.delete(`/api/v1/watchlist/${id}`).then((r) => r.data);

// --- Continuous maritime picture --------------------------------------------------
export const fetchAisPicture = (hours = 2, trackHours = 6, at = null) =>
  get("/api/v1/maritime/ais", { params: atParams(at, { hours, track_hours: trackHours }) });
export const fetchGfwSar = (days = 30, at = null) =>
  get("/api/v1/maritime/gfw-sar", { params: atParams(at, { days }), timeout: 150000 });
export const fetchFishingEffort = (days = 30, at = null) =>
  get("/api/v1/maritime/fishing-effort", { params: atParams(at, { days }), timeout: 150000 });
export const fetchCoverage = () => get("/api/v1/maritime/coverage");
export const fetchVesselProfile = (mmsi, at = null) =>
  get(`/api/v1/maritime/vessel/${encodeURIComponent(mmsi)}`, { params: atParams(at) });
export const fetchActivityReport = (start, end, at = null) =>
  get("/api/v1/maritime/activity-report", { params: atParams(at, { start, end }), timeout: 400000 });
export const fetchIsobaths = () => get("/api/v1/maritime/isobaths", { timeout: 120000 });
export const fetchMarineProtectedAreas = () => get("/api/v1/maritime/mpa", { timeout: 60000 });
export const fetchOceanLayers = (at = null) => get("/api/v1/maritime/ocean-layers", { params: atParams(at) });
export const importVms = (file) =>
  client
    .post("/api/v1/maritime/vms/import", file, {
      headers: { "Content-Type": "application/octet-stream" },
      timeout: 120000,
    })
    .then((r) => r.data);

// --- Vessels authorised to fish (licences) -----------------------------------
export const fetchAuthorized = (q) => get("/api/v1/authorized", { params: q ? { q } : {} });
export const addAuthorized = (entry) => post("/api/v1/authorized", entry);
export const deleteAuthorized = (id) => client.delete(`/api/v1/authorized/${id}`).then((r) => r.data);
export const importAuthorized = (file, replace = true) =>
  client
    .post("/api/v1/authorized/import", file, {
      params: { replace },
      headers: { "Content-Type": "application/octet-stream" },
      timeout: 120000,
    })
    .then((r) => r.data);

// --- Detection rules (built-in explained, custom rules) --------------------------
export const fetchRules = () => get("/api/v1/rules");
export const toggleBuiltinRule = (id, enabled) => put(`/api/v1/rules/builtin/${encodeURIComponent(id)}`, { enabled });
export const previewRule = (definition, at = null) => post("/api/v1/rules/preview", { definition, ...(at ? { at } : {}) });
export const createRule = (rule) => post("/api/v1/rules/custom", rule);
export const updateRule = (id, rule) => put(`/api/v1/rules/custom/${id}`, rule);
export const deleteRule = (id) => client.delete(`/api/v1/rules/custom/${id}`).then((r) => r.data);

// --- Entity graph over time ----------------------------------------------------------
export const fetchGraph = (focus, { days = 7, depth = 1, at = null } = {}) => {
  const params = new URLSearchParams();
  focus.forEach((f) => params.append("focus", f));
  params.set("days", String(days));
  params.set("depth", String(depth));
  if (at) params.set("at", at);
  return get(`/api/v1/graph?${params.toString()}`, { timeout: 60000 });
};
export const searchEntities = (q) => get("/api/v1/graph/search", { params: { q } });

// --- Surveillance zones drawn on the map -------------------------------------------
export const fetchZones = () => get("/api/v1/zones");
export const createZone = (zone) => post("/api/v1/zones", zone);
export const deleteZone = (id) => client.delete(`/api/v1/zones/${id}`).then((r) => r.data);
