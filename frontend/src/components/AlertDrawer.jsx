import { useEffect, useState } from "react";
import {
  ChevronLeft,
  ChevronRight,
  CircleCheck,
  CircleSlash,
  ExternalLink,
  ImageOff,
  Info,
  Loader2,
  Radar,
  Satellite,
  X,
} from "lucide-react";
import C2PABadge from "./C2PABadge.jsx";
import DocLink from "./DocLink.jsx";
import RiskBadge from "./RiskBadge.jsx";
import { Button, Pill } from "./ui.jsx";
import { hasRole } from "../lib/format.js";
import { FOOTPRINTS, QUALITY, SEVERITY, STATUS } from "../theme.js";
import { assetUrl, errorMessage, fetchImagery, previewUrl, transitionIncident } from "../services/api.js";
import { formatInstant, useReferenceTime } from "../lib/timeContext.js";

const ZONE_LABELS = {
  territorial: "Mer territoriale (12 NM)",
  contiguous: "Zone contiguë (24 NM)",
  eez: "ZEE (200 NM)",
};

const AIS_LABELS = {
  "aisstream.io": "aisstream.io (temps réel, passage récent)",
  unavailable: "Aucune position AIS au moment du passage — absence d'AIS non démontrée",
  mock: "Simulé — à confirmer",
};

function formatCoords(alert) {
  const [lon, lat] = alert?.geometry?.coordinates || [];
  if (lat == null || lon == null) return "—";
  const ns = lat >= 0 ? "N" : "S";
  const ew = lon >= 0 ? "E" : "W";
  return `${Math.abs(Number(lat)).toFixed(4)}°${ns}, ${Math.abs(Number(lon)).toFixed(4)}°${ew}`;
}

function formatTimestamp(value) {
  const match = /^(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})$/.exec(value || "");
  if (!match) return value || "—";
  const [, y, mo, d, h, mi, s] = match;
  return `${y}-${mo}-${d} ${h}:${mi}:${s} UTC`;
}

function formatIso(value) {
  return value ? `${String(value).slice(0, 16).replace("T", " ")} UTC` : "—";
}

function formatDelta(hours) {
  if (hours == null) return "";
  const sign = hours >= 0 ? "+" : "−";
  const abs = Math.abs(hours);
  return abs < 48 ? `${sign}${abs.toFixed(0)} h` : `${sign}${(abs / 24).toFixed(0)} j`;
}

function RadarImage({ url }) {
  const [state, setState] = useState("loading");
  if (!url) {
    return (
      <div className="flex h-56 items-center justify-center border border-dashed border-tactical-line text-slate-500">
        <Radar size={18} className="mr-2" /> Vignette radar indisponible
      </div>
    );
  }
  return (
    <a href={assetUrl(url)} target="_blank" rel="noreferrer" className="relative block">
      {state === "loading" && (
        <div className="absolute inset-0 flex items-center justify-center bg-tactical-bg/80 text-slate-500">
          <Loader2 size={18} className="animate-spin" />
        </div>
      )}
      {state === "error" ? (
        <div className="flex h-56 items-center justify-center border border-dashed border-tactical-red/40 text-tactical-red">
          <ImageOff size={18} className="mr-2" /> Image introuvable
        </div>
      ) : (
        <img
          src={previewUrl(url)}
          alt="Vignette radar Sentinel-1 de la cible"
          onLoad={() => setState("ready")}
          onError={() => setState("error")}
          className="aspect-square w-full border border-tactical-line object-cover"
        />
      )}
    </a>
  );
}

function OpticalImagery({ alert, onFootprints }) {
  const [state, setState] = useState({ status: "loading", data: null });
  const [lon, lat] = alert.geometry?.coordinates || [];
  const around = alert.properties?.scene_datetime;

  useEffect(() => {
    if (lat == null || lon == null) return undefined;
    const controller = new AbortController();
    setState({ status: "loading", data: null });
    fetchImagery(lat, lon, around, controller.signal)
      .then((data) => {
        setState({ status: "ready", data });
        onFootprints(data);
      })
      .catch((err) => {
        if (controller.signal.aborted) return; // superseded by another vessel
        setState({ status: "error", data: null, message: err.message });
      });
    return () => {
      controller.abort();
      onFootprints(null);
    };
  }, [lat, lon, around, onFootprints]);

  const features = state.data?.features || [];
  return (
    <section className="space-y-2 border border-tactical-line p-3 text-[11px]">
      <p className="flex items-center gap-2 uppercase tracking-[0.2em] text-slate-500">
        <Satellite size={12} /> Imagerie autour de la cible (±3 j)
        <span className="legend-dot" style={{ backgroundColor: FOOTPRINTS.optical.color }} />
      </p>
      {state.status === "loading" && (
        <p className="flex items-center gap-2 text-slate-500">
          <Loader2 size={12} className="animate-spin" /> Recherche Pléiades, SPOT, radars THR, Sentinel-1/2 (Copernicus + CNES)…
        </p>
      )}
      {state.status === "error" && <p className="text-tactical-red">Catalogue injoignable.</p>}
      {state.status === "ready" && features.length === 0 && (
        <p className="text-slate-500">Aucune acquisition optique sur cette cible dans la fenêtre.</p>
      )}
      {features.some((f) => f.properties.thumbnail) && (
        <div className="grid grid-cols-3 gap-1.5">
          {features
            .filter((f) => f.properties.thumbnail)
            .slice(0, 6)
            .map((f) => (
              <a key={`q-${f.properties.image_id}`} href={f.properties.thumbnail} target="_blank" rel="noreferrer" className="group relative block" title={`${f.properties.label} · ${formatIso(f.properties.acquired)}`}>
                <img src={f.properties.thumbnail} alt={f.properties.label} loading="lazy" referrerPolicy="no-referrer" className="aspect-square w-full border border-tactical-line object-cover" />
                <span className="absolute inset-x-0 bottom-0 bg-black/70 px-1 py-0.5 text-[9px] text-slate-200">
                  {f.properties.sensor === "sar" ? "SAR" : "OPT"} {formatDelta(f.properties.delta_hours_vs_sar)}
                </span>
              </a>
            ))}
        </div>
      )}
      {features.length > 0 && (
        <ul className="space-y-1">
          {features.slice(0, 8).map((f) => {
            const p = f.properties;
            return (
              <li key={p.image_id} className="flex items-center justify-between gap-2">
                <span className="text-slate-300">
                  <strong className={["ccm-optical", "ccm-sar"].includes(p.collection) ? "text-lime-300" : ""}>{p.label}</strong>{" "}
                  {p.gsd_m != null && `${p.gsd_m} m`} · {formatIso(p.acquired).slice(0, 16)}{" "}
                  <span className="text-slate-500">
                    {formatDelta(p.delta_hours_vs_sar)}
                    {p.cloud_cover != null && ` · ☁ ${Math.round(p.cloud_cover)} %`}
                  </span>
                </span>
                {p.product_url && (
                  <a href={p.product_url} target="_blank" rel="noreferrer" className="shrink-0 text-tactical-cyan underline" title={p.access}>
                    produit ↗
                  </a>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {state.data?.vhr_count > 0 && (
        <p className="text-slate-500">
          Images très haute résolution (THR, 0,3 à 1,5 m, programme Copernicus CCM) : téléchargement réservé aux comptes Copernicus éligibles
          (services de l&apos;État). Pour commander une prise de vue Pléiades dédiée : DINAMIS (CNES/IGN).
        </p>
      )}
    </section>
  );
}

function IncidentPanel({ alert, user, navigate, onIncidentUpdated }) {
  const props = alert.properties || {};
  const { at } = useReferenceTime();
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const status = props.incident_status;
  // In replay the status shown is the one of that instant: acting on it would act on today's incident.
  const canAct = hasRole(user, "analyst") && !at;

  const run = async (label, action) => {
    setBusy(label);
    setError("");
    try {
      await action();
      onIncidentUpdated();
    } catch (err) {
      setError(errorMessage(err, "Action impossible"));
    } finally {
      setBusy("");
    }
  };

  if (!props.incident_id) {
    return (
      <section className="border border-tactical-line p-3 text-[11px] text-slate-500">
        Détection antérieure au module d&apos;incidents : aucun ticket associé.
      </section>
    );
  }

  return (
    <section className="space-y-2 border border-tactical-line p-3 text-[11px]">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="uppercase tracking-[0.2em] text-slate-500">Incident</span>
        <span className="flex items-center gap-1">
          <Pill color={SEVERITY[props.incident_severity]?.color}>{props.incident_severity}</Pill>
          <Pill color={STATUS[status]?.color}>{STATUS[status]?.label}</Pill>
        </span>
      </div>
      <button
        type="button"
        onClick={() => navigate("incidents", props.incident_id)}
        className="flex w-full items-center justify-between border border-tactical-cyan/40 bg-tactical-cyan/10 px-3 py-2 text-tactical-cyan hover:bg-tactical-cyan/20"
      >
        <span>{props.incident_ref}</span>
        <span className="flex items-center gap-1">
          Ouvrir le dossier <ExternalLink size={12} />
        </span>
      </button>
      {canAct && status === "new" && (
        <div className="flex flex-wrap gap-2">
          <Button
            variant="primary"
            icon={CircleCheck}
            busy={busy === "triage"}
            onClick={() => run("triage", () => transitionIncident(props.incident_id, "triage"))}
          >
            Prendre en charge
          </Button>
          <Button
            icon={CircleSlash}
            busy={busy === "fp"}
            onClick={() =>
              run("fp", () =>
                transitionIncident(props.incident_id, "closed", {
                  resolution: "false_positive",
                  note: "Classé depuis la carte : fausse alarme",
                }),
              )
            }
          >
            Fausse alarme
          </Button>
        </div>
      )}
      {at && (
        <p className="text-tactical-amber">
          Statut au {formatInstant(at)} (rejeu). Revenez au direct pour agir sur l&apos;incident.
        </p>
      )}
      {error && <p className="text-tactical-red">{error}</p>}
    </section>
  );
}

const pct = (v) => `${Math.round(v * 100)} %`;

/** xView3 model opinion: shown for information, never used to decide or score. */
function Xview3Opinion({ opinion, radarLength }) {
  const object = opinion.objectness >= 0.3;
  const rows = [
    ["Objet maritime présent", opinion.objectness, "Probabilité qu'il y ait un objet (navire ou structure) à cet endroit"],
    ...(object
      ? [
          ["Si objet : navire", opinion.vessel, "Navire plutôt que structure fixe (plateforme, bouée, rocher)"],
          ["Si navire : en pêche", opinion.fishing, "Navire de pêche en activité plutôt qu'un autre navire"],
        ]
      : []),
  ];
  return (
    <div className="col-span-2 border border-tactical-line bg-tactical-bg/40 p-2">
      <dt className="mb-1 flex items-center gap-1 text-slate-500" title="Modèle open source xView3 (1re place du concours xView3, licence MIT), entraîné sur des images Sentinel-1 VH/VV. Information seule : n'intervient ni dans la fiabilité, ni dans le risque, ni dans la création des alertes.">
        <Info size={12} /> Avis xView3 (information, sans effet sur l'alerte)
      </dt>
      <dd className="space-y-1">
        {rows.map(([label, value, hint]) => (
          <div key={label} className="flex items-center gap-2" title={hint}>
            <span className="w-28 shrink-0 text-slate-400 sm:w-40">{label}</span>
            <span className="h-1.5 flex-1 bg-tactical-line">
              <span className="block h-1.5 bg-slate-300" style={{ width: `${Math.round(Math.min(1, Math.max(0, value)) * 100)}%` }} />
            </span>
            <span className="w-10 text-right text-slate-100">{pct(value)}</span>
          </div>
        ))}
        {object && opinion.length_m > 0 && (
          <p className="text-slate-400">
            Longueur estimée par le modèle : ~{Math.round(opinion.length_m)} m
            {radarLength != null && <span className="text-slate-500"> (mesure de l'écho : ~{Math.round(radarLength)} m)</span>}
          </p>
        )}
        {!object && <p className="text-slate-500">Le modèle ne voit pas d'objet net : navire/pêche non évalués.</p>}
      </dd>
    </div>
  );
}

export default function AlertDrawer({
  alert,
  position,
  total,
  onPrev,
  onNext,
  onClose,
  user,
  navigate,
  onIncidentUpdated,
  onOpticalFootprints,
}) {
  const open = Boolean(alert);
  const props = alert?.properties || {};
  const quality = QUALITY[props.quality_level];

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "Escape") onClose();
      if (e.key === "ArrowRight") onNext();
      if (e.key === "ArrowLeft") onPrev();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose, onNext, onPrev]);

  return (
    <aside
      aria-hidden={!open}
      className={`absolute inset-y-0 right-0 z-[1000] flex w-full max-w-md flex-col border-l border-tactical-line bg-tactical-panel/95 shadow-2xl backdrop-blur transition-[transform,visibility] duration-300 ease-out ${
        open ? "visible translate-x-0" : "pointer-events-none invisible translate-x-full"
      }`}
    >
      {alert && (
        // key: remount per vessel → scroll, image and sub-requests reset on every switch.
        <div key={alert.key} className="flex min-h-0 flex-1 flex-col">
          <div className="flex items-center justify-between gap-2 border-b border-tactical-line px-4 py-3">
            <button type="button" onClick={onPrev} className="p-1 text-slate-400 hover:text-white" aria-label="Cible précédente">
              <ChevronLeft size={18} />
            </button>
            <div className="min-w-0 flex-1 text-center">
              <p className="text-[10px] uppercase tracking-[0.25em] text-slate-500">
                Navire sans AIS (dark vessel) · {position + 1}/{total}
              </p>
              <h2 className="truncate text-base text-white">
                {props.vessel_id || "CIBLE"} <span className="text-slate-500">/ {props.alert_id || "—"}</span>
              </h2>
            </div>
            <button type="button" onClick={onNext} className="p-1 text-slate-400 hover:text-white" aria-label="Cible suivante">
              <ChevronRight size={18} />
            </button>
            <button type="button" onClick={onClose} className="p-1 text-slate-400 hover:text-white" aria-label="Fermer">
              <X size={18} />
            </button>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto p-4 text-xs">
            <RadarImage url={props.image_url} />
            {props.sheet_url && props.sheet_url !== props.image_url && (
              <a href={assetUrl(props.sheet_url)} target="_blank" rel="noreferrer" className="block text-[11px] text-tactical-cyan underline">
                Planche scellée de l&apos;alerte (toutes les cibles) ↗
              </a>
            )}

            <C2PABadge status={props.c2pa_status} />

            <section className="space-y-2 border border-tactical-line p-3">
              <div className="flex flex-wrap items-center gap-2">
                <RiskBadge score={props.risk_score} level={props.risk_level} />
                <DocLink page="moteur/risque/" label="calcul du score de risque" />
                {quality && (
                  <span
                    className="border px-2 py-0.5 text-[10px] uppercase tracking-wider"
                    style={{ color: quality.color, borderColor: `${quality.color}80` }}
                  >
                    Détection {quality.label} · {Math.round((props.quality_score ?? 0) * 100)} %
                  </span>
                )}
                {quality && <DocLink page="moteur/fiabilite/" label="indice de fiabilité de la détection" />}
              </div>
              {(props.risk_reasons?.length > 0 || props.quality_flags?.length > 0) && (
                <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">
                  Pourquoi cette cible ?{" "}
                  <span className="normal-case tracking-normal">
                    — puces : ce qui a fait monter (ou baisser) le risque ; sous le trait : ce qui renforce (ou affaiblit) la fiabilité de la détection
                  </span>
                </p>
              )}
              {props.risk_reasons?.length > 0 && (
                <ul className="list-inside list-disc space-y-1 text-[11px] text-slate-400">
                  {props.risk_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
              {props.quality_flags?.length > 0 && (
                <ul className="space-y-1 border-t border-tactical-line pt-2 text-[11px] text-slate-500">
                  {props.quality_flags.map((flag) => (
                    <li key={flag}>• {flag}</li>
                  ))}
                </ul>
              )}
            </section>

            <IncidentPanel alert={alert} user={user} navigate={navigate} onIncidentUpdated={onIncidentUpdated} />
            {props.alert_id && props.vessel_id && (
              <button
                type="button"
                onClick={() => navigate("graph", encodeURIComponent(`detection:${props.alert_id}:${props.vessel_id}`))}
                className="w-full border border-tactical-line px-3 py-2 text-left text-[11px] text-tactical-cyan hover:bg-tactical-cyan/10"
              >
                Explorer les liens de cet écho : navires AIS proches au passage, incident, liste de surveillance →
              </button>
            )}

            <dl className="grid grid-cols-2 gap-3">
              <div className="col-span-2">
                <dt className="text-slate-500">Coordonnées</dt>
                <dd className="text-tactical-cyan">{formatCoords(alert)}</dd>
              </div>
              <div>
                <dt className="text-slate-500" title="Contraste de l'écho par rapport à la mer environnante (CFAR), ramené de 50 à 99 % : ce n'est pas une probabilité que ce soit un navire — voir la fiabilité de détection">
                  Contraste radar (CFAR)
                </dt>
                <dd>{props.confidence != null ? `${Math.round(props.confidence * 100)} %` : "—"}</dd>
              </div>
              {props.ai_confidence != null && (
                <div>
                  <dt className="text-slate-500" title="Second avis d'un modèle d'IA open source (Administration › Paramètres › ai_detector_mode)">Avis IA</dt>
                  <dd>
                    {Math.round(props.ai_confidence * 100)} %{props.ai_only ? " · détecté par l'IA seule" : ""}
                  </dd>
                </div>
              )}
              {props.xview3 && <Xview3Opinion opinion={props.xview3} radarLength={props.length_m} />}
              <div>
                <dt className="text-slate-500" title="Zone maritime sous juridiction française où se trouve l'écho (Marine Regions) : elle fixe les pouvoirs de police applicables et pèse dans le risque">Zone</dt>
                <dd>{ZONE_LABELS[props.zone] || "—"}</dd>
              </div>
              {props.type === "dark_vessel" && "shore_m" in props && (
                <div>
                  <dt className="text-slate-500" title="Distance au rivage mesurée sur l'image radar et le trait de côte OpenStreetMap. Les échos sur la côte, les îlets, les ouvrages et les bouées cartographiés sont écartés à la détection ; sous 1 km du rivage, la fiabilité est réduite.">
                    Rivage
                  </dt>
                  <dd className={props.shore_m != null && props.shore_m < 500 ? "text-tactical-amber" : ""}>
                    {props.shore_m == null
                      ? "loin de toute côte connue"
                      : props.shore_m < 1000
                        ? `à ~${props.shore_m} m`
                        : `à ~${(props.shore_m / 1000).toFixed(1).replace(".", ",")} km`}
                  </dd>
                </div>
              )}
              <div>
                <dt className="text-slate-500" title="Longueur de l'écho le long de son grand axe, moins ~20 m d'étalement radar (résolution Sentinel-1 ≈ 20 m pour des pixels de 10 m). Précision ± 10 à 20 m. Entre parenthèses : allongement (navire ≥ 2, bruit ≈ 1). Sous 6 pixels, la taille n'est pas mesurable.">
                  Taille estimée
                </dt>
                <dd>
                  {props.pixels != null && props.pixels < 6
                    ? `non mesurable (${props.pixels} px)`
                    : props.length_m != null
                      ? `~${Math.round(props.length_m)} m`
                      : "—"}
                  {props.elongation != null && ` (×${props.elongation})`}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500" title="Double ✓ : l'écho apparaît aussi dans la seconde polarisation radar (VV en plus de VH), ce qui écarte la plupart des faux échos. Double ✗ : absent de la seconde polarisation, fiabilité réduite. Simple : une seule polarisation disponible.">
                  Polarisation
                </dt>
                <dd>
                  {props.dual_pol == null ? "Simple" : props.dual_pol ? "Double ✓" : "Double ✗ (non confirmée)"}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500" title="Flux AIS qui ont servi à vérifier que cet écho ne correspond à aucun navire déclaré au moment du passage (positions projetées à l'instant du passage)">
                  Source AIS
                </dt>
                <dd>{AIS_LABELS[props.ais_source] || props.ais_source || "—"}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Passage Sentinel-1</dt>
                <dd>{formatIso(props.scene_datetime)}</dd>
              </div>
              <div>
                <dt className="text-slate-500" title="Date à laquelle la vignette et ses métadonnées ont été scellées (manifeste C2PA signé par la PKI de l'instance) : toute modification ultérieure est détectable">
                  Scellement
                </dt>
                <dd>{formatTimestamp(props.timestamp)}</dd>
              </div>
              <div>
                <dt className="text-slate-500" title="Mode d'acquisition Sentinel-1 (IW = Interferometric Wide swath, 250 km de fauchée, pixels de 10 m)">Mode SAR</dt>
                <dd>{props.sar_mode || "—"}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500" title="Identifiant de la scène Sentinel-1 dans le catalogue STAC (Copernicus ou Planetary Computer) : permet de retrouver l'image d'origine">
                  Scène Sentinel-1 (catalogue STAC)
                </dt>
                <dd className="break-all">{props.stac_source || "—"}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-slate-500" title="Empreinte du certificat de l'instance qui a signé la preuve : elle permet de vérifier, hors de la plateforme, qui a scellé cette alerte">
                  Empreinte du certificat signataire (SHA-256)
                </dt>
                <dd className="break-all text-slate-300">{props.pki_fingerprint || "—"}</dd>
              </div>
            </dl>

            <OpticalImagery alert={alert} onFootprints={onOpticalFootprints} />
          </div>

        </div>
      )}
    </aside>
  );
}
