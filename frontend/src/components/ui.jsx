import { useEffect, useRef } from "react";
import { Loader2, X } from "lucide-react";

// UI kit: tactical dark theme, square corners, 1 px lines, accent = tailwind "tactical-cyan".

export function Pill({ color, children, title, solid = false }) {
  return (
    <span
      title={title}
      className="inline-flex items-center gap-1 whitespace-nowrap border px-1.5 py-0.5 text-[10px] uppercase tracking-wider"
      style={
        solid
          ? { color: "#070b10", backgroundColor: color, borderColor: color }
          : { color, borderColor: `${color}80`, backgroundColor: `${color}14` }
      }
    >
      {children}
    </span>
  );
}

const VARIANTS = {
  primary: "border-tactical-cyan/50 bg-tactical-cyan/10 text-tactical-cyan hover:bg-tactical-cyan/20",
  danger: "border-tactical-red/50 bg-tactical-red/10 text-tactical-red hover:bg-tactical-red/20",
  ghost: "border-tactical-line text-slate-300 hover:bg-white/5",
};

export function Button({ variant = "ghost", busy = false, icon: Icon, children, className = "", ...props }) {
  return (
    <button
      type="button"
      {...props}
      disabled={busy || props.disabled}
      className={`inline-flex items-center justify-center gap-1.5 border px-3 py-1.5 text-xs transition disabled:cursor-not-allowed disabled:opacity-40 ${VARIANTS[variant]} ${className}`}
    >
      {busy ? <Loader2 size={13} className="animate-spin" /> : Icon ? <Icon size={13} /> : null}
      {children}
    </button>
  );
}

export function Panel({ title, icon: Icon, actions, children, className = "" }) {
  return (
    <section className={`min-w-0 border border-tactical-line bg-tactical-panel ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-center justify-between gap-2 border-b border-tactical-line px-3 py-2.5 sm:px-4">
          <h2 className="flex items-center gap-2 text-[10px] uppercase tracking-[0.25em] text-slate-400">
            {Icon && <Icon size={12} className="text-tactical-cyan" />}
            {title}
          </h2>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-3 sm:p-4">{children}</div>
    </section>
  );
}

export function Field({ label, hint, children }) {
  return (
    <label className="block space-y-1 text-xs">
      <span className="flex justify-between text-slate-500">
        <span>{label}</span>
        {hint && <span>{hint}</span>}
      </span>
      {children}
    </label>
  );
}

export const inputClass =
  "w-full border border-tactical-line bg-tactical-bg px-3 py-2 text-xs text-slate-100 placeholder:text-slate-600 focus:border-tactical-cyan/60 focus:outline-none";

export function Spinner({ label = "Chargement…" }) {
  return (
    <p className="flex items-center gap-2 p-4 text-xs text-slate-500" role="status">
      <Loader2 size={14} className="animate-spin" /> {label}
    </p>
  );
}

export function Empty({ children }) {
  return <p className="p-6 text-center text-xs text-slate-500">{children}</p>;
}

export function ErrorBanner({ message }) {
  if (!message) return null;
  return (
    <p className="border border-tactical-red/40 bg-tactical-red/10 px-3 py-2 text-xs text-tactical-red" role="alert">
      {message}
    </p>
  );
}

/** Horizontal tab bar (scrolls sideways on narrow screens). ``tabs``: [{ id, label, icon }]. */
export function Tabs({ tabs, value, onChange, label = "Onglets", className = "" }) {
  return (
    <div
      role="tablist"
      aria-label={label}
      className={`flex shrink-0 overflow-x-auto border-b border-tactical-line bg-tactical-panel/60 px-2 sm:px-4 ${className}`}
    >
      {tabs.map(({ id, label: text, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="tab"
          aria-selected={value === id}
          onClick={() => onChange(id)}
          className={`flex shrink-0 items-center gap-2 whitespace-nowrap border-b-2 px-3 py-3 text-xs ${
            value === id ? "border-tactical-cyan text-tactical-cyan" : "border-transparent text-slate-400 hover:text-white"
          }`}
        >
          {Icon && <Icon size={13} aria-hidden="true" />} {text}
        </button>
      ))}
    </div>
  );
}

/** Modal dialog: Escape and a click outside close it; focus moves inside and comes back on close. */
export function Modal({ title, icon: Icon, onClose, children, size = "max-w-sm", closable = true }) {
  const ref = useRef(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement;
    ref.current?.focus();
    const onKey = (e) => {
      if (e.key === "Escape" && closable) closeRef.current?.();
    };
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("keydown", onKey);
      if (previous && typeof previous.focus === "function") previous.focus();
    };
  }, [closable]);
  return (
    <div className="fixed inset-0 z-[3000] flex items-center justify-center bg-black/70 p-2 sm:p-4" onClick={() => closable && onClose?.()}>
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={typeof title === "string" ? title : undefined}
        tabIndex={-1}
        className={`flex max-h-full w-full ${size} flex-col border border-tactical-line bg-tactical-panel shadow-2xl focus:outline-none`}
        onClick={(e) => e.stopPropagation()}
      >
        {title && (
          <header className="flex items-center justify-between gap-2 border-b border-tactical-line px-4 py-3">
            <h2 className="flex items-center gap-2 text-xs uppercase tracking-[0.2em] text-tactical-cyan">
              {Icon && <Icon size={14} aria-hidden="true" />} {title}
            </h2>
            {closable && (
              <button type="button" onClick={onClose} aria-label="Fermer" className="p-1 text-slate-400 hover:text-white">
                <X size={18} />
              </button>
            )}
          </header>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-6">{children}</div>
      </div>
    </div>
  );
}
