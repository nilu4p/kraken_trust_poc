import { useEffect, useMemo, useState } from "react";
import { CheckCircle2, Copy, Plug, Plus, Save, TestTube2, Trash2, X, XCircle } from "lucide-react";
import { Button, ErrorBanner, Field, Panel, Pill, Spinner, inputClass } from "../components/ui.jsx";
import { latestDate as latestGibsDate } from "../lib/dates.js";
import { PLATFORM } from "../platform.js";
import { formatRelative } from "../lib/format.js";
import { createCustomSource, deleteCustomSource, errorMessage, fetchCustomSources, testCustomSource, updateCustomSource } from "../services/api.js";

const BLANK = {
  id: "",
  name: "",
  description: "",
  category: "Source personnalisée",
  enabled: true,
  kind: "features",
  url: "",
  method: "GET",
  params: [],
  headers: [],
  body: "",
  auth: { type: "none", name: "", username: "" },
  format: "auto",
  csv_delimiter: "",
  mapping: { records_path: "", lat: "", lon: "", geometry: "", time: "", id: "", label: "", fields: [] },
  pagination: { type: "none", param: "page", start: 1, size_param: "", size: 100, next_path: "", max_pages: 1 },
  tiles: { max_native_zoom: 18, attribution: "", opacity: 0.8, blend: "normal" },
  wms: { layers: "", styles: "", format: "image/png", version: "1.3.0", transparent: true, time: false },
  style: { color: "#22d3ee", radius: 5 },
  lookback_days: 7,
  refresh_minutes: 15,
  timeout_s: 20,
};

// Starting points: each one is a real, keyless service (test it right away) or a pattern to adapt.
const PRESETS = [
  {
    id: "rest",
    label: "API REST JSON",
    hint: "Liste d'objets avec latitude / longitude ; filtre sur la zone et la période",
    definition: {
      name: "Séismes récents (USGS)",
      url: "https://earthquake.usgs.gov/fdsnws/event/1/query",
      params: [
        { key: "format", value: "geojson" },
        { key: "starttime", value: "{start}" },
        { key: "minlatitude", value: "{south}" },
        { key: "maxlatitude", value: "{north}" },
        { key: "minlongitude", value: "{west}" },
        { key: "maxlongitude", value: "{east}" },
      ],
      lookback_days: 365,
      mapping: { ...BLANK.mapping, label: "title", time: "time" },
      style: { color: "#f97316", radius: 6 },
    },
  },
  {
    id: "geojson",
    label: "Fichier GeoJSON",
    hint: "URL d'un GeoJSON publié (open data, export SIG)",
    definition: {
      name: "Séismes de la semaine (GeoJSON)",
      format: "geojson",
      url: "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/2.5_week.geojson",
      mapping: { ...BLANK.mapping, label: "title" },
    },
  },
  {
    id: "single",
    label: "Objet unique (station, prévision)",
    hint: "La réponse entière est un seul enregistrement positionné",
    definition: {
      name: "Houle au large de Cayenne (Open-Meteo)",
      url: "https://marine-api.open-meteo.com/v1/marine",
      params: [
        { key: "latitude", value: "5.6" },
        { key: "longitude", value: "-52.4" },
        { key: "current", value: "wave_height,wave_direction,ocean_current_velocity" },
      ],
      mapping: { ...BLANK.mapping, records_path: ".", label: "current.wave_height" },
      refresh_minutes: 60,
    },
  },
  {
    id: "csv",
    label: "Fichier CSV",
    hint: "Colonnes latitude / longitude détectées automatiquement",
    definition: { name: "Export CSV", format: "csv", url: "https://exemple.org/export.csv" },
  },
  {
    id: "arcgis",
    label: "ArcGIS FeatureServer",
    hint: `Requête /query en GeoJSON sur l'emprise de ${PLATFORM.territoryLabel}`,
    definition: {
      name: "Couche ArcGIS",
      format: "geojson",
      url: "https://services.arcgis.com/<organisation>/arcgis/rest/services/<service>/FeatureServer/0/query",
      params: [
        { key: "where", value: "1=1" },
        { key: "outFields", value: "*" },
        { key: "geometry", value: "{bbox}" },
        { key: "geometryType", value: "esriGeometryEnvelope" },
        { key: "inSR", value: "4326" },
        { key: "outSR", value: "4326" },
        { key: "f", value: "geojson" },
      ],
    },
  },
  {
    id: "gibs",
    label: "Tuiles NASA GIBS (datées)",
    hint: "WMTS en {z}/{y}/{x} ; {date} = date d'imagerie de la carte",
    definition: {
      name: "Lumières nocturnes NOAA-20 (GIBS)",
      kind: "tiles",
      url: "https://gibs.earthdata.nasa.gov/wmts/epsg3857/best/VIIRS_NOAA20_DayNightBand_At_Sensor_Radiance/default/{date}/GoogleMapsCompatible_Level8/{z}/{y}/{x}.png",
      tiles: { max_native_zoom: 8, attribution: "NASA GIBS", opacity: 0.9, blend: "screen" },
      refresh_minutes: 720,
    },
  },
  {
    id: "wms",
    label: "Service OGC WMS",
    hint: "GetMap en EPSG:3857 tuile par tuile",
    definition: {
      name: "Bathymétrie GEBCO (WMS)",
      kind: "wms",
      url: "https://wms.gebco.net/mapserv",
      wms: { ...BLANK.wms, layers: "GEBCO_LATEST" },
      tiles: { max_native_zoom: 12, attribution: "GEBCO Compilation Group", opacity: 0.6, blend: "normal" },
      refresh_minutes: 10080,
    },
  },
  { id: "xyz", label: "Tuiles XYZ", hint: "Modèle {z}/{x}/{y} ({-y} pour TMS)", definition: { name: "Tuiles", kind: "tiles", url: "https://tuiles.exemple.org/{z}/{x}/{y}.png" } },
];

const PLACEHOLDER_HELP = [
  ["{secret}", "secret de la source (jamais affiché, jamais envoyé au navigateur)"],
  ["{bbox} · {west} {south} {east} {north}", `emprise de ${PLATFORM.territoryLabel} (lon/lat WGS84)`],
  ["{start} · {end} · {start_iso} · {end_iso}", "période : aujourd'hui − « jours d'historique » → aujourd'hui"],
  ["{date} · {today}", "date d'imagerie de la carte (veille par défaut) · aujourd'hui"],
  ["{page} · {offset} · {limit}", "pagination"],
  ["{z} {x} {y} {-y}", "tuiles (y inversé TMS)"],
];

const STAGES = [
  ["url", "URL et sécurité"],
  ["network", "Connexion"],
  ["http", "Réponse HTTP"],
  ["parse", "Lecture"],
  ["mapping", "Positions"],
];

const KIND_LABELS = { features: "Objets (JSON / GeoJSON / CSV)", tiles: "Tuiles XYZ / WMTS", wms: "OGC WMS" };
const MAPPING_TARGETS = [
  ["lat", "Latitude"],
  ["lon", "Longitude"],
  ["geometry", "Géométrie"],
  ["label", "Libellé"],
  ["time", "Date"],
  ["id", "Identifiant"],
  ["fields", "Champ affiché"],
];

const smallInput = `${inputClass} py-1.5`;

function merge(base, patch) {
  const out = { ...base };
  Object.entries(patch || {}).forEach(([k, v]) => {
    out[k] = v && typeof v === "object" && !Array.isArray(v) ? { ...(base[k] || {}), ...v } : v;
  });
  return out;
}

/** Saved definition → editable form (drops server-only fields). */
function toForm(source) {
  const { has_secret: _h, secret_hint: _s, updated_at: _u, ...definition } = source;
  return merge(BLANK, definition);
}

function KeyValueEditor({ label, items, onChange, keyPlaceholder, valuePlaceholder }) {
  const update = (index, patch) => onChange(items.map((item, i) => (i === index ? { ...item, ...patch } : item)));
  return (
    <div className="space-y-1.5 text-xs">
      <p className="flex items-center justify-between text-slate-500">
        {label}
        <button type="button" onClick={() => onChange([...items, { key: "", value: "" }])} className="flex items-center gap-1 text-tactical-cyan hover:underline">
          <Plus size={12} /> ajouter
        </button>
      </p>
      {items.length === 0 && <p className="text-[11px] text-slate-600">Aucun.</p>}
      {items.map((item, index) => (
        <div key={index} className="flex gap-1.5">
          <input className={`${smallInput} w-2/5`} value={item.key} placeholder={keyPlaceholder} onChange={(e) => update(index, { key: e.target.value })} spellCheck={false} />
          <input className={smallInput} value={item.value} placeholder={valuePlaceholder} onChange={(e) => update(index, { value: e.target.value })} spellCheck={false} />
          <button type="button" onClick={() => onChange(items.filter((_, i) => i !== index))} className="px-1 text-slate-500 hover:text-tactical-red" aria-label="Retirer">
            <X size={13} />
          </button>
        </div>
      ))}
    </div>
  );
}

function Select({ value, onChange, options }) {
  return (
    <select className={smallInput} value={value} onChange={(e) => onChange(e.target.value)}>
      {options.map(([v, l]) => (
        <option key={v} value={v}>
          {l}
        </option>
      ))}
    </select>
  );
}

function Num({ value, onChange, min, max, step = 1 }) {
  return <input type="number" className={smallInput} value={value} min={min} max={max} step={step} onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
}

function Text({ value, onChange, placeholder, mono = false }) {
  return <input className={`${smallInput}${mono ? " font-mono" : ""}`} value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} spellCheck={false} />;
}

function StagePipeline({ result }) {
  const failed = result.ok ? -1 : STAGES.findIndex(([id]) => id === result.stage);
  return (
    <ol className="flex flex-wrap gap-1 text-[10px]">
      {STAGES.filter(([id]) => result.kind === "features" || id !== "mapping").map(([id, label], index) => {
        const state = result.ok || index < failed ? "ok" : index === failed ? "ko" : "skip";
        const color = { ok: "#3dff8a", ko: "#ff3b3b", skip: "#475569" }[state];
        return (
          <li key={id} className="flex items-center gap-1 border px-1.5 py-0.5" style={{ color, borderColor: `${color}66` }}>
            {state === "ok" ? <CheckCircle2 size={11} /> : state === "ko" ? <XCircle size={11} /> : null}
            {label}
          </li>
        );
      })}
    </ol>
  );
}

function TestReport({ result, onPickField, pickTarget, onPickTarget }) {
  if (!result) return null;
  return (
    <div className="space-y-3 text-xs">
      <StagePipeline result={result} />
      <p className={`flex items-start gap-2 ${result.ok ? "text-tactical-green" : "text-tactical-red"}`}>
        {result.ok ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" /> : <XCircle size={14} className="mt-0.5 shrink-0" />}
        {result.message}
      </p>
      {result.warnings?.length > 0 && (
        <ul className="space-y-1 text-tactical-amber">
          {result.warnings.map((w) => (
            <li key={w}>⚠ {w}</li>
          ))}
        </ul>
      )}
      {result.request && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">Requête envoyée</p>
          <pre className="overflow-x-auto whitespace-pre-wrap break-all border border-tactical-line bg-tactical-bg p-2 text-[11px] text-slate-300">
            {result.request.method} {result.request.url}
            {Object.entries(result.request.headers || {}).map(([k, v]) => `\n${k}: ${v}`).join("")}
            {result.request.body ? `\n\n${result.request.body}` : ""}
          </pre>
        </div>
      )}
      {result.http && (
        <p className="text-slate-400">
          HTTP {result.http.status} · {result.http.content_type || "type inconnu"} · {Math.round(result.http.bytes / 1024)} Ko · {result.http.elapsed_ms} ms
          {result.pages > 1 && ` · ${result.pages} pages`}
        </p>
      )}
      {result.tile?.preview && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">
            Tuile {result.tile.z}/{result.tile.x}/{result.tile.y} (Guyane)
          </p>
          <img src={result.tile.preview} alt="Aperçu de la tuile" className="h-48 w-48 border border-tactical-line bg-[repeating-conic-gradient(#1e293b_0_25%,#0f172a_0_50%)] bg-[length:16px_16px]" />
        </div>
      )}
      {result.detected_fields?.length > 0 && (
        <div className="space-y-1.5">
          <p className="flex flex-wrap items-center gap-2 text-[10px] uppercase tracking-widest text-slate-500">
            Champs du 1er enregistrement{result.records_path ? ` (chemin « ${result.records_path} »)` : ""} — cliquer pour affecter à
            <select value={pickTarget} onChange={(e) => onPickTarget(e.target.value)} className="border border-tactical-line bg-tactical-bg px-1 py-0.5 normal-case tracking-normal text-slate-300">
              {MAPPING_TARGETS.map(([v, l]) => (
                <option key={v} value={v}>
                  {l}
                </option>
              ))}
            </select>
          </p>
          <div className="flex flex-wrap gap-1">
            {result.detected_fields.map((f) => (
              <button
                key={f.path}
                type="button"
                onClick={() => onPickField(f.path)}
                className="border border-tactical-line px-1.5 py-0.5 text-left text-[11px] text-slate-300 hover:border-tactical-cyan/60 hover:text-tactical-cyan"
                title={`Exemple : ${JSON.stringify(f.sample)}`}
              >
                {f.path}
                <span className="ml-1 text-slate-600">{String(JSON.stringify(f.sample)).slice(0, 24)}</span>
              </button>
            ))}
          </div>
          {result.mapping_used && (result.mapping_used.lat || result.mapping_used.geometry) && (
            <p className="text-[11px] text-slate-500">
              Position lue dans : {result.mapping_used.geometry || `${result.mapping_used.lat} / ${result.mapping_used.lon}`}
            </p>
          )}
        </div>
      )}
      {result.sample?.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] uppercase tracking-widest text-slate-500">Aperçu ({result.sample.length} premiers objets)</p>
          <div className="overflow-x-auto border border-tactical-line">
            <table className="w-full text-[11px]">
              <thead className="text-left text-slate-500">
                <tr>
                  <th className="px-2 py-1">Position</th>
                  <th className="px-2 py-1">Libellé</th>
                  <th className="px-2 py-1">Date</th>
                  <th className="px-2 py-1">Attributs</th>
                </tr>
              </thead>
              <tbody>
                {result.sample.map((f, i) => (
                  <tr key={i} className="border-t border-tactical-line align-top text-slate-300">
                    <td className="whitespace-nowrap px-2 py-1">
                      {f.geometry.type === "Point" ? f.geometry.coordinates.map((c) => Number(c).toFixed(3)).reverse().join(", ") : f.geometry.type}
                    </td>
                    <td className="px-2 py-1">{String(f.properties._label ?? "—")}</td>
                    <td className="px-2 py-1">{String(f.properties._time ?? "—")}</td>
                    <td className="px-2 py-1 text-slate-500">{Object.keys(f.properties).filter((k) => !k.startsWith("_")).length} champ(s)</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
      {result.preview && (
        <details>
          <summary className="cursor-pointer text-[10px] uppercase tracking-widest text-slate-500">Réponse brute (début)</summary>
          <pre className="mt-1 max-h-64 overflow-auto whitespace-pre-wrap break-all border border-tactical-line bg-tactical-bg p-2 text-[11px] text-slate-400">
            {result.preview}
          </pre>
        </details>
      )}
    </div>
  );
}

function Editor({ initial, saved, onSaved, onDeleted, onClose }) {
  const [form, setForm] = useState(initial);
  const [secret, setSecret] = useState("");
  const [removeSecret, setRemoveSecret] = useState(false);
  const [result, setResult] = useState(null);
  const [pickTarget, setPickTarget] = useState("label");
  const [testDate, setTestDate] = useState(latestGibsDate());
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const set = (key) => (value) => setForm((f) => ({ ...f, [key]: value }));
  const setIn = (group, key) => (value) => setForm((f) => ({ ...f, [group]: { ...f[group], [key]: value } }));
  const isTiles = form.kind !== "features";
  const usesDate = /\{date\}/.test(form.url) || form.params.some((p) => /\{date\}/.test(p.value)) || (form.kind === "wms" && form.wms.time);
  // None = keep the stored secret · "" = remove · value = replace.
  const secretPayload = removeSecret ? "" : secret.trim() ? secret.trim() : saved ? null : "";

  const definition = () => ({
    ...form,
    mapping: { ...form.mapping, fields: (form.mapping.fields || []).filter(Boolean) },
    params: form.params.filter((p) => p.key.trim()),
    headers: form.headers.filter((h) => h.key.trim()),
  });

  const run = async (label, action) => {
    setBusy(label);
    setError("");
    try {
      await action();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy("");
    }
  };

  const pickField = (path) => {
    if (pickTarget === "fields") {
      const fields = form.mapping.fields || [];
      setIn("mapping", "fields")(fields.includes(path) ? fields.filter((f) => f !== path) : [...fields, path]);
    } else {
      setIn("mapping", pickTarget)(path);
    }
  };

  return (
    <div className="grid grid-cols-1 gap-4 2xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="space-y-4">
        <Panel
          title={saved ? `Modifier « ${saved.name} »` : "Nouvelle source"}
          icon={Plug}
          actions={
            <button type="button" onClick={onClose} className="text-slate-500 hover:text-white" aria-label="Fermer">
              <X size={14} />
            </button>
          }
        >
          {!saved && (
            <div className="mb-4 space-y-1.5">
              <p className="text-[10px] uppercase tracking-widest text-slate-500">Partir d&apos;un modèle</p>
              <div className="flex flex-wrap gap-1.5">
                {PRESETS.map((preset) => (
                  <button
                    key={preset.id}
                    type="button"
                    title={preset.hint}
                    onClick={() => {
                      setForm(merge(BLANK, preset.definition));
                      setResult(null);
                    }}
                    className="border border-tactical-line px-2 py-1 text-[11px] text-slate-300 hover:border-tactical-cyan/60 hover:text-tactical-cyan"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </div>
          )}
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Nom affiché">
              <Text value={form.name} onChange={set("name")} placeholder="Balises de pêche, radar côtier…" />
            </Field>
            <Field label="Catégorie">
              <Text value={form.category} onChange={set("category")} />
            </Field>
            <div className="sm:col-span-2">
              <Field label="Description (aide aux opérateurs)">
                <Text value={form.description} onChange={set("description")} placeholder="Ce que montre la couche et comment l'interpréter" />
              </Field>
            </div>
            <Field label="Type de source">
              <Select value={form.kind} onChange={set("kind")} options={Object.entries(KIND_LABELS)} />
            </Field>
            <label className="flex items-end gap-2 pb-2 text-xs text-slate-300">
              <input type="checkbox" checked={form.enabled} onChange={() => set("enabled")(!form.enabled)} className="accent-cyan-400" />
              Active (visible dans les couches de la carte)
            </label>
          </div>
        </Panel>

        <Panel title="Requête" icon={Plug}>
          <div className="space-y-3">
            <div className="flex gap-2">
              {!isTiles && (
                <div className="w-24 shrink-0">
                  <Select value={form.method} onChange={set("method")} options={[["GET", "GET"], ["POST", "POST"]]} />
                </div>
              )}
              <Text value={form.url} onChange={set("url")} placeholder={isTiles && form.kind === "tiles" ? "https://…/{z}/{x}/{y}.png" : "https://api.exemple.org/v1/…"} mono />
            </div>
            <KeyValueEditor label="Paramètres de requête (?clé=valeur)" items={form.params} onChange={set("params")} keyPlaceholder="bbox" valuePlaceholder="{bbox}" />
            <KeyValueEditor label="En-têtes HTTP" items={form.headers} onChange={set("headers")} keyPlaceholder="Accept" valuePlaceholder="application/json" />
            {form.method === "POST" && !isTiles && (
              <Field label="Corps (JSON, placeholders acceptés)">
                <textarea className={`${inputClass} h-28 font-mono`} value={form.body} onChange={(e) => set("body")(e.target.value)} spellCheck={false} />
              </Field>
            )}
            <details className="text-[11px] text-slate-500">
              <summary className="cursor-pointer">Variables utilisables dans l&apos;URL, les paramètres, les en-têtes et le corps</summary>
              <ul className="mt-1 space-y-0.5">
                {PLACEHOLDER_HELP.map(([token, help]) => (
                  <li key={token}>
                    <code className="text-tactical-cyan">{token}</code> — {help}
                  </li>
                ))}
              </ul>
            </details>
          </div>
        </Panel>

        <Panel title="Authentification" icon={Plug}>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="Méthode">
              <Select
                value={form.auth.type}
                onChange={setIn("auth", "type")}
                options={[
                  ["none", "Aucune (ou {secret} placé à la main)"],
                  ["header", "Clé dans un en-tête"],
                  ["query", "Clé dans l'URL (?param=)"],
                  ["bearer", "Jeton Bearer"],
                  ["basic", "HTTP Basic (identifiant + mot de passe)"],
                ]}
              />
            </Field>
            {(form.auth.type === "header" || form.auth.type === "query") && (
              <Field label={form.auth.type === "header" ? "Nom de l'en-tête" : "Nom du paramètre"}>
                <Text value={form.auth.name} onChange={setIn("auth", "name")} placeholder={form.auth.type === "header" ? "X-API-Key" : "api_key"} />
              </Field>
            )}
            {form.auth.type === "basic" && (
              <Field label="Identifiant">
                <Text value={form.auth.username} onChange={setIn("auth", "username")} />
              </Field>
            )}
            <div className="sm:col-span-2">
              <Field
                label="Secret (clé, jeton ou mot de passe)"
                hint={saved?.has_secret ? `enregistré ${saved.secret_hint} · chiffré` : "chiffré au repos, jamais renvoyé"}
              >
                <input
                  className={inputClass}
                  type="password"
                  name="custom-source-secret"
                  value={secret}
                  onChange={(e) => setSecret(e.target.value)}
                  placeholder={saved?.has_secret ? "Laisser vide pour conserver" : "Coller la valeur"}
                  autoComplete="new-password"
                  data-lpignore="true"
                  data-1p-ignore="true"
                  readOnly
                  onFocus={(e) => {
                    e.currentTarget.readOnly = false;
                  }}
                  disabled={removeSecret}
                  spellCheck={false}
                />
              </Field>
              {saved?.has_secret && (
                <label className="mt-1 flex items-center gap-2 text-[11px] text-slate-400">
                  <input type="checkbox" checked={removeSecret} onChange={() => setRemoveSecret((v) => !v)} className="accent-cyan-400" />
                  Supprimer le secret enregistré
                </label>
              )}
            </div>
          </div>
        </Panel>

        {!isTiles && (
          <Panel title="Lecture de la réponse" icon={Plug}>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Format">
                <Select value={form.format} onChange={set("format")} options={[["auto", "Détection automatique"], ["json", "JSON"], ["geojson", "GeoJSON"], ["csv", "CSV"]]} />
              </Field>
              {form.format === "csv" ? (
                <Field label="Séparateur CSV" hint="vide = détecté">
                  <Text value={form.csv_delimiter} onChange={(v) => set("csv_delimiter")(v.slice(0, 1))} placeholder=";" />
                </Field>
              ) : (
                <Field label="Chemin des enregistrements" hint="vide = détecté · . = objet unique">
                  <Text value={form.mapping.records_path} onChange={setIn("mapping", "records_path")} placeholder="data.items" mono />
                </Field>
              )}
              <Field label="Champ latitude" hint="vide = détecté">
                <Text value={form.mapping.lat} onChange={setIn("mapping", "lat")} placeholder="position.lat" mono />
              </Field>
              <Field label="Champ longitude" hint="vide = détecté">
                <Text value={form.mapping.lon} onChange={setIn("mapping", "lon")} placeholder="position.lon" mono />
              </Field>
              <Field label="Ou champ géométrie" hint="GeoJSON ou WKT POINT">
                <Text value={form.mapping.geometry} onChange={setIn("mapping", "geometry")} placeholder="geom" mono />
              </Field>
              <Field label="Champ libellé">
                <Text value={form.mapping.label} onChange={setIn("mapping", "label")} placeholder="name" mono />
              </Field>
              <Field label="Champ date">
                <Text value={form.mapping.time} onChange={setIn("mapping", "time")} placeholder="timestamp" mono />
              </Field>
              <Field label="Champ identifiant">
                <Text value={form.mapping.id} onChange={setIn("mapping", "id")} placeholder="id" mono />
              </Field>
              <div className="sm:col-span-2">
                <Field label="Champs affichés dans la fiche" hint="séparés par des virgules · vide = tous">
                  <Text
                    value={(form.mapping.fields || []).join(", ")}
                    onChange={(v) => setIn("mapping", "fields")(v.split(",").map((x) => x.trim()))}
                    placeholder="speed, heading, status"
                    mono
                  />
                </Field>
              </div>
            </div>
            <div className="mt-4 grid grid-cols-1 gap-3 border-t border-tactical-line pt-3 sm:grid-cols-3">
              <Field label="Pagination">
                <Select
                  value={form.pagination.type}
                  onChange={setIn("pagination", "type")}
                  options={[["none", "Aucune"], ["page", "Numéro de page"], ["offset", "Décalage (offset)"], ["next", "URL suivante dans la réponse"]]}
                />
              </Field>
              {form.pagination.type !== "none" && (
                <>
                  <Field label="Pages maximum">
                    <Num value={form.pagination.max_pages} onChange={setIn("pagination", "max_pages")} min={1} max={20} />
                  </Field>
                  {form.pagination.type === "next" ? (
                    <Field label="Chemin de l'URL suivante">
                      <Text value={form.pagination.next_path} onChange={setIn("pagination", "next_path")} placeholder="links.next" mono />
                    </Field>
                  ) : (
                    <>
                      <Field label="Paramètre">
                        <Text value={form.pagination.param} onChange={setIn("pagination", "param")} placeholder={form.pagination.type === "page" ? "page" : "offset"} />
                      </Field>
                      {form.pagination.type === "page" && (
                        <Field label="Première page">
                          <Num value={form.pagination.start} onChange={setIn("pagination", "start")} min={0} />
                        </Field>
                      )}
                      <Field label="Paramètre taille" hint="optionnel">
                        <Text value={form.pagination.size_param} onChange={setIn("pagination", "size_param")} placeholder="limit" />
                      </Field>
                      <Field label="Taille de page">
                        <Num value={form.pagination.size} onChange={setIn("pagination", "size")} min={1} max={10000} />
                      </Field>
                    </>
                  )}
                </>
              )}
            </div>
          </Panel>
        )}

        {form.kind === "wms" && (
          <Panel title="Paramètres WMS" icon={Plug}>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <Field label="Couches (LAYERS)">
                <Text value={form.wms.layers} onChange={setIn("wms", "layers")} placeholder="espace:couche" mono />
              </Field>
              <Field label="Styles (STYLES)">
                <Text value={form.wms.styles} onChange={setIn("wms", "styles")} mono />
              </Field>
              <Field label="Format">
                <Select value={form.wms.format} onChange={setIn("wms", "format")} options={[["image/png", "PNG"], ["image/jpeg", "JPEG"], ["image/webp", "WebP"]]} />
              </Field>
              <Field label="Version">
                <Select value={form.wms.version} onChange={setIn("wms", "version")} options={[["1.3.0", "1.3.0 (CRS)"], ["1.1.1", "1.1.1 (SRS)"]]} />
              </Field>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input type="checkbox" checked={form.wms.transparent} onChange={() => setIn("wms", "transparent")(!form.wms.transparent)} className="accent-cyan-400" />
                Fond transparent
              </label>
              <label className="flex items-center gap-2 text-xs text-slate-300">
                <input type="checkbox" checked={form.wms.time} onChange={() => setIn("wms", "time")(!form.wms.time)} className="accent-cyan-400" />
                Envoyer TIME = date d&apos;imagerie de la carte
              </label>
            </div>
          </Panel>
        )}

        <Panel title="Affichage et rafraîchissement" icon={Plug}>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            {isTiles ? (
              <>
                <Field label="Opacité">
                  <Num value={form.tiles.opacity} onChange={setIn("tiles", "opacity")} min={0.05} max={1} step={0.05} />
                </Field>
                <Field label="Zoom natif max">
                  <Num value={form.tiles.max_native_zoom} onChange={setIn("tiles", "max_native_zoom")} min={0} max={22} />
                </Field>
                <Field label="Fusion" hint="screen : noir transparent">
                  <Select
                    value={form.tiles.blend}
                    onChange={setIn("tiles", "blend")}
                    options={[["normal", "Normale"], ["screen", "Écran (lumières, radiance)"], ["multiply", "Produit (blanc transparent)"], ["lighten", "Éclaircir"]]}
                  />
                </Field>
                <div className="sm:col-span-3">
                  <Field label="Attribution (crédit affiché sur la carte)">
                    <Text value={form.tiles.attribution} onChange={setIn("tiles", "attribution")} />
                  </Field>
                </div>
              </>
            ) : (
              <>
                <Field label="Couleur">
                  <input type="color" className="h-8 w-full border border-tactical-line bg-tactical-bg" value={form.style.color} onChange={(e) => setIn("style", "color")(e.target.value)} />
                </Field>
                <Field label="Taille des points">
                  <Num value={form.style.radius} onChange={setIn("style", "radius")} min={2} max={20} />
                </Field>
                <Field label="Jours d'historique" hint="{start}">
                  <Num value={form.lookback_days} onChange={set("lookback_days")} min={0} max={366} />
                </Field>
              </>
            )}
            <Field label="Rafraîchissement (min)" hint="cache serveur">
              <Num value={form.refresh_minutes} onChange={set("refresh_minutes")} min={1} max={10080} />
            </Field>
            <Field label="Délai max (s)">
              <Num value={form.timeout_s} onChange={set("timeout_s")} min={2} max={120} />
            </Field>
          </div>
        </Panel>
      </div>

      <div className="space-y-4 2xl:sticky 2xl:top-0 2xl:self-start">
        <Panel
          title="Tester la source"
          icon={TestTube2}
          actions={
            usesDate && (
              <input
                type="date"
                value={testDate}
                max={latestGibsDate()}
                onChange={(e) => setTestDate(e.target.value)}
                className="border border-tactical-line bg-tactical-bg px-2 py-0.5 text-[11px] text-slate-300"
                title="Valeur de {date} pour ce test"
              />
            )
          }
        >
          <p className="mb-3 text-xs text-slate-500">
            Exécute la requête une fois, sans rien enregistrer : requête réellement envoyée (secret masqué), réponse, champs détectés et
            aperçu des objets ou de la tuile sur la Guyane. Cliquez un champ détecté pour l&apos;affecter.
          </p>
          <ErrorBanner message={error} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              icon={TestTube2}
              busy={busy === "test"}
              disabled={!form.url || !form.name}
              onClick={() => run("test", async () => setResult(await testCustomSource(definition(), secretPayload, usesDate ? testDate : "")))}
            >
              Tester
            </Button>
            <Button
              icon={Save}
              busy={busy === "save"}
              disabled={!form.url || !form.name}
              onClick={() =>
                run("save", async () => {
                  const response = saved
                    ? await updateCustomSource(saved.id, definition(), secretPayload)
                    : await createCustomSource(definition(), secretPayload);
                  setSecret("");
                  setRemoveSecret(false);
                  onSaved(response.source);
                })
              }
            >
              Enregistrer
            </Button>
            {saved && (
              <Button
                variant="danger"
                icon={Trash2}
                className="ml-auto"
                busy={busy === "delete"}
                onClick={() =>
                  window.confirm(`Supprimer la source « ${saved.name} » et son secret ?`) &&
                  run("delete", async () => {
                    await deleteCustomSource(saved.id);
                    onDeleted(saved.id);
                  })
                }
              >
                Supprimer
              </Button>
            )}
          </div>
          <div className="mt-4">
            <TestReport result={result} onPickField={pickField} pickTarget={pickTarget} onPickTarget={setPickTarget} />
          </div>
        </Panel>
      </div>
    </div>
  );
}

export default function CustomSourcesTab() {
  const [sources, setSources] = useState(null);
  const [editing, setEditing] = useState(null); // { saved, initial }
  const [error, setError] = useState("");

  const load = () =>
    fetchCustomSources()
      .then((d) => setSources(d.sources || []))
      .catch((err) => setError(errorMessage(err)));
  useEffect(() => {
    load();
  }, []);

  const sorted = useMemo(() => [...(sources || [])].sort((a, b) => a.name.localeCompare(b.name)), [sources]);

  if (!sources) return error ? <ErrorBanner message={error} /> : <Spinner />;
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Branchez n&apos;importe quelle API ou service cartographique sans développement : JSON, GeoJSON ou CSV positionnés (objets sur
        la carte), tuiles XYZ / WMTS ou OGC WMS (couches image). Les appels partent du serveur : le secret reste chiffré dans le coffre et
        n&apos;est jamais transmis aux navigateurs ; seules les adresses publiques sont joignables (protection SSRF).
      </p>
      <ErrorBanner message={error} />
      {editing ? (
        <Editor
          key={editing.saved?.id || "new"}
          initial={editing.initial}
          saved={editing.saved}
          onClose={() => setEditing(null)}
          onSaved={(source) => {
            setSources((list) => [...list.filter((s) => s.id !== source.id), source]);
            setEditing({ saved: source, initial: toForm(source) });
          }}
          onDeleted={(id) => {
            setSources((list) => list.filter((s) => s.id !== id));
            setEditing(null);
          }}
        />
      ) : (
        <Panel
          title={`Sources personnalisées (${sources.length})`}
          icon={Plug}
          actions={
            <Button variant="primary" icon={Plus} onClick={() => setEditing({ saved: null, initial: { ...BLANK } })}>
              Nouvelle source
            </Button>
          }
        >
          {sorted.length === 0 ? (
            <p className="text-xs text-slate-500">Aucune source. « Nouvelle source » propose des modèles prêts à tester (API REST, GeoJSON, WMS, tuiles NASA…).</p>
          ) : (
            <ul className="divide-y divide-tactical-line text-xs">
              {sorted.map((source) => (
                <li key={source.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                  <div className="min-w-0 flex-1">
                    <p className="flex items-center gap-2 text-slate-100">
                      <span className="legend-dot" style={{ backgroundColor: source.kind === "features" ? source.style?.color : "#94a3b8" }} />
                      {source.name}
                      <span className="text-[10px] text-slate-600">{source.id}</span>
                    </p>
                    <p className="text-slate-500">
                      {KIND_LABELS[source.kind]} · {source.category}
                      {source.has_secret && ` · secret ${source.secret_hint}`}
                      {source.updated_at && ` · modifiée ${formatRelative(source.updated_at)}`}
                    </p>
                  </div>
                  <Pill color={source.enabled ? "#3dff8a" : "#64748b"}>{source.enabled ? "Active" : "Désactivée"}</Pill>
                  <Button onClick={() => setEditing({ saved: source, initial: toForm(source) })}>Modifier</Button>
                  <Button
                    icon={Copy}
                    title="Dupliquer (sans le secret)"
                    onClick={() => setEditing({ saved: null, initial: { ...toForm(source), id: "", name: `${source.name} (copie)` } })}
                  >
                    Dupliquer
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      )}
    </div>
  );
}
