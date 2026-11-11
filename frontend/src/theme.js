// Single source of truth for every colour on the map, legend, badges and lists.
// Families never overlap: risk = red→amber→slate, AIS = green, GFW events =
// fuchsia/violet/teal/blue, zones = cyan/sky/white, imagery footprints = indigo/lime.

export const RISK = {
  critical: { color: "#ff3b3b", label: "Critique", min: 75 },
  high: { color: "#ff8c1a", label: "Élevé", min: 55 },
  medium: { color: "#f5c542", label: "Moyen", min: 35 },
  low: { color: "#94a3b8", label: "Faible", min: 0 },
  unscored: { color: "#ff3b3b", label: "Non évalué", min: null },
};

export const QUALITY = {
  reliable: { color: "#3dff8a", label: "Fiable" },
  check: { color: "#f5c542", label: "À vérifier" },
  doubtful: { color: "#ff3b3b", label: "Douteuse" },
};

export const C2PA = {
  verified: { color: "#3dff8a", label: "Preuve intègre — manifeste C2PA signé par le certificat de l'instance KRAKEN" },
  fallback: { color: "#f5c542", label: "Manifeste signé — signature KRAKEN sans C2PA embarqué" },
  unverifiable: { color: "#fb923c", label: "Non vérifiable — empreintes sans signature (ancienne preuve)" },
  tampered: { color: "#ff3b3b", label: "Preuve altérée — intégrité rompue depuis le scellement" },
};

export const AIS_COLOR = "#3dff8a";
export const SELECTED_COLOR = "#00e5ff";

export const EVENTS = {
  gap: { color: "#e879f9", label: "Coupure AIS" },
  encounter: { color: "#a78bfa", label: "Rencontre en mer" },
  loitering: { color: "#2dd4bf", label: "Flânage" },
  fishing: { color: "#60a5fa", label: "Pêche" },
  port_visit: { color: "#facc15", label: "Escale au port" },
};

export const ZONES = {
  eez: { color: "#00e5ff", label: "ZEE (200 NM)", dash: "6 4", weight: 2, fill: 0.04 },
  contiguous: { color: "#38bdf8", label: "Zone contiguë (24 NM)", dash: "2 4", weight: 1, fill: 0.02 },
  territorial: { color: "#e2e8f0", label: "Mer territoriale (12 NM)", dash: "2 4", weight: 1, fill: 0.02 },
};

export const FOOTPRINTS = {
  s1: { color: "#818cf8", label: "Empreinte Sentinel-1" },
  optical: { color: "#bef264", label: "Image optique (Pléiades / SPOT / S2)" },
  s2: { color: "#fbbf24", label: "Sentinel-2 10 m (dernière image avant l'instant)" },
};

export function riskKey(level, score) {
  if (RISK[level] && level !== "unscored") return level;
  if (score == null) return "unscored";
  if (score >= 75) return "critical";
  if (score >= 55) return "high";
  if (score >= 35) return "medium";
  return "low";
}

export const SEVERITY = {
  P1: { color: "#ff3b3b", label: "P1 — Critique" },
  P2: { color: "#ff8c1a", label: "P2 — Élevée" },
  P3: { color: "#f5c542", label: "P3 — Moyenne" },
  P4: { color: "#94a3b8", label: "P4 — Faible" },
};

export const STATUS = {
  new: { color: "#ff3b3b", label: "Nouveau" },
  triage: { color: "#f5c542", label: "En triage" },
  qualified: { color: "#38bdf8", label: "Qualifié" },
  escalated: { color: "#a78bfa", label: "Escaladé" },
  response: { color: "#00e5ff", label: "Intervention" },
  closed: { color: "#64748b", label: "Clos" },
};

export const INTEGRITY = C2PA;

// Simplified target signage: two classes only on the map.
export const TARGET = {
  priority: { color: "#ff3b3b", label: "Cible prioritaire", hint: "risque ≥ 55 ou P1/P2 — clignote" },
  check: { color: "#f5c542", label: "Cible à vérifier", hint: "risque < 55" },
  listed: { color: "#ff3b3b", label: "Navire AIS sur liste INN" },
};

export function targetClass(props = {}) {
  if (["P1", "P2"].includes(props.incident_severity)) return "priority";
  return (props.risk_score ?? 100) >= 55 ? "priority" : "check";
}

export const FISHING_LIMITS = {
  fishing_100nm: { color: "#c4b5fd", label: "Limite de pêche 100 NM (SHOM)", dash: "10 6" },
  fishing_3nm: { color: "#e2e8f0", label: "Limite des 3 NM (SHOM)", dash: "2 6" },
};

// Live AIS vessels are drawn as heading arrows (shape ≠ radar circles), coloured by category.
export const AIS_CATEGORIES = {
  french: { color: "#3dff8a", label: "Pavillon français" },
  foreign: { color: "#93c5fd", label: "Pavillon étranger" },
  foreign_fishing: { color: "#fb923c", label: "Pêcheur étranger" },
  licensed: { color: "#5eead4", label: "Pêcheur sous licence" },
  listed: { color: "#ff3b3b", label: "Liste INN" },
  unknown: { color: "#94a3b8", label: "Pavillon inconnu" },
};

export const GFW_SAR = {
  unmatched: { color: "#f472b6", label: "Détection SAR GFW sans AIS" },
  matched: { color: "#64748b", label: "Détection SAR GFW avec AIS" },
};

export const EFFORT_COLOR = "#38bdf8";
// Effort of foreign-flagged vessels: same orange as the "foreign fisher" AIS category.
export const EFFORT_FOREIGN_COLOR = "#fb923c";

// Depth contours (ETOPO): pale shallow → dark deep; the 200 m shelf break stands out.
export const ISOBATH = { color: "#7dd3fc", deep: "#3b82f6", shelf: "#93c5fd", label: "Isobathes (m)", shelfLabel: "Rebord du plateau (200 m)" };

// Marine protected areas (WDPA) — polygons, never confused with point symbols.
export const MPA = { color: "#10b981", label: "Aire marine protégée (WDPA)" };

// Feeds fused in the vessel history.
export const FEEDS = {
  aisstream: { label: "aisstream.io", short: "AIS" },
  aishub: { label: "AISHub", short: "AIS-H" },
  vesselapi: { label: "VesselAPI", short: "AIS-V" },
  vms: { label: "VMS (licence)", short: "VMS" },
};

// Activity report chart (validated pair on the dark panel: CVD ΔE ≥ 24): foreign at the baseline.
export const ACTIVITY = {
  foreign: { color: "#e0661a", label: "Pavillon étranger" },
  french: { color: "#2f8fd6", label: "Pavillon français" },
  unknown: { color: "#475569", label: "Pavillon inconnu" },
};
