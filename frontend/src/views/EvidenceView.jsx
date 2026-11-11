import { useCallback, useEffect, useState } from "react";
import { Download, FileLock2, Fingerprint, RefreshCw, ShieldCheck } from "lucide-react";
import { Button, Empty, ErrorBanner, Panel, Pill, Spinner } from "../components/ui.jsx";
import { formatBytes, formatDateTime, hasRole } from "../lib/format.js";
import { downloadEvidence, errorMessage, fetchEvidence, verifyEvidence } from "../services/api.js";
import DocLink from "../components/DocLink.jsx";
import { INTEGRITY, SEVERITY, STATUS } from "../theme.js";

function VerificationReport({ report }) {
  return (
    <div className="space-y-2 border border-tactical-line bg-tactical-bg/60 p-3 text-xs">
      <p className="flex items-center gap-2">
        <Pill color={INTEGRITY[report.integrity]?.color}>{INTEGRITY[report.integrity]?.label || report.integrity}</Pill>
        <span className="text-slate-500">
          vérifié {formatDateTime(report.verified_at)} par {report.verified_by} · C2PA embarqué : {report.c2pa_embedded_state || "absent"}
        </span>
      </p>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead className="text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="py-1">Fichier</th>
              <th className="py-1">SHA-256 scellé</th>
              <th className="py-1">Contrôle</th>
            </tr>
          </thead>
          <tbody>
            {report.files.map((f) => (
              <tr key={f.file} className="border-t border-tactical-line">
                <td className="py-1 text-slate-300">{f.file}</td>
                <td className="py-1 font-mono text-[10px] text-slate-500">{f.expected?.slice(0, 24)}…</td>
                <td className={`py-1 ${f.match ? "text-tactical-green" : "text-tactical-red"}`}>{f.match ? "conforme" : "ALTÉRÉ"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function Dossier({ dossier, user, focused, navigate }) {
  const [report, setReport] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const analyst = hasRole(user, "analyst");
  const sheet = dossier.files.find((f) => f.name === "radar_crop.jpg");

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

  return (
    <Panel
      className={focused ? "ring-1 ring-tactical-cyan" : ""}
      title={dossier.dossier}
      icon={FileLock2}
      actions={
        <>
          <Pill color={INTEGRITY[dossier.integrity]?.color}>{INTEGRITY[dossier.integrity]?.label || dossier.integrity}</Pill>
          {analyst && (
            <>
              <Button icon={ShieldCheck} busy={busy === "verify"} onClick={() => run("verify", async () => setReport(await verifyEvidence(dossier.dossier)))}>
                Vérifier
              </Button>
              <Button variant="primary" icon={Download} busy={busy === "export"} onClick={() => run("export", () => downloadEvidence(dossier.dossier))}>
                Exporter (ZIP)
              </Button>
            </>
          )}
        </>
      }
    >
      <div className="grid gap-4 md:grid-cols-[12rem_1fr]">
        {sheet ? (
          <a href={`/api/v1/evidence/files/${dossier.dossier}/radar_crop.jpg`} target="_blank" rel="noreferrer">
            <img src={`/api/v1/evidence/files/${dossier.dossier}/radar_crop.jpg?preview=1`} alt="Planche SAR scellée" className="w-full border border-tactical-line" />
          </a>
        ) : (
          <div className="flex h-32 items-center justify-center border border-dashed border-tactical-line text-xs text-slate-600">Pas de planche</div>
        )}
        <div className="space-y-2 text-xs">
          <p className="text-slate-400">
            Scellé {formatDateTime(dossier.created_at)} · {dossier.targets} cible(s) · signature {dossier.signature_status || "—"}
          </p>
          <p className="break-all text-slate-500">Scène : {dossier.stac_source || "—"}</p>
          <p className="flex items-center gap-1 break-all text-[10px] text-slate-600">
            <Fingerprint size={11} /> PKI {dossier.pki_fingerprint || "—"}
          </p>
          <div className="flex flex-wrap gap-2">
            {dossier.incidents.map((i) => (
              <button key={i.id} type="button" onClick={() => navigate("incidents", i.id)} className="flex items-center gap-1 hover:text-tactical-cyan">
                <Pill color={SEVERITY[i.severity]?.color}>{i.severity}</Pill>
                {i.ref}
                <Pill color={STATUS[i.status]?.color}>{STATUS[i.status]?.label}</Pill>
              </button>
            ))}
            {dossier.incidents.length === 0 && <span className="text-slate-600">Aucun incident lié</span>}
          </div>
          <ul className="flex flex-wrap gap-x-3 text-[10px] text-slate-500">
            {dossier.files.map((f) => (
              <li key={f.name}>
                {f.name} ({formatBytes(f.size)})
              </li>
            ))}
          </ul>
          <ErrorBanner message={error} />
        </div>
      </div>
      {report && <div className="mt-3"><VerificationReport report={report} /></div>}
    </Panel>
  );
}

export default function EvidenceView({ user, navigate }) {
  const [dossiers, setDossiers] = useState(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState("all");
  const focused = window.location.hash.split("/")[2] || "";

  const load = useCallback(() => {
    fetchEvidence()
      .then((data) => {
        setDossiers(data.dossiers);
        setError("");
      })
      .catch((err) => setError(errorMessage(err, "Coffre de preuves indisponible")));
  }, []);

  useEffect(load, [load]);

  useEffect(() => {
    if (focused && dossiers) document.getElementById(`dossier-${focused}`)?.scrollIntoView({ block: "start" });
  }, [focused, dossiers]);

  const shown = (dossiers || []).filter((d) => filter === "all" || d.integrity === filter);
  const counts = (dossiers || []).reduce((acc, d) => ({ ...acc, [d.integrity]: (acc[d.integrity] || 0) + 1 }), {});

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-sm uppercase tracking-[0.2em] text-white">
            Coffre de preuves <DocLink page="preuves/" label="preuves, C2PA, export" />
          </h1>
          <p className="text-xs text-slate-500">
            Dossiers scellés C2PA (X.509) · accès authentifié et tracé · export judiciaire avec chaîne de possession
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          {["all", "verified", "fallback", "unverifiable", "tampered"].map((key) => (
            <button
              key={key}
              type="button"
              onClick={() => setFilter(key)}
              className={`border px-2 py-1 ${filter === key ? "border-tactical-cyan text-tactical-cyan" : "border-tactical-line text-slate-400"}`}
            >
              {key === "all" ? `Tous (${dossiers?.length || 0})` : `${INTEGRITY[key].label.split(" —")[0]} (${counts[key] || 0})`}
            </button>
          ))}
          <Button icon={RefreshCw} onClick={load}>Actualiser</Button>
        </div>
      </div>
      <ErrorBanner message={error} />
      {dossiers === null ? (
        <Spinner />
      ) : shown.length === 0 ? (
        <Empty>Aucun dossier de preuves.</Empty>
      ) : (
        <div className="space-y-4">
          {shown.map((d) => (
            <div key={d.dossier} id={`dossier-${d.dossier}`}>
              <Dossier dossier={d} user={user} focused={focused === d.dossier} navigate={navigate} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
