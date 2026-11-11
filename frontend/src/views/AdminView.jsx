import { lazy, Suspense, useCallback, useEffect, useState } from "react";
import {
  Activity,
  BadgeCheck,
  Gauge,
  BookOpen,
  CheckCircle2,
  ListX,
  Upload,
  KeyRound,
  Loader2,
  Plug,
  Save,
  SlidersHorizontal,
  Trash2,
  Users,
  XCircle,
} from "lucide-react";
import { Button, ErrorBanner, Field, Panel, Pill, Spinner, Tabs, inputClass } from "../components/ui.jsx";
import SupervisionTab from "./SupervisionTab.jsx";
import UsersTab from "./UsersTab.jsx";
import { formatDateTime, formatRelative } from "../lib/format.js";
import {
  addWatchlistEntry,
  deleteWatchlistEntry,
  fetchCatalogue,
  fetchWatchlist,
  importWatchlist,
  importVms,
  addAuthorized,
  deleteAuthorized,
  fetchAuthorized,
  importAuthorized,
  errorMessage,
  fetchKeys,
  fetchRuntimeSettings,
  fetchSystemHealth,
  saveKeys,
  saveRuntimeSettings,
  testKeys,
} from "../services/api.js";

// Large editor, loaded only when its tab is opened.
const CustomSourcesTab = lazy(() => import("./CustomSourcesTab.jsx"));

const TABS = [
  { id: "users", label: "Utilisateurs", icon: Users },
  { id: "keys", label: "Clés & intégrations", icon: KeyRound },
  { id: "settings", label: "Paramètres", icon: SlidersHorizontal },
  { id: "watchlist", label: "Liste de surveillance", icon: ListX },
  { id: "authorized", label: "Navires autorisés", icon: BadgeCheck },
  { id: "catalogue", label: "Catalogue des sources", icon: BookOpen },
  { id: "custom", label: "Sources personnalisées", icon: Plug },
  { id: "health", label: "Santé des sources", icon: Activity },
  { id: "supervision", label: "Supervision technique", icon: Gauge },
];

function watchDetails(entry) {
  try {
    const details = JSON.parse(entry.details || "{}");
    return { ...details, aliasLabel: (entry.alias_keys || "").split(",").filter(Boolean).join(", ") };
  } catch {
    return { aliasLabel: entry.alias_keys || "" };
  }
}

function WatchlistTab() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState("");
  const [entry, setEntry] = useState({ name: "", mmsi: "", imo: "", flag: "", reason: "" });
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    fetchWatchlist(search).then(setData).catch((err) => setError(errorMessage(err)));
  }, [search]);
  useEffect(load, [load]);

  const run = async (label, action) => {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="space-y-4">
      <Panel title="Import de la liste INN combinée" icon={Upload}>
        <p className="mb-3 text-xs text-slate-500">
          Téléchargez le fichier Excel sur{" "}
          <a href="https://www.iuu-vessels.org/Home/Download" target="_blank" rel="noreferrer" className="text-tactical-cyan underline">
            iuu-vessels.org
          </a>{" "}
          (liste combinée des ORGP, maintenue par TMT) puis importez-le ici (XLS, XLSX ou CSV, 10 Mo max). L&apos;import remplace la version
          précédente. Les navires listés sont signalés sur la carte (AIS, événements GFW), et un incident est ouvert dès qu&apos;un navire
          listé émet à l&apos;AIS dans la ZEE.
        </p>
        <label className="inline-flex cursor-pointer items-center gap-2 border border-tactical-cyan/50 bg-tactical-cyan/10 px-3 py-1.5 text-xs text-tactical-cyan hover:bg-tactical-cyan/20">
          <Upload size={13} /> {busy === "import" ? "Import en cours…" : "Choisir le fichier"}
          <input
            type="file"
            accept=".xls,.xlsx,.csv,text/csv,application/vnd.ms-excel,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            className="hidden"
            disabled={Boolean(busy)}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file)
                run("import", async () => {
                  const r = await importWatchlist(file);
                  setNotice(
                    `${r.imported} navires actuellement listés importés (format ${r.format.toUpperCase()}, feuille ${r.sheet}, ` +
                      `présentation ${r.layout})${r.skipped_delisted ? ` · ${r.skipped_delisted} navires radiés ignorés` : ""}. ` +
                      `Identifiants : ${r.with_imo} IMO, ${r.with_mmsi} MMSI, ${r.with_call_sign} indicatifs radio ; ` +
                      `les autres sont reconnus par nom ou alias ORGP.`,
                  );
                });
            }}
          />
        </label>
        {notice && <p className="mt-2 text-xs text-tactical-green">{notice}</p>}
      </Panel>
      <Panel title="Ajout manuel (signalement, renseignement)" icon={ListX}>
        <form
          className="grid gap-3 md:grid-cols-6"
          onSubmit={(e) => {
            e.preventDefault();
            run("add", async () => {
              await addWatchlistEntry(entry);
              setEntry({ name: "", mmsi: "", imo: "", flag: "", reason: "" });
            });
          }}
        >
          {[
            ["name", "Nom du navire"],
            ["mmsi", "MMSI (9 chiffres)"],
            ["imo", "IMO (7 chiffres)"],
            ["flag", "Pavillon"],
            ["reason", "Motif"],
          ].map(([key, label]) => (
            <Field key={key} label={label}>
              <input className={inputClass} value={entry[key]} onChange={(e) => setEntry({ ...entry, [key]: e.target.value })} maxLength={key === "reason" ? 300 : 200} />
            </Field>
          ))}
          <div className="flex items-end">
            <Button type="submit" variant="primary" busy={busy === "add"}>
              Ajouter
            </Button>
          </div>
        </form>
      </Panel>
      <ErrorBanner message={error} />
      <Panel
        title={`Navires surveillés · ${Object.values(data?.counts || {}).reduce((a, b) => a + b, 0)}`}
        icon={ListX}
        actions={<input className="border border-tactical-line bg-tactical-bg px-2 py-1 text-xs text-slate-200" placeholder="Rechercher" value={search} onChange={(e) => setSearch(e.target.value)} maxLength={64} />}
      >
        {!data ? (
          <Spinner />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  <th className="py-1">Navire</th>
                  <th className="py-1">MMSI</th>
                  <th className="py-1">IMO</th>
                  <th className="py-1">Indicatif</th>
                  <th className="py-1">Pavillon</th>
                  <th className="py-1">Engin / type</th>
                  <th className="py-1">Motif</th>
                  <th className="py-1">Source</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.entries.map((e) => (
                  <tr key={e.id} className="border-t border-tactical-line">
                    <td className="py-1.5 text-slate-200">{e.name || "—"}</td>
                    <td className="py-1.5 text-slate-400">{e.mmsi || "—"}</td>
                    <td className="py-1.5 text-slate-400">{e.imo || "—"}</td>
                    <td className="py-1.5 text-slate-400">{e.call_sign || "—"}</td>
                    <td className="py-1.5 text-slate-400">{e.flag || "—"}</td>
                    <td className="py-1.5 text-slate-400">{watchDetails(e).gear || watchDetails(e).vessel_type || "—"}</td>
                    <td className="max-w-md py-1.5 text-slate-500" title={e.reason}>
                      <span className="line-clamp-2">{e.reason}</span>
                      {e.alias_keys && <span className="block text-[10px] text-slate-600">alias : {watchDetails(e).aliasLabel}</span>}
                    </td>
                    <td className="py-1.5"><Pill color={e.source === "manual" ? "#38bdf8" : "#ff3b3b"}>{e.source}</Pill></td>
                    <td className="py-1.5 text-right">
                      <Button icon={Trash2} busy={busy === `del:${e.id}`} onClick={() => run(`del:${e.id}`, () => deleteWatchlistEntry(e.id))}>
                        Retirer
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

const CATALOGUE_STATUS = {
  active: { color: "#3dff8a", label: "Actif" },
  needs_key: { color: "#f5c542", label: "Clé requise" },
  available: { color: "#94a3b8", label: "Branchable" },
  disabled: { color: "#64748b", label: "Désactivée" },
};

const EMPTY_LICENCE = { name: "", mmsi: "", imo: "", call_sign: "", flag: "", licence: "", valid_until: "", note: "" };

/** Vessels fishing legally under licence: excluded from the foreign-fishing rule and from the risk context. */
function AuthorizedTab() {
  const [data, setData] = useState(null);
  const [search, setSearch] = useState("");
  const [entry, setEntry] = useState(EMPTY_LICENCE);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(() => {
    fetchAuthorized(search).then(setData).catch((err) => setError(errorMessage(err)));
  }, [search]);
  useEffect(load, [load]);

  const run = async (label, action) => {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="space-y-4">
      <Panel title="Navires autorisés à pêcher dans la ZEE" icon={BadgeCheck}>
        <p className="mb-3 text-xs text-slate-500">
          Navires sous licence (accords UE, licences de la DM Guyane — par exemple la flottille vénézuélienne de vivaneau). Tant que
          leur licence est valide, ils ne déclenchent pas la règle « pêche étrangère », apparaissent en « Pêcheur sous licence » sur la
          carte et leurs événements GFW ne comptent plus dans le risque des cibles radar. La règle « pêche en aire protégée » continue
          de s&apos;appliquer. Correspondance <strong>uniquement par MMSI, IMO ou indicatif radio</strong> : jamais par le nom, pour
          qu&apos;un navire INN ne puisse pas être blanchi par homonymie.
        </p>
        <label className="inline-flex cursor-pointer items-center gap-2 border border-tactical-cyan/50 bg-tactical-cyan/10 px-3 py-1.5 text-xs text-tactical-cyan hover:bg-tactical-cyan/20">
          <Upload size={13} /> {busy === "import" ? "Import en cours…" : "Importer la liste des licences (remplace)"}
          <input
            type="file"
            accept=".csv,.xls,.xlsx,text/csv"
            className="hidden"
            disabled={Boolean(busy)}
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = "";
              if (file)
                run("import", async () => {
                  const r = await importAuthorized(file, true);
                  setNotice(
                    `${r.imported} navire(s) autorisé(s) importé(s)` +
                      (r.rejected_count ? ` · ${r.rejected_count} ligne(s) rejetée(s) : ${r.rejected.join(" ; ")}` : ""),
                  );
                });
            }}
          />
        </label>
        <p className="mt-2 text-[10px] text-slate-600">
          CSV / XLS / XLSX avec en-têtes : nom, mmsi, imo, indicatif, pavillon, licence, fin validité (AAAA-MM-JJ ou JJ/MM/AAAA), note.
        </p>
        {notice && <p className="mt-2 text-xs text-tactical-green">{notice}</p>}
      </Panel>
      <Panel title="Ajouter une licence" icon={BadgeCheck}>
        <form
          className="grid gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault();
            run("add", async () => {
              await addAuthorized({ ...entry, valid_until: entry.valid_until || null });
              setEntry(EMPTY_LICENCE);
            });
          }}
        >
          {[
            ["name", "Nom du navire"],
            ["mmsi", "MMSI (9 chiffres)"],
            ["imo", "IMO (7 chiffres)"],
            ["call_sign", "Indicatif radio"],
            ["flag", "Pavillon"],
            ["licence", "N° de licence"],
            ["valid_until", "Fin de validité"],
            ["note", "Note"],
          ].map(([key, label]) => (
            <Field key={key} label={label}>
              <input
                className={inputClass}
                type={key === "valid_until" ? "date" : "text"}
                value={entry[key]}
                onChange={(e) => setEntry({ ...entry, [key]: e.target.value })}
              />
            </Field>
          ))}
          <div className="md:col-span-4">
            <Button type="submit" variant="primary" icon={BadgeCheck} busy={busy === "add"}>
              Ajouter
            </Button>
          </div>
        </form>
      </Panel>
      <Panel title={`Licences · ${data?.active ?? 0} valide(s) / ${data?.total ?? 0}`} icon={BadgeCheck}>
        <input className={`${inputClass} mb-3 max-w-xs`} placeholder="Rechercher" value={search} onChange={(e) => setSearch(e.target.value)} />
        <ErrorBanner message={error} />
        {!data ? (
          <Spinner />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                <tr>
                  {["Navire", "MMSI", "IMO", "Indicatif", "Pavillon", "Licence", "Validité", ""].map((h) => (
                    <th key={h} className="py-2 pr-3">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {data.entries.map((e) => (
                  <tr key={e.id} className={`border-t border-tactical-line ${e.expired ? "text-slate-600" : "text-slate-300"}`}>
                    <td className="py-2 pr-3">{e.name || "—"}</td>
                    <td className="pr-3">{e.mmsi || "—"}</td>
                    <td className="pr-3">{e.imo || "—"}</td>
                    <td className="pr-3">{e.call_sign || "—"}</td>
                    <td className="pr-3">{e.flag || "—"}</td>
                    <td className="pr-3">{e.licence || "—"}</td>
                    <td className="pr-3">{e.valid_until ? `${e.expired ? "expirée le" : "jusqu'au"} ${e.valid_until}` : "sans limite"}</td>
                    <td className="text-right">
                      <Button
                        variant="danger"
                        busy={busy === `del:${e.id}`}
                        onClick={() =>
                          window.confirm(`Retirer ${e.name || e.mmsi || e.imo} de la liste ? Il sera de nouveau soumis à la règle « pêche étrangère ».`) &&
                          run(`del:${e.id}`, () => deleteAuthorized(e.id))
                        }
                      >
                        Retirer
                      </Button>
                    </td>
                  </tr>
                ))}
                {data.entries.length === 0 && (
                  <tr>
                    <td colSpan={8} className="py-4 text-slate-500">
                      Aucun navire autorisé : tout navire de pêche étranger dans la ZEE déclenche une alerte.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </div>
  );
}

/** VMS positions exported by the fisheries authority (NAF or CSV), fused with the AIS history. */
function VmsImportPanel() {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  return (
    <Panel title="Import VMS (balises des navires sous licence)" icon={Upload}>
      <p className="mb-3 text-xs text-slate-500">
        Export fourni par le CNSP ou la DM Guyane : messages NAF (<code>//SR//…//ER//</code>) ou CSV avec latitude, longitude, date/heure
        et un identifiant (MMSI, indicatif radio, CFR ou nom). Les positions sont fusionnées à l&apos;AIS : un écho radar expliqué par
        une balise VMS n&apos;est plus « sans AIS », et les navires sous licence ne déclenchent pas la règle « pêche étrangère ».
      </p>
      <label className="inline-flex cursor-pointer items-center gap-2 border border-tactical-cyan/50 bg-tactical-cyan/10 px-3 py-1.5 text-xs text-tactical-cyan hover:bg-tactical-cyan/20">
        <Upload size={13} /> {busy ? "Import en cours…" : "Choisir le fichier VMS"}
        <input
          type="file"
          accept=".naf,.txt,.csv,text/plain,text/csv"
          className="hidden"
          disabled={busy}
          onChange={async (e) => {
            const file = e.target.files?.[0];
            e.target.value = "";
            if (!file) return;
            setBusy(true);
            setError("");
            setNotice("");
            try {
              const r = await importVms(file);
              setNotice(
                `${r.positions} position(s) de ${r.vessels} navire(s) importées (format ${r.format.toUpperCase()}, ` +
                  `du ${formatDateTime(r.from)} au ${formatDateTime(r.to)}).`,
              );
            } catch (err) {
              setError(errorMessage(err));
            } finally {
              setBusy(false);
            }
          }}
        />
      </label>
      {notice && <p className="mt-2 text-xs text-tactical-green">{notice}</p>}
      {error && <p className="mt-2 text-xs text-tactical-red">{error}</p>}
    </Panel>
  );
}

function CatalogueTab() {
  const [sources, setSources] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    fetchCatalogue().then((d) => setSources(d.sources)).catch((err) => setError(errorMessage(err)));
  }, []);
  if (!sources) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const categories = [...new Set(sources.map((s) => s.category))];
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Toutes les sources exploitées ou exploitables par la plateforme. « Branchable » : source identifiée, à accès restreint ou à
        connecter (procédure d&apos;accès via le lien). Les clés se gèrent dans « Clés & intégrations ».
      </p>
      <VmsImportPanel />
      {categories.map((category) => (
        <Panel key={category} title={category} icon={BookOpen}>
          <ul className="space-y-2 text-xs">
            {sources
              .filter((s) => s.category === category)
              .map((s) => (
                <li key={s.id} className="flex flex-wrap items-start justify-between gap-2 border-b border-tactical-line pb-2 last:border-0">
                  <div className="min-w-0 flex-1">
                    {s.url ? (
                      <a href={s.url} target="_blank" rel="noreferrer" className="text-slate-100 hover:text-tactical-cyan">
                        {s.name} ↗
                      </a>
                    ) : (
                      <span className="text-slate-100">{s.name}</span>
                    )}
                    <p className="text-slate-500">{s.use}</p>
                    <p className="text-[10px] text-slate-600">Accès : {s.access}</p>
                  </div>
                  <Pill color={CATALOGUE_STATUS[s.status].color}>{CATALOGUE_STATUS[s.status].label}</Pill>
                </li>
              ))}
          </ul>
        </Panel>
      ))}
    </div>
  );
}


const SERVICES = [
  { id: "copernicus_s3", title: "Copernicus S3", note: "Obligatoire pour lire les images radar Sentinel-1", fields: ["access_key", "secret_key"] },
  { id: "global_fishing_watch", title: "Global Fishing Watch", note: "Coupures AIS, rencontres, flânage, pêche", fields: ["gfw_token"] },
  { id: "aisstream", title: "aisstream.io", note: "AIS temps réel (sinon simulé)", fields: ["aisstream_api_key"] },
  {
    id: "aishub",
    title: "AISHub",
    note: "2e flux AIS fusionné (autres stations côtières). Compte gratuit si vous partagez le flux d'un récepteur AIS ; le nom d'utilisateur sert de clé.",
    fields: ["aishub_username"],
  },
  {
    id: "opensanctions",
    title: "OpenSanctions",
    note: "Criblage sanctions (OFAC, UE, ONU, UK…) des navires présents dans la ZEE. Clé gratuite pour usage d'intérêt public (demande motivée).",
    fields: ["opensanctions_api_key"],
  },
  {
    id: "vesselapi",
    title: "VesselAPI",
    note: "Registre des navires (IMO, type, année, tonnage, anciens noms) et 3e flux AIS optionnel. Offre gratuite : 150 appels / mois ; régler le budget et le flux dans Paramètres. Le test consomme 1 appel.",
    fields: ["vesselapi_api_key"],
  },
  {
    id: "webhook",
    title: "Notifications",
    note: "Webhook Slack / Mattermost (HTTPS public). PoC : serveur local http://notif-sink:8090/hooks/kraken, consultable sur /notifs/ de la console",
    fields: ["alert_webhook_url"],
  },
];

function KeysTab() {
  const [keys, setKeys] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [results, setResults] = useState({});
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    fetchKeys().then((d) => setKeys(d.keys)).catch((err) => setError(errorMessage(err)));
  }, []);

  const valuesFor = (service) =>
    Object.fromEntries(service.fields.filter((f) => drafts[f]?.trim()).map((f) => [f, drafts[f].trim()]));

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

  if (!keys) return error ? <ErrorBanner message={error} /> : <Spinner />;
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">
        Les secrets sont chiffrés au repos (Fernet, clé maître hors base), jamais renvoyés au navigateur ni journalisés. Seuls les
        4 derniers caractères sont affichés. Chaque modification est tracée dans l&apos;audit.
      </p>
      <ErrorBanner message={error} />
      <div className="grid gap-4 xl:grid-cols-2">
        {SERVICES.map((service) => {
          const result = results[service.id];
          const fromDashboard = service.fields.some((f) => keys[f]?.origin === "dashboard");
          return (
            <Panel
              key={service.id}
              title={service.title}
              icon={KeyRound}
              actions={
                <a href={keys[service.fields[0]]?.signup} target="_blank" rel="noreferrer" className="text-[10px] text-tactical-amber underline">
                  Obtenir ↗
                </a>
              }
            >
              <p className="mb-3 text-xs text-slate-500">{service.note}</p>
              <div className="space-y-3">
                {service.fields.map((field) => {
                  const info = keys[field] || {};
                  return (
                    <Field
                      key={field}
                      label={info.env}
                      hint={info.configured ? `${info.hint} · ${info.origin === "dashboard" ? "coffre" : ".env"}` : "non configurée"}
                    >
                      {/* Read-only until focused + new-password: browsers never inject the KRAKEN login
                          password here (that autofill made "Tester" send wrong keys). */}
                      <input
                        className={inputClass}
                        type="password"
                        name={`secret-${field}`}
                        value={drafts[field] || ""}
                        onChange={(e) => setDrafts((d) => ({ ...d, [field]: e.target.value }))}
                        placeholder={info.configured ? "Laisser vide pour conserver" : "Coller la valeur"}
                        autoComplete="new-password"
                        data-lpignore="true"
                        data-1p-ignore="true"
                        readOnly
                        onFocus={(e) => {
                          e.currentTarget.readOnly = false;
                        }}
                        spellCheck={false}
                      />
                    </Field>
                  );
                })}
              </div>
              {result && (
                <p className={`mt-3 flex items-start gap-2 text-xs ${result.ok ? "text-tactical-green" : "text-tactical-red"}`}>
                  {result.ok ? <CheckCircle2 size={14} className="mt-0.5 shrink-0" /> : <XCircle size={14} className="mt-0.5 shrink-0" />}
                  <span>
                    {result.message}
                    {result.tested && <span className="block text-[10px] text-slate-500">Valeurs testées : {result.tested}</span>}
                  </span>
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button
                  variant="primary"
                  icon={Save}
                  busy={busy === `save:${service.id}`}
                  onClick={() =>
                    run(`save:${service.id}`, async () => {
                      const values = valuesFor(service);
                      if (!Object.keys(values).length) throw new Error("Saisissez au moins une valeur.");
                      setKeys((await saveKeys(values)).keys);
                      setDrafts((d) => Object.fromEntries(Object.entries(d).filter(([f]) => !service.fields.includes(f))));
                      const res = await testKeys(service.id, null);
                      setResults((r) => ({ ...r, [service.id]: res }));
                    })
                  }
                >
                  Enregistrer
                </Button>
                <Button
                  icon={busy === `test:${service.id}` ? Loader2 : CheckCircle2}
                  busy={busy === `test:${service.id}`}
                  onClick={() =>
                    run(`test:${service.id}`, async () => {
                      const res = await testKeys(service.id, valuesFor(service));
                      setResults((r) => ({ ...r, [service.id]: res }));
                    })
                  }
                >
                  {Object.keys(valuesFor(service)).length ? "Tester la saisie" : "Tester les clés enregistrées"}
                </Button>
                {Object.keys(valuesFor(service)).length > 0 && (
                  <Button onClick={() => setDrafts((d) => Object.fromEntries(Object.entries(d).filter(([f]) => !service.fields.includes(f))))}>
                    Vider la saisie
                  </Button>
                )}
                {fromDashboard && (
                  <Button
                    icon={Trash2}
                    className="ml-auto"
                    busy={busy === `remove:${service.id}`}
                    onClick={() =>
                      run(`remove:${service.id}`, async () => {
                        setKeys((await saveKeys(Object.fromEntries(service.fields.map((f) => [f, ""])))).keys);
                        setResults((r) => ({ ...r, [service.id]: undefined }));
                      })
                    }
                  >
                    Supprimer
                  </Button>
                )}
              </div>
            </Panel>
          );
        })}
      </div>
    </div>
  );
}

function SettingsTab() {
  const [settings, setSettings] = useState(null);
  const [drafts, setDrafts] = useState({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    fetchRuntimeSettings().then((d) => setSettings(d.settings)).catch((err) => setError(errorMessage(err)));
  }, []);

  if (!settings) return error ? <ErrorBanner message={error} /> : <Spinner />;

  const save = async () => {
    setBusy(true);
    setError("");
    setSaved(false);
    try {
      setSettings((await saveRuntimeSettings(drafts)).settings);
      setDrafts({});
      setSaved(true);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="space-y-4">
      {/* Sticky: the Apply button stays reachable while scrolling through the 16 settings. */}
      <div className="sticky top-0 z-10 -mx-1 flex flex-wrap items-center justify-between gap-3 border-b border-tactical-line bg-tactical-bg/95 px-1 py-2 backdrop-blur">
        <p className="max-w-3xl text-xs text-slate-500">
          Ces réglages s&apos;appliquent immédiatement, sans redémarrage, et chaque modification est tracée dans l&apos;audit. Lisez
          l&apos;effet de chaque sens de variation avant de modifier : ils arbitrent entre détection, fausses alarmes et charge des
          analystes.
        </p>
        <div className="flex items-center gap-3">
          <a href="/docs/administration/parametres/" target="_blank" rel="noopener" className="text-xs text-tactical-cyan underline">
            Guide des paramètres ↗
          </a>
          {saved && <span className="text-xs text-tactical-green">Paramètres appliqués et audités.</span>}
          {Object.keys(drafts).length > 0 && (
            <button type="button" onClick={() => setDrafts({})} className="text-xs text-slate-400 underline hover:text-white">
              Annuler les modifications
            </button>
          )}
          <Button variant="primary" icon={Save} busy={busy} disabled={!Object.keys(drafts).length} onClick={save}>
            Appliquer {Object.keys(drafts).length ? `(${Object.keys(drafts).length})` : ""}
          </Button>
        </div>
      </div>
      <ErrorBanner message={error} />
      <div className="grid gap-4 xl:grid-cols-2">
        {Object.entries(settings).map(([name, spec]) => {
          const value = drafts[name] ?? spec.value;
          const changed = name in drafts && drafts[name] !== spec.value;
          return (
            <Panel
              key={name}
              title={spec.label}
              icon={SlidersHorizontal}
              className={changed ? "ring-1 ring-tactical-amber" : ""}
              actions={
                value !== spec.default && (
                  <button type="button" onClick={() => setDrafts((d) => ({ ...d, [name]: spec.default }))} className="text-[10px] text-slate-500 underline hover:text-white">
                    défaut ({spec.default})
                  </button>
                )
              }
            >
              <div className="flex items-center gap-3">
                <input
                  className={`${inputClass} w-32`}
                  type="number"
                  step={spec.type === "int" ? 1 : 0.05}
                  min={spec.min}
                  max={spec.max}
                  value={value}
                  onChange={(e) => setDrafts((d) => ({ ...d, [name]: e.target.value === "" ? "" : Number(e.target.value) }))}
                  aria-label={spec.label}
                />
                <span className="text-xs text-slate-500">
                  {spec.unit} · plage {spec.min} – {spec.max}
                </span>
              </div>
              <dl className="mt-3 space-y-2 text-xs">
                <div>
                  <dt className="text-[10px] uppercase tracking-wider text-slate-500">Rôle</dt>
                  <dd className="text-slate-300">{spec.what}</dd>
                </div>
                <div className="grid gap-2 md:grid-cols-2">
                  <div className="border-l-2 border-tactical-cyan/50 pl-2">
                    <dt className="text-[10px] uppercase tracking-wider text-tactical-cyan">▲ Augmenter</dt>
                    <dd className="text-slate-400">{spec.higher}</dd>
                  </div>
                  <div className="border-l-2 border-tactical-amber/50 pl-2">
                    <dt className="text-[10px] uppercase tracking-wider text-tactical-amber">▼ Diminuer</dt>
                    <dd className="text-slate-400">{spec.lower}</dd>
                  </div>
                </div>
                <div>
                  <dt className="text-[10px] uppercase tracking-wider text-slate-500">Recommandation</dt>
                  <dd className="text-slate-300">{spec.advice}</dd>
                </div>
              </dl>
            </Panel>
          );
        })}
      </div>
    </div>
  );
}

const SOURCE_LABELS = {
  copernicus_stac: "Copernicus STAC",
  copernicus_s3: "Copernicus S3",
  marine_regions: "Marine Regions",
  open_meteo: "Open-Meteo",
  global_fishing_watch: "Global Fishing Watch",
  aisstream: "aisstream.io",
  webhook: "Webhook",
  cnes_geodes: "CNES GEODES",
  shom: "SHOM",
  noaa_bathymetry: "NOAA bathymétrie",
  noaa_chlorophyll: "NOAA chlorophylle",
};
const HEALTH_COLORS = { ok: "#3dff8a", degraded: "#f5c542", down: "#ff3b3b" };

function HealthTab() {
  const [health, setHealth] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const load = () => fetchSystemHealth().then(setHealth).catch((err) => setError(errorMessage(err)));
    load();
    const timer = setInterval(load, 15000);
    return () => clearInterval(timer);
  }, []);
  if (!health) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const sources = Object.entries(health.sources);
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="Sources de données" icon={Activity}>
        {sources.length === 0 && <p className="text-xs text-slate-500">Aucun appel externe depuis le démarrage.</p>}
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <tbody>
              {sources.map(([name, s]) => (
                <tr key={name} className="border-t border-tactical-line">
                  <td className="py-2 text-slate-200">{SOURCE_LABELS[name] || name}</td>
                  <td className="py-2"><Pill color={HEALTH_COLORS[s.status]}>{s.status}</Pill></td>
                  <td className="py-2 text-slate-500">succès {s.last_success ? formatRelative(s.last_success) : "—"}</td>
                  <td className="py-2 text-slate-500">{s.consecutive_failures ? `${s.consecutive_failures} échec(s) · ${s.last_error}` : ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
      <Panel title="Surveillance" icon={SlidersHorizontal}>
        <dl className="grid grid-cols-2 gap-3 text-xs">
          <div>
            <dt className="text-slate-500">Veille automatique</dt>
            <dd>{health.surveillance.auto_scan_interval_minutes ? `toutes les ${health.surveillance.auto_scan_interval_minutes} min` : "désactivée"}</dd>
          </div>
          <div>
            <dt className="text-slate-500">Scènes traitées</dt>
            <dd>{health.surveillance.scenes_processed}</dd>
          </div>
          <div className="col-span-2">
            <dt className="text-slate-500">Dernier traitement</dt>
            <dd>{formatDateTime(health.surveillance.last_processed_at)}</dd>
          </div>
          {Object.entries(health.configured).map(([name, ok]) => (
            <div key={name}>
              <dt className="text-slate-500">{SOURCE_LABELS[name] || name}</dt>
              <dd style={{ color: ok ? "#3dff8a" : "#f5c542" }}>{ok ? "configuré" : "non configuré"}</dd>
            </div>
          ))}
        </dl>
      </Panel>
    </div>
  );
}

export default function AdminView({ user }) {
  const [tab, setTab] = useState("users");
  return (
    <div className="flex h-full flex-col">
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Administration" />
      <div className="min-h-0 flex-1 overflow-y-auto p-3 sm:p-4">
        {tab === "users" && <UsersTab user={user} />}
        {tab === "keys" && <KeysTab />}
        {tab === "settings" && <SettingsTab />}
        {tab === "watchlist" && <WatchlistTab />}
        {tab === "authorized" && <AuthorizedTab />}
        {tab === "catalogue" && <CatalogueTab />}
        {tab === "custom" && (
          <Suspense fallback={<Spinner />}>
            <CustomSourcesTab />
          </Suspense>
        )}
        {tab === "health" && <HealthTab />}
        {tab === "supervision" && <SupervisionTab />}
      </div>
    </div>
  );
}
