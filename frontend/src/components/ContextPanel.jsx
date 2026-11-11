import { CloudSun, Database, Eye, Layers, Map as MapIcon, Moon, PenTool, Radar, Satellite, Waves, Wind } from "lucide-react";
import { BASEMAPS, GEO_MODES, NIGHT_SATELLITES, OVERLAYS, goesLayer, nightLightsDate } from "../imagery.js";
import { AIS_CATEGORIES, AIS_COLOR, EFFORT_COLOR, EFFORT_FOREIGN_COLOR, EVENTS, FISHING_LIMITS, FOOTPRINTS, GFW_SAR, ISOBATH, MPA, QUALITY, ZONES } from "../theme.js";

const LAYERS = [
  { id: "zones", label: "Frontières maritimes", colors: Object.values(ZONES).map((z) => z.color) },
  { id: "limits", label: "Limites de pêche SHOM (100 NM, 3 NM)", colors: Object.values(FISHING_LIMITS).map((l) => l.color) },
  { id: "mpa", label: "Aires marines protégées (WDPA)", colors: [MPA.color] },
  { id: "isobaths", label: "Isobathes 20 à 4 000 m (ETOPO)", colors: [ISOBATH.color, ISOBATH.shelf, ISOBATH.deep] },
  { id: "ais", label: "Navires AIS (temps réel)", colors: Object.values(AIS_CATEGORIES).map((c) => c.color) },
  { id: "tracks", label: "Traces AIS (6 h)", colors: [AIS_CATEGORIES.french.color] },
  { id: "gfwSar", label: "Détections SAR GFW", colors: [GFW_SAR.unmatched.color, GFW_SAR.matched.color], period: true },
  { id: "effort", label: "Effort de pêche AIS GFW", colors: [EFFORT_FOREIGN_COLOR, EFFORT_COLOR], period: true },
  { id: "events", label: "Événements GFW", colors: Object.values(EVENTS).map((e) => e.color) },
  { id: "scenes", label: FOOTPRINTS.s1.label, colors: [FOOTPRINTS.s1.color] },
  { id: "optical", label: "Images optiques de la cible", colors: [FOOTPRINTS.optical.color] },
  { id: "nightLights", label: OVERLAYS.gibs_dnb.label, colors: ["#fde68a"] },
  { id: "sentinel2", label: FOOTPRINTS.s2.label, colors: [FOOTPRINTS.s2.color] },
];

const RELIABILITY = {
  high: QUALITY.reliable.color,
  medium: QUALITY.check.color,
  low: QUALITY.doubtful.color,
};

const SOURCE_LABELS = {
  copernicus_stac: "Copernicus STAC (Sentinel-1)",
  copernicus_s3: "Copernicus S3 (pixels SAR)",
  copernicus_ccm_optical: "Pléiades / SPOT (catalogue CCM)",
  nasa_gibs: "NASA GIBS (VIIRS)",
  marine_regions: "Marine Regions",
  open_meteo: "Open-Meteo",
  global_fishing_watch: "Global Fishing Watch",
  aisstream: "aisstream.io (AIS live)",
  aishub: "AISHub (2e flux AIS)",
  opensanctions: "OpenSanctions (sanctions)",
  vesselapi: "VesselAPI (registre navires)",
  copernicus_marine: "Copernicus Marine (océan)",
  wdpa: "WDPA (aires protégées)",
};

const OCEAN_OVERLAYS = [
  ["none", "Aucune"],
  ["sst", "Température de surface (fronts thermiques)"],
  ["chl", "Chlorophylle-a (eaux productives)"],
  ["current", "Courants de surface"],
];

function fmt(value, unit, digits = 1) {
  return value == null ? "—" : `${Number(value).toFixed(digits)} ${unit}`;
}

function age(iso) {
  if (!iso) return null;
  const minutes = (Date.now() - new Date(iso).getTime()) / 60000;
  if (minutes < 90) return `${Math.max(0, Math.round(minutes))} min`;
  if (minutes < 2880) return `${Math.round(minutes / 60)} h`;
  return `${Math.round(minutes / 1440)} j`;
}

function FeedRow({ label, state, detail }) {
  const color = { ok: AIS_CATEGORIES.french.color, warn: QUALITY.check.color, off: "#64748b" }[state];
  return (
    <li className="flex items-start justify-between gap-2">
      <span className="flex items-center gap-2 text-slate-300">
        <span className="legend-dot" style={{ backgroundColor: color }} /> {label}
      </span>
      <span className="text-right text-[10px] text-slate-500">{detail}</span>
    </li>
  );
}

/** Temporal coverage: which feed watches the EEZ right now and how fresh it is. */
export function CoverageSection({ coverage, aisLive, gfwSar, effort }) {
  if (!coverage) return null;
  const sar = coverage.sar || {};
  const ais = coverage.ais || {};
  const lastScene = sar.last_scene?.processed_at;
  const sarAge = lastScene ? (Date.now() - new Date(lastScene).getTime()) / 3600000 : null;
  const aisAge = ais.latest ? (Date.now() - new Date(ais.latest).getTime()) / 60000 : null;
  const foreignFishing = (aisLive?.features || []).filter((f) => f.properties.category === "foreign_fishing").length;
  const listed = (aisLive?.features || []).filter((f) => f.properties.category === "listed").length;
  return (
    <section>
      <p className="mb-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-slate-500">
        <Radar size={12} /> Couverture temporelle
      </p>
      <ul className="space-y-1.5">
        <FeedRow
          label="Radar Sentinel-1"
          state={sarAge == null ? "off" : sarAge < 48 ? "ok" : "warn"}
          detail={lastScene ? `dernier scan il y a ${age(lastScene)} · ${sar.scenes_7d} scène(s)/7 j` : "aucun scan"}
        />
        <FeedRow
          label="AIS continu"
          state={!ais.collector ? "off" : aisAge != null && aisAge < 15 ? "ok" : "warn"}
          detail={
            !ais.collector
              ? "clé aisstream.io requise"
              : !ais.stream?.connected_since
                ? `déconnecté${ais.stream?.last_error ? ` (${ais.stream.last_error})` : ""}`
                : ais.stream.messages === 0
                  ? `connecté, aucun message depuis ${Math.round(ais.stream.silent_minutes || 0)} min : zone hors couverture aisstream ?`
                  : `${ais.vessels_1h} navire(s)/h · dernier signal ${ais.latest ? age(ais.latest) : "—"}`
          }
        />
        {ais.feeds?.aishub && (
          <FeedRow
            label="AISHub (2e flux)"
            state={ais.by_source?.aishub ? "ok" : "warn"}
            detail={ais.by_source?.aishub ? `${ais.by_source.aishub.vessels_24h} navire(s)/24 h · ${age(ais.by_source.aishub.latest)}` : "aucune donnée"}
          />
        )}
        {(() => {
          const listening = aisLive?.collection?.coverage;
          const rejected = Object.values(ais.rejected || {}).reduce(
            (sum, reasons) => sum + Object.values(reasons).reduce((a, b) => a + b, 0),
            0,
          );
          return (
            <>
              {listening != null && (
                <FeedRow
                  label="Collecte AIS (2 h)"
                  state={listening >= 0.9 ? "ok" : listening >= 0.5 ? "warn" : "off"}
                  detail={`${Math.round(listening * 100)} % des minutes écoutées${
                    aisLive.collection.gaps?.length ? ` · ${aisLive.collection.gaps.length} interruption(s)` : ""
                  }`}
                />
              )}
              {rejected > 0 && (
                <FeedRow
                  label="Positions rejetées"
                  state="warn"
                  detail={`${rejected} depuis le démarrage (horloge, saut de position impossible…) : écartées de l'historique`}
                />
              )}
            </>
          );
        })()}
        {ais.feeds?.vesselapi && (
          <FeedRow
            label="VesselAPI (3e flux)"
            state={ais.by_source?.vesselapi ? "ok" : "warn"}
            detail={ais.by_source?.vesselapi ? `${ais.by_source.vesselapi.vessels_24h} navire(s)/24 h · ${age(ais.by_source.vesselapi.latest)}` : "aucune donnée"}
          />
        )}
        {ais.by_source?.vms && (
          <FeedRow
            label="VMS importé"
            state="ok"
            detail={`${ais.by_source.vms.vessels_24h} navire(s) sous licence/24 h · ${age(ais.by_source.vms.latest)}`}
          />
        )}
        <FeedRow
          label="Renseignement navire"
          state={
            coverage.fusion?.vesselapi && coverage.fusion.vesselapi.left === 0
              ? "warn"
              : coverage.fusion?.opensanctions || coverage.fusion?.gfw_insights || coverage.fusion?.vesselapi
                ? "ok"
                : "off"
          }
          detail={
            [
              coverage.fusion?.gfw_insights && "GFW Insights",
              coverage.fusion?.opensanctions && "OpenSanctions",
              coverage.fusion?.vesselapi &&
                `VesselAPI ${coverage.fusion.vesselapi.calls}/${coverage.fusion.vesselapi.budget} appels ce mois${coverage.fusion.vesselapi.left === 0 ? " (budget épuisé)" : ""}`,
            ]
              .filter(Boolean)
              .join(" + ") || "token GFW, clé OpenSanctions ou VesselAPI requis"
          }
        />
        <FeedRow
          label="Notifications"
          state={!coverage.notifications?.configured ? "off" : coverage.notifications?.status === "ok" || !coverage.notifications?.status ? "ok" : "warn"}
          detail={
            !coverage.notifications?.configured
              ? "webhook non configuré"
              : coverage.notifications?.last_failure && coverage.notifications?.status !== "ok"
                ? `échec d'envoi : ${coverage.notifications.last_error || "?"}`
                : coverage.notifications?.last_success
                  ? `dernier envoi il y a ${age(coverage.notifications.last_success)}`
                  : "aucun envoi depuis le démarrage"
          }
        />
        <FeedRow label="Océan (Copernicus Marine)" state={coverage.fusion?.cmems ? "ok" : "off"} detail="courants · SST · chlorophylle" />
        <FeedRow
          label="Règles 24/7"
          state={coverage.rules?.last_run ? "ok" : "off"}
          detail={coverage.rules?.last_run ? `exécutées il y a ${age(coverage.rules.last_run)}` : "en attente"}
        />
        <FeedRow
          label="Global Fishing Watch"
          state={coverage.gfw?.configured ? "ok" : "off"}
          detail={
            coverage.gfw?.configured
              ? [gfwSar?.available && `${gfwSar.unmatched} SAR sans AIS`, effort?.available && "effort chargé"].filter(Boolean).join(" · ") || "prêt"
              : "token requis"
          }
        />
      </ul>
      {(foreignFishing > 0 || listed > 0) && (
        <p className="mt-2 text-[11px]" style={{ color: AIS_CATEGORIES.foreign_fishing.color }}>
          En ce moment : {foreignFishing} pêcheur(s) étranger(s){listed ? `, ${listed} navire(s) listé(s) INN` : ""} à l&apos;AIS.
        </p>
      )}
    </section>
  );
}

function Swatches({ colors }) {
  return (
    <span className="flex gap-0.5">
      {colors.map((color) => (
        <span key={color} className="legend-dot" style={{ backgroundColor: color }} />
      ))}
    </span>
  );
}

const sectionTitle = "mb-2 flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-slate-500";
const selectClass = "w-full border border-tactical-line bg-tactical-bg px-2 py-1.5 text-slate-300";

export const DEFAULT_VISUAL = {
  oceanLayer: "none",
  oceanOpacity: 0.6,
  tileBrightness: 1,
  tileOpacity: 1,
  nightLightsOpacity: 0.9,
  geoMode: "auto",
  geoOpacity: 0.75,
  nightSatellite: "noaa20",
  symbolScale: "normal",
  showLegend: true,
  followSelection: true,
};

export function WeatherSection({ environment }) {
  const reliability = environment?.sar_reliability;
  return (
    <section>
      <p className={sectionTitle}>
        <CloudSun size={12} /> État de la mer
      </p>
      {environment?.available ? (
        <div className="grid grid-cols-2 gap-2 text-slate-300">
          <span className="flex items-center gap-1">
            <Wind size={12} className="text-tactical-cyan" /> {fmt(environment.wind_speed_ms, "m/s")}
          </span>
          <span className="flex items-center gap-1">
            <Waves size={12} className="text-tactical-cyan" /> {fmt(environment.wave_height_m, "m")}
          </span>
          <span>SST {fmt(environment.sea_surface_temperature_c, "°C")}</span>
          <span>Courant {fmt(environment.current_velocity_kmh, "km/h")}</span>
          <span className="col-span-2" style={{ color: RELIABILITY[reliability?.level] || "#94a3b8" }}>
            SAR : {reliability?.reason}
          </span>
        </div>
      ) : (
        <p className="text-slate-500">Météo marine indisponible.</p>
      )}
    </section>
  );
}

function Slider({ label, value, min, max, step, onChange, format }) {
  return (
    <label className="block space-y-1">
      <span className="flex justify-between text-slate-400">
        <span>{label}</span>
        <span className="text-slate-500">{format(value)}</span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-cyan-400"
      />
    </label>
  );
}

function Toggle({ label, checked, onChange, hint }) {
  return (
    <label className="flex cursor-pointer items-start gap-2 text-slate-300">
      <input type="checkbox" checked={checked} onChange={onChange} className="mt-0.5 accent-cyan-400" />
      <span>
        {label}
        {hint && <span className="block text-[10px] text-slate-500">{hint}</span>}
      </span>
    </label>
  );
}

/** Everything visual: basemap, satellite imagery date, display mode, brightness, symbols, legend. */
export function MapStyleSection({
  basemap,
  onBasemapChange,
  imageryDate,
  onImageryDateChange,
  maxImageryDate,
  displayMode,
  onDisplayModeChange,
  layers,
  onToggleLayer,
  visual,
  onVisualChange,
  geoSlot,
}) {
  const needsDate = BASEMAPS[basemap]?.dated || layers.nightLights;
  const geo = geoSlot ? goesLayer(visual.geoMode || "auto", geoSlot) : null;
  const set = (key) => (value) => onVisualChange({ ...visual, [key]: value });
  const pct = (v) => `${Math.round(v * 100)} %`;
  return (
    <div className="space-y-5">
      <section className="space-y-2">
        <p className={sectionTitle}>
          <MapIcon size={12} /> Fond de carte
        </p>
        <div className="grid grid-cols-1 gap-1.5">
          {Object.entries(BASEMAPS).map(([id, b]) => (
            <label
              key={id}
              className={`flex cursor-pointer items-center gap-2 border px-2 py-1.5 ${
                basemap === id ? "border-tactical-cyan/60 bg-tactical-cyan/10 text-tactical-cyan" : "border-tactical-line text-slate-300 hover:bg-white/5"
              }`}
            >
              <input type="radio" name="basemap" className="accent-cyan-400" checked={basemap === id} onChange={() => onBasemapChange(id)} />
              {b.label}
            </label>
          ))}
        </div>
        {needsDate && (
          <label className="flex items-center justify-between gap-2 text-slate-400">
            Date de l&apos;imagerie NASA
            <input
              type="date"
              value={imageryDate}
              max={maxImageryDate}
              onChange={(e) => e.target.value && onImageryDateChange(e.target.value)}
              className="border border-tactical-line bg-tactical-bg px-2 py-1 text-slate-300"
            />
          </label>
        )}
        <Slider label="Luminosité du fond" value={visual.tileBrightness} min={0.4} max={1.6} step={0.05} onChange={set("tileBrightness")} format={pct} />
        <Slider label="Opacité du fond" value={visual.tileOpacity} min={0.2} max={1} step={0.05} onChange={set("tileOpacity")} format={pct} />
      </section>

      <section className="space-y-2">
        <p className={sectionTitle}>
          <Satellite size={12} /> Satellite géostationnaire
        </p>
        <Toggle
          label="GOES-Est — image toutes les 10 min"
          checked={Boolean(layers.geostationary)}
          onChange={() => onToggleLayer("geostationary")}
          hint="Nuages et météo sur toute la ZEE (~1–2 km), synchronisés avec l'instant de la frise"
        />
        {layers.geostationary && geo && (
          <>
            <select
              value={visual.geoMode || "auto"}
              onChange={(e) => set("geoMode")(e.target.value)}
              className="w-full border border-tactical-line bg-tactical-bg px-2 py-1 text-slate-300"
              aria-label="Canal GOES-Est"
            >
              {Object.entries(GEO_MODES).map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
            <Slider label="Opacité" value={visual.geoOpacity ?? 0.75} min={0.2} max={1} step={0.05} onChange={set("geoOpacity")} format={pct} />
            <p className="text-[10px] text-slate-500">
              {geo.label} · image du {geo.time.replace("T", " ").slice(0, 16)} Z (la plus récente publiée à l&apos;instant de la
              frise ; délai NASA ≈ 30 min en direct). Contexte nuageux d&apos;un passage radar ; trop grossier pour identifier un navire.
            </p>
          </>
        )}
      </section>

      <section className="space-y-2">
        <p className={sectionTitle}>
          <Moon size={12} /> Imagerie satellite nocturne
        </p>
        <Toggle
          label={OVERLAYS.gibs_dnb.label}
          checked={Boolean(layers.nightLights)}
          onChange={() => onToggleLayer("nightLights")}
          hint="NASA VIIRS Day/Night Band : bateaux éclairés la nuit précédant la date ci-dessus"
        />
        {layers.nightLights && (
          <>
            <div className="flex border border-tactical-line" role="radiogroup" aria-label="Satellite VIIRS">
              {Object.entries(NIGHT_SATELLITES).map(([id, sat]) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={(visual.nightSatellite || "noaa20") === id}
                  onClick={() => set("nightSatellite")(id)}
                  className={`flex-1 px-2 py-1.5 ${(visual.nightSatellite || "noaa20") === id ? "bg-tactical-cyan/15 text-tactical-cyan" : "text-slate-400 hover:text-white"}`}
                >
                  {sat.label}
                </button>
              ))}
            </div>
            <Slider label="Intensité des lumières" value={visual.nightLightsOpacity} min={0.2} max={1} step={0.05} onChange={set("nightLightsOpacity")} format={pct} />
            {(() => {
              const satellite = NIGHT_SATELLITES[visual.nightSatellite] || NIGHT_SATELLITES.noaa20;
              const shown = nightLightsDate(visual.nightSatellite, imageryDate);
              return (
                <>
                  {shown !== imageryDate && (
                    <p className="text-[10px] text-amber-300">
                      {satellite.label} : mosaïque du {shown} affichée (publiée avec {satellite.lagDays} jours de retard par la NASA ;
                      celle du {imageryDate} est encore incomplète).
                    </p>
                  )}
                  {satellite.since && shown < satellite.since && (
                    <p className="text-[10px] text-amber-300">
                      Aucune mosaïque {satellite.label} sur NASA GIBS avant le {satellite.since} : passez sur l&apos;autre satellite.
                    </p>
                  )}
                </>
              );
            })()}
            <p className="text-[10px] text-slate-500">
              Points lumineux isolés en mer = navires (lamparos des flottilles). Carte vide ou voile gris : mosaïque non publiée
              pour cette date (reculez d&apos;un jour ou changez de satellite).
            </p>
          </>
        )}
      </section>

      <section className="space-y-2">
        <p className={sectionTitle}>
          <Waves size={12} /> Océan (Copernicus Marine)
        </p>
        <select
          value={visual.oceanLayer}
          onChange={(e) => set("oceanLayer")(e.target.value)}
          className={selectClass}
          aria-label="Surcouche océanographique"
        >
          {OCEAN_OVERLAYS.map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
        {visual.oceanLayer !== "none" && (
          <>
            <Slider label="Opacité de la surcouche" value={visual.oceanOpacity} min={0.2} max={1} step={0.05} onChange={set("oceanOpacity")} format={pct} />
            <p className="text-[10px] text-slate-500">
              {visual.oceanLayer === "sst" && "Les fortes variations de couleur sur une courte distance sont des fronts thermiques : les poissons et les flottilles s'y concentrent."}
              {visual.oceanLayer === "chl" && "Vert intense = eaux riches en plancton (panache de l'Amazone) : zones de pêche probables."}
              {visual.oceanLayer === "current" && "Courant des Guyanes (≈ 2 nœuds vers le nord-ouest) : explique la dérive d'une cible entre deux passages."}
            </p>
          </>
        )}
      </section>

      <section className="space-y-2">
        <p className={sectionTitle}>
          <Eye size={12} /> Affichage
        </p>
        <select value={displayMode} onChange={(e) => onDisplayModeChange(e.target.value)} className={selectClass} aria-label="Mode d'affichage">
          <option value="standard">Affichage standard</option>
          <option value="nvg">Vision nocturne (NVG)</option>
          <option value="contrast">Contraste élevé</option>
        </select>
        <div className="flex border border-tactical-line" role="radiogroup" aria-label="Taille des symboles">
          {[
            ["small", "Symboles petits"],
            ["normal", "Normaux"],
            ["large", "Grands"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              role="radio"
              aria-checked={visual.symbolScale === value}
              onClick={() => set("symbolScale")(value)}
              className={`flex-1 px-2 py-1.5 ${visual.symbolScale === value ? "bg-tactical-cyan/15 text-tactical-cyan" : "text-slate-400 hover:text-white"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <Toggle label="Afficher la légende" checked={visual.showLegend} onChange={() => set("showLegend")(!visual.showLegend)} />
        <Toggle
          label="Recentrer sur la cible sélectionnée"
          checked={visual.followSelection}
          onChange={() => set("followSelection")(!visual.followSelection)}
          hint="La carte se déplace pour montrer la cible choisie dans la liste"
        />
        <button type="button" onClick={() => onVisualChange(DEFAULT_VISUAL)} className="text-[11px] text-slate-500 underline hover:text-white">
          Rétablir l&apos;affichage par défaut
        </button>
      </section>
    </div>
  );
}

function CustomLayerStatus({ layer, state }) {
  if (layer.kind !== "features") return <p className="ml-6 text-[10px] text-slate-600">couche image · relayée par le serveur</p>;
  if (!state || state.loading) return <p className="ml-6 text-[10px] text-slate-500">chargement…</p>;
  const count = state.data?.features?.length ?? 0;
  return (
    <p className="ml-6 text-[10px]" style={{ color: state.error ? QUALITY.check.color : "#64748b" }}>
      {state.data ? `${count} objet(s)` : ""}
      {state.data?.stale && " · dernière copie"}
      {state.error && ` · ${state.error}`}
      {state.data?.warnings?.[0] && !state.error && ` · ${state.data.warnings[0]}`}
    </p>
  );
}

export function ZonesSection({ zones, layers, onToggleLayer, canEdit, drawing, onStartDraw, onDelete }) {
  const items = zones?.features || [];
  return (
    <section className="mt-4">
      <p className={sectionTitle}>
        <PenTool size={12} /> Zones de surveillance
      </p>
      <label className="mb-2 flex cursor-pointer items-center gap-2 text-slate-300">
        <input type="checkbox" checked={Boolean(layers.drawnZones)} onChange={() => onToggleLayer("drawnZones")} className="accent-cyan-400" />
        Afficher les zones dessinées ({items.length})
      </label>
      <ul className="space-y-1.5">
        {items.map((f) => (
          <li key={f.properties.id} className="flex items-start justify-between gap-2 border border-tactical-line px-2 py-1.5">
            <span className="min-w-0">
              <span className="flex items-center gap-1.5 text-slate-200">
                <span className="legend-dot" style={{ backgroundColor: f.properties.color }} /> {f.properties.name}
              </span>
              <span className="block text-[10px] text-slate-500">
                {f.properties.used_by?.length ? `règle(s) : ${f.properties.used_by.join(", ")}` : "utilisée par aucune règle active"}
              </span>
            </span>
            {canEdit && (
              <button type="button" onClick={() => onDelete(f.properties)} className="shrink-0 text-[10px] text-slate-500 hover:text-tactical-red"
                title={f.properties.used_by?.length ? "Utilisée par une règle active : désactivez la règle d'abord" : "Supprimer la zone"}>
                supprimer
              </button>
            )}
          </li>
        ))}
      </ul>
      {canEdit && (
        <button type="button" disabled={Boolean(drawing)} onClick={onStartDraw}
          className="mt-2 w-full border border-tactical-cyan/40 px-2 py-1.5 text-tactical-cyan hover:bg-tactical-cyan/10 disabled:opacity-40">
          Dessiner une zone sur la carte
        </button>
      )}
      <p className="mt-1 text-[10px] text-slate-500">
        Une zone sert de critère aux règles sur mesure (onglet Règles › critère « Zone de surveillance »).
      </p>
    </section>
  );
}

export const GFW_PERIODS = [7, 30, 90];

export function LayersSection({ layers, onToggleLayer, events, customLayers = [], customData = {}, gfwDays = 30, onGfwDaysChange }) {
  const counts = events?.counts || {};
  return (
    <section>
      <p className={sectionTitle}>
        <Layers size={12} /> Couches de données
      </p>
      <ul className="space-y-1.5">
        {LAYERS.filter((layer) => layer.id !== "nightLights").map((layer) => (
          <li key={layer.id}>
            <label className="flex cursor-pointer items-center gap-2 text-slate-300">
              <input type="checkbox" checked={Boolean(layers[layer.id])} onChange={() => onToggleLayer(layer.id)} className="accent-cyan-400" />
              <Swatches colors={layer.colors} />
              <span>
                {layer.label}
                {layer.period && ` (${gfwDays} j)`}
              </span>
            </label>
            {layer.id === "effort" && layers.effort && (
              <p className="ml-6 text-[10px] text-slate-500">orange = pavillon étranger, bleu = français</p>
            )}
            {layer.id === "events" && events?.available && (
              <p className="ml-6 text-[10px] text-slate-500">
                {Object.entries(EVENTS)
                  .map(([kind, e]) => `${e.label} ${counts[kind] || 0}`)
                  .join(" · ")}
              </p>
            )}
            {layer.id === "events" && events && !events.available && <p className="ml-6 text-[10px] text-slate-600">{events.reason}</p>}
          </li>
        ))}
      </ul>
      {onGfwDaysChange && (
        <label className="mt-2 flex items-center justify-between gap-2 text-[11px] text-slate-400">
          Période des couches GFW (SAR, effort)
          <select
            value={gfwDays}
            onChange={(e) => onGfwDaysChange(Number(e.target.value))}
            className="border border-tactical-line bg-tactical-bg px-1 py-0.5 text-slate-200"
          >
            {GFW_PERIODS.map((d) => (
              <option key={d} value={d}>
                {d} jours
              </option>
            ))}
          </select>
        </label>
      )}
      {customLayers.length > 0 && (
        <>
          <p className={`${sectionTitle} mt-4`}>
            <Database size={12} /> Sources personnalisées
          </p>
          <ul className="space-y-1.5">
            {customLayers.map((layer) => {
              const key = `custom:${layer.id}`;
              return (
                <li key={layer.id} title={layer.description || undefined}>
                  <label className="flex cursor-pointer items-center gap-2 text-slate-300">
                    <input type="checkbox" checked={Boolean(layers[key])} onChange={() => onToggleLayer(key)} className="accent-cyan-400" />
                    <Swatches colors={[layer.kind === "features" ? layer.style?.color || "#22d3ee" : "#94a3b8"]} />
                    <span>{layer.name}</span>
                  </label>
                  {layers[key] && <CustomLayerStatus layer={layer} state={customData[layer.id]} />}
                </li>
              );
            })}
          </ul>
        </>
      )}
    </section>
  );
}

export function SourcesSection({ sources, onOpenSettings }) {
  return (
    <section>
      <p className={sectionTitle}>
        <Database size={12} /> Sources
      </p>
      <ul className="space-y-1">
        {Object.entries(sources || {}).map(([id, info]) => (
          <li key={id} className="flex items-center justify-between gap-2">
            <span className="text-slate-300">{SOURCE_LABELS[id] || id}</span>
            {info.configured ? (
              <span style={{ color: AIS_COLOR }}>{info.key_required ? "clé OK" : "libre"}</span>
            ) : onOpenSettings ? (
              <button type="button" onClick={onOpenSettings} className="text-tactical-amber underline">
                configurer
              </button>
            ) : (
              <span className="text-tactical-amber">non configurée</span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
