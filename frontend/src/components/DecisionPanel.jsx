import { useCallback, useEffect, useState } from "react";
import { Anchor, CheckCircle2, Compass, Layers, Radar, RefreshCw, Scale, Target, XCircle } from "lucide-react";
import { Button, ErrorBanner, Panel, Pill, Spinner } from "./ui.jsx";
import DocLink from "./DocLink.jsx";
import { formatCoords, formatDateTime } from "../lib/format.js";
import { errorMessage, fetchDecision, scanOptical } from "../services/api.js";

const CONTACT_STATUS = {
  dark_corroborated: { color: "#ff3b3b", label: "Sans AIS — corroboré" },
  dark_single: { color: "#f5c542", label: "Sans AIS — à confirmer" },
  cooperative: { color: "#3dff8a", label: "Identifié (AIS / VMS)" },
};
// Sensor disciplines of the corroboration (see docs: moteur/corroboration).
const INT_COLORS = { SAR: "#818cf8", EO: "#22d3ee", IR: "#fb923c", NIGHT: "#facc15", GFW: "#a78bfa", AIS: "#3dff8a", VMS: "#86efac" };

function IntBadges({ ints }) {
  return (
    <span className="flex flex-wrap gap-1">
      {ints.map((i) => (
        <Pill key={i.int} color={INT_COLORS[i.int] || "#94a3b8"} title={`${i.label} — ${i.count} observation(s), poids ${Math.round(i.p * 100)} %`}>
          {i.int}
        </Pill>
      ))}
    </span>
  );
}

/** How a nearby contact reads for an operator: the vessel's name / MMSI when identified, else "sans AIS". */
function contactName(c) {
  if (c.identity) return c.identity.name || `MMSI ${c.identity.mmsi}`;
  return CONTACT_STATUS[c.status].label.toLowerCase();
}

/** Multi-sensor corroboration of the incident and an on-demand optical / thermal look around it. */
function Corroboration({ data, canScan, onScanned }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const fused = data.corroboration;
  const t = data.target;
  const scan = async () => {
    setBusy(true);
    setError("");
    try {
      const radius = Math.min(25, Math.max(5, Math.round((t.uncertainty_nm || 3) * 1.852)));
      setResult(await scanOptical({ lat: t.lat_now, lon: t.lon_now, radius_km: radius, days: 5 }));
      onScanned();
    } catch (err) {
      setError(errorMessage(err, "Analyse optique impossible"));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-2">
      <p className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
        <Layers size={12} /> Corroboration multi-capteurs
        <DocLink page="moteur/corroboration/" label="corroboration multi-capteurs" />
      </p>
      <p className="text-slate-500">
        Un autre capteur (optique Sentinel-2 / Landsat, lumières nocturnes VIIRS, détections radar GFW) a-t-il vu un navire au même
        endroit dans les 36 h ? Deux capteurs indépendants qui concordent font d&apos;un écho faible une cible confirmée.
      </p>
      {!fused ? (
        <p className="text-slate-500">Corroboration indisponible (sources capteurs injoignables).</p>
      ) : (
        <>
          <p className={fused.corroborated ? "text-tactical-green" : "text-slate-300"}>{fused.summary}</p>
          {fused.contact && (
            <div className="flex flex-wrap items-center gap-2">
              <Pill color={CONTACT_STATUS[fused.contact.status].color}>{CONTACT_STATUS[fused.contact.status].label}</Pill>
              <IntBadges ints={fused.contact.ints} />
            </div>
          )}
          {fused.nearby?.length > 0 && (
            <p className="text-slate-500">
              Autres navires vus à moins de 25 km : {fused.nearby.map((c) => `${contactName(c)} (${c.ints.map((i) => i.int).join("+")}, ${c.distance_km} km)`).join(" · ")}
            </p>
          )}
        </>
      )}
      {canScan && (
        <Button icon={Radar} busy={busy} onClick={scan} title="Sentinel-2 (10 m) et Landsat 8/9 (30 m + thermique) des 5 derniers jours autour de la position estimée">
          Analyser l&apos;imagerie optique / thermique autour
        </Button>
      )}
      {busy && <Spinner label="Lecture des images Sentinel-2 / Landsat (jusqu'à 2 min)…" />}
      <ErrorBanner message={error} />
      {result && (
        <div className="space-y-2">
          <p className="text-slate-300">
            {result.detections.length} navire(s) vu(s) sur {result.scenes.length} scène(s) (
            {result.scenes.map((s) => `${s.sensor === "s2_msi" ? "S2" : "Landsat"} ${formatDateTime(s.acquired_at)} nuages ${s.cloud_pct ?? "—"} %`).join(" ; ") || "aucune image exploitable"}).
          </p>
          <div className="grid grid-cols-3 gap-2 md:grid-cols-6">
            {result.detections.slice(0, 12).map((d) => (
              <figure key={`${d.scene_id}${d.lat}${d.lon}`} className="border border-tactical-line p-1">
                {d.attributes.chip && <img src={d.attributes.chip} alt="Vignette optique" className="h-20 w-full object-contain [image-rendering:pixelated]" />}
                <figcaption className="text-[10px] text-slate-400">{d.distance_km} km · ~{Math.round(d.length_m)} m · {Math.round(d.confidence * 100)} %</figcaption>
              </figure>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

const OPTION_COLORS = { intercept: "#ff8c1a", identify: "#00e5ff", monitor: "#94a3b8", cooperate: "#a78bfa" };

function ageText(minutes) {
  if (minutes == null) return "—";
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 2880) return `${Math.floor(minutes / 60)} h ${String(minutes % 60).padStart(2, "0")}`;
  return `${Math.round(minutes / 1440)} j`;
}

/**
 * Decision support for one incident: where the target is now, which means can reach it
 * (and where, legally), the courses of action and the recommended one — with every assumption.
 */
export default function DecisionPanel({ incidentId, refreshKey, canScan = false }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    setBusy(true);
    fetchDecision(incidentId)
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((err) => setError(errorMessage(err, "Aide à la décision indisponible")))
      .finally(() => setBusy(false));
  }, [incidentId]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 120000); // the target keeps moving: refresh every 2 min
    return () => clearInterval(timer);
  }, [load, refreshKey]);

  const t = data?.target;
  return (
    <Panel
      title="Aide à la décision"
      icon={Compass}
      actions={
        <>
          <DocLink page="commandement/decision/" label="calculs et hypothèses" />
          <Button icon={RefreshCw} busy={busy} onClick={load} title="Recalculer avec la dernière position connue">
            Recalculer
          </Button>
        </>
      }
    >
      <ErrorBanner message={error} />
      {!data && !error && <Spinner label="Calcul des options…" />}
      {data && (
        <div className="space-y-4 text-xs">
          {/* Recommendation first: what the operator needs in a glance */}
          {data.options
            .filter((o) => o.recommended)
            .map((o) => (
              <div key={o.id} className="border-l-4 bg-white/5 p-3" style={{ borderColor: OPTION_COLORS[o.id] }}>
                <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Option recommandée</p>
                <p className="mt-1 text-sm text-white">{o.title}</p>
                <p className="mt-1 text-slate-300">{o.summary}</p>
                <p className="mt-1 text-slate-400">
                  <span className="text-slate-500">Motif : </span>
                  {data.recommendation}
                </p>
              </div>
            ))}

          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <p className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
                <Target size={12} /> Cible maintenant (estimée)
              </p>
              <p className="text-slate-100">{formatCoords(t.lat_now, t.lon_now)}</p>
              <p className="text-slate-400">
                Incertitude ±{t.uncertainty_nm} NM · {data.zone_now_label}
              </p>
              <p className="text-slate-400">
                Dernière position {formatDateTime(t.time)} (il y a {ageText(t.age_minutes)})
              </p>
              <p className="text-slate-400">
                {t.kinematics_label}
                {t.sog_kn != null && ` — ${t.sog_kn} nd au ${t.cog_deg}°`}
              </p>
              {t.mmsi && (
                <p className="text-slate-400">
                  MMSI {t.mmsi}
                  {t.name && ` · ${t.name}`}
                  {t.flag && ` · ${t.flag}`}
                </p>
              )}
            </div>
            <div className="space-y-1">
              <p className="text-[10px] uppercase tracking-wider text-slate-500">Projection de route</p>
              <p className={data.projection.exit_eez_h != null ? "text-tactical-amber" : "text-slate-300"}>{data.projection.note}</p>
              {data.projection.exit_point && (
                <p className="text-slate-400">Point de sortie : {formatCoords(data.projection.exit_point.lat, data.projection.exit_point.lon)}</p>
              )}
            </div>
          </div>

          <div>
            <p className="mb-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
              <Anchor size={12} /> Moyens — délai pour être sur zone
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left">
                <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                  <tr>
                    <th className="py-1 pr-3">Moyen</th>
                    <th className="py-1 pr-3">Sur zone dans</th>
                    <th className="py-1 pr-3">Distance</th>
                    <th className="py-1 pr-3">Lieu d&apos;interception</th>
                    <th className="py-1 pr-3">Rayon de recherche</th>
                    <th className="py-1">Réalisable</th>
                  </tr>
                </thead>
                <tbody>
                  {data.means.map((m) => (
                    <tr key={m.asset_id} className="border-t border-tactical-line align-top">
                      <td className="py-1.5 pr-3">
                        <p className="text-slate-100">{m.name}</p>
                        <p className="text-[10px] text-slate-500">
                          {m.kind_label} · {m.base_name} · {m.can_board ? "peut arraisonner" : "observation seule"}
                        </p>
                      </td>
                      <td className="py-1.5 pr-3 text-slate-200">
                        {m.eta_label ?? "—"}
                        {m.eta_minutes != null && <p className="text-[10px] text-slate-500">dont {m.readiness_min} min d&apos;appareillage</p>}
                      </td>
                      <td className="py-1.5 pr-3 text-slate-300">{m.distance_nm != null ? `${m.distance_nm} NM` : "—"}</td>
                      <td className="py-1.5 pr-3 text-slate-300">{m.intercept_zone_label ?? "—"}</td>
                      <td className="py-1.5 pr-3 text-slate-300">{m.search_radius_nm != null ? `${m.search_radius_nm} NM` : "—"}</td>
                      <td className="py-1.5">
                        {m.feasible ? (
                          <span className="flex items-center gap-1 text-tactical-green">
                            <CheckCircle2 size={13} /> oui
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-slate-500">
                            <XCircle size={13} /> non
                          </span>
                        )}
                        {m.issues.map((issue) => (
                          <p key={issue} className="text-[10px] text-tactical-amber">
                            {issue}
                          </p>
                        ))}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {data.means.length === 0 && <p className="text-slate-500">Aucun moyen renseigné (Commandement › Moyens).</p>}
            </div>
          </div>

          <div>
            <p className="mb-1 text-[10px] uppercase tracking-wider text-slate-500">Options d&apos;action</p>
            <div className="grid gap-2 md:grid-cols-2">
              {data.options.map((o) => (
                <div key={o.id} className={`border p-2 ${o.recommended ? "border-tactical-cyan/50" : "border-tactical-line"}`}>
                  <p className="flex items-center gap-2 text-slate-100">
                    {o.title} {o.recommended && <Pill color="#00e5ff">recommandée</Pill>}
                  </p>
                  <p className="mt-1 text-slate-400">{o.summary}</p>
                  <ul className="mt-1 space-y-0.5">
                    {o.pros.map((p) => (
                      <li key={p} className="text-tactical-green/80">+ {p}</li>
                    ))}
                    {o.cons.map((c) => (
                      <li key={c} className="text-tactical-amber/90">− {c}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          </div>

          <Corroboration data={data} canScan={canScan} onScanned={load} />

          <div className="space-y-1">
            <p className="flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-slate-500">
              <Scale size={12} /> Cadre juridique
            </p>
            {data.legal.map((text) => (
              <p key={text} className="text-slate-400">{text}</p>
            ))}
          </div>
          <div className="space-y-1">
            <p className="text-[10px] uppercase tracking-wider text-slate-500">Hypothèses de calcul</p>
            {data.caveats.map((text) => (
              <p key={text} className="text-slate-500">• {text}</p>
            ))}
            <p className="text-[10px] text-slate-600">Calculé le {formatDateTime(data.computed_at)} — rien n&apos;est enregistré : la décision reste à l&apos;opérateur.</p>
          </div>
        </div>
      )}
    </Panel>
  );
}
