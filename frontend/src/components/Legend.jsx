import { useState } from "react";
import { ChevronDown, ChevronRight, Info, X } from "lucide-react";
import {
  AIS_CATEGORIES,
  EFFORT_COLOR,
  EFFORT_FOREIGN_COLOR,
  EVENTS,
  FISHING_LIMITS,
  FOOTPRINTS,
  GFW_SAR,
  ISOBATH,
  MPA,
  SELECTED_COLOR,
  TARGET,
  ZONES,
} from "../theme.js";

const OCEAN_LABELS = {
  sst: "Température de surface (bleu froid → rouge chaud)",
  chl: "Chlorophylle-a (vert intense = eaux productives)",
  current: "Courants de surface",
};

function Dot({ color, pulse = false }) {
  return <span className={`legend-dot shrink-0${pulse ? " animate-pulse" : ""}`} style={{ backgroundColor: color }} />;
}

function Arrow({ color }) {
  return (
    <svg width="12" height="12" viewBox="0 0 18 18" aria-hidden="true" className="shrink-0">
      <path d="M9 2 L14 15 L9 12 L4 15 Z" fill={color} />
    </svg>
  );
}

function Diamond({ color }) {
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true" className="shrink-0">
      <rect x="2.5" y="2.5" width="7" height="7" transform="rotate(45 6 6)" fill={color} />
    </svg>
  );
}

function Line({ color, dash }) {
  return (
    <svg width="18" height="8" viewBox="0 0 18 8" aria-hidden="true" className="shrink-0">
      <line x1="0" y1="4" x2="18" y2="4" stroke={color} strokeWidth="2" strokeDasharray={dash} />
    </svg>
  );
}

function Area({ color, dashed = true }) {
  return (
    <span
      className={`inline-block h-2.5 w-3.5 shrink-0 border ${dashed ? "border-dashed" : ""}`}
      style={{ borderColor: color, backgroundColor: `${color}33` }}
    />
  );
}

function Row({ symbol, children, hint }) {
  return (
    <li className="flex items-start gap-2">
      <span className="mt-0.5 flex w-[18px] justify-center">{symbol}</span>
      <span className="min-w-0">
        {children}
        {hint && <span className="block text-[10px] text-slate-500">{hint}</span>}
      </span>
    </li>
  );
}

function Group({ id, title, open, onToggle, children }) {
  return (
    <section className="border-t border-tactical-line/70 pt-1.5 first:border-0 first:pt-0">
      <button
        type="button"
        onClick={() => onToggle(id)}
        aria-expanded={open}
        className="flex w-full items-center gap-1 text-[10px] uppercase tracking-widest text-slate-400 hover:text-white"
      >
        {open ? <ChevronDown size={11} /> : <ChevronRight size={11} />} {title}
      </button>
      {open && <ul className="mt-1.5 space-y-1">{children}</ul>}
    </section>
  );
}

/**
 * Map legend split by family (targets, AIS, detections, boundaries, imagery). Only what is
 * currently drawn is listed; each family folds independently.
 */
export default function Legend({ layers, visual, customLayers = [], gfwDays = 30 }) {
  const [open, setOpen] = useState(false);
  const [folded, setFolded] = useState({});
  const toggle = (id) => setFolded((f) => ({ ...f, [id]: !f[id] }));

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 border border-tactical-line bg-tactical-bg/90 px-2.5 py-1.5 text-slate-300 shadow-xl hover:text-white"
      >
        <Info size={13} className="text-tactical-cyan" />
        <span className="flex items-center gap-1">
          <Dot color={TARGET.priority.color} />
          <Dot color={TARGET.check.color} />
          <Dot color={AIS_CATEGORIES.french.color} />
        </span>
        Légende
      </button>
    );
  }

  const activeCustom = customLayers.filter((l) => layers[`custom:${l.id}`]);
  const groups = [
    {
      id: "targets",
      title: "Cibles radar (sans AIS)",
      rows: [
        <Row key="p" symbol={<Dot color={TARGET.priority.color} pulse />} hint={TARGET.priority.hint}>{TARGET.priority.label}</Row>,
        <Row key="c" symbol={<Dot color={TARGET.check.color} />} hint={TARGET.check.hint}>{TARGET.check.label}</Row>,
        <Row key="s" symbol={<Dot color={SELECTED_COLOR} />}>Cible sélectionnée</Row>,
      ],
    },
    {
      id: "ais",
      title: "Navires AIS (flèche = cap)",
      rows: layers.ais
        ? [
            ...Object.entries(AIS_CATEGORIES).map(([key, cat]) => <Row key={key} symbol={<Arrow color={cat.color} />}>{cat.label}</Row>),
            layers.tracks && <Row key="trk" symbol={<Line color={AIS_CATEGORIES.french.color} />}>Trace des 6 dernières heures</Row>,
          ]
        : [],
    },
    {
      id: "detections",
      title: "Détections et comportements",
      rows: [
        layers.gfwSar && <Row key="su" symbol={<Diamond color={GFW_SAR.unmatched.color} />}>{GFW_SAR.unmatched.label}</Row>,
        layers.gfwSar && <Row key="sm" symbol={<Diamond color={GFW_SAR.matched.color} />}>{GFW_SAR.matched.label}</Row>,
        layers.effort && (
          <Row key="eff" symbol={<Dot color={EFFORT_FOREIGN_COLOR} />} hint={`taille = heures de pêche AIS (${gfwDays} j)`}>
            Effort de pêche GFW, pavillon étranger
          </Row>
        ),
        layers.effort && <Row key="eff-fr" symbol={<Dot color={EFFORT_COLOR} />}>Effort de pêche GFW, pavillon français</Row>,
        ...(layers.events ? Object.entries(EVENTS).map(([key, e]) => <Row key={key} symbol={<Dot color={e.color} />}>{e.label} (GFW)</Row>) : []),
      ],
    },
    {
      id: "zones",
      title: "Zones et limites",
      rows: [
        ...(layers.zones ? Object.entries(ZONES).map(([key, z]) => <Row key={key} symbol={<Line color={z.color} dash={z.dash} />}>{z.label}</Row>) : []),
        ...(layers.limits ? Object.entries(FISHING_LIMITS).map(([key, l]) => <Row key={key} symbol={<Line color={l.color} dash={l.dash} />}>{l.label}</Row>) : []),
        layers.mpa && <Row key="mpa" symbol={<Area color={MPA.color} />}>{MPA.label}</Row>,
        layers.isobaths && <Row key="iso" symbol={<Line color={ISOBATH.color} />} hint="20, 50, 100 m (plateau)">Isobathes du plateau</Row>,
        layers.isobaths && <Row key="iso-shelf" symbol={<Line color={ISOBATH.shelf} />}>{ISOBATH.shelfLabel}</Row>,
        layers.isobaths && <Row key="iso-deep" symbol={<Line color={ISOBATH.deep} />} hint="1 000 à 4 000 m">Isobathes du talus et des fonds</Row>,
      ],
    },
    {
      id: "imagery",
      title: "Imagerie et couches",
      rows: [
        layers.scenes && <Row key="s1" symbol={<Area color={FOOTPRINTS.s1.color} />}>{FOOTPRINTS.s1.label}</Row>,
        layers.optical && <Row key="opt" symbol={<Area color={FOOTPRINTS.optical.color} />}>{FOOTPRINTS.optical.label}</Row>,
        layers.nightLights && (
          <Row key="dnb" symbol={<Dot color="#fde68a" />} hint="point isolé en mer = navire éclairé (nuit précédente)">Lumières nocturnes VIIRS</Row>
        ),
        visual?.oceanLayer && visual.oceanLayer !== "none" && (
          <Row key="ocean" symbol={<Area color="#38bdf8" dashed={false} />}>{OCEAN_LABELS[visual.oceanLayer]}</Row>
        ),
      ],
    },
    {
      id: "custom",
      title: "Sources personnalisées",
      rows: activeCustom.map((l) => (
        <Row
          key={l.id}
          symbol={l.kind === "features" ? <Dot color={l.style?.color || "#22d3ee"} /> : <Area color="#94a3b8" dashed={false} />}
          hint={l.kind === "features" ? l.category : "couche image"}
        >
          {l.name}
        </Row>
      )),
    },
  ]
    .map((g) => ({ ...g, rows: g.rows.filter(Boolean) }))
    .filter((g) => g.rows.length);

  return (
    <div className="w-64 max-w-full border border-tactical-line bg-tactical-bg/90 text-slate-300 shadow-xl">
      <button
        type="button"
        onClick={() => setOpen(false)}
        className="flex w-full items-center justify-between border-b border-tactical-line px-3 py-2 text-[10px] uppercase tracking-widest text-tactical-cyan"
      >
        Légende <X size={12} />
      </button>
      <div className="max-h-[35vh] space-y-2 sm:max-h-[55vh] overflow-y-auto px-3 py-2">
        {groups.map((g) => (
          <Group key={g.id} id={g.id} title={g.title} open={!folded[g.id]} onToggle={toggle}>
            {g.rows}
          </Group>
        ))}
      </div>
      <div className="space-y-1 border-t border-tactical-line px-3 py-2 text-[10px] text-slate-500">
        <p>Seules les couches affichées sont listées. Clic : fiche cible · Clic droit : dossier du point</p>
        <a href="/docs/situation/legende/" target="_blank" rel="noopener" className="block text-tactical-cyan underline">
          Légende détaillée ↗
        </a>
      </div>
    </div>
  );
}
