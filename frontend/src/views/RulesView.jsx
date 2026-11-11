import { useCallback, useEffect, useMemo, useState } from "react";
import {
  BookOpen,
  CheckCircle2,
  CircleSlash,
  FlaskConical,
  Pencil,
  Plus,
  Power,
  Save,
  Trash2,
  Wand2,
  X,
  XCircle,
} from "lucide-react";
import DocLink from "../components/DocLink.jsx";
import { Button, Empty, ErrorBanner, Field, Panel, Pill, Spinner, inputClass } from "../components/ui.jsx";
import { formatDateTime, hasRole } from "../lib/format.js";
import { createRule, deleteRule, errorMessage, fetchRules, previewRule, toggleBuiltinRule, updateRule } from "../services/api.js";
import { SEVERITY } from "../theme.js";

const FAMILIES = {
  Radar: "#818cf8",
  Présence: "#38bdf8",
  Comportement: "#f59e0b",
  Renseignement: "#f472b6",
  "Sur mesure": "#22d3ee",
};

function severityFor(risk) {
  if (risk >= 75) return "P1";
  if (risk >= 55) return "P2";
  if (risk >= 35) return "P3";
  return "P4";
}

function Performance({ performance }) {
  if (!performance) return null;
  const rate = performance.false_alarm_rate;
  return (
    <p className="text-[11px] text-slate-400">
      <span className="text-slate-500">Bilan 30 j :</span> {performance.opened} incident(s) ouvert(s)
      {performance.closed > 0 && (
        <>
          {" · "}
          {performance.closed} clos dont{" "}
          <span style={{ color: rate > 0.5 ? "#ff3b3b" : rate > 0.25 ? "#f5c542" : "#3dff8a" }}>
            {Math.round((rate || 0) * 100)} % de fausses alarmes
          </span>
        </>
      )}
      {performance.last_triggered && <> · dernier déclenchement {formatDateTime(performance.last_triggered)}</>}
    </p>
  );
}

function StateBadge({ rule }) {
  if (!rule.enabled) return <Pill color="#64748b">désactivée</Pill>;
  if (rule.ready === false) return <Pill color="#f5c542" title={`Source manquante : ${rule.missing_sources.join(" ; ")}`}>en attente de source</Pill>;
  return <Pill color="#3dff8a">active</Pill>;
}

function BuiltinRule({ rule, canEdit, onToggle, isAdmin, navigate }) {
  const [open, setOpen] = useState(false);
  return (
    <article className="border border-tactical-line bg-tactical-panel">
      <header className="flex flex-wrap items-start justify-between gap-2 px-4 py-3">
        <div className="min-w-0 space-y-1">
          <p className="flex flex-wrap items-center gap-2 text-sm text-slate-100">
            {rule.label}
            <Pill color={FAMILIES[rule.family]}>{rule.family}</Pill>
            <StateBadge rule={rule} />
            <DocLink page={rule.doc} label={rule.label} />
          </p>
          <p className="text-xs text-slate-300">{rule.summary}</p>
          <Performance performance={rule.performance} />
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button icon={BookOpen} onClick={() => setOpen((o) => !o)}>
            {open ? "Masquer" : "Comprendre"}
          </Button>
          {canEdit && rule.can_disable && (
            <Button icon={Power} onClick={() => onToggle(rule)} title={rule.enabled ? "Désactiver la règle" : "Réactiver la règle"}>
              {rule.enabled ? "Désactiver" : "Activer"}
            </Button>
          )}
        </div>
      </header>
      {open && (
        <div className="grid gap-4 border-t border-tactical-line px-4 py-3 text-xs lg:grid-cols-2">
          <section className="space-y-2">
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Pourquoi cette règle</p>
            <p className="text-slate-300">{rule.why}</p>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Se déclenche quand</p>
            <ul className="space-y-1">
              {rule.conditions.map((c) => (
                <li key={c} className="flex gap-2 text-slate-300">
                  <CheckCircle2 size={13} className="mt-0.5 shrink-0 text-tactical-cyan" /> {c}
                </li>
              ))}
            </ul>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Effet</p>
            <p className="text-slate-300">{rule.outcome}</p>
            <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Limites connues</p>
            <p className="text-slate-400">{rule.limits}</p>
            {rule.missing_sources?.length > 0 && (
              <p className="text-tactical-amber">
                Inactive faute de source : {rule.missing_sources.join(" ; ")} (Administration › Clés &amp; intégrations).
              </p>
            )}
          </section>
          <section className="space-y-2">
            <p className="flex items-center justify-between text-[10px] uppercase tracking-[0.2em] text-slate-500">
              Paramètres de réglage
              {isAdmin && rule.parameters.length > 0 && (
                <button type="button" onClick={() => navigate("admin")} className="normal-case tracking-normal text-tactical-cyan hover:underline">
                  modifier dans Administration › Paramètres
                </button>
              )}
            </p>
            {rule.parameters.length === 0 && <p className="text-slate-500">Aucun paramètre réglable : seuils fixes décrits ci-contre.</p>}
            <ul className="space-y-2">
              {rule.parameters.map((p) => (
                <li key={p.key} className="border border-tactical-line p-2">
                  <p className="flex items-center justify-between gap-2">
                    <span className="text-slate-200">{p.label}</span>
                    <span className="shrink-0 text-tactical-cyan">
                      {String(p.value)} {p.unit}
                      {p.value !== p.default && <span className="text-slate-500"> (défaut {String(p.default)})</span>}
                    </span>
                  </p>
                  <p className="mt-1 text-slate-400">{p.what}</p>
                  <p className="mt-1 text-slate-500">
                    ▲ {p.higher} <br />▼ {p.lower}
                  </p>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </article>
  );
}

// --------------------------------------------------------------------------- builder

function defaultValue(field) {
  switch (field.kind) {
    case "bool":
      return true;
    case "number":
      return field.min ?? 0;
    case "choice":
    case "zone":
      return [];
    case "text":
      return "";
    case "circle":
      return { lat: 5.0, lon: -52.5, radius_km: 10 };
    default:
      return null;
  }
}

function ConditionRow({ condition, fields, onChange, onRemove }) {
  const field = fields.find((f) => f.id === condition.field) || fields[0];
  const options = field.options || [];
  const set = (patch) => onChange({ ...condition, ...patch });
  return (
    <div className="space-y-1.5 border border-tactical-line p-2">
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={field.id}
          onChange={(e) => {
            const next = fields.find((f) => f.id === e.target.value);
            onChange({ field: next.id, op: next.operators[0][0], value: defaultValue(next) });
          }}
          className={`${inputClass} w-auto min-w-0 flex-1 basis-full sm:min-w-[12rem] sm:basis-auto`}
          aria-label="Critère"
        >
          {fields.map((f) => (
            <option key={f.id} value={f.id}>
              {f.label}
            </option>
          ))}
        </select>
        {field.kind !== "bool" && (
          <select value={condition.op} onChange={(e) => set({ op: e.target.value })} className={`${inputClass} w-auto`} aria-label="Opérateur">
            {field.operators.map(([id, label]) => (
              <option key={id} value={id}>
                {label}
              </option>
            ))}
          </select>
        )}
        {field.kind === "bool" && (
          <select value={condition.value ? "yes" : "no"} onChange={(e) => set({ value: e.target.value === "yes" })} className={`${inputClass} w-auto`} aria-label="Valeur">
            <option value="yes">oui</option>
            <option value="no">non</option>
          </select>
        )}
        {field.kind === "number" && (
          <span className="flex items-center gap-1">
            <input
              type="number"
              min={field.min}
              max={field.max}
              step="0.5"
              value={condition.value}
              onChange={(e) => set({ value: e.target.value === "" ? "" : Number(e.target.value) })}
              className={`${inputClass} w-24`}
              aria-label={field.label}
            />
            <span className="text-slate-500">{field.unit}</span>
          </span>
        )}
        {field.kind === "text" && (
          <input value={condition.value} onChange={(e) => set({ value: e.target.value })} className={`${inputClass} w-full sm:w-48`} placeholder="texte" aria-label={field.label} />
        )}
        <button type="button" onClick={onRemove} className="ml-auto text-slate-500 hover:text-tactical-red" aria-label="Retirer la condition">
          <X size={14} />
        </button>
      </div>
      {field.kind === "zone" && options.length === 0 && (
        <p className="text-tactical-amber">Aucune zone dessinée : Situation › Couches › Zones de surveillance › Dessiner une zone.</p>
      )}
      {(field.kind === "choice" || field.kind === "zone") && (
        <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto">
          {options.map(([id, label]) => {
            const active = condition.value.includes(id);
            return (
              <button
                key={id}
                type="button"
                onClick={() => set({ value: active ? condition.value.filter((v) => v !== id) : [...condition.value, id] })}
                className={`border px-1.5 py-0.5 text-[11px] ${active ? "border-tactical-cyan/60 bg-tactical-cyan/15 text-tactical-cyan" : "border-tactical-line text-slate-400 hover:text-white"}`}
                aria-pressed={active}
              >
                {label}
              </button>
            );
          })}
        </div>
      )}
      {field.kind === "circle" && (
        <div className="flex flex-wrap items-center gap-2">
          {[
            ["lat", "Latitude", 0.01],
            ["lon", "Longitude", 0.01],
            ["radius_km", "Rayon (km)", 0.5],
          ].map(([key, label, step]) => (
            <label key={key} className="flex items-center gap-1 text-slate-500">
              {label}
              <input
                type="number"
                step={step}
                value={condition.value[key]}
                onChange={(e) => set({ value: { ...condition.value, [key]: Number(e.target.value) } })}
                className={`${inputClass} w-24`}
              />
            </label>
          ))}
          <span className="text-[10px] text-slate-500">Astuce : clic droit sur la carte → coordonnées du point.</span>
        </div>
      )}
      <p className="text-[10px] text-slate-500">{field.help}</p>
    </div>
  );
}

function PreviewResult({ result }) {
  if (!result) return null;
  return (
    <div className="space-y-2 border border-tactical-cyan/30 bg-tactical-cyan/5 p-3 text-xs">
      <p className="text-slate-200">
        <strong>{result.match_count}</strong> navire(s) détecté(s) sur {result.evaluated} évalué(s){" "}
        {result.mode === "replay" ? `au ${result.at.slice(0, 16).replace("T", " ")} Z (rejeu)` : "maintenant"}.
      </p>
      {result.matches.slice(0, 8).map((m) => (
        <details key={m.mmsi} className="border border-tactical-line p-2">
          <summary className="cursor-pointer text-slate-200">
            {m.name || "sans nom"} · MMSI {m.mmsi} · {m.flag || "pavillon inconnu"}
          </summary>
          <ul className="mt-1 space-y-0.5">
            {m.checks.map((c) => (
              <li key={c.condition} className="flex gap-2 text-slate-400">
                <CheckCircle2 size={12} className="mt-0.5 shrink-0 text-tactical-green" /> {c.condition} — <span className="text-slate-200">{c.observed}</span>
              </li>
            ))}
          </ul>
        </details>
      ))}
      {result.near_misses?.length > 0 && (
        <div>
          <p className="text-slate-500">Presque détectés (écartés par la dernière condition) :</p>
          <ul className="mt-1 space-y-0.5">
            {result.near_misses.slice(0, 5).map((m) => {
              const failed = m.checks[m.checks.length - 1];
              return (
                <li key={m.mmsi} className="flex gap-2 text-slate-400">
                  <XCircle size={12} className="mt-0.5 shrink-0 text-tactical-amber" />
                  {m.name || m.mmsi} : {failed.condition} — observé <span className="text-slate-200">{failed.observed}</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}

const EMPTY_RULE = {
  name: "",
  description: "",
  enabled: true,
  definition: { groups: [[]], risk: 60, channel: "alertes", cooldown_hours: 24 },
};

/** Groups of a definition ("or" between groups, "and" inside); legacy rules have a single "conditions" list. */
function normalise(definition) {
  const groups = definition.groups || [definition.conditions || []];
  const { conditions: _legacy, ...rest } = definition;
  return { ...rest, groups: groups.length ? groups : [[]] };
}

const TEMPLATES = [
  {
    label: "Pêcheur étranger lent près des côtes",
    rule: {
      name: "Pêcheur étranger lent en mer territoriale",
      description: "Pêche probable d'un navire étranger dans les 12 milles.",
      conditions: [
        { field: "fishing", op: "is", value: true },
        { field: "foreign", op: "is", value: true },
        { field: "licensed", op: "is", value: false },
        { field: "speed", op: "lt", value: 5 },
        { field: "zone", op: "in", value: ["territorial"] },
      ],
      risk: 80,
    },
  },
  {
    label: "Navire dans une zone sensible (cercle)",
    rule: {
      name: "Présence dans la zone de l'estuaire",
      description: "Tout navire non autorisé dans un cercle à surveiller.",
      conditions: [
        { field: "area", op: "inside", value: { lat: 5.75, lon: -54.0, radius_km: 15 } },
        { field: "licensed", op: "is", value: false },
      ],
      risk: 60,
    },
  },
  {
    label: "Silence AIS prolongé d'un pêcheur",
    rule: {
      name: "Pêcheur silencieux depuis 6 h",
      description: "Navire de pêche qui n'émet plus depuis longtemps dans la ZEE.",
      conditions: [
        { field: "fishing", op: "is", value: true },
        { field: "silent_hours", op: "gt", value: 6 },
        { field: "zone", op: "in", value: ["territorial", "contiguous", "eez"] },
      ],
      risk: 65,
    },
  },
];

function RuleBuilder({ builder, initial, onSaved, onCancel }) {
  const [rule, setRule] = useState(() => (initial ? { ...initial, definition: normalise(initial.definition) } : EMPTY_RULE));
  const [preview, setPreview] = useState(null);
  const [replayAt, setReplayAt] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const definition = rule.definition;
  const fields = builder.fields;
  const setDefinition = (patch) => setRule((r) => ({ ...r, definition: { ...r.definition, ...patch } }));
  const severity = severityFor(definition.risk);
  const conditionCount = definition.groups.reduce((n, g) => n + g.length, 0);
  // Empty groups are dropped before testing / saving (a group needs at least one condition).
  const payload = { ...definition, groups: definition.groups.filter((g) => g.length) };
  const setGroup = (index, conditions) =>
    setDefinition({ groups: definition.groups.map((g, i) => (i === index ? conditions : g)) });
  const newCondition = () => ({ field: fields[0].id, op: fields[0].operators[0][0], value: defaultValue(fields[0]) });

  const applyTemplate = (template) => {
    setRule({
      ...EMPTY_RULE,
      name: template.rule.name,
      description: template.rule.description,
      definition: { ...EMPTY_RULE.definition, groups: [template.rule.conditions], risk: template.rule.risk },
    });
    setPreview(null);
  };

  const test = async () => {
    setBusy("test");
    setError("");
    try {
      setPreview(await previewRule(payload, replayAt ? new Date(`${replayAt}:00Z`).toISOString() : null));
    } catch (err) {
      setPreview(null);
      setError(errorMessage(err, "Test impossible"));
    } finally {
      setBusy("");
    }
  };

  const save = async () => {
    setBusy("save");
    setError("");
    try {
      const body = { ...rule, definition: payload };
      const saved = initial?.id ? await updateRule(initial.id, body) : await createRule(body);
      onSaved(saved);
    } catch (err) {
      setError(errorMessage(err, "Enregistrement impossible"));
    } finally {
      setBusy("");
    }
  };

  return (
    <Panel title={initial?.id ? `Modifier « ${initial.name} »` : "Nouvelle règle sur mesure"} icon={Wand2}
      actions={<Button icon={X} onClick={onCancel}>Fermer</Button>}>
      <div className="space-y-4 text-xs">
        {!initial?.id && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-slate-500">Partir d&apos;un modèle :</span>
            {TEMPLATES.map((t) => (
              <Button key={t.label} onClick={() => applyTemplate(t)}>
                {t.label}
              </Button>
            ))}
          </div>
        )}
        <div className="grid gap-3 md:grid-cols-2">
          <Field label="Nom de la règle">
            <input className={inputClass} value={rule.name} maxLength={80} onChange={(e) => setRule({ ...rule, name: e.target.value })} placeholder="ex. Pêcheur étranger dans l'estuaire" />
          </Field>
          <Field label="Description (pour les opérateurs)">
            <input className={inputClass} value={rule.description} maxLength={1000} onChange={(e) => setRule({ ...rule, description: e.target.value })} placeholder="Ce que la règle cherche et pourquoi" />
          </Field>
        </div>

        <section className="space-y-3">
          <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">
            Conditions — dans un groupe, toutes doivent être remplies (« et ») ; il suffit qu&apos;un groupe le soit (« ou »)
          </p>
          {conditionCount === 0 && <p className="text-slate-500">Ajoutez une première condition, ou partez d&apos;un modèle.</p>}
          {definition.groups.map((group, g) => (
            <div key={g} className="space-y-2">
              {g > 0 && (
                <p className="flex items-center gap-2 text-[11px] font-semibold uppercase tracking-[0.2em] text-tactical-amber">
                  <span className="h-px flex-1 bg-tactical-amber/40" /> ou <span className="h-px flex-1 bg-tactical-amber/40" />
                </p>
              )}
              <div className={`space-y-2 ${definition.groups.length > 1 ? "border-l-2 border-tactical-cyan/40 pl-3" : ""}`}>
                {definition.groups.length > 1 && (
                  <p className="flex items-center justify-between text-[10px] uppercase tracking-wider text-slate-500">
                    Groupe {g + 1} · {group.length}/{builder.limits.conditions}
                    <button type="button" className="normal-case tracking-normal text-slate-500 hover:text-tactical-red"
                      onClick={() => setDefinition({ groups: definition.groups.filter((_x, i) => i !== g) })}>
                      retirer le groupe
                    </button>
                  </p>
                )}
                {group.map((condition, index) => (
                  <ConditionRow
                    key={index}
                    condition={condition}
                    fields={fields}
                    onChange={(next) => setGroup(g, group.map((c, i) => (i === index ? next : c)))}
                    onRemove={() => setGroup(g, group.filter((_c, i) => i !== index))}
                  />
                ))}
                <Button icon={Plus} disabled={group.length >= builder.limits.conditions} onClick={() => setGroup(g, [...group, newCondition()])}>
                  Ajouter une condition {definition.groups.length > 1 ? "(et)" : ""}
                </Button>
              </div>
            </div>
          ))}
          <Button
            icon={Plus}
            disabled={definition.groups.length >= builder.limits.groups || !definition.groups[definition.groups.length - 1].length}
            onClick={() => setDefinition({ groups: [...definition.groups, [newCondition()]] })}
            title="Un autre ensemble de conditions qui, à lui seul, déclenche aussi la règle"
          >
            Ajouter un groupe « ou »
          </Button>
        </section>

        <section className="grid gap-3 md:grid-cols-3">
          <Field label={`Risque : ${definition.risk}/100 → incident ${severity}`} hint={SEVERITY[severity]?.label}>
            <input type="range" min={1} max={100} value={definition.risk} onChange={(e) => setDefinition({ risk: Number(e.target.value) })} className="w-full accent-cyan-400" />
          </Field>
          <Field label="Notification">
            <select className={inputClass} value={definition.channel} onChange={(e) => setDefinition({ channel: e.target.value })}>
              {builder.channels.map(([id, label]) => (
                <option key={id} value={id}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Au plus une alerte par navire toutes les">
            <select className={inputClass} value={definition.cooldown_hours} onChange={(e) => setDefinition({ cooldown_hours: Number(e.target.value) })}>
              {[1, 6, 12, 24, 72, 168].map((h) => (
                <option key={h} value={h}>
                  {h < 24 ? `${h} h` : `${h / 24} jour(s)`}
                </option>
              ))}
            </select>
          </Field>
        </section>

        <section className="space-y-2 border-t border-tactical-line pt-3">
          <div className="flex flex-wrap items-center gap-2">
            <Button variant="primary" icon={FlaskConical} busy={busy === "test"} disabled={!conditionCount} onClick={test}>
              Tester
            </Button>
            <label className="flex items-center gap-1 text-slate-500">
              à l&apos;instant
              <input type="datetime-local" value={replayAt} onChange={(e) => setReplayAt(e.target.value)} className={`${inputClass} w-auto`} title="Vide = maintenant ; sinon rejeu à cet instant (UTC)" />
              UTC {replayAt && <button type="button" className="text-tactical-cyan" onClick={() => setReplayAt("")}>maintenant</button>}
            </label>
            <span className="text-slate-500">Rien n&apos;est créé pendant un test.</span>
          </div>
          {preview && <p className="border-l-2 border-tactical-cyan pl-2 text-slate-300">{preview.sentence}</p>}
          <PreviewResult result={preview} />
        </section>

        <ErrorBanner message={error} />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" icon={Save} busy={busy === "save"} disabled={!rule.name.trim() || !conditionCount} onClick={save}>
            Enregistrer {rule.enabled ? "et activer" : ""}
          </Button>
          <label className="flex items-center gap-1 text-slate-400">
            <input type="checkbox" checked={rule.enabled} onChange={() => setRule({ ...rule, enabled: !rule.enabled })} className="accent-cyan-400" />
            active dès l&apos;enregistrement
          </label>
        </div>
      </div>
    </Panel>
  );
}

function CustomRule({ rule, canEdit, onEdit, onChanged }) {
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const run = async (label, action) => {
    setBusy(label);
    setError("");
    try {
      await action();
      onChanged();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy("");
    }
  };
  const severity = severityFor(rule.definition.risk);
  return (
    <article className="space-y-2 border border-tactical-line bg-tactical-panel px-4 py-3 text-xs">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0 space-y-1">
          <p className="flex flex-wrap items-center gap-2 text-sm text-slate-100">
            {rule.name}
            <Pill color={FAMILIES["Sur mesure"]}>Sur mesure</Pill>
            <Pill color={SEVERITY[severity]?.color}>{severity}</Pill>
            <StateBadge rule={rule} />
          </p>
          {rule.description && <p className="text-slate-300">{rule.description}</p>}
          <p className="border-l-2 border-tactical-cyan/50 pl-2 text-slate-400">{rule.sentence}</p>
          <Performance performance={rule.performance} />
          <p className="text-[10px] text-slate-600">
            Créée par {rule.created_by} le {formatDateTime(rule.created_at)} · modifiée par {rule.updated_by} le {formatDateTime(rule.updated_at)}
          </p>
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-2">
            <Button icon={Pencil} onClick={() => onEdit(rule)}>Modifier</Button>
            <Button icon={rule.enabled ? CircleSlash : Power} busy={busy === "toggle"} onClick={() => run("toggle", () => updateRule(rule.id, { enabled: !rule.enabled }))}>
              {rule.enabled ? "Désactiver" : "Activer"}
            </Button>
            <Button
              icon={Trash2}
              busy={busy === "delete"}
              onClick={() => window.confirm(`Supprimer la règle « ${rule.name} » ? Les incidents déjà ouverts restent.`) && run("delete", () => deleteRule(rule.id))}
            >
              Supprimer
            </Button>
          </div>
        )}
      </div>
      <ErrorBanner message={error} />
    </article>
  );
}

export default function RulesView({ user, navigate }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState("");
  const [editing, setEditing] = useState(null); // null | "new" | rule
  const canEdit = hasRole(user, "supervisor");

  const load = useCallback(() => {
    fetchRules()
      .then((d) => {
        setData(d);
        setError("");
      })
      .catch((err) => setError(errorMessage(err, "Impossible de charger les règles")));
  }, []);
  useEffect(load, [load]);

  const toggle = async (rule) => {
    try {
      await toggleBuiltinRule(rule.id, !rule.enabled);
      load();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  const families = useMemo(() => {
    const groups = {};
    (data?.builtin || []).forEach((r) => {
      (groups[r.family] = groups[r.family] || []).push(r);
    });
    return groups;
  }, [data]);

  if (!data && !error) return <Spinner />;
  return (
    <div className="h-full space-y-5 overflow-y-auto p-3 sm:p-4 md:p-6">
      <header className="space-y-2">
        <h1 className="flex items-center gap-2 text-lg text-slate-100">
          Règles de détection <DocLink page="moteur/regles/" label="règles de détection" />
        </h1>
        <p className="max-w-3xl text-xs text-slate-400">
          Une règle surveille en continu l&apos;image maritime (AIS, VMS, radar, renseignement) et ouvre un incident quand toutes ses
          conditions sont réunies. Chaque incident garde la trace de la règle qui l&apos;a déclenché, des valeurs observées et des
          paramètres en vigueur (« Pourquoi cette alerte ? » dans le dossier). Les règles intégrées se règlent dans
          Administration › Paramètres ; les règles sur mesure se composent ici, sans programmation, et se testent avant d&apos;être activées.
        </p>
      </header>
      <ErrorBanner message={error} />

      {editing && data && (
        <RuleBuilder
          builder={data.builder}
          initial={editing === "new" ? null : { ...editing }}
          onCancel={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            load();
          }}
        />
      )}

      <section className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-[10px] uppercase tracking-[0.25em] text-slate-500">Règles sur mesure · {data?.custom.length || 0}</h2>
          {canEdit && !editing && (
            <Button variant="primary" icon={Plus} onClick={() => setEditing("new")}>
              Créer une règle
            </Button>
          )}
        </div>
        {data?.custom.length === 0 && (
          <Empty>
            Aucune règle sur mesure. {canEdit ? "Créez-en une à partir d'un modèle : elle se lit comme une phrase et se teste avant activation." : "Un superviseur peut en créer."}
          </Empty>
        )}
        {data?.custom.map((rule) => (
          <CustomRule key={rule.id} rule={rule} canEdit={canEdit} onEdit={(r) => setEditing(r)} onChanged={load} />
        ))}
      </section>

      {Object.entries(families).map(([family, items]) => (
        <section key={family} className="space-y-3">
          <h2 className="flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-slate-500">
            <span className="legend-dot" style={{ backgroundColor: FAMILIES[family] }} /> Règles intégrées · {family}
          </h2>
          {items.map((rule) => (
            <BuiltinRule key={rule.id} rule={rule} canEdit={canEdit} onToggle={toggle} isAdmin={hasRole(user, "admin")} navigate={navigate} />
          ))}
        </section>
      ))}
    </div>
  );
}
