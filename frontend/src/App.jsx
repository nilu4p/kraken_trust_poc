import { Suspense, lazy, useCallback, useEffect, useState } from "react";
import { AlarmClock, FileLock2, Landmark, ListChecks, Map as MapIcon, Network, ScrollText, Settings, ShieldAlert, Siren } from "lucide-react";
import { AppShell, SessionGate } from "./components/AppShell.jsx";
import Clocks from "./components/Clocks.jsx";
import { Spinner } from "./components/ui.jsx";
import { hasRole } from "./lib/format.js";
import { fetchKpis } from "./services/api.js";
import { SEVERITY } from "./theme.js";

// Views load on demand: the first screen (map) does not download the administration,
// audit or evidence code (smaller first load, each chunk cached separately).
const SituationView = lazy(() => import("./views/SituationView.jsx"));
const IncidentsView = lazy(() => import("./views/IncidentsView.jsx"));
const EvidenceView = lazy(() => import("./views/EvidenceView.jsx"));
const AuditView = lazy(() => import("./views/AuditView.jsx"));
const AdminView = lazy(() => import("./views/AdminView.jsx"));
const RulesView = lazy(() => import("./views/RulesView.jsx"));
const GraphView = lazy(() => import("./views/GraphView.jsx"));
const CommandView = lazy(() => import("./views/CommandView.jsx"));

const NAV = [
  { id: "situation", label: "Situation", icon: MapIcon, role: "viewer", hint: "Carte tactique de la ZEE : cibles, AIS, imagerie" },
  { id: "incidents", label: "Incidents", icon: Siren, role: "viewer", hint: "File d'incidents, SLA, playbooks" },
  { id: "command", label: "Commandement", icon: Landmark, role: "viewer", hint: "Aide à la décision, moyens, rapports signés" },
  { id: "graph", label: "Graphe", icon: Network, role: "viewer", hint: "Liens entre navires, zones et événements" },
  { id: "evidence", label: "Preuves", icon: FileLock2, role: "viewer", hint: "Dossiers de preuves scellés (C2PA)" },
  { id: "rules", label: "Règles", icon: ListChecks, role: "viewer", hint: "Règles de détection 24/7" },
  { id: "audit", label: "Audit", icon: ScrollText, role: "supervisor", hint: "Journal d'audit chaîné" },
  { id: "admin", label: "Administration", icon: Settings, role: "admin", hint: "Comptes, clés, paramètres, sources" },
];

/** "#/incidents/12" → { view: "incidents", param: "12" } */
function parseHash() {
  const [, view = "situation", param = ""] = window.location.hash.split("/");
  return { view: NAV.some((n) => n.id === view) ? view : "situation", param };
}

function KpiChips({ kpis, onOpen, className = "hidden shrink-0 items-center gap-1.5 whitespace-nowrap lg:flex" }) {
  if (!kpis) return null;
  return (
    <div className={className}>
      {["P1", "P2"].map((p) => (
        <button
          key={p}
          type="button"
          onClick={() => onOpen("incidents")}
          className="flex items-center gap-1 border px-2 py-1 font-mono text-[10px] uppercase tracking-wider"
          style={{ color: SEVERITY[p].color, borderColor: `${SEVERITY[p].color}66` }}
          title={`Incidents ${p} ouverts — ouvrir la file`}
        >
          <ShieldAlert size={12} /> {p} {kpis.open_by_severity?.[p] ?? 0}
        </button>
      ))}
      <button
        type="button"
        onClick={() => onOpen("incidents")}
        className={`flex items-center gap-1 border px-2 py-1 font-mono text-[10px] uppercase tracking-wider ${
          kpis.sla_breached ? "border-tactical-red/60 text-tactical-red" : "border-tactical-line text-slate-500"
        }`}
        title="Incidents ouverts dont une échéance (triage ou résolution) est dépassée"
      >
        <AlarmClock size={12} /> SLA {kpis.sla_breached}
      </button>
      <span
        className="border border-tactical-line px-2 py-1 font-mono text-[10px] uppercase tracking-wider text-slate-500"
        title={`${kpis.open} incident(s) ouvert(s), dont ${kpis.unassigned} non assigné(s)`}
      >
        Ouverts {kpis.open}
        {kpis.unassigned > 0 && <span className="text-tactical-amber"> · {kpis.unassigned} libres</span>}
      </span>
    </div>
  );
}

function Console({ user, onLogout, onUserChange }) {
  const [route, setRoute] = useState(parseHash());
  const [kpis, setKpis] = useState(null);

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  const refreshKpis = useCallback(() => {
    fetchKpis().then(setKpis).catch(() => {});
  }, []);

  useEffect(() => {
    refreshKpis();
    const timer = setInterval(refreshKpis, 30000);
    return () => clearInterval(timer);
  }, [refreshKpis]);

  const navigate = useCallback((view, param = "") => {
    window.location.hash = `#/${view}${param !== "" && param != null ? `/${param}` : ""}`;
  }, []);

  const allowed = NAV.filter((n) => hasRole(user, n.role));
  const current = allowed.some((n) => n.id === route.view) ? route.view : "situation";

  return (
    <AppShell
      user={user}
      nav={allowed}
      current={current}
      onLogout={onLogout}
      onUserChange={onUserChange}
      headerExtras={
        <>
          <Clocks />
          <KpiChips kpis={kpis} onOpen={navigate} />
        </>
      }
      drawerExtras={(close) => (
        <KpiChips
          kpis={kpis}
          onOpen={(v) => {
            close();
            navigate(v);
          }}
          className="flex flex-wrap items-center gap-1.5"
        />
      )}
    >
      <Suspense fallback={<div className="flex h-full items-center justify-center"><Spinner /></div>}>
        {current === "situation" && <SituationView user={user} navigate={navigate} onIncidentChange={refreshKpis} focus={route.param} />}
        {current === "incidents" && <IncidentsView user={user} selectedId={route.param} navigate={navigate} onChange={refreshKpis} />}
        {current === "evidence" && <EvidenceView user={user} navigate={navigate} />}
        {current === "rules" && <RulesView user={user} navigate={navigate} />}
        {current === "graph" && <GraphView initialFocus={route.param ? decodeURIComponent(route.param) : ""} navigate={navigate} />}
        {current === "command" && <CommandView user={user} param={route.param} navigate={navigate} />}
        {current === "audit" && <AuditView />}
        {current === "admin" && <AdminView user={user} />}
      </Suspense>
    </AppShell>
  );
}

export default function App() {
  return <SessionGate>{(user, session) => <Console user={user} {...session} />}</SessionGate>;
}
