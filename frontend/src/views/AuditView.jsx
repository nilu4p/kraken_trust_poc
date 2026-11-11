import { useCallback, useEffect, useState } from "react";
import { Link2, Link2Off, RefreshCw, Search } from "lucide-react";
import { Button, Empty, ErrorBanner, Pill, Spinner } from "../components/ui.jsx";
import { formatDateTime } from "../lib/format.js";
import { errorMessage, fetchAudit, verifyAudit } from "../services/api.js";
import DocLink from "../components/DocLink.jsx";
import { PLATFORM } from "../platform.js";

// Action families recorded by this platform (platform.js), each a prefix of audit.record() actions.
const ACTION_FILTERS = [{ id: "", label: "Toutes" }, ...PLATFORM.auditFamilies];

export default function AuditView() {
  const [entries, setEntries] = useState(null);
  const [chain, setChain] = useState(null);
  const [action, setAction] = useState("");
  const [actor, setActor] = useState("");
  const [actorQuery, setActorQuery] = useState("");
  const [error, setError] = useState("");

  // The actor filter is applied 400 ms after the last keystroke (one request, not one per letter).
  useEffect(() => {
    const timer = setTimeout(() => setActorQuery(actor.trim()), 400);
    return () => clearTimeout(timer);
  }, [actor]);

  const load = useCallback(() => {
    let current = true; // a slower answer for a previous filter must not replace the latest one
    setError("");
    fetchAudit({ action: action || undefined, actor: actorQuery || undefined, limit: 300 })
      .then((data) => current && setEntries(data.entries))
      .catch((err) => current && setError(errorMessage(err)));
    return () => { current = false; };
  }, [action, actorQuery]);
  // Recomputing the whole hash chain is costly: on opening and on "Actualiser", not on every filter.
  const verify = useCallback(() => {
    verifyAudit().then(setChain).catch(() => setChain(null));
  }, []);

  useEffect(() => load(), [load]);
  useEffect(() => { verify(); }, [verify]);

  return (
    <div className="flex h-full flex-col overflow-y-auto md:overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-tactical-line bg-tactical-panel/60 p-3 text-xs">
        <div>
          <h1 className="flex items-center gap-2 text-sm uppercase tracking-[0.2em] text-white">
            Journal d&apos;audit <DocLink page="audit/" label="journal d'audit" />
          </h1>
          <p className="text-slate-500">Append-only, chaîné SHA-256 : toute modification d&apos;une entrée passée est détectée.</p>
        </div>
        {chain && (
          <span
            className={`flex items-center gap-2 border px-3 py-1.5 ${
              chain.valid ? "border-tactical-green/50 text-tactical-green" : "border-tactical-red/60 bg-tactical-red/10 text-tactical-red"
            }`}
          >
            {chain.valid ? <Link2 size={14} /> : <Link2Off size={14} />}
            {chain.valid
              ? `Chaîne intègre · ${chain.entries} entrées · tête ${chain.head?.slice(0, 12)}…`
              : `CHAÎNE ROMPUE à l'entrée #${chain.broken_at}`}
          </span>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2 border-b border-tactical-line p-3 text-xs">
        {ACTION_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setAction(f.id)}
            aria-pressed={action === f.id}
            className={`border px-2 py-1 ${action === f.id ? "border-tactical-cyan text-tactical-cyan" : "border-tactical-line text-slate-400"}`}
          >
            {f.label}
          </button>
        ))}
        <label className="flex min-w-0 items-center gap-2 border border-tactical-line bg-tactical-bg px-2">
          <Search size={13} className="text-slate-500" />
          <input className="min-w-0 bg-transparent py-1 text-slate-200 focus:outline-none" value={actor} onChange={(e) => setActor(e.target.value)} placeholder="Acteur (partie du nom)" maxLength={64} />
        </label>
        <Button icon={RefreshCw} onClick={() => { load(); verify(); }}>Actualiser</Button>
      </div>
      <ErrorBanner message={error} />
      <div className="overflow-x-auto md:min-h-0 md:flex-1 md:overflow-auto">
        {entries === null ? (
          <Spinner />
        ) : entries.length === 0 ? (
          <Empty>Aucune entrée.</Empty>
        ) : (
          <table className="w-full text-left text-xs">
            <thead className="sticky top-0 bg-tactical-panel text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="px-3 py-2">#</th>
                <th className="px-3 py-2">Horodatage</th>
                <th className="px-3 py-2">Acteur</th>
                <th className="px-3 py-2">Action</th>
                <th className="px-3 py-2">Cible</th>
                <th className="px-3 py-2">Résultat</th>
                <th className="px-3 py-2">IP</th>
                <th className="px-3 py-2">Détails</th>
                <th className="px-3 py-2">Hash</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.id} className="border-t border-tactical-line align-top">
                  <td className="px-3 py-1.5 text-slate-600">{e.id}</td>
                  <td className="whitespace-nowrap px-3 py-1.5 text-slate-400">{formatDateTime(e.at)}</td>
                  <td className="px-3 py-1.5 text-slate-200">{e.actor}</td>
                  <td className="px-3 py-1.5 font-mono text-tactical-cyan">{e.action}</td>
                  <td className="px-3 py-1.5 text-slate-400">{e.target}</td>
                  <td className="px-3 py-1.5">
                    <Pill color={e.outcome === "success" ? "#3dff8a" : e.outcome === "partial" ? "#f5c542" : "#ff3b3b"}>{e.outcome}</Pill>
                  </td>
                  <td className="px-3 py-1.5 font-mono text-slate-500">{e.ip || "—"}</td>
                  <td className="max-w-xs truncate px-3 py-1.5 text-slate-500" title={typeof e.details === "string" ? e.details : JSON.stringify(e.details)}>
                    {typeof e.details === "string" ? e.details : e.details && Object.keys(e.details).length ? JSON.stringify(e.details) : ""}
                  </td>
                  <td className="px-3 py-1.5 font-mono text-[10px] text-slate-600" title={e.hash}>{(e.hash || "").slice(0, 10)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
