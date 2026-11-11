import axios from "axios";
import { PLATFORM } from "../platform.js";

// HTTP core. Session = HttpOnly cookie (never readable
// from JS). State-changing requests carry the per-session CSRF token returned by
// /auth/login and /auth/me. Platform endpoints live in api.js, which re-exports this file.
export const client = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "",
  timeout: PLATFORM.apiTimeoutMs,
  withCredentials: true,
});

// Browser events: "<platform>:unauthorized" (session gone) and "<platform>:password-change".
export const EVENTS = {
  unauthorized: `${PLATFORM.id}:unauthorized`,
  passwordChange: `${PLATFORM.id}:password-change`,
};

let csrfToken = "";

export function setCsrfToken(token) {
  csrfToken = token || "";
}

client.interceptors.request.use((config) => {
  const method = (config.method || "get").toLowerCase();
  if (csrfToken && !["get", "head", "options"].includes(method)) {
    config.headers["X-CSRF-Token"] = csrfToken;
  }
  return config;
});

client.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const url = error.config?.url || "";
    if (status === 401 && !url.includes("/auth/login")) {
      window.dispatchEvent(new CustomEvent(EVENTS.unauthorized));
    }
    if (status === 403 && error.response?.data?.detail === "Changement de mot de passe requis") {
      window.dispatchEvent(new CustomEvent(EVENTS.passwordChange));
    }
    return Promise.reject(error);
  },
);

export function errorMessage(err, fallback = "Erreur inattendue") {
  if (err && !err.isAxiosError && err.message) return err.message;
  const detail = err?.response?.data?.detail;
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join(", ");
  if (typeof detail === "string") return detail;
  if (err?.response?.status === 429) return "Trop de requêtes : patientez une minute.";
  if (err?.code === "ECONNABORTED") return "Délai dépassé";
  if (!err?.response) return "API injoignable";
  return fallback;
}

export const get = (url, config) => client.get(url, config).then((r) => r.data);
export const post = (url, body, config) => client.post(url, body, config).then((r) => r.data);
export const put = (url, body, config) => client.put(url, body, config).then((r) => r.data);
export const patch = (url, body, config) => client.patch(url, body, config).then((r) => r.data);
export const del = (url, config) => client.delete(url, config).then((r) => r.data);

/** Absolute URL of an API path (images, tiles); absolute URLs are returned unchanged. */
export function assetUrl(path) {
  if (!path) return "";
  if (/^https?:\/\//.test(path)) return path;
  return `${import.meta.env.VITE_API_URL || ""}${path}`;
}

/** Authenticated file download (blob) saved under ``filename``; API errors keep their JSON detail. */
export async function download(url, filename, config = {}) {
  let response;
  try {
    response = await client.get(url, { responseType: "blob", timeout: 120000, ...config });
  } catch (err) {
    // Errors of a blob request arrive as a Blob too: decode the JSON detail for errorMessage().
    const data = err?.response?.data;
    if (data instanceof Blob) {
      try {
        err.response.data = JSON.parse(await data.text());
      } catch {
        /* not JSON: keep the generic message */
      }
    }
    throw err;
  }
  const href = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  link.rel = "noopener";
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}

// --- Auth (shared) --------------------------------------------------------------
export const login = (username, password) => post("/api/v1/auth/login", { username, password });
export const logout = () => post("/api/v1/auth/logout");
export const fetchMe = () => get("/api/v1/auth/me");
export const changePassword = (current_password, new_password) =>
  post("/api/v1/auth/password", { current_password, new_password });

// --- Audit, accounts (shared) ----------------------------------------------------
export const fetchAudit = (params) => get("/api/v1/audit", { params });
export const verifyAudit = () => get("/api/v1/audit/verify");
export const fetchUsers = () => get("/api/v1/users");
export const createUser = (body) => post("/api/v1/users", body);
export const updateUser = (id, body) => patch(`/api/v1/users/${id}`, body);
export const resetUserPassword = (id) => post(`/api/v1/users/${id}/reset-password`);

// --- Administration (shared) ------------------------------------------------------
export const fetchKeys = () => get("/api/v1/settings/keys");
export const saveKeys = (values) => put("/api/v1/settings/keys", values);
export const fetchRuntimeSettings = () => get("/api/v1/system/settings");
export const saveRuntimeSettings = (values) => put("/api/v1/system/settings", values);
export const fetchSystemHealth = () => get("/api/v1/system/health");
export const fetchSupervision = () => get("/api/v1/system/supervision");
export const fetchCatalogue = () => get("/api/v1/system/catalogue");

// --- Pluggable data sources (shared) ----------------------------------------------
export const fetchCustomSources = () => get("/api/v1/custom-sources");
export const fetchCustomLayerData = (id) => get(`/api/v1/custom-sources/${encodeURIComponent(id)}/data`, { timeout: 130000 });
export const createCustomSource = (definition, secret) => post("/api/v1/custom-sources", { definition, secret });
export const updateCustomSource = (id, definition, secret) =>
  put(`/api/v1/custom-sources/${encodeURIComponent(id)}`, { definition, secret });
export const deleteCustomSource = (id) => del(`/api/v1/custom-sources/${encodeURIComponent(id)}`);
export const testCustomSource = (definition, secret, date) =>
  post("/api/v1/custom-sources/test", { definition, secret, date: date || "" }, { timeout: 130000 });
export const customTileUrl = (id, date) =>
  `${import.meta.env.VITE_API_URL || ""}/api/v1/custom-sources/${encodeURIComponent(id)}/tiles/{z}/{x}/{y}${date ? `?date=${encodeURIComponent(date)}` : ""}`;
