import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlarmClock,
  AlertTriangle,
  ArrowUpRight,
  CheckCircle2,
  CheckSquare,
  ClipboardList,
  FileLock2,
  FileText,
  Flag,
  History,
  Info,
  Lightbulb,
  MessageSquare,
  Network,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Siren,
  Square,
  Undo2,
  UserPlus,
  X,
} from "lucide-react";
import { Button, Empty, ErrorBanner, Field, Panel, Pill, Spinner, inputClass } from "../components/ui.jsx";
import { formatCoords, formatDateTime, formatRelative, hasRole } from "../lib/format.js";
import {
  assignIncident,
  commentIncident,
  errorMessage,
  escalateIncident,
  fetchAssignable,
  fetchIncident,
  fetchIncidentMeta,
  fetchIncidents,
  setIncidentAction,
  transitionIncident,
} from "../services/api.js";
import { QUALITY, SEVERITY, STATUS } from "../theme.js";
import DocLink from "../components/DocLink.jsx";
import DecisionPanel from "../components/DecisionPanel.jsx";
import ReportComposer from "../components/ReportComposer.jsx";

const SCOPES = [
  { id: "open", label: "Ouverts" },
  { id: "mine", label: "Mes incidents" },
  { id: "unassigned", label: "Non assignés" },
  { id: "all", label: "Tous" },
];

const KIND_LABELS = {
  dark_vessel: "Radar : navire sans AIS",
  foreign_fishing: "AIS : pêche étrangère",
  watchlist_ais: "AIS : liste INN",
  ais_gap: "AIS : coupure suspecte",
  encounter: "AIS : rendez-vous en mer",
  sanctions: "Navire sous sanctions",
  mpa_fishing: "Pêche en aire protégée",
};

function kindLabel(kind) {
  if (kind?.startsWith("custom:")) return "Règle sur mesure";
  return KIND_LABELS[kind] || kind;
}

/**
 * "Why this alert?": the reasoning recorded when the incident was opened (rule, each
 * condition with the value observed, parameters in force, risk factors) — exactly as decided.
 */
function WhyPanel({ incident, navigate }) {
  const created = [...incident.events].reverse().find((e) => e.kind === "created");
  const why = created?.payload?.explanation;
  const later = incident.events.filter((e) => e.kind === "observation" && e.payload?.explanation).length;
  return (
    <Panel title="Pourquoi cette alerte ?" icon={Lightbulb}>
      {!why && (
        <p className="text-xs text-slate-500">
          Explication non enregistrée : incident ouvert avant l&apos;enregistrement du raisonnement. Voir le score de risque et les observations.
        </p>
      )}
      {why && (
        <div className="space-y-3 text-xs">
          <p className="text-slate-200">
            Règle déclenchée :{" "}
            <button type="button" onClick={() => navigate("rules")} className="text-tactical-cyan hover:underline">
              {why.label}
            </button>
            <span className="text-slate-500"> · décidé le {formatDateTime(why.decided_at)}</span>
          </p>
          {why.summary && <p className="text-slate-400">{why.summary}</p>}
          <ul className="space-y-1">
            {why.checks.map((c) => (
              <li key={c.condition} className="flex gap-2">
                {c.info ? (
                  <Info size={13} className="mt-0.5 shrink-0 text-slate-400" />
                ) : c.ok ? (
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-tactical-green" />
                ) : (
                  <AlertTriangle size={13} className="mt-0.5 shrink-0 text-tactical-amber" />
                )}
                <span className="text-slate-300">
                  {c.condition}
                  {c.observed != null && c.observed !== "" && <span className="text-slate-500"> — observé : </span>}
                  <span className="text-slate-100">{String(c.observed ?? "")}</span>
                  {!c.ok && <span className="text-tactical-amber"> (réserve : à vérifier)</span>}
                </span>
              </li>
            ))}
          </ul>
          {why.risk?.reasons?.length > 0 && (
            <div>
              <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Score de risque {why.risk.score}/100 — facteurs</p>
              <ul className="mt-1 list-inside list-disc space-y-0.5 text-slate-400">
                {why.risk.reasons.map((r) => (
                  <li key={r}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {Object.keys(why.parameters || {}).length > 0 && (
            <p className="text-slate-500">
              Paramètres en vigueur :{" "}
              {Object.entries(why.parameters)
                .map(([k, v]) => `${k} = ${typeof v === "object" ? JSON.stringify(v) : v}`)
                .join(" · ")}
            </p>
          )}
          {later > 0 && <p className="text-slate-500">{later} observation(s) ultérieure(s) avec leur propre explication dans la chronologie.</p>}
        </div>
      )}
    </Panel>
  );
}

const EVENT_LABELS = {
  created: "Incident créé par la détection",
  observation: "Nouvelle observation corrélée",
  severity: "Sévérité relevée",
  status: "Changement de statut",
  assign: "Affectation",
  comment: "Commentaire",
  escalation: "Escalade",
  action: "Playbook",
  sla_breach: "SLA dépassé",
  evidence_export: "Export du dossier de preuves",
  report: "Rapport émis",
  intel: "Renseignement rattaché",
};

/** "P1 30 min · P2 2 h · …" from the configured SLA minutes per severity. */
function slaText(minutes) {
  if (!minutes) return "selon la sévérité";
  const span = (m) => (m < 60 ? `${m} min` : m < 2880 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} j`);
  return Object.entries(minutes).map(([p, m]) => `${p} ${span(m)}`).join(" · ");
}

/** Time between the observation (satellite pass / AIS fix) and the incident opening. */
function detectionDelay(incident) {
  const seen = Date.parse(incident.first_seen);
  const opened = Date.parse(incident.created_at);
  if (Number.isNaN(seen) || Number.isNaN(opened)) return "—";
  const hours = (opened - seen) / 3600000;
  if (hours < 1) return `${Math.max(0, Math.round(hours * 60))} min après l'observation`;
  return `${hours < 48 ? hours.toFixed(1) + " h" : Math.round(hours / 24) + " j"} après l'observation${hours > 6 ? " (image ancienne : situation à revérifier)" : ""}`;
}

function SlaCell({ incident }) {
  const sla = incident.sla || {};
  if (incident.status === "closed") {
    return <span className="text-slate-600">{sla.resolve_breached ? "clos hors SLA" : "clos"}</span>;
  }
  return (
    <span className={`flex items-center gap-1 ${sla.breached ? "text-tactical-red" : "text-slate-400"}`}>
      <AlarmClock size={11} />
      {sla.breached ? "dépassé" : formatRelative(sla.next_due)}
    </span>
  );
}

function describeEvent(event, meta) {
  const p = event.payload || {};
  switch (event.kind) {
    case "status":
      if (p.cancelled) {
        const what = p.cancelled === "escalation" ? "Escalade annulée" : "Intervention annulée";
        const withdrawn = p.withdrawn_escalation
          ? ` · escalade retirée (${meta?.escalation_targets?.[p.withdrawn_escalation.target] || p.withdrawn_escalation.target}${
              p.withdrawn_escalation.reference ? `, réf. ${p.withdrawn_escalation.reference}` : ""
            })`
          : "";
        return `${what} : ${STATUS[p.from]?.label || p.from} → ${STATUS[p.to]?.label || p.to}${withdrawn} — motif : ${p.note}`;
      }
      return `${STATUS[p.from]?.label || p.from} → ${STATUS[p.to]?.label || p.to}${
        p.resolution ? ` (${meta?.resolutions?.[p.resolution] || p.resolution})` : ""
      }${p.note ? ` — ${p.note}` : ""}`;
    case "assign":
      return p.to ? `Assigné à ${p.to}` : "Désassigné";
    case "comment":
      return p.text;
    case "escalation":
      return `${p.label}${p.reference ? ` · réf. ${p.reference}` : ""}${p.note ? ` — ${p.note}` : ""}`;
    case "action":
      return `${p.done ? "✓" : "✗"} ${p.key}`;
    case "observation":
      return `${p.vessel_id} (alerte ${p.alert_id}) à ${p.distance_km} km, risque ${p.risk ?? "—"}`;
    case "severity":
      return `${p.from} → ${p.to}`;
    case "sla_breach":
      return `Échéance de ${p.stage === "triage" ? "triage" : "résolution"} dépassée`;
    case "evidence_export":
      return p.dossier;
    case "intel":
      return `${(p.discipline || "").toUpperCase()} ${p.grading} — ${p.title}`;
    case "report":
      return `${p.ref}${p.addressees ? ` → ${p.addressees}` : ""} · SHA-256 ${String(p.sha256 || "").slice(0, 16)}…`;
    case "created":
      return `${p.vessel_id} (alerte ${p.alert_id}), risque ${p.risk ?? "—"}`;
    default:
      return JSON.stringify(p);
  }
}

/** Cancel an escalation or an intervention: back one step, with a mandatory motive kept in the history. */
function CancelForm({ incident, onDone, onCancel }) {
  const intervention = incident.status === "response";
  const target = intervention ? (incident.escalation_target ? "escalated" : "qualified") : "qualified";
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onDone((await transitionIncident(incident.id, target, { note })).incident);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3 border border-tactical-amber/40 bg-tactical-bg/60 p-3 text-xs">
      <p className="text-slate-300">
        {intervention
          ? `L'intervention est annulée et l'incident revient à « ${STATUS[target].label} »${
              incident.escalation_target ? " : l'escalade auprès de l'autorité est conservée" : ""
            }. L'étape « Suivre l'intervention » du playbook est décochée.`
          : "L'escalade est retirée (autorité et référence effacées du dossier, conservées dans la chronologie) et l'incident revient à « Qualifié ». L'étape « Transmettre à l'autorité » du playbook est décochée."}
      </p>
      <Field label="Motif de l'annulation (obligatoire, versé à la chronologie et à l'audit)">
        <textarea className={inputClass} rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000}
          placeholder={intervention ? "ex. moyen nautique indisponible, météo, navire sorti de la ZEE…" : "ex. navire identifié sous licence, erreur d'autorité…"} />
      </Field>
      <ErrorBanner message={error} />
      <div className="flex gap-2">
        <Button variant="danger" icon={Undo2} busy={busy} onClick={submit} disabled={note.trim().length < 5}>
          {intervention ? "Annuler l'intervention" : "Annuler l'escalade"}
        </Button>
        <Button onClick={onCancel}>Retour</Button>
      </div>
    </div>
  );
}

function CloseForm({ incident, user, meta, onDone, onCancel }) {
  const supervisor = hasRole(user, "supervisor");
  const early = ["new", "triage"].includes(incident.status);
  const options = Object.entries(meta.resolutions).filter(
    ([key]) => supervisor || (early && meta.triage_close_resolutions.includes(key)),
  );
  const [resolution, setResolution] = useState(options[0]?.[0] || "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onDone((await transitionIncident(incident.id, "closed", { resolution, note })).incident);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 border border-tactical-line bg-tactical-bg/60 p-3">
      <Field label="Résolution">
        <select className={inputClass} value={resolution} onChange={(e) => setResolution(e.target.value)}>
          {options.map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Conclusion (versée au dossier)">
        <textarea className={inputClass} rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
      </Field>
      <ErrorBanner message={error} />
      <div className="flex gap-2">
        <Button variant="danger" icon={ShieldCheck} busy={busy} onClick={submit} disabled={!resolution}>
          Clore l&apos;incident
        </Button>
        <Button onClick={onCancel}>Annuler</Button>
      </div>
    </div>
  );
}

function EscalateForm({ incident, meta, onDone, onCancel }) {
  const [target, setTarget] = useState(incident.escalation_target || "cross_ag");
  const [reference, setReference] = useState(incident.escalation_ref || "");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      onDone((await escalateIncident(incident.id, { target, reference, note })).incident);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-3 border border-violet-400/40 bg-violet-400/5 p-3">
      <Field label="Autorité saisie (Action de l'État en Mer)">
        <select className={inputClass} value={target} onChange={(e) => setTarget(e.target.value)}>
          {Object.entries(meta.escalation_targets).map(([key, label]) => (
            <option key={key} value={key}>
              {label}
            </option>
          ))}
        </select>
      </Field>
      <Field label="Référence de transmission (message, n° d'affaire)">
        <input className={inputClass} value={reference} onChange={(e) => setReference(e.target.value)} maxLength={120} />
      </Field>
      <Field label="Éléments transmis">
        <textarea className={inputClass} rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={2000} />
      </Field>
      <ErrorBanner message={error} />
      <div className="flex gap-2">
        <Button variant="primary" icon={Send} busy={busy} onClick={submit}>
          Escalader
        </Button>
        <Button onClick={onCancel}>Annuler</Button>
      </div>
    </div>
  );
}

function IncidentDetail({ id, user, meta, assignable, navigate, onChanged }) {
  const [incident, setIncident] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [form, setForm] = useState(null);
  const [comment, setComment] = useState("");
  const analyst = hasRole(user, "analyst");
  const supervisor = hasRole(user, "supervisor");

  const load = useCallback(() => {
    fetchIncident(id)
      .then((data) => {
        setIncident(data.incident);
        setError("");
      })
      .catch((err) => setError(errorMessage(err, "Incident introuvable")));
  }, [id]);

  useEffect(() => {
    setIncident(null);
    setForm(null);
    load();
  }, [load]);

  const apply = (updated) => {
    setIncident(updated);
    setForm(null);
    onChanged();
  };

  const run = async (label, action) => {
    setBusy(label);
    setError("");
    try {
      apply((await action()).incident);
      return true;
    } catch (err) {
      setError(errorMessage(err));
      return false;
    } finally {
      setBusy("");
    }
  };

  if (error && !incident) return <ErrorBanner message={error} />;
  if (!incident) return <Spinner />;

  const s = incident.status;
  const done = incident.actions.filter((a) => a.done).length;

  return (
    <div className="space-y-4">
      <div className="border border-tactical-line bg-tactical-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <p className="flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-slate-500">
              <Siren size={12} className="text-tactical-cyan" /> {incident.ref}
            </p>
            <h2 className="mt-1 text-base text-white">{incident.title}</h2>
            <p className="mt-1 text-xs text-slate-500">
              {kindLabel(incident.kind)} · {formatCoords(incident.lat, incident.lon)} · ouvert{" "}
              {formatRelative(incident.created_at)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Pill color={SEVERITY[incident.severity].color} solid>
              {SEVERITY[incident.severity].label}
            </Pill>
            <Pill color={STATUS[s].color}>{STATUS[s].label}</Pill>
            <SlaCell incident={incident} />
          </div>
        </div>

        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-tactical-line pt-3">
          {analyst && s === "new" && (
            <Button variant="primary" icon={CheckSquare} busy={busy === "triage"} onClick={() => run("triage", () => transitionIncident(incident.id, "triage"))}>
              Prendre en charge
            </Button>
          )}
          {analyst && s === "triage" && (
            <Button variant="primary" icon={Flag} busy={busy === "qualify"} onClick={() => run("qualify", () => transitionIncident(incident.id, "qualified"))}>
              Qualifier (navire suspect)
            </Button>
          )}
          {supervisor && ["qualified", "escalated", "response"].includes(s) && (
            <Button icon={ArrowUpRight} onClick={() => setForm(form === "escalate" ? null : "escalate")}>
              {incident.escalation_target ? "Mettre à jour l'escalade" : "Escalader"}
            </Button>
          )}
          {supervisor && ["qualified", "escalated"].includes(s) && (
            <Button icon={Siren} busy={busy === "response"} onClick={() => run("response", () => transitionIncident(incident.id, "response"))}>
              Intervention engagée
            </Button>
          )}
          {supervisor && s === "escalated" && (
            <Button icon={Undo2} onClick={() => setForm(form === "cancel" ? null : "cancel")}>
              Annuler l&apos;escalade
            </Button>
          )}
          {supervisor && s === "response" && (
            <Button icon={Undo2} onClick={() => setForm(form === "cancel" ? null : "cancel")}>
              Annuler l&apos;intervention
            </Button>
          )}
          {s !== "closed" && (supervisor || (analyst && ["new", "triage"].includes(s))) && (
            <Button variant="danger" icon={ShieldCheck} onClick={() => setForm(form === "close" ? null : "close")}>
              Clore
            </Button>
          )}
          {analyst && (
            <Button icon={FileText} onClick={() => setForm(form === "report" ? null : "report")} title="Compte rendu signé pour la chaîne de commandement">
              Compte rendu
            </Button>
          )}
          {supervisor && s === "closed" && (
            <Button icon={RefreshCw} busy={busy === "reopen"} onClick={() => run("reopen", () => transitionIncident(incident.id, "triage", { note: "Réouverture" }))}>
              Rouvrir
            </Button>
          )}
          <span className="ml-auto flex items-center gap-2 text-xs text-slate-400">
            <UserPlus size={13} />
            {supervisor ? (
              <select
                className="border border-tactical-line bg-tactical-bg px-2 py-1 text-xs text-slate-200"
                value={incident.assignee_id || ""}
                onChange={(e) => run("assign", () => assignIncident(incident.id, e.target.value ? Number(e.target.value) : null))}
                aria-label="Assigner"
              >
                <option value="">Non assigné</option>
                {assignable.map((u) => (
                  <option key={u.id} value={u.id}>
                    {u.display_name || u.username}
                  </option>
                ))}
              </select>
            ) : (
              <>
                {incident.assignee_name || incident.assignee || "Non assigné"}
                {analyst && incident.assignee_id !== user.id && s !== "closed" && (
                  <Button busy={busy === "self"} onClick={() => run("self", () => assignIncident(incident.id, user.id))}>
                    Me l&apos;attribuer
                  </Button>
                )}
              </>
            )}
          </span>
        </div>
        {form === "close" && <div className="mt-3"><CloseForm incident={incident} user={user} meta={meta} onDone={apply} onCancel={() => setForm(null)} /></div>}
        {form === "cancel" && <div className="mt-3"><CancelForm incident={incident} onDone={apply} onCancel={() => setForm(null)} /></div>}
        {form === "escalate" && <div className="mt-3"><EscalateForm incident={incident} meta={meta} onDone={apply} onCancel={() => setForm(null)} /></div>}
        {form === "report" && (
          <div className="mt-3 border-t border-tactical-line pt-3">
            <ReportComposer kind="incident" incidentId={incident.id} user={user} onIssued={() => { load(); onChanged(); }} />
          </div>
        )}
        <div className="mt-3">
          <ErrorBanner message={error} />
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <Panel title="Synthèse" icon={ClipboardList}>
          <dl className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <dt className="text-slate-500" title="Score de risque INN de 0 à 100 : zone, comportement, taille, contexte. Il fixe la sévérité : ≥ 75 → P1, ≥ 55 → P2, ≥ 35 → P3, sinon P4.">
                Risque <DocLink page="moteur/risque/" label="calcul du score de risque" />
              </dt>
              <dd>{incident.risk_score ?? "—"}/100</dd>
            </div>
            <div>
              <dt className="text-slate-500" title="Indice de fiabilité : la cible radar est-elle vraiment un navire ? ≥ 70 % fiable, 45 à 70 % à vérifier, < 45 % douteuse. Vaut 100 % pour une détection AIS (règle 24/7).">
                Fiabilité détection <DocLink page="moteur/fiabilite/" label="indice de fiabilité" />
              </dt>
              <dd style={{ color: incident.quality_score >= 0.7 ? QUALITY.reliable.color : incident.quality_score >= 0.45 ? QUALITY.check.color : QUALITY.doubtful.color }}>
                {incident.quality_score != null ? `${Math.round(incident.quality_score * 100)} %` : "—"}
              </dd>
            </div>
            <div><dt className="text-slate-500">Première détection</dt><dd>{formatDateTime(incident.first_seen)}</dd></div>
            <div><dt className="text-slate-500">Dernière détection</dt><dd>{formatDateTime(incident.last_seen)}</dd></div>
            <div className="col-span-2">
              <dt className="text-slate-500">Délai de détection</dt>
              <dd title="Temps écoulé entre la première détection (passage satellite ou signal AIS) et l'ouverture de l'incident">
                {detectionDelay(incident)}
              </dd>
            </div>
            <div>
              <dt className="text-slate-500" title={`Heure limite pour prendre l'incident en charge (SLA de triage : ${slaText(meta?.sla?.triage_minutes)})`}>Échéance triage</dt>
              <dd>{formatDateTime(incident.triage_due)}</dd>
            </div>
            <div>
              <dt className="text-slate-500" title={`Heure limite pour clore l'incident (SLA de résolution : ${slaText(meta?.sla?.resolve_minutes)})`}>Échéance résolution</dt>
              <dd>{formatDateTime(incident.resolve_due)}</dd>
            </div>
            {incident.escalation_label && (
              <div className="col-span-2">
                <dt className="text-slate-500">Escaladé à</dt>
                <dd className="text-violet-300">
                  {incident.escalation_label}
                  {incident.escalation_ref && ` · réf. ${incident.escalation_ref}`} · {formatDateTime(incident.escalated_at)}
                </dd>
              </div>
            )}
            {incident.resolution_label && (
              <div className="col-span-2">
                <dt className="text-slate-500">Résolution</dt>
                <dd>{incident.resolution_label}{incident.resolution_note && ` — ${incident.resolution_note}`}</dd>
              </div>
            )}
          </dl>
          <div className="mt-3 flex flex-wrap gap-2">
            <Button icon={ArrowUpRight} onClick={() => navigate("situation", `incident:${incident.id}`)} title="Ouvrir la carte centrée sur la dernière détection radar de cet incident">
              Voir la situation tactique
            </Button>
            <Button icon={Network} onClick={() => navigate("graph", `incident:${incident.id}`)}>
              Explorer les liens
            </Button>
          </div>
        </Panel>

        <Panel title={`Playbook · ${done}/${incident.actions.length}`} icon={CheckSquare}>
          <p className="mb-2 text-[11px] text-slate-500">
            Vérifications à mener pour qualifier l&apos;incident ; chaque case cochée est tracée dans la chronologie.{" "}
            <DocLink page="incidents/dossier/" label="playbook du dossier d'incident" />
          </p>
          <ul className="space-y-1.5 text-xs">
            {incident.actions.map((action) => (
              <li key={action.key}>
                <button
                  type="button"
                  disabled={!analyst || s === "closed" || Boolean(busy)}
                  onClick={() => run(`action:${action.key}`, () => setIncidentAction(incident.id, action.key, !action.done))}
                  className="flex w-full items-start gap-2 text-left disabled:cursor-default"
                >
                  {action.done ? <CheckSquare size={14} className="mt-0.5 shrink-0 text-tactical-green" /> : <Square size={14} className="mt-0.5 shrink-0 text-slate-500" />}
                  <span className={action.done ? "text-slate-500 line-through" : "text-slate-200"}>{action.label}</span>
                  {action.done && <span className="ml-auto shrink-0 text-[10px] text-slate-600">{action.done_by}</span>}
                </button>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      {s !== "closed" && <DecisionPanel incidentId={incident.id} refreshKey={incident.last_seen} canScan={analyst} />}

      <WhyPanel incident={incident} navigate={navigate} />

      <Panel title={`Observations · ${incident.observations.length}`} icon={FileLock2}>
        <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {incident.observations.map((o) => (
            <li key={o.id} className="flex gap-3 border border-tactical-line p-2 text-xs">
              {o.dossier ? (
                <img
                  src={`/api/v1/evidence/files/${o.dossier}/chip_${o.vessel_id}.jpg`}
                  alt={`Vignette SAR ${o.vessel_id}`}
                  className="h-20 w-20 shrink-0 border border-tactical-line object-cover"
                  onError={(e) => {
                    e.currentTarget.style.visibility = "hidden";
                  }}
                />
              ) : (
                <div className="flex h-20 w-20 shrink-0 items-center justify-center border border-dashed border-tactical-line text-center text-[10px] text-slate-500">
                  Détection AIS
                </div>
              )}
              <div className="min-w-0 space-y-1">
                <p className="text-slate-200">{o.vessel_id} · risque {o.risk_score ?? "—"}</p>
                <p className="text-slate-500">{formatDateTime(o.seen_at)}</p>
                <p className="text-slate-500">{formatCoords(o.lat, o.lon)}</p>
                {o.dossier && (
                  <button type="button" onClick={() => navigate("evidence", o.dossier)} className="text-tactical-cyan underline">
                    Dossier de preuves
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <Panel title="Chronologie" icon={History}>
        {analyst && s !== "closed" && (
          <form
            className="mb-4 flex gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (!comment.trim()) return;
              run("comment", () => commentIncident(incident.id, comment.trim())).then((ok) => ok && setComment(""));
            }}
          >
            <input className={inputClass} value={comment} onChange={(e) => setComment(e.target.value)} placeholder="Ajouter une note d'analyse…" maxLength={4000} />
            <Button type="submit" variant="primary" icon={MessageSquare} busy={busy === "comment"} disabled={!comment.trim()}>
              Publier
            </Button>
          </form>
        )}
        <ol className="space-y-3 border-l border-tactical-line pl-4 text-xs">
          {incident.events.map((event) => (
            <li key={event.id} className="relative">
              <span className="absolute -left-[21px] top-1 h-2 w-2 rounded-full bg-tactical-cyan" />
              <p className="text-slate-500">
                {formatDateTime(event.at)} · <span className="text-slate-300">{event.actor}</span> · {EVENT_LABELS[event.kind] || event.kind}
              </p>
              <p className={`whitespace-pre-wrap ${event.kind === "comment" ? "text-slate-100" : "text-slate-400"}`}>{describeEvent(event, meta)}</p>
            </li>
          ))}
        </ol>
      </Panel>
    </div>
  );
}

export default function IncidentsView({ user, selectedId, navigate, onChange }) {
  const [scope, setScope] = useState(hasRole(user, "analyst") ? "open" : "all");
  const [severity, setSeverity] = useState("");
  // Chronological by opening date: newest first by default; oldest first or by priority on demand.
  const [sort, setSort] = useState(() => {
    try {
      return window.localStorage.getItem("kraken.incidents.sort") || "recent";
    } catch {
      return "recent";
    }
  });
  useEffect(() => {
    try {
      window.localStorage.setItem("kraken.incidents.sort", sort);
    } catch {
      /* preference simply not remembered */
    }
  }, [sort]);
  const [search, setSearch] = useState("");
  const [items, setItems] = useState(null);
  const [meta, setMeta] = useState(null);
  const [assignable, setAssignable] = useState([]);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchIncidents({ scope, severity: severity || undefined, q: search || undefined, sort })
      .then((data) => {
        setItems(data.incidents);
        setError("");
      })
      .catch((err) => setError(errorMessage(err, "Impossible de charger les incidents")));
  }, [scope, severity, search, sort]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 30000);
    return () => clearInterval(timer);
  }, [load]);

  useEffect(() => {
    fetchIncidentMeta().then(setMeta).catch(() => setMeta(null));
    if (hasRole(user, "analyst")) fetchAssignable().then((d) => setAssignable(d.users)).catch(() => setAssignable([]));
  }, [user]);

  const onChanged = useCallback(() => {
    load();
    onChange?.();
  }, [load, onChange]);

  const selected = selectedId ? Number(selectedId) : null;
  const rows = useMemo(() => items || [], [items]);

  return (
    <div className="flex h-full min-h-0">
      <section className={`flex min-h-0 flex-col border-r border-tactical-line ${selected ? "hidden w-[34rem] shrink-0 xl:flex" : "flex-1"}`}>
        <div className="flex flex-wrap items-center gap-2 border-b border-tactical-line bg-tactical-panel/60 p-3 text-xs">
          <DocLink page="incidents/cycle-de-vie/" label="cycle de vie, sévérités, SLA" />
          <div className="flex border border-tactical-line">
            {SCOPES.map((s) => (
              <button
                key={s.id}
                type="button"
                onClick={() => setScope(s.id)}
                className={`px-3 py-1.5 ${scope === s.id ? "bg-tactical-cyan/15 text-tactical-cyan" : "text-slate-400 hover:text-white"}`}
              >
                {s.label}
              </button>
            ))}
          </div>
          <select className="border border-tactical-line bg-tactical-bg px-2 py-1.5 text-slate-300" value={severity} onChange={(e) => setSeverity(e.target.value)} aria-label="Sévérité">
            <option value="">Toutes sévérités</option>
            {Object.entries(SEVERITY).map(([key, s]) => (
              <option key={key} value={key}>
                {s.label}
              </option>
            ))}
          </select>
          <select className="border border-tactical-line bg-tactical-bg px-2 py-1.5 text-slate-300" value={sort} onChange={(e) => setSort(e.target.value)} aria-label="Ordre">
            <option value="recent">Plus récents d&apos;abord</option>
            <option value="oldest">Plus anciens d&apos;abord</option>
            <option value="priority">Par priorité (sévérité)</option>
          </select>
          <label className="flex min-w-[10rem] flex-1 items-center gap-2 border border-tactical-line bg-tactical-bg px-2">
            <Search size={13} className="text-slate-500" />
            <input className="w-full bg-transparent py-1.5 text-slate-200 focus:outline-none" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Réf. ou titre" maxLength={64} />
          </label>
        </div>
        <ErrorBanner message={error} />
        <div className="min-h-0 flex-1 overflow-auto">
          {items === null ? (
            <Spinner />
          ) : rows.length === 0 ? (
            <Empty>Aucun incident dans cette vue.</Empty>
          ) : (
            <table className="w-full text-left text-xs">
              <thead className="sticky top-0 bg-tactical-panel text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="px-3 py-2" title="Sévérité P1 (critique) à P4 (faible), déduite du score de risque">Sév.</th>
                  <th className="px-3 py-2">Ouvert le</th>
                  <th className="px-3 py-2">Incident</th>
                  <th className="px-3 py-2">Statut</th>
                  <th className="px-3 py-2">Assigné</th>
                  <th className="px-3 py-2" title="Prochaine échéance (triage puis résolution) fixée selon la sévérité">SLA</th>
                  <th className="px-3 py-2">Playbook</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((incident) => (
                  <tr
                    key={incident.id}
                    onClick={() => navigate("incidents", incident.id)}
                    className={`cursor-pointer border-t border-tactical-line ${selected === incident.id ? "bg-tactical-cyan/10" : "hover:bg-white/5"}`}
                  >
                    <td className="px-3 py-2"><Pill color={SEVERITY[incident.severity].color} solid>{incident.severity}</Pill></td>
                    <td className="whitespace-nowrap px-3 py-2 text-slate-400">{formatDateTime(incident.created_at)}</td>
                    <td className="px-3 py-2">
                      <p className="text-slate-100">{incident.ref}</p>
                      <p className="max-w-[16rem] truncate text-slate-500">{incident.title}</p>
                      <p className="text-[10px] uppercase tracking-wider text-slate-600">{kindLabel(incident.kind)}</p>
                    </td>
                    <td className="px-3 py-2"><Pill color={STATUS[incident.status].color}>{STATUS[incident.status].label}</Pill></td>
                    <td className="px-3 py-2 text-slate-400">{incident.assignee_name || incident.assignee || "—"}</td>
                    <td className="px-3 py-2"><SlaCell incident={incident} /></td>
                    <td className="px-3 py-2 text-slate-500">{incident.actions_done}/{incident.actions_total}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
      {selected && (
        <section className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
          <div className="mb-3 flex justify-end">
            <Button icon={X} onClick={() => navigate("incidents")}>
              Fermer
            </Button>
          </div>
          {meta ? (
            <IncidentDetail id={selected} user={user} meta={meta} assignable={assignable} navigate={navigate} onChanged={onChanged} />
          ) : (
            <Spinner />
          )}
        </section>
      )}
    </div>
  );
}
