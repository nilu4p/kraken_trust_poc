// Keyless imagery tile services (verified reachable): OSM, EOX Sentinel-2 cloudless, NASA GIBS VIIRS.

const GIBS = "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best";

export const BASEMAPS = {
  osm: {
    label: "Carte (OpenStreetMap)",
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    className: "tactical-tiles",
    maxNativeZoom: 19,
  },
  eox: {
    label: "Sentinel-2 sans nuages (EOX)",
    url: "https://tiles.maps.eox.at/wmts/1.0.0/s2cloudless-2024_3857/default/g/{z}/{y}/{x}.jpg",
    attribution:
      '<a href="https://s2maps.eu">Sentinel-2 cloudless</a> by EOX (Copernicus Sentinel data 2024)',
    maxNativeZoom: 15,
  },
  gibs_truecolor: {
    label: "VIIRS couleurs vraies du jour (NASA GIBS)",
    url: `${GIBS}/VIIRS_SNPP_CorrectedReflectance_TrueColor/default/{date}/GoogleMapsCompatible_Level9/{z}/{y}/{x}.jpg`,
    attribution: 'Imagery: <a href="https://earthdata.nasa.gov/gibs">NASA GIBS</a> / VIIRS',
    maxNativeZoom: 9,
    dated: true,
  },
};

// VIIRS Day/Night Band radiance (boats lit at night). Tiles are OPAQUE black where there is
// no light: they are drawn with "screen" blending (black = transparent) over the basemap.
// Each satellite has its own publication lag on NASA GIBS. Suomi-NPP's mosaic of the previous
// day is still being assembled: GIBS serves empty tiles and grey dithered RGBA tiles (partial
// granules) that "screen" blending turns into a whitish veil over the map. Its date is
// therefore held back to the last complete mosaic (lagDays).
export const NIGHT_SATELLITES = {
  noaa20: { label: "NOAA-20", layer: "VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance", lagDays: 1 },
  snpp: { label: "Suomi-NPP", layer: "VIIRS_SNPP_DayNightBand_At_Sensor_Radiance", lagDays: 2, since: "2026-07-16" },
};

export const OVERLAYS = {
  gibs_dnb: {
    label: "Lumières nocturnes VIIRS (flottilles de pêche)",
    attribution: "NASA GIBS / VIIRS Day/Night Band",
    maxNativeZoom: 8,
    opacity: 0.9,
  },
};

/** Date of the night-lights mosaic actually shown: the requested date, held back to the satellite's last complete one. */
export function nightLightsDate(satellite, date) {
  const latest = latestGibsDate((NIGHT_SATELLITES[satellite] || NIGHT_SATELLITES.noaa20).lagDays);
  return date && date < latest ? date : latest;
}

export function nightLightsUrl(satellite, date) {
  const layer = (NIGHT_SATELLITES[satellite] || NIGHT_SATELLITES.noaa20).layer;
  return `${GIBS}/${layer}/default/${nightLightsDate(satellite, date)}/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png`;
}

/** Latest date with complete NASA GIBS daily coverage (``lagDays`` ago, UTC; yesterday by default). */
export function latestGibsDate(lagDays = 1) {
  const date = new Date(Date.now() - lagDays * 24 * 3600 * 1000);
  return date.toISOString().slice(0, 10);
}

// --- Geostationary imagery: GOES-East (GOES-19) ABI over French Guiana, every 10 minutes ------------
// NASA GIBS keeps it (at least) for the 90 days of the replay. GeoColor = true colours by day,
// Band 13 "clean" infrared = clouds day and night (cold cloud tops bright). ~1-2 km per pixel:
// weather / cloud context of a radar pass or of a replayed instant, not vessel identification.
export const GEO_MODES = {
  auto: "Auto (couleurs le jour, infrarouge la nuit)",
  geocolor: "Couleurs vraies (GeoColor)",
  ir: "Infrarouge (nuages, jour et nuit)",
};
const GOES = {
  geocolor: { layer: "GOES-East_ABI_GeoColor", level: 7, label: "GOES-Est GeoColor" },
  ir: { layer: "GOES-East_ABI_Band13_Clean_Infrared", level: 6, label: "GOES-Est infrarouge (bande 13)" },
};
// Publication delay on GIBS (~10–20 min): live mode asks for the last image certainly available.
export const GOES_LATENCY_MS = 30 * 60 * 1000;
const TEN_MIN = 10 * 60 * 1000;

/** Image slot (epoch ms, floored to 10 min) shown for a reference instant (null = live). */
export function goesSlot(atIso, nowMs = Date.now()) {
  const t = atIso ? Math.min(Date.parse(atIso), nowMs - GOES_LATENCY_MS) : nowMs - GOES_LATENCY_MS;
  return Math.floor(t / TEN_MIN) * TEN_MIN;
}

/** Daylight over the EEZ (local solar time at 53° W ≈ UTC − 3 h 32). */
export function isDaylight(ms) {
  const solarHour = (((ms / 3600000 - 3.53) % 24) + 24) % 24;
  return solarHour >= 7 && solarHour < 17.5;
}

export function goesLayer(mode, slotMs) {
  const kind = mode === "auto" ? (isDaylight(slotMs) ? "geocolor" : "ir") : GOES[mode] ? mode : "geocolor";
  const meta = GOES[kind];
  const time = new Date(slotMs).toISOString().slice(0, 16) + ":00Z";
  return {
    url: `${GIBS}/${meta.layer}/default/${time}/GoogleMapsCompatible_Level${meta.level}/{z}/{y}/{x}.png`,
    label: meta.label,
    maxNativeZoom: meta.level,
    time,
  };
}
