import { useCallback, useEffect, useState } from "react";
import { Activity, BrainCircuit, Database, Gauge, HardDrive, Radar, RefreshCw, Satellite, Timer } from "lucide-react";
import { Button, ErrorBanner, Panel, Spinner } from "../components/ui.jsx";
import { formatDateTime } from "../lib/format.js";
import { errorMessage, fetchSupervision } from "../services/api.js";

// Status = icon-free text label + colour (never colour alone).
const STATE = {
  ok: { color: "#3dff8a", label: "OK" },
  warn: { color: "#f5c542", label: "À surveiller" },
  bad: { color: "#ff3b3b", label: "Problème" },
  off: { color: "#64748b", label: "—" },
};

function Badge({ state }) {
  const s = STATE[state] || STATE.off;
  return (
    <span className="inline-flex items-center gap-1 border px-1.5 py-0.5 text-[10px] uppercase tracking-wider" style={{ color: s.color, borderColor: `${s.color}80` }}>
      {s.label}
    </span>
  );
}

function Tile({ label, value, hint, state }) {
  return (
    <div className="border border-tactical-line bg-tactical-bg/40 p-3">
      <p className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-[0.2em] text-slate-500">
        {label} {state && <Badge state={state} />}
      </p>
      <p className="mt-1 text-xl text-slate-100">{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-slate-500">{hint}</p>}
    </div>
  );
}

const pct = (v) => (v == null ? "—" : `${Math.round(v * 100)} %`);
const age = (s) => (s == null ? "—" : s < 120 ? `${s} s` : s < 7200 ? `${Math.round(s / 60)} min` : s < 172800 ? `${Math.round(s / 3600)} h` : `${Math.round(s / 86400)} j`);
const bytes = (b) => {
  if (b == null) return "—";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let v = b;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v < 10 && i ? 1 : 0)} ${units[i]}`;
};
const FEEDS = { aisstream: "aisstream.io", aishub: "AISHub", vesselapi: "VesselAPI" };

/** Technical supervision: is the platform itself healthy, fast and complete? */
function Agreement({ evaluation }) {
  if (!evaluation?.judged) return <span className="text-slate-500">pas encore d'incident clos jugé avec cet avis</span>;
  return (
    <span>
      accord avec les opérateurs {pct(evaluation.agreement)} sur {evaluation.judged} incident(s) clos · précision {pct(evaluation.precision)} · rappel{" "}
      {pct(evaluation.recall)}
      {!evaluation.enough_to_decide && <span className="text-slate-500"> (moins de 30 jugements : indicatif)</span>}
    </span>
  );
}

/** AI opinions on radar targets: information only, measured against the operators' verdicts. */
function AiPanel({ ai }) {
  const x = ai.xview3 || {};
  const xState = !x.enabled ? "off" : x.ready && x.sha256_ok !== false ? "ok" : "bad";
  return (
    <Panel title="Avis IA sur les cibles radar (information seule, sans effet sur les alertes)" icon={BrainCircuit}>
      <ul className="space-y-2">
        <li>
          <div className="flex items-center gap-2">
            <Badge state={xState} />
            <span className="text-slate-200">xView3 (1re place du concours xView3, MIT)</span>
          </div>
          <p className="pl-1 text-slate-400">
            {!x.enabled
              ? "Service non démarré : make up-ai pour l'activer."
              : x.ready
                ? `Modèle chargé${x.sha256_ok === false ? " — empreinte inattendue !" : " (empreinte SHA-256 vérifiée)"}.`
                : `Indisponible : ${x.error || "chargement en cours"}.`}{" "}
            {x.enabled && <Agreement evaluation={x.evaluation} />}
          </p>
        </li>
        <li>
          <div className="flex items-center gap-2">
            <Badge state={ai.yolo?.mode >= 1 ? "ok" : "off"} />
            <span className="text-slate-200">YOLOv8n SAR (paramètre ai_detector_mode = {ai.yolo?.mode ?? 0})</span>
          </div>
          {ai.yolo?.mode >= 1 && <p className="pl-1 text-slate-400"><Agreement evaluation={ai.yolo.evaluation} /></p>}
        </li>
      </ul>
    </Panel>
  );
}

export default function SupervisionTab() {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    fetchSupervision()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((err) => setError(errorMessage(err, "Supervision indisponible")));
  }, []);
  useEffect(() => {
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  if (!data && !error) return <Spinner />;
  if (!data) return <ErrorBanner message={error} />;
  const c = data.collection;
  const collectionState = c.coverage_30min == null ? "off" : c.coverage_30min >= 0.8 ? "ok" : c.coverage_30min >= 0.5 ? "warn" : "bad";
  const rejected = Object.values(c.rejected || {}).reduce((n, reasons) => n + Object.values(reasons).reduce((a, b) => a + b, 0), 0);
  const down = data.sources.filter((s) => s.status === "down").length;
  const degraded = data.sources.filter((s) => s.status === "degraded").length;
  const freeShare = data.disk.total_bytes ? data.disk.free_bytes / data.disk.total_bytes : null;
  const slowest = data.api[0];

  return (
    <div className="space-y-4 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-slate-400">
          Instantané du {formatDateTime(data.generated_at)} · mode {data.background_mode} · processus démarré le {formatDateTime(data.process_started_at)} ·
          calculé en {data.built_in_ms} ms (aucun appel externe) · rafraîchi toutes les 30 s
        </p>
        <Button icon={RefreshCw} onClick={load}>Actualiser</Button>
      </div>
      <ErrorBanner message={error} />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Tile label="Sources externes" value={`${data.sources.length - down - degraded}/${data.sources.length}`} hint={`${down} indisponible(s) · ${degraded} dégradée(s)`}
          state={down ? "bad" : degraded ? "warn" : "ok"} />
        <Tile label="Collecte AIS (30 min)" value={pct(c.coverage_30min)} hint={`24 h : ${pct(c.coverage_24h)} · ${c.gaps_24h.length} interruption(s)`} state={collectionState} />
        <Tile label="Cycle des règles 24/7" value={age(data.rules.age_s)} hint={`dernier passage · ${data.rules.duration_ms ?? "—"} ms`}
          state={!data.rules.enabled ? "off" : data.rules.stalled ? "bad" : "ok"} />
        <Tile label="Espace disque libre" value={bytes(data.disk.free_bytes)} hint={freeShare != null ? `${pct(freeShare)} du volume` : ""}
          state={freeShare == null ? "off" : freeShare < 0.1 ? "bad" : freeShare < 0.2 ? "warn" : "ok"} />
      </div>

      <Panel title="Sources externes" icon={Activity}>
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <thead className="text-[10px] uppercase tracking-wider text-slate-500">
              <tr><th className="py-1">Source</th><th>État</th><th>Dernier succès</th><th>Dernier échec</th><th>Erreurs consécutives</th><th>Dernière erreur</th></tr>
            </thead>
            <tbody>
              {data.sources.map((s) => (
                <tr key={s.source} className="border-t border-tactical-line">
                  <td className="py-1.5 text-slate-200">{s.source}</td>
                  <td><Badge state={s.status === "ok" ? "ok" : s.status === "degraded" ? "warn" : "bad"} /></td>
                  <td className="text-slate-400">il y a {age(s.success_age_s)}</td>
                  <td className="text-slate-400">{s.failure_age_s != null ? `il y a ${age(s.failure_age_s)}` : "—"}</td>
                  <td className="text-slate-400">{s.consecutive_failures}</td>
                  <td className="max-w-xs truncate text-slate-500" title={s.last_error}>{s.last_error || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {data.sources.length === 0 && <p className="text-slate-500">Aucune source appelée depuis le démarrage de ce processus.</p>}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Collecte AIS" icon={Satellite}>
          <ul className="space-y-1.5">
            {Object.entries(c.per_feed_24h).map(([feed, value]) => (
              <li key={feed} className="flex justify-between"><span className="text-slate-300">{FEEDS[feed] || feed} — écoute sur 24 h</span><span>{pct(value)}</span></li>
            ))}
            <li className="flex justify-between"><span className="text-slate-300">Positions reçues (1 h)</span><span>{c.positions_1h}</span></li>
            <li className="flex justify-between"><span className="text-slate-300">Flux aisstream</span>
              <span>{c.stream?.connected_since ? `connecté depuis ${formatDateTime(c.stream.connected_since)}` : `déconnecté${c.stream?.last_error ? ` (${c.stream.last_error})` : ""}`}</span></li>
            <li className="flex justify-between"><span className="text-slate-300">Positions rejetées (depuis le démarrage)</span><span>{rejected}</span></li>
            {Object.entries(c.rejected || {}).map(([feed, reasons]) => (
              <li key={feed} className="pl-3 text-slate-500">{FEEDS[feed] || feed} : {Object.entries(reasons).map(([r, n]) => `${r} ${n}`).join(" · ")}</li>
            ))}
            {c.state?.degraded && <li className="text-tactical-red">Alerte en cours : collecte dégradée depuis {formatDateTime(c.state.since)}</li>}
          </ul>
          {c.gaps_24h.length > 0 && (
            <p className="mt-2 text-slate-500">Interruptions (24 h) : {c.gaps_24h.map((g) => `${g.start.slice(11, 16)}–${g.end.slice(11, 16)} Z`).join(", ")}</p>
          )}
        </Panel>

        <Panel title="Scans radar" icon={Radar}>
          <p className="mb-2 text-slate-400">
            Dernier succès : {data.scans.last_success ? formatDateTime(data.scans.last_success) : "—"} · durée moyenne{" "}
            {data.scans.average_duration_s != null ? `${Math.round(data.scans.average_duration_s)} s` : "—"} · {data.scans.errors_recent} échec(s) sur les 10 derniers
          </p>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="text-[10px] uppercase tracking-wider text-slate-500"><tr><th className="py-1">Fin</th><th>Issue</th><th>Durée</th><th>Couverture</th><th>Cibles</th><th>Avis xView3</th></tr></thead>
              <tbody>
                {data.scans.runs.map((r) => (
                  <tr key={`${r.finished_at}-${r.scene}`} className="border-t border-tactical-line" title={r.message}>
                    <td className="py-1.5 text-slate-400">{formatDateTime(r.finished_at)}</td>
                    <td><Badge state={r.status === "done" ? "ok" : "bad"} /></td>
                    <td className="text-slate-400">{r.duration_s != null ? `${Math.round(r.duration_s)} s` : "—"}</td>
                    <td className="text-slate-400">{r.coverage_pct != null ? `${r.coverage_pct} %` : "—"}</td>
                    <td className="text-slate-400">{r.dark_vessels ?? "—"}</td>
                    <td className={r.xview3_error ? "text-tactical-amber" : "text-slate-400"} title={r.xview3_error || ""}>
                      {r.xview3_error ? "échec" : r.xview3_scored != null ? `${r.xview3_scored} cible(s)` : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.scans.runs.length === 0 && <p className="text-slate-500">Aucun scan enregistré depuis la mise à jour.</p>}
        </Panel>
      </div>

      {data.ai && <AiPanel ai={data.ai} />}

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Temps de réponse de l'API (500 dernières requêtes par domaine, ce processus)" icon={Timer}>
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <thead className="text-[10px] uppercase tracking-wider text-slate-500"><tr><th className="py-1">Domaine</th><th>Requêtes</th><th>Médiane</th><th>95 %</th><th>Max</th><th>Erreurs 5xx</th></tr></thead>
              <tbody>
                {data.api.map((t) => (
                  <tr key={t.area} className="border-t border-tactical-line">
                    <td className="py-1.5 text-slate-200">{t.area}</td>
                    <td className="text-slate-400">{t.requests}</td>
                    <td className="text-slate-400">{t.p50_ms} ms</td>
                    <td style={{ color: t.p95_ms > 3000 ? STATE.bad.color : t.p95_ms > 1000 ? STATE.warn.color : undefined }}>{t.p95_ms} ms</td>
                    <td className="text-slate-500">{t.max_ms} ms</td>
                    <td style={{ color: t.errors ? STATE.bad.color : undefined }}>{t.errors}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {slowest && <p className="mt-2 text-slate-500">Domaine le plus lent (95 %) : {slowest.area}. Au-delà de 1 s, l&apos;opérateur ressent la latence.</p>}
        </Panel>

        <Panel title="Base de données et stockage" icon={Database}>
          <p className="mb-2 text-slate-400">
            {data.database.engine === "postgresql" ? `PostgreSQL${data.database.timescaledb ? " + TimescaleDB" : ""}` : "SQLite"} · taille {bytes(data.database.size_bytes)} ·
            historique conservé {data.retention_days} j
          </p>
          <ul className="grid grid-cols-2 gap-x-4 gap-y-1">
            {Object.entries(data.database.rows).map(([table, count]) => (
              <li key={table} className="flex justify-between"><span className="text-slate-400">{table}</span><span>{count == null ? "—" : count.toLocaleString("fr-FR")}</span></li>
            ))}
          </ul>
          <p className="mt-3 flex items-center gap-1.5 text-[10px] uppercase tracking-[0.2em] text-slate-500"><HardDrive size={12} /> Dossiers</p>
          <ul className="mt-1 grid grid-cols-2 gap-x-4 gap-y-1">
            {["preuves", "cache", "backups", "data"].map((name) => (
              <li key={name} className="flex justify-between"><span className="text-slate-400">{name}/</span><span>{bytes(data.disk[name])}</span></li>
            ))}
          </ul>
        </Panel>
      </div>
      <p className="flex items-center gap-1.5 text-[11px] text-slate-500">
        <Gauge size={12} /> Les seuils de couleur : collecte AIS ≥ 80 % OK, 50–80 % à surveiller, &lt; 50 % problème (alerte #operations) ; cycle des règles bloqué
        au-delà de 3 min ; disque &lt; 20 % à surveiller, &lt; 10 % problème.
      </p>
    </div>
  );
}
