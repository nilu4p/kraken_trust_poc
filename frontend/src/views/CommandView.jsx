import { useCallback, useEffect, useState } from "react";
import { Anchor, FileSignature, Library, Pencil, Plus, Trash2 } from "lucide-react";
import { Button, Empty, ErrorBanner, Field, Panel, Pill, Spinner, inputClass } from "../components/ui.jsx";
import DocLink from "../components/DocLink.jsx";
import ReportComposer, { IssuedReport } from "../components/ReportComposer.jsx";
import { formatDateTime, hasRole } from "../lib/format.js";
import { createAsset, deleteAsset, errorMessage, fetchAssets, fetchReports, updateAsset } from "../services/api.js";

const TABS = [
  { id: "sitrep", label: "SITREP", icon: FileSignature },
  { id: "register", label: "Rapports émis", icon: Library },
  { id: "assets", label: "Moyens", icon: Anchor },
];
const STATUS_COLORS = { available: "#3dff8a", engaged: "#f5c542", unavailable: "#64748b" };
const EMPTY_ASSET = {
  name: "", kind: "fast_boat", base_name: "", base_lat: "", base_lon: "", speed_kn: "", endurance_nm: "",
  readiness_min: 60, can_board: true, status: "available", notes: "",
};

function Register({ user, selected, navigate }) {
  const [items, setItems] = useState(null);
  const [kind, setKind] = useState("");
  const [error, setError] = useState("");
  useEffect(() => {
    fetchReports(kind ? { kind } : {})
      .then((d) => setItems(d.reports))
      .catch((err) => setError(errorMessage(err)));
  }, [kind]);
  return (
    <div className="grid min-h-0 gap-4 xl:grid-cols-[28rem_1fr]">
      <Panel
        title="Registre"
        icon={Library}
        actions={
          <select className="border border-tactical-line bg-tactical-bg px-2 py-1 text-xs text-slate-300" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Type">
            <option value="">Tous</option>
            <option value="incident">Comptes rendus</option>
            <option value="sitrep">SITREP</option>
          </select>
        }
      >
        <ErrorBanner message={error} />
        {items === null ? (
          <Spinner />
        ) : items.length === 0 ? (
          <Empty>Aucun rapport émis.</Empty>
        ) : (
          <ul className="divide-y divide-tactical-line text-xs">
            {items.map((r) => (
              <li key={r.id}>
                <button
                  type="button"
                  onClick={() => navigate("command", `register:${r.id}`)}
                  className={`w-full px-2 py-2 text-left ${String(selected) === String(r.id) ? "bg-tactical-cyan/10" : "hover:bg-white/5"}`}
                >
                  <p className="flex items-center gap-2 text-slate-100">
                    {r.ref} <Pill color={r.kind === "sitrep" ? "#a78bfa" : "#00e5ff"}>{r.kind === "sitrep" ? "SITREP" : "CR"}</Pill>
                    {!r.signed && <Pill color="#f5c542">non signé</Pill>}
                  </p>
                  <p className="truncate text-slate-400">{r.title}</p>
                  <p className="text-[10px] text-slate-500">
                    {formatDateTime(r.created_at)} · {r.created_by} · {r.classification}
                  </p>
                </button>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      <div className="min-w-0">
        {selected ? <IssuedReport id={selected} user={user} /> : <Empty>Sélectionnez un rapport pour l&apos;afficher, l&apos;imprimer ou vérifier sa signature.</Empty>}
      </div>
    </div>
  );
}

function AssetForm({ initial, kinds, statuses, onSaved, onCancel }) {
  const [form, setForm] = useState(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key, cast = (v) => v) => (e) => {
    const value = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    setForm((f) => ({ ...f, [key]: cast(value) }));
  };
  const submit = async () => {
    setBusy(true);
    setError("");
    const body = {
      ...form,
      base_lat: Number(form.base_lat), base_lon: Number(form.base_lon), speed_kn: Number(form.speed_kn),
      endurance_nm: Number(form.endurance_nm), readiness_min: Number(form.readiness_min),
    };
    delete body.id;
    try {
      onSaved(form.id ? await updateAsset(form.id, body) : await createAsset(body));
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="space-y-3 border border-tactical-line p-3">
      <div className="grid gap-3 md:grid-cols-3">
        <Field label="Nom"><input className={inputClass} value={form.name} onChange={set("name")} maxLength={80} /></Field>
        <Field label="Type">
          <select className={inputClass} value={form.kind} onChange={set("kind")}>
            {Object.entries(kinds).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
        <Field label="Disponibilité">
          <select className={inputClass} value={form.status} onChange={set("status")}>
            {Object.entries(statuses).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
        </Field>
        <Field label="Point de départ en mer" hint="sortie de port, en mer">
          <input className={inputClass} value={form.base_name} onChange={set("base_name")} maxLength={80} />
        </Field>
        <Field label="Latitude (°, N > 0)"><input className={inputClass} inputMode="decimal" value={form.base_lat} onChange={set("base_lat")} /></Field>
        <Field label="Longitude (°, W < 0)"><input className={inputClass} inputMode="decimal" value={form.base_lon} onChange={set("base_lon")} /></Field>
        <Field label="Vitesse de transit (nœuds)"><input className={inputClass} inputMode="decimal" value={form.speed_kn} onChange={set("speed_kn")} /></Field>
        <Field label="Distance franchissable (NM)" hint="aller + retour"><input className={inputClass} inputMode="decimal" value={form.endurance_nm} onChange={set("endurance_nm")} /></Field>
        <Field label="Délai d'appareillage (min)"><input className={inputClass} inputMode="numeric" value={form.readiness_min} onChange={set("readiness_min")} /></Field>
      </div>
      <label className="flex items-center gap-2 text-xs text-slate-300">
        <input type="checkbox" checked={form.can_board} onChange={set("can_board")} /> Peut arraisonner (équipe de visite embarquée)
      </label>
      <Field label="Notes"><input className={inputClass} value={form.notes} onChange={set("notes")} maxLength={500} /></Field>
      <ErrorBanner message={error} />
      <div className="flex gap-2">
        <Button variant="primary" busy={busy} onClick={submit}>Enregistrer</Button>
        <Button onClick={onCancel}>Annuler</Button>
      </div>
    </div>
  );
}

function Assets({ user }) {
  const [data, setData] = useState(null);
  const [editing, setEditing] = useState(null);
  const [error, setError] = useState("");
  const supervisor = hasRole(user, "supervisor");
  const load = useCallback(() => {
    fetchAssets().then(setData).catch((err) => setError(errorMessage(err)));
  }, []);
  useEffect(load, [load]);

  const quickStatus = async (asset, status) => {
    try {
      await updateAsset(asset.id, { status });
      load();
    } catch (err) {
      setError(errorMessage(err));
    }
  };
  const remove = async (asset) => {
    if (!window.confirm(`Supprimer le moyen « ${asset.name} » ?`)) return;
    try {
      await deleteAsset(asset.id);
      load();
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  if (!data) return error ? <ErrorBanner message={error} /> : <Spinner />;
  return (
    <Panel
      title="Moyens d'intervention"
      icon={Anchor}
      actions={
        <>
          <DocLink page="commandement/moyens/" label="moyens d'intervention" />
          {supervisor && !editing && <Button icon={Plus} onClick={() => setEditing(EMPTY_ASSET)}>Ajouter</Button>}
        </>
      }
    >
      <p className="mb-3 text-xs text-slate-500">
        Utilisés par l&apos;aide à la décision (délai pour être sur zone, lieu d&apos;interception). Tenez la disponibilité à jour à chaque
        prise de quart. Les valeurs installées par défaut sont génériques : remplacez-les par celles des moyens réels.
      </p>
      <ErrorBanner message={error} />
      {editing && (
        <div className="mb-3">
          <AssetForm key={editing.id || "new"} initial={editing} kinds={data.kinds} statuses={data.statuses} onCancel={() => setEditing(null)}
            onSaved={() => { setEditing(null); load(); }} />
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wider text-slate-500">
            <tr>
              <th className="py-1 pr-3">Moyen</th>
              <th className="py-1 pr-3">Départ</th>
              <th className="py-1 pr-3">Vitesse</th>
              <th className="py-1 pr-3">Franchissable</th>
              <th className="py-1 pr-3">Appareillage</th>
              <th className="py-1 pr-3">Disponibilité</th>
              <th className="py-1">Mis à jour</th>
            </tr>
          </thead>
          <tbody>
            {data.assets.map((a) => (
              <tr key={a.id} className="border-t border-tactical-line align-top">
                <td className="py-2 pr-3">
                  <p className="text-slate-100">{a.name}</p>
                  <p className="text-[10px] text-slate-500">{a.kind_label} · {a.can_board ? "peut arraisonner" : "observation seule"}</p>
                  {a.notes && <p className="text-[10px] text-slate-600">{a.notes}</p>}
                </td>
                <td className="py-2 pr-3 text-slate-300">{a.base_name || "—"}<p className="text-[10px] text-slate-500">{a.base_lat}, {a.base_lon}</p></td>
                <td className="py-2 pr-3 text-slate-300">{a.speed_kn} nd</td>
                <td className="py-2 pr-3 text-slate-300">{a.endurance_nm} NM</td>
                <td className="py-2 pr-3 text-slate-300">{a.readiness_min} min</td>
                <td className="py-2 pr-3">
                  {supervisor ? (
                    <select
                      className="border bg-tactical-bg px-1 py-0.5 text-xs"
                      style={{ color: STATUS_COLORS[a.status], borderColor: `${STATUS_COLORS[a.status]}66` }}
                      value={a.status}
                      onChange={(e) => quickStatus(a, e.target.value)}
                      aria-label={`Disponibilité de ${a.name}`}
                    >
                      {Object.entries(data.statuses).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
                    </select>
                  ) : (
                    <Pill color={STATUS_COLORS[a.status]}>{a.status_label}</Pill>
                  )}
                </td>
                <td className="py-2 text-[10px] text-slate-500">
                  {a.updated_by === "system" ? <span className="text-tactical-amber">valeurs génériques</span> : <>{formatDateTime(a.updated_at)}<br />{a.updated_by}</>}
                  {supervisor && (
                    <span className="mt-1 flex gap-2">
                      <button type="button" onClick={() => setEditing(a)} className="text-slate-400 hover:text-white" aria-label={`Modifier ${a.name}`}><Pencil size={13} /></button>
                      <button type="button" onClick={() => remove(a)} className="text-slate-400 hover:text-tactical-red" aria-label={`Supprimer ${a.name}`}><Trash2 size={13} /></button>
                    </span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {data.assets.length === 0 && <Empty>Aucun moyen : l&apos;aide à la décision ne pourra pas proposer d&apos;interception.</Empty>}
      </div>
    </Panel>
  );
}

/** "#/command/register:12" → { tab: "register", id: "12" } */
function parseParam(param) {
  const [tab, id = ""] = decodeURIComponent(param || "").split(":");
  return { tab: TABS.some((t) => t.id === tab) ? tab : "sitrep", id };
}

export default function CommandView({ user, param, navigate }) {
  const { tab, id } = parseParam(param);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-1 overflow-x-auto border-b border-tactical-line bg-tactical-panel/60 px-3">
        {TABS.map(({ id: t, label, icon: Icon }) => (
          <button
            key={t}
            type="button"
            onClick={() => navigate("command", t)}
            className={`flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 py-2.5 text-xs uppercase tracking-wider ${
              tab === t ? "border-tactical-cyan text-tactical-cyan" : "border-transparent text-slate-400 hover:text-white"
            }`}
          >
            <Icon size={13} /> {label}
          </button>
        ))}
        <span className="ml-auto shrink-0"><DocLink page="commandement/" label="commandement" /></span>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {tab === "sitrep" && (
          <Panel title="Rapport de situation (SITREP)" icon={FileSignature} actions={<DocLink page="commandement/rapports/" label="rapports" />}>
            <p className="mb-3 text-xs text-slate-500">
              Synthèse de la période pour la chaîne de commandement : incidents, action de l&apos;État en mer, capteurs, moyens. Les chiffres
              viennent des enregistrements de la plateforme ; complétez l&apos;appréciation et les propositions, prévisualisez, puis émettez
              (superviseur) : le texte est figé et signé.
            </p>
            <ReportComposer kind="sitrep" user={user} onIssued={(r) => navigate("command", `register:${r.id}`)} />
          </Panel>
        )}
        {tab === "register" && <Register user={user} selected={id} navigate={navigate} />}
        {tab === "assets" && <Assets user={user} />}
      </div>
    </div>
  );
}
