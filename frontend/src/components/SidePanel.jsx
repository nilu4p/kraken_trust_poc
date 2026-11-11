import { useCallback, useEffect, useRef } from "react";
import { BarChart3, ChevronLeft, ChevronRight, Layers, Palette, Pin, PinOff, Radar, Ship } from "lucide-react";
import { TARGET } from "../theme.js";
import DocLink from "./DocLink.jsx";

export const PANEL_TABS = [
  { id: "map", label: "Carte", icon: Palette, hint: "Fond, imagerie satellite, affichage" },
  { id: "layers", label: "Couches", icon: Layers, hint: "Données affichées sur la carte" },
  { id: "context", label: "Contexte", icon: Radar, hint: "Mer, couverture, sources" },
  { id: "targets", label: "Cibles", icon: Ship, hint: "Cibles radar par priorité" },
  { id: "activity", label: "Activité", icon: BarChart3, hint: "Rapport d'activité de pêche de la ZEE (GFW)" },
];

const IDLE_MS = 12000;

/**
 * Right-hand panel: a slim rail when folded (each icon opens its tab), tabs when unfolded.
 * Auto-fold only when unpinned, after the pointer has left AND no control inside has focus
 * (native <select> menus and date pickers render outside the panel: hovering them must not fold it).
 * Overlays the map on narrow screens.
 */
export default function SidePanel({ open, onOpenChange, pinned, onPinnedChange, tab, onTabChange, counts, children }) {
  const panelRef = useRef(null);
  const timer = useRef(null);
  const hovering = useRef(false);

  const cancel = useCallback(() => clearTimeout(timer.current), []);
  const arm = useCallback(() => {
    clearTimeout(timer.current);
    if (pinned) return;
    timer.current = setTimeout(() => {
      const focused = panelRef.current?.contains(document.activeElement);
      if (!hovering.current && !focused) onOpenChange(false);
    }, IDLE_MS);
  }, [pinned, onOpenChange]);

  useEffect(() => () => clearTimeout(timer.current), []);
  useEffect(() => {
    if (pinned) cancel();
  }, [pinned, cancel]);

  const openTab = (id) => {
    onTabChange(id);
    onOpenChange(true);
  };

  if (!open) {
    return (
      <aside
        className="relative z-[1200] flex w-14 shrink-0 flex-col items-center gap-1 border-l border-tactical-line bg-tactical-panel py-2"
        aria-label="Panneau latéral replié"
      >
        <button
          type="button"
          onClick={() => onOpenChange(true)}
          className="mb-1 p-2 text-slate-400 hover:text-tactical-cyan"
          title="Déplier le panneau"
          aria-label="Déplier le panneau"
        >
          <ChevronLeft size={16} />
        </button>
        {PANEL_TABS.map(({ id, label, icon: Icon, hint }) => (
          <button
            key={id}
            type="button"
            onClick={() => openTab(id)}
            title={`${label} — ${hint}`}
            className="flex w-full flex-col items-center gap-0.5 px-1 py-2 text-[9px] uppercase tracking-wider text-slate-400 hover:bg-white/5 hover:text-tactical-cyan"
          >
            <Icon size={16} />
            {label}
            {id === "targets" && (
              <span className="flex gap-1 text-[10px] normal-case tracking-normal">
                <span style={{ color: counts.priority ? TARGET.priority.color : undefined }}>{counts.priority}</span>
                <span style={{ color: TARGET.check.color }}>{counts.check}</span>
              </span>
            )}
          </button>
        ))}
      </aside>
    );
  }

  return (
    <aside
      ref={panelRef}
      className="absolute inset-y-0 right-0 z-[1200] flex w-80 max-w-[90vw] flex-col border-l border-tactical-line bg-tactical-panel shadow-2xl lg:relative lg:shadow-none"
      onMouseEnter={() => {
        hovering.current = true;
        cancel();
      }}
      onMouseLeave={() => {
        hovering.current = false;
        arm();
      }}
      onFocusCapture={cancel}
      onBlurCapture={() => {
        if (!hovering.current) arm();
      }}
      aria-label="Panneau latéral"
    >
      <div className="flex items-center justify-between border-b border-tactical-line px-3 py-1.5">
        <button
          type="button"
          onClick={() => onPinnedChange(!pinned)}
          className={`flex items-center gap-1 text-[10px] uppercase tracking-wider ${pinned ? "text-tactical-cyan" : "text-slate-500 hover:text-white"}`}
          title={pinned ? "Désépingler : repli automatique après inactivité" : "Épingler le panneau ouvert"}
        >
          {pinned ? <Pin size={12} /> : <PinOff size={12} />} {pinned ? "Épinglé" : "Repli auto"}
        </button>
        <DocLink page="situation/panneau-lateral/" label="panneau latéral" className="ml-auto mr-2" />
        <button type="button" onClick={() => onOpenChange(false)} className="p-1 text-slate-400 hover:text-white" aria-label="Replier le panneau">
          <ChevronRight size={16} />
        </button>
      </div>
      <nav className="grid grid-cols-5 border-b border-tactical-line" role="tablist">
        {PANEL_TABS.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            type="button"
            role="tab"
            aria-selected={tab === id}
            onClick={() => onTabChange(id)}
            className={`flex flex-col items-center gap-0.5 border-b-2 py-2 text-[10px] uppercase tracking-wider ${
              tab === id ? "border-tactical-cyan text-tactical-cyan" : "border-transparent text-slate-500 hover:text-white"
            }`}
          >
            <Icon size={14} />
            {label}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-y-auto" role="tabpanel">
        {children}
      </div>
    </aside>
  );
}
