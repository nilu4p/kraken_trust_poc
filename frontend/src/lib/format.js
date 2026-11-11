import { PLATFORM } from "../platform.js";

// Display helpers. Dates follow the platform display time zone (platform.js): "UTC" gives the
// ISO Zulu form used by maritime operations ("2026-10-19 21:34 Z"), any other IANA zone the
// French local form ("19/10/2026 18:34", Guiana time: UTC-3, no daylight saving).

const DATE_FORMAT =
  PLATFORM.timeZone === "UTC"
    ? null
    : new Intl.DateTimeFormat("fr-FR", {
        timeZone: PLATFORM.timeZone, day: "2-digit", month: "2-digit", year: "numeric", hour: "2-digit", minute: "2-digit",
      });

export function formatDateTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value);
  if (!DATE_FORMAT) return `${date.toISOString().slice(0, 16).replace("T", " ")} Z`;
  return DATE_FORMAT.format(date);
}

const NUMBER_CACHE = {};

/** French number formatting ("54 429", "4,2"); "—" for missing values. */
export function fmt(value, decimals = 0) {
  if (value == null || Number.isNaN(Number(value))) return "—";
  const key = String(decimals);
  NUMBER_CACHE[key] ||= new Intl.NumberFormat("fr-FR", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  return NUMBER_CACHE[key].format(Number(value));
}

export function pct(value, decimals = 0) {
  return value == null ? "—" : `${fmt(value * 100, decimals)} %`;
}

export function km(meters, decimals = 1) {
  if (meters == null || meters < 0) return "—";
  return meters >= 1000 ? `${fmt(meters / 1000, decimals)} km` : `${fmt(meters)} m`;
}

export function formatRelative(value) {
  if (!value) return "—";
  const diff = (new Date(value).getTime() - Date.now()) / 60000;
  const abs = Math.abs(diff);
  const text = abs < 60 ? `${Math.round(abs)} min` : abs < 2880 ? `${Math.round(abs / 60)} h` : `${Math.round(abs / 1440)} j`;
  return diff >= 0 ? `dans ${text}` : `il y a ${text}`;
}

export function formatCoords(lat, lon) {
  if (lat == null || lon == null) return "—";
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(Number(lat)).toFixed(4)}°${ns} ${Math.abs(Number(lon)).toFixed(4)}°${ew}`;
}

export function formatBytes(size) {
  if (size == null) return "—";
  if (size < 1024) return `${size} o`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} Ko`;
  return `${(size / 1024 / 1024).toFixed(1)} Mo`;
}

const ROLE_RANK = { viewer: 0, analyst: 1, supervisor: 2, admin: 3 };
export const ROLES = Object.keys(ROLE_RANK);

/** Display hint only: every right is enforced by the API on each request. */
export function hasRole(user, minimum) {
  return (ROLE_RANK[user?.role] ?? -1) >= ROLE_RANK[minimum];
}

/** localStorage read / write that never throws (private mode, storage disabled), namespaced per platform. */
export const storage = {
  get(key) {
    try {
      return window.localStorage.getItem(`${PLATFORM.id}:${key}`);
    } catch {
      return null;
    }
  },
  set(key, value) {
    try {
      window.localStorage.setItem(`${PLATFORM.id}:${key}`, value);
    } catch {
      /* private mode */
    }
  },
};
