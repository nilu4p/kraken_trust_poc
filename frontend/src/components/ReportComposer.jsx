import { useCallback, useEffect, useState } from "react";
import { Copy, Download, Eye, FileSignature, Printer, ShieldCheck } from "lucide-react";
import { Button, ErrorBanner, Field, Spinner, inputClass } from "./ui.jsx";
import ReportDocument from "./ReportDocument.jsx";
import { hasRole } from "../lib/format.js";
import {
  draftIncidentReport,
  draftSitrep,
  errorMessage,
  fetchReport,
  fetchReports,
  reportExportUrl,
  verifyReport,
} from "../services/api.js";

const ADDRESSEE_HINTS = ["CROSS Antilles-Guyane", "Préfet de Guyane (AEM)", "COMSUP FAG", "DM Guyane", "Parquet de Cayenne"];

/** "2026-10-22T06:00" typed in UTC → ISO with an explicit zone. */
const toIso = (local) => (local ? `${local}:00Z` : undefined);
const utcInput = (date) => date.toISOString().slice(0, 16);

function ReportActions({ report, user, onVerified }) {
  const [busy, setBusy] = useState("");
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const analyst = hasRole(user, "analyst");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(report.content);
      setResult({ copied: true });
    } catch {
      setError("Copie impossible : sélectionnez le texte exporté");
    }
  };
  const verify = async () => {
    setBusy("verify");
    setError("");
    try {
      const check = await verifyReport(report.id);
      setResult(check);
      onVerified?.(check);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy("");
    }
  };
  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button icon={Printer} onClick={() => window.print()} title="Imprimer ou enregistrer en PDF (le rapport seul est imprimé)">
          Imprimer / PDF
        </Button>
        {analyst && (
          <a
            href={reportExportUrl(report.id)}
            className="inline-flex items-center gap-1.5 border border-tactical-line px-3 py-1.5 text-xs text-slate-300 hover:bg-white/5"
            title="Texte signé suivi du bloc de scellement (empreinte, signature, certificat)"
          >
            <Download size={13} /> Message signé (.txt)
          </a>
        )}
        <Button icon={Copy} onClick={copy} title="Copier le texte du message (messagerie, chat opérationnel)">
          Copier le texte
        </Button>
        {analyst && (
          <Button icon={ShieldCheck} busy={busy === "verify"} onClick={verify}>
            Vérifier l&apos;intégrité
          </Button>
        )}
      </div>
      {result?.copied && <p className="text-xs text-tactical-green">Texte copié.</p>}
      {result && "valid" in result && (
        <p className={`text-xs ${result.valid ? "text-tactical-green" : "text-tactical-red"}`}>
          {result.valid
            ? `Intègre : empreinte conforme${result.signed ? ", signature valide, certificat émis par la PKI de l'instance" : " (rapport non signé)"}.`
            : `ALTÉRÉ ou invalide — empreinte ${result.hash_ok ? "conforme" : "différente"}, signature ${
                result.signature_ok === false ? "invalide" : result.signature_ok ? "valide" : "absente"
              }, chaîne ${result.chain_ok === false ? "inconnue" : "valide"}.`}
        </p>
      )}
      <ErrorBanner message={error} />
    </div>
  );
}

/** A report from the register: the frozen, signed text with its seal. */
export function IssuedReport({ id, user }) {
  const [report, setReport] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setReport(null);
    fetchReport(id)
      .then((d) => setReport(d.report))
      .catch((err) => setError(errorMessage(err)));
  }, [id]);
  if (error) return <ErrorBanner message={error} />;
  if (!report) return <Spinner />;
  return (
    <div className="space-y-3">
      <ReportActions report={report} user={user} />
      <ReportDocument header={report.header} sections={report.sections} seal={report} printable />
    </div>
  );
}

/**
 * Draft → preview → issue. The preview is recomputed from the platform's records each time;
 * issuing freezes the text, signs it and registers it (audit, incident timeline).
 */
export default function ReportComposer({ kind, incidentId, user, onIssued }) {
  const [form, setForm] = useState(() => {
    const end = new Date();
    return {
      addressees: "",
      classification: "",
      assessment: "",
      proposals: "",
      start: utcInput(new Date(end.getTime() - 24 * 3600 * 1000)),
      end: utcInput(end),
    };
  });
  const [classifications, setClassifications] = useState([]);
  const [draft, setDraft] = useState(null);
  const [issued, setIssued] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const canIssue = kind === "sitrep" ? hasRole(user, "supervisor") : hasRole(user, "analyst");

  useEffect(() => {
    fetchReports({ kind })
      .then((d) => {
        setClassifications(d.classifications);
        setForm((f) => ({ ...f, classification: f.classification || d.default_classification }));
      })
      .catch(() => setClassifications([]));
  }, [kind]);

  const body = useCallback(
    (issue) => ({
      addressees: form.addressees,
      classification: form.classification || undefined,
      assessment: form.assessment,
      proposals: form.proposals,
      issue,
      ...(kind === "sitrep" ? { start: toIso(form.start), end: toIso(form.end) } : {}),
    }),
    [form, kind],
  );

  const run = async (issue) => {
    setBusy(issue ? "issue" : "preview");
    setError("");
    try {
      const result = kind === "sitrep" ? await draftSitrep(body(issue)) : await draftIncidentReport(incidentId, body(issue));
      if (issue) {
        setIssued(result);
        setDraft(null);
        onIssued?.(result);
      } else {
        setDraft(result);
      }
    } catch (err) {
      setError(errorMessage(err, "Rédaction impossible"));
    } finally {
      setBusy("");
    }
  };

  useEffect(() => {
    if (kind === "incident") run(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [incidentId, kind]);

  const set = (key) => (e) => setForm((f) => ({ ...f, [key]: e.target.value }));

  if (issued) {
    return (
      <div className="space-y-3">
        <p className="border border-tactical-green/40 bg-tactical-green/10 px-3 py-2 text-xs text-tactical-green">
          Rapport {issued.ref} émis {issued.signed ? "et signé" : "(non signé : PKI indisponible)"} — enregistré au registre, tracé
          dans l&apos;audit{kind === "incident" ? " et la chronologie de l'incident" : ""}.
        </p>
        <ReportActions report={issued} user={user} />
        <ReportDocument header={issued.header} sections={issued.sections} seal={issued} printable />
        <Button onClick={() => setIssued(null)}>Rédiger un autre rapport</Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-2">
        {kind === "sitrep" && (
          <>
            <Field label="Début de période (UTC)">
              <input type="datetime-local" className={inputClass} value={form.start} onChange={set("start")} />
            </Field>
            <Field label="Fin de période (UTC)">
              <input type="datetime-local" className={inputClass} value={form.end} onChange={set("end")} />
            </Field>
          </>
        )}
        <Field label="Destinataires" hint="séparés par « ; »">
          <input className={inputClass} list="report-addressees" value={form.addressees} onChange={set("addressees")} maxLength={500}
            placeholder="CROSS Antilles-Guyane ; Préfet de Guyane (AEM)" />
          <datalist id="report-addressees">
            {ADDRESSEE_HINTS.map((a) => (
              <option key={a} value={a} />
            ))}
          </datalist>
        </Field>
        <Field label="Mention de protection">
          <select className={inputClass} value={form.classification} onChange={set("classification")}>
            {classifications.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Appréciation de l'opérateur" hint="ce que l'on comprend de la situation">
          <textarea className={`${inputClass} h-24`} value={form.assessment} onChange={set("assessment")} maxLength={8000}
            placeholder="Ex. : chalutier de type tapouille, activité de pêche probable à 40 NM des côtes, aucune licence connue." />
        </Field>
        <Field label="Propositions et demandes" hint="ce que l'on demande au commandement">
          <textarea className={`${inputClass} h-24`} value={form.proposals} onChange={set("proposals")} maxLength={8000}
            placeholder="Ex. : engagement de la vedette côtière, demande de survol par l'avion de surveillance maritime." />
        </Field>
      </div>
      <div className="flex flex-wrap gap-2">
        <Button icon={Eye} busy={busy === "preview"} onClick={() => run(false)}>
          Prévisualiser
        </Button>
        {canIssue ? (
          <Button
            variant="primary"
            icon={FileSignature}
            busy={busy === "issue"}
            onClick={() => {
              if (window.confirm("Émettre ce rapport ? Son texte sera figé, signé et enregistré au registre ; il ne pourra plus être modifié.")) run(true);
            }}
          >
            Émettre et signer
          </Button>
        ) : (
          <span className="self-center text-xs text-slate-500">
            {kind === "sitrep" ? "L'émission d'un SITREP est réservée aux superviseurs." : "Lecture seule."}
          </span>
        )}
      </div>
      <ErrorBanner message={error} />
      {busy === "preview" && !draft && <Spinner label="Rédaction du projet…" />}
      {draft && (
        <div className="space-y-2">
          <p className="text-[10px] uppercase tracking-[0.2em] text-tactical-amber">Projet — non émis, non signé</p>
          <ReportDocument header={draft.header} sections={draft.sections} printable />
        </div>
      )}
    </div>
  );
}
