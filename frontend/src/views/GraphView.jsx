import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ExternalLink, List, Network, Pause, Play, Plus, Radio, Search, Share2, Trash2, X } from "lucide-react";
import DocLink from "../components/DocLink.jsx";
import { Button, Empty, ErrorBanner, Pill, Spinner, inputClass } from "../components/ui.jsx";
import { formatDateTime } from "../lib/format.js";
import { errorMessage, fetchGraph, searchEntities } from "../services/api.js";
import { SEVERITY, STATUS } from "../theme.js";

// Entity types: hue + shape (shape = second encoding: blue / violet sit in the CVD
// tolerance band, so identity never relies on colour alone). Intelligence entities
// (watchlist, sanctions) are neutral grey with their own shapes. Hues validated on the
// dark background, all pairs (graph nodes all sit next to each other).
const TYPES = {
  vessel: { label: "Navire", color: "#0284c7", shape: "circle" },
  incident: { label: "Incident", color: "#9333ea", shape: "square" },
  detection: { label: "Écho radar", color: "#dc2626", shape: "diamond" },
  watch: { label: "Liste de surveillance", color: "#94a3b8", shape: "triangle" },
  sanction: { label: "Entité sanctionnée", color: "#94a3b8", shape: "hexagon" },
};
// Link types: neutral strokes, told apart by dash pattern and named in the legend / tooltip.
const LINKS = {
  subject: { label: "objet de l'incident", dash: "" },
  observation: { label: "détection radar de l'incident", dash: "" },
  proximity: { label: "proximité AIS (même lieu, même moment)", dash: "6 3" },
  radar_neighbour: { label: "navire AIS proche d'un écho radar", dash: "2 3" },
  listed: { label: "inscrit sur la liste de surveillance", dash: "8 2 2 2" },
  sanctioned: { label: "correspond à une entité sanctionnée", dash: "8 2 2 2" },
};
const SPANS = [
  { days: 1, label: "24 h" },
  { days: 7, label: "7 j" },
  { days: 30, label: "30 j" },
  { days: 90, label: "90 j" },
];
const W = 900;
const H = 620;

function shapePath(shape, r) {
  switch (shape) {
    case "square":
      return `M${-r},${-r}H${r}V${r}H${-r}Z`;
    case "diamond":
      return `M0,${-r * 1.3}L${r * 1.3},0L0,${r * 1.3}L${-r * 1.3},0Z`;
    case "triangle":
      return `M0,${-r * 1.3}L${r * 1.2},${r}L${-r * 1.2},${r}Z`;
    case "hexagon": {
      const pts = Array.from({ length: 6 }, (_, i) => {
        const a = (Math.PI / 3) * i;
        return `${(r * 1.15 * Math.cos(a)).toFixed(1)},${(r * 1.15 * Math.sin(a)).toFixed(1)}`;
      });
      return `M${pts.join("L")}Z`;
    }
    default:
      return `M${r},0A${r},${r} 0 1,1 ${-r},0A${r},${r} 0 1,1 ${r},0Z`;
  }
}

/**
 * Fruchterman–Reingold layout, unbounded (the view fits the drawing afterwards, so nodes never
 * pile up on a border). Nodes already placed stay where they are: expanding the graph adds the
 * newcomers around them instead of reshuffling what the analyst has already read.
 */
function layout(nodes, edges, previous) {
  const pos = {};
  const settled = {};
  nodes.forEach((n, i) => {
    const old = previous[n.id];
    settled[n.id] = Boolean(old);
    const angle = (2 * Math.PI * i) / Math.max(1, nodes.length);
    // Newcomers start next to a neighbour already placed (or on a circle around the centre).
    const anchor = old ? null : edges.map((e) => (e.source === n.id ? previous[e.target] : e.target === n.id ? previous[e.source] : null)).find(Boolean);
    pos[n.id] = old
      ? { ...old }
      : anchor
        ? { x: anchor.x + 40 * Math.cos(angle), y: anchor.y + 40 * Math.sin(angle) }
        : { x: W / 2 + 200 * Math.cos(angle), y: H / 2 + 200 * Math.sin(angle) };
  });
  const k = 70;
  const gravity = 0.4 + nodes.length / 100;
  let temperature = 40;
  for (let iter = 0; iter < 300; iter += 1) {
    const disp = {};
    nodes.forEach((n) => {
      disp[n.id] = { x: 0, y: 0 };
    });
    for (let i = 0; i < nodes.length; i += 1) {
      for (let j = i + 1; j < nodes.length; j += 1) {
        const a = pos[nodes[i].id];
        const b = pos[nodes[j].id];
        let dx = a.x - b.x;
        let dy = a.y - b.y;
        let d = Math.hypot(dx, dy);
        if (d < 0.01) {
          dx = Math.cos(i + j);
          dy = Math.sin(i + j);
          d = 1;
        }
        const f = (k * k) / d;
        disp[nodes[i].id].x += (dx / d) * f;
        disp[nodes[i].id].y += (dy / d) * f;
        disp[nodes[j].id].x -= (dx / d) * f;
        disp[nodes[j].id].y -= (dy / d) * f;
      }
    }
    edges.forEach((e) => {
      const a = pos[e.source];
      const b = pos[e.target];
      if (!a || !b) return;
      const dx = a.x - b.x;
      const dy = a.y - b.y;
      const d = Math.max(0.01, Math.hypot(dx, dy));
      const f = (d * d) / k;
      disp[e.source].x -= (dx / d) * f;
      disp[e.source].y -= (dy / d) * f;
      disp[e.target].x += (dx / d) * f;
      disp[e.target].y += (dy / d) * f;
    });
    nodes.forEach((n) => {
      const p = pos[n.id];
      const d = disp[n.id];
      d.x += (W / 2 - p.x) * gravity; // keeps disconnected pieces and large graphs compact
      d.y += (H / 2 - p.y) * gravity;
      const len = Math.max(0.01, Math.hypot(d.x, d.y));
      const limit = settled[n.id] ? 0 : temperature; // already placed: does not move
      p.x += (d.x / len) * Math.min(len, limit);
      p.y += (d.y / len) * Math.min(len, limit);
    });
    temperature *= 0.985;
  }
  return pos;
}

/** viewBox fitting the visible nodes (with room for the labels). */
function fitBox(nodes, pos) {
  const pts = nodes.map((n) => pos[n.id]).filter(Boolean);
  if (!pts.length) return `0 0 ${W} ${H}`;
  const xs = pts.map((p) => p.x);
  const ys = pts.map((p) => p.y);
  const pad = 60;
  const minX = Math.min(...xs) - pad;
  const minY = Math.min(...ys) - pad;
  const width = Math.max(320, Math.max(...xs) - Math.min(...xs) + 2 * pad);
  const height = Math.max(240, Math.max(...ys) - Math.min(...ys) + 2 * pad + 20);
  return `${minX.toFixed(0)} ${minY.toFixed(0)} ${width.toFixed(0)} ${height.toFixed(0)}`;
}

function nodeColor(node) {
  return TYPES[node.type]?.color || "#94a3b8";
}

function NodeDetails({ node, edges, nodesById, onExpand, onRemove, navigate, isFocus }) {
  const links = edges.filter((e) => e.source === node.id || e.target === node.id);
  return (
    <div className="space-y-3 text-xs">
      <div>
        <p className="flex flex-wrap items-center gap-2 text-sm text-slate-100">
          {node.label}
          <Pill color={nodeColor(node)}>{TYPES[node.type]?.label}</Pill>
          {node.severity && <Pill color={SEVERITY[node.severity]?.color}>{node.severity}</Pill>}
          {node.status && <Pill color={STATUS[node.status]?.color}>{STATUS[node.status]?.label}</Pill>}
        </p>
        {node.title && <p className="mt-1 text-slate-300">{node.title}</p>}
      </div>
      <dl className="grid grid-cols-2 gap-x-3 gap-y-1.5">
        {node.mmsi && (<><dt className="text-slate-500">MMSI</dt><dd>{node.mmsi}</dd></>)}
        {node.imo && (<><dt className="text-slate-500">IMO</dt><dd>{node.imo}</dd></>)}
        {node.flag && (<><dt className="text-slate-500">Pavillon</dt><dd>{node.flag}</dd></>)}
        {node.type === "vessel" && (<><dt className="text-slate-500">Type</dt><dd>{node.fishing ? "pêche" : node.vessel_type || (node.ship_type != null ? `AIS ${node.ship_type}` : "—")}</dd></>)}
        {node.former_names?.length > 0 && (<><dt className="text-slate-500">Anciens noms</dt><dd className="text-tactical-amber">{node.former_names.join(", ")}</dd></>)}
        {node.last_seen && (<><dt className="text-slate-500">Dernière position</dt><dd>{formatDateTime(node.last_seen)}</dd></>)}
        {node.seen_at && (<><dt className="text-slate-500">Passage radar</dt><dd>{formatDateTime(node.seen_at)}</dd></>)}
        {node.risk != null && (<><dt className="text-slate-500">Risque</dt><dd>{node.risk}/100</dd></>)}
        {node.quality != null && (<><dt className="text-slate-500">Fiabilité</dt><dd>{Math.round(node.quality * 100)} %</dd></>)}
        {node.reason && (<><dt className="text-slate-500">Motif</dt><dd>{node.reason}</dd></>)}
        {node.score != null && (<><dt className="text-slate-500">Score</dt><dd>{node.score}</dd></>)}
        {node.lat != null && (<><dt className="text-slate-500">Position</dt><dd>{node.lat.toFixed(3)}, {node.lon.toFixed(3)}</dd></>)}
      </dl>
      <div className="flex flex-wrap gap-2">
        {!isFocus && ["vessel", "incident", "detection"].includes(node.type) && (
          <Button variant="primary" icon={Plus} onClick={() => onExpand(node.id)}>
            Étendre depuis ce nœud
          </Button>
        )}
        {isFocus && (
          <Button icon={Trash2} onClick={() => onRemove(node.id)}>
            Retirer du départ
          </Button>
        )}
        {node.incident_id && (
          <Button icon={ExternalLink} onClick={() => navigate("incidents", node.incident_id)}>
            Ouvrir l&apos;incident
          </Button>
        )}
        {node.url && (
          <a href={node.url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 border border-tactical-line px-3 py-1.5 text-tactical-cyan">
            Fiche OpenSanctions <ExternalLink size={12} />
          </a>
        )}
      </div>
      <div>
        <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Liens · {links.length}</p>
        <ul className="mt-1 space-y-1.5">
          {links.map((e) => {
            const other = nodesById[e.source === node.id ? e.target : e.source];
            return (
              <li key={e.id} className="border border-tactical-line p-2">
                <p className="text-slate-200">
                  {LINKS[e.type]?.label || e.label} → {other?.label}
                </p>
                <p className="text-slate-500">
                  {formatDateTime(e.first)}
                  {e.last && e.last !== e.first && ` → ${formatDateTime(e.last)}`}
                </p>
                {e.evidence.map((ev) => (
                  <p key={ev} className="text-slate-400">
                    {ev}
                  </p>
                ))}
              </li>
            );
          })}
        </ul>
      </div>
    </div>
  );
}

export default function GraphView({ initialFocus, navigate }) {
  const [focus, setFocus] = useState(initialFocus ? [initialFocus] : []);
  const [days, setDays] = useState(7);
  const [depth, setDepth] = useState(1);
  const [endAt, setEndAt] = useState(""); // "" = live
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [selected, setSelected] = useState(null);
  const [hiddenLinks, setHiddenLinks] = useState({});
  const [cursor, setCursor] = useState(1); // 0..1 within the window: links appear as time passes
  const [playing, setPlaying] = useState(false);
  const [listView, setListView] = useState(false);
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const positions = useRef({});
  const drag = useRef(null);

  useEffect(() => {
    if (initialFocus) setFocus((f) => (f.includes(initialFocus) ? f : [initialFocus]));
  }, [initialFocus]);

  useEffect(() => {
    if (!focus.length) {
      setData(null);
      return undefined;
    }
    let alive = true;
    setLoading(true);
    fetchGraph(focus, { days, depth, at: endAt ? new Date(`${endAt}:00Z`).toISOString() : null })
      .then((d) => {
        if (!alive) return;
        positions.current = layout(d.nodes, d.edges, positions.current);
        setData(d);
        setError("");
        setCursor(1);
      })
      .catch((err) => alive && setError(errorMessage(err, "Graphe indisponible")))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [focus, days, depth, endAt]);

  useEffect(() => {
    if (query.trim().length < 2) {
      setResults([]);
      return undefined;
    }
    const timer = setTimeout(() => {
      searchEntities(query.trim())
        .then((d) => setResults(d.results))
        .catch(() => setResults([]));
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  // Replay of the window: the cursor sweeps from start to end in ~12 s.
  useEffect(() => {
    if (!playing) return undefined;
    const timer = setInterval(() => {
      setCursor((c) => {
        if (c >= 1) {
          setPlaying(false);
          return 1;
        }
        return Math.min(1, c + 1 / 120);
      });
    }, 100);
    return () => clearInterval(timer);
  }, [playing]);

  const windowStart = data ? Date.parse(data.window.start) : 0;
  const windowEnd = data ? Date.parse(data.window.end) : 0;
  const cursorMs = windowStart + (windowEnd - windowStart) * cursor;

  const { visibleNodes, visibleEdges, nodesById } = useMemo(() => {
    if (!data) return { visibleNodes: [], visibleEdges: [], nodesById: {} };
    const byId = Object.fromEntries(data.nodes.map((n) => [n.id, n]));
    const edges = data.edges.filter((e) => !hiddenLinks[e.type] && (!e.first || Date.parse(e.first) <= cursorMs));
    const linked = new Set(edges.flatMap((e) => [e.source, e.target]));
    const nodes = data.nodes.filter((n) => n.focus || linked.has(n.id));
    return { visibleNodes: nodes, visibleEdges: edges, nodesById: byId };
  }, [data, hiddenLinks, cursorMs]);

  const addFocus = useCallback((id) => {
    setFocus((f) => (f.includes(id) ? f : [...f, id].slice(-10)));
    setQuery("");
    setResults([]);
  }, []);
  const removeFocus = useCallback((id) => setFocus((f) => f.filter((x) => x !== id)), []);

  const onWheel = (event) => {
    const factor = event.deltaY < 0 ? 1.15 : 1 / 1.15;
    setView((v) => ({ ...v, k: Math.min(4, Math.max(0.3, v.k * factor)) }));
  };
  const onPointerDown = (event) => {
    if (event.target.dataset.node) return;
    drag.current = { x: event.clientX, y: event.clientY, start: view };
    event.currentTarget.setPointerCapture?.(event.pointerId);
  };
  const onPointerMove = (event) => {
    if (!drag.current) return;
    const { x, y, start } = drag.current;
    setView({ ...start, x: start.x + (event.clientX - x), y: start.y + (event.clientY - y) });
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  const selectedNode = selected?.kind === "node" ? nodesById[selected.id] : null;
  const selectedEdge = selected?.kind === "edge" ? data?.edges.find((e) => e.id === selected.id) : null;

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-tactical-line bg-tactical-panel/60 px-3 py-2 text-xs sm:px-4">
        <h1 className="flex items-center gap-2 text-sm text-slate-100">
          <Network size={15} className="text-tactical-cyan" /> Graphe d&apos;entités <DocLink page="situation/graphe/" label="graphe d'entités" />
        </h1>
        <div className="relative min-w-0 basis-full sm:ml-2 sm:min-w-[16rem] sm:basis-auto sm:flex-1">
          <Search size={13} className="absolute left-2 top-2.5 text-slate-500" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Navire (nom, MMSI, IMO, indicatif) ou incident (KRK-…)"
            className={`${inputClass} pl-7`}
            aria-label="Rechercher une entité"
          />
          {results.length > 0 && (
            <ul className="absolute z-30 mt-1 max-h-72 w-full overflow-y-auto border border-tactical-line bg-tactical-panel shadow-2xl">
              {results.map((r) => (
                <li key={r.id}>
                  <button type="button" onClick={() => addFocus(r.id)} className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left hover:bg-white/5">
                    <span className="text-slate-200">{r.label}</span>
                    <span className="truncate text-slate-500">{r.detail}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
        <div className="flex border border-tactical-line" role="radiogroup" aria-label="Fenêtre de temps">
          {SPANS.map((s) => (
            <button key={s.days} type="button" role="radio" aria-checked={days === s.days} onClick={() => setDays(s.days)}
              className={`px-2 py-1 ${days === s.days ? "bg-tactical-cyan/15 text-tactical-cyan" : "text-slate-400 hover:text-white"}`}>
              {s.label}
            </button>
          ))}
        </div>
        <label className="flex flex-wrap items-center gap-1 text-slate-400">
          jusqu&apos;au
          <input type="datetime-local" value={endAt} onChange={(e) => setEndAt(e.target.value)} className={`${inputClass} w-auto py-1`} title="Fin de la fenêtre (UTC). Vide = maintenant" />
          {endAt ? (
            <button type="button" onClick={() => setEndAt("")} className="flex items-center gap-1 text-tactical-green"><Radio size={12} /> direct</button>
          ) : (
            <span className="text-tactical-green">maintenant</span>
          )}
        </label>
        <select value={depth} onChange={(e) => setDepth(Number(e.target.value))} className={`${inputClass} w-auto py-1`} aria-label="Profondeur">
          <option value={1}>liens directs</option>
          <option value={2}>liens des liens</option>
        </select>
        <Button icon={List} onClick={() => setListView((v) => !v)}>{listView ? "Graphe" : "Liste des liens"}</Button>
      </div>

      {focus.length > 0 && (
        <div className="flex flex-wrap items-center gap-2 border-b border-tactical-line px-4 py-1.5 text-[11px]">
          <span className="text-slate-500">Départ :</span>
          {focus.map((f) => (
            <span key={f} className="flex items-center gap-1 border border-tactical-cyan/40 px-2 py-0.5 text-tactical-cyan">
              {nodesById[f]?.label || f}
              <button type="button" onClick={() => removeFocus(f)} aria-label="Retirer"><X size={11} /></button>
            </span>
          ))}
          {data?.truncated && <span className="text-tactical-amber">Graphe limité à {data.limits.nodes} nœuds : réduisez la fenêtre ou la profondeur.</span>}
        </div>
      )}
      <ErrorBanner message={error} />

      {!focus.length && (
        <div className="p-6">
          <Empty>
            Choisissez un point de départ : un navire ou un incident (recherche ci-dessus), ou depuis une fiche navire, une cible radar ou
            un dossier d&apos;incident (« Explorer les liens »). Le graphe montre qui est lié à qui, depuis quand, et pourquoi.
          </Empty>
        </div>
      )}

      {focus.length > 0 && (
        <div className="flex min-h-0 flex-1 flex-col md:flex-row">
          <div className="relative min-h-[16rem] min-w-0 flex-1">
            {loading && <div className="absolute left-4 top-3 z-10"><Spinner label="Construction du graphe…" /></div>}
            {!listView && data && (
              <svg
                viewBox={fitBox(visibleNodes, positions.current)}
                className="h-full w-full cursor-grab touch-none select-none"
                onWheel={onWheel}
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerLeave={onPointerUp}
                role="img"
                aria-label={`Graphe : ${visibleNodes.length} entités et ${visibleEdges.length} liens au ${new Date(cursorMs).toISOString().slice(0, 16)} Z`}
              >
                <g transform={`translate(${view.x},${view.y}) scale(${view.k})`}>
                  {visibleEdges.map((e) => {
                    const a = positions.current[e.source];
                    const b = positions.current[e.target];
                    if (!a || !b) return null;
                    const active = selected?.id === e.id || selected?.id === e.source || selected?.id === e.target;
                    return (
                      <g key={e.id}>
                        <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={active ? "#22d3ee" : "#64748b"} strokeWidth={active ? 2.5 : 1.5}
                          strokeDasharray={LINKS[e.type]?.dash || ""} opacity={active || !selected ? 0.9 : 0.35} />
                        <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="transparent" strokeWidth={10} className="cursor-pointer"
                          onClick={() => setSelected({ kind: "edge", id: e.id })}>
                          <title>{`${LINKS[e.type]?.label || e.label} — ${e.evidence[0] || ""}`}</title>
                        </line>
                      </g>
                    );
                  })}
                  {visibleNodes.map((n) => {
                    const p = positions.current[n.id];
                    if (!p) return null;
                    const type = TYPES[n.type] || TYPES.vessel;
                    const r = n.focus ? 11 : 8;
                    const active = selected?.id === n.id;
                    return (
                      <g key={n.id} transform={`translate(${p.x},${p.y})`} className="cursor-pointer" onClick={() => setSelected({ kind: "node", id: n.id })}>
                        <path d={shapePath(type.shape, r + 3)} fill="#070b10" />
                        <path data-node={n.id} d={shapePath(type.shape, r)} fill={type.color} stroke={active ? "#22d3ee" : n.focus ? "#e2e8f0" : "#070b10"} strokeWidth={active || n.focus ? 2.5 : 1.5} />
                        {n.severity && <circle cx={r} cy={-r} r={3.5} fill={SEVERITY[n.severity]?.color} stroke="#070b10" />}
                        <text y={r + 13} textAnchor="middle" fontSize="10" fill="#cbd5e1" pointerEvents="none">
                          {n.label.length > 22 ? `${n.label.slice(0, 21)}…` : n.label}
                        </text>
                        <title>{`${type.label} : ${n.label}`}</title>
                      </g>
                    );
                  })}
                </g>
              </svg>
            )}
            {listView && data && (
              <div className="h-full overflow-auto p-4 text-xs">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="text-left text-[10px] uppercase tracking-wider text-slate-500">
                      <th className="border-b border-tactical-line py-1">Depuis</th>
                      <th className="border-b border-tactical-line py-1">Entité</th>
                      <th className="border-b border-tactical-line py-1">Lien</th>
                      <th className="border-b border-tactical-line py-1">Entité</th>
                      <th className="border-b border-tactical-line py-1">Preuve</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...visibleEdges].sort((a, b) => String(a.first).localeCompare(String(b.first))).map((e) => (
                      <tr key={e.id} className="align-top hover:bg-white/5" onClick={() => setSelected({ kind: "edge", id: e.id })}>
                        <td className="border-b border-tactical-line py-1.5 pr-2 text-slate-400">{formatDateTime(e.first)}</td>
                        <td className="border-b border-tactical-line py-1.5 pr-2">{nodesById[e.source]?.label}</td>
                        <td className="border-b border-tactical-line py-1.5 pr-2 text-slate-400">{LINKS[e.type]?.label || e.label}</td>
                        <td className="border-b border-tactical-line py-1.5 pr-2">{nodesById[e.target]?.label}</td>
                        <td className="border-b border-tactical-line py-1.5 text-slate-500">{e.evidence.join(" · ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {visibleEdges.length === 0 && <Empty>Aucun lien dans la fenêtre à cet instant.</Empty>}
              </div>
            )}
            {data && (
              <div className="absolute bottom-3 left-3 max-h-[calc(100%-1.5rem)] space-y-1 overflow-y-auto border border-tactical-line bg-tactical-bg/90 p-2 text-[10px]">
                {Object.entries(TYPES).map(([id, t]) => (
                  <p key={id} className="flex items-center gap-1.5 text-slate-300">
                    <svg width="14" height="14" viewBox="-8 -8 16 16" aria-hidden="true"><path d={shapePath(t.shape, 5)} fill={t.color} /></svg>
                    {t.label}
                  </p>
                ))}
                <div className="border-t border-tactical-line pt-1">
                  {Object.entries(LINKS).map(([id, l]) => (
                    <label key={id} className="flex cursor-pointer items-center gap-1.5 text-slate-400">
                      <input type="checkbox" checked={!hiddenLinks[id]} onChange={() => setHiddenLinks((h) => ({ ...h, [id]: !h[id] }))} className="accent-cyan-400" />
                      <svg width="22" height="6" aria-hidden="true"><line x1="0" y1="3" x2="22" y2="3" stroke="#94a3b8" strokeWidth="1.5" strokeDasharray={l.dash} /></svg>
                      {l.label}
                    </label>
                  ))}
                </div>
              </div>
            )}
          </div>

          <aside className="max-h-[40%] shrink-0 overflow-y-auto border-t border-tactical-line bg-tactical-panel p-4 md:max-h-none md:w-96 md:max-w-[45%] md:border-l md:border-t-0">
            {!selected && data && (
              <div className="space-y-2 text-xs text-slate-400">
                <p className="text-slate-200">
                  {visibleNodes.length} entités · {visibleEdges.length} liens du {formatDateTime(data.window.start)} au {formatDateTime(new Date(cursorMs).toISOString())}
                </p>
                <p>Cliquez une entité ou un lien pour voir pourquoi ils sont reliés. « Étendre » ajoute le voisinage d&apos;une entité.</p>
                <p>
                  Proximité AIS : à moins de {data.limits.proximity_km} km dans le même créneau de 10 min, au moins deux fois. Écho radar :
                  navires AIS à moins de {data.limits.radar_neighbour_km} km au moment du passage. Seul ce qui était connu à la fin de la
                  fenêtre est utilisé.
                </p>
              </div>
            )}
            {selectedNode && (
              <NodeDetails node={selectedNode} edges={visibleEdges} nodesById={nodesById} onExpand={addFocus}
                onRemove={removeFocus} navigate={navigate} isFocus={focus.includes(selectedNode.id)} />
            )}
            {selectedEdge && (
              <div className="space-y-2 text-xs">
                <p className="flex items-center gap-2 text-sm text-slate-100"><Share2 size={14} /> {LINKS[selectedEdge.type]?.label || selectedEdge.label}</p>
                <p className="text-slate-300">
                  {nodesById[selectedEdge.source]?.label} ↔ {nodesById[selectedEdge.target]?.label}
                </p>
                <p className="text-slate-500">
                  {formatDateTime(selectedEdge.first)}
                  {selectedEdge.last && selectedEdge.last !== selectedEdge.first && ` → ${formatDateTime(selectedEdge.last)}`}
                </p>
                <p className="text-[10px] uppercase tracking-[0.2em] text-slate-500">Preuves</p>
                <ul className="space-y-1">
                  {selectedEdge.evidence.map((ev) => <li key={ev} className="text-slate-300">{ev}</li>)}
                </ul>
              </div>
            )}
            {selected && <Button className="mt-4" icon={X} onClick={() => setSelected(null)}>Fermer</Button>}
          </aside>
        </div>
      )}

      {data && (
        <div className="flex items-center gap-3 border-t border-tactical-line bg-tactical-panel/95 px-3 py-2 text-[11px] text-slate-400 sm:px-4">
          <button type="button" onClick={() => { if (cursor >= 1) setCursor(0); setPlaying((p) => !p); }} className="text-tactical-cyan" aria-label={playing ? "Pause" : "Lecture"}>
            {playing ? <Pause size={14} /> : <Play size={14} />}
          </button>
          <span className="hidden w-40 shrink-0 sm:inline">{formatDateTime(data.window.start)}</span>
          <input type="range" min={0} max={1} step={0.001} value={cursor} onChange={(e) => { setPlaying(false); setCursor(Number(e.target.value)); }}
            className="flex-1 accent-cyan-400" aria-label="Instant affiché dans la fenêtre" />
          <span className="shrink-0 text-right text-slate-200 sm:w-44">{formatDateTime(new Date(cursorMs).toISOString())}</span>
        </div>
      )}
    </div>
  );
}
