import { useCallback, useEffect, useRef, useState } from "react";
import { BookOpen, KeyRound, LogOut, Menu, UserCircle2, X } from "lucide-react";
import { DOCS_URL } from "./DocLink.jsx";
import ErrorBoundary from "./ErrorBoundary.jsx";
import { Modal, Spinner } from "./ui.jsx";
import { PLATFORM } from "../platform.js";
import { EVENTS, fetchMe, logout, setCsrfToken } from "../services/client.js";
import { ChangePasswordView, LoginView } from "../views/LoginView.jsx";

// Console frame:
//   <SessionGate>  session check, login, forced password change, return to /docs/ or /notifs/
//   <AppShell>     header (brand, navigation, platform widgets, account menu), mobile drawer,
//                  password dialog, error boundary around the current view.
// A platform only supplies its navigation entries, its header widgets and its views.

/**
 * Page of the knowledge base (or of the PoC notification server) the user was sent away from:
 * nginx protects /docs/ and /notifs/ with the platform session and redirects to "/?next=/docs/…".
 * Same-origin /docs/ and /notifs/ paths only (no open redirect).
 */
function docsReturnPath() {
  const next = new URLSearchParams(window.location.search).get("next") || "";
  return /^\/(docs|notifs)\/[^\\]*$/.test(next) && !next.startsWith("//") && !next.includes("/../") ? next : "";
}

/** Session state machine; renders ``children(user, { onLogout, onUserChange })`` once signed in. */
export function SessionGate({ children }) {
  const [state, setState] = useState({ status: "loading", user: null });

  const loadMe = useCallback(() => {
    fetchMe()
      .then((data) => {
        setCsrfToken(data.csrf_token);
        setState({ status: "ready", user: data.user });
      })
      .catch((err) => setState({ status: err.response ? "anonymous" : "offline", user: null }));
  }, []);

  useEffect(() => {
    loadMe();
    const onUnauthorized = () => {
      setCsrfToken("");
      setState({ status: "anonymous", user: null });
    };
    const onPasswordChange = () => setState((s) => (s.user ? { ...s, user: { ...s.user, must_change_password: true } } : s));
    window.addEventListener(EVENTS.unauthorized, onUnauthorized);
    window.addEventListener(EVENTS.passwordChange, onPasswordChange);
    return () => {
      window.removeEventListener(EVENTS.unauthorized, onUnauthorized);
      window.removeEventListener(EVENTS.passwordChange, onPasswordChange);
    };
  }, [loadMe]);

  const onLoggedIn = (data) => {
    setCsrfToken(data.csrf_token);
    setState({ status: "ready", user: data.user });
  };

  const onLogout = useCallback(async () => {
    try {
      await logout();
    } catch {
      /* session may already be gone */
    }
    setCsrfToken("");
    setState({ status: "anonymous", user: null });
  }, []);

  const onUserChange = useCallback((user) => setState({ status: "ready", user }), []);

  const docsReturn = docsReturnPath();
  const ready = state.status === "ready" && state.user && !state.user.must_change_password;
  useEffect(() => {
    if (ready && docsReturn) window.location.replace(docsReturn);
  }, [ready, docsReturn]);

  if (state.status === "loading") return <Spinner label={PLATFORM.loadingLabel} />;
  if (ready && docsReturn) return <Spinner label="Ouverture de la documentation…" />;
  if (state.status === "offline") {
    return (
      <div className="flex h-full flex-wrap items-center justify-center gap-3 p-6 font-sans text-xs text-tactical-red" role="alert">
        API injoignable. Vérifiez que le backend est démarré.
        <button type="button" onClick={loadMe} className="underline">
          Réessayer
        </button>
      </div>
    );
  }
  if (state.status !== "ready" || !state.user) {
    return <LoginView onLoggedIn={onLoggedIn} notice={docsReturn ? "La documentation est réservée aux utilisateurs connectés." : ""} />;
  }
  if (state.user.must_change_password) {
    return <ChangePasswordView forced user={state.user} onChanged={onUserChange} onLogout={onLogout} />;
  }
  return children(state.user, { onLogout, onUserChange });
}

function NavLink({ item, href, active, onClick, variant }) {
  const { label, icon: Icon, hint } = item;
  if (variant === "drawer") {
    return (
      <a
        href={href}
        onClick={onClick}
        aria-current={active ? "page" : undefined}
        className={`flex items-start gap-3 border-l-2 px-4 py-3 text-xs ${
          active ? "border-tactical-cyan bg-tactical-cyan/10 text-tactical-cyan" : "border-transparent text-slate-300 hover:bg-white/5"
        }`}
      >
        <Icon size={16} className="mt-0.5 shrink-0" aria-hidden="true" />
        <span className="min-w-0">
          <span className="block uppercase tracking-wider">{label}</span>
          {hint && <span className="block text-[10px] normal-case tracking-normal text-slate-500">{hint}</span>}
        </span>
      </a>
    );
  }
  return (
    <a
      href={href}
      aria-current={active ? "page" : undefined}
      title={hint ? `${label} — ${hint}` : label}
      aria-label={label}
      className={`flex shrink-0 items-center gap-2 border-b-2 px-3 py-4 text-xs uppercase tracking-wider ${
        active ? "border-tactical-cyan text-tactical-cyan" : "border-transparent text-slate-400 hover:text-white"
      }`}
    >
      <Icon size={15} aria-hidden="true" />
      {/* Labels: the current view from lg, every entry on very wide screens. */}
      <span className={active ? "hidden lg:inline" : "hidden min-[1760px]:inline"}>{label}</span>
    </a>
  );
}

/**
 * Console frame.
 * - ``nav``: [{ id, label, icon, hint }] already filtered by role; ``current``: id of the open view.
 * - ``hrefFor(item)``: link of an entry (default "#/<id>").
 * - ``headerExtras``: platform widgets on the right of the header (clocks, KPIs, pickers…).
 * - ``drawerExtras(close)``: the same information at the bottom of the mobile drawer.
 * - ``menuExtras(close)``: additional entries at the top of the account menu.
 */
export function AppShell({
  user,
  nav,
  current,
  hrefFor = (item) => `#/${item.id}`,
  headerExtras = null,
  drawerExtras = null,
  menuExtras = null,
  onLogout,
  onUserChange,
  viewKey,
  children,
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [navOpen, setNavOpen] = useState(false);
  const [passwordOpen, setPasswordOpen] = useState(false);
  const menuRef = useRef(null);

  // Navigating closes the drawer and the account menu.
  useEffect(() => {
    const onHash = () => {
      setNavOpen(false);
      setMenuOpen(false);
    };
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Escape closes the drawer and the menu; a click elsewhere closes the menu.
  useEffect(() => {
    if (!menuOpen && !navOpen) return undefined;
    const onKey = (e) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        setNavOpen(false);
      }
    };
    const onClick = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("mousedown", onClick);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("mousedown", onClick);
    };
  }, [menuOpen, navOpen]);

  const closeNav = () => setNavOpen(false);
  const closeMenu = () => setMenuOpen(false);

  return (
    <div className="flex h-full flex-col bg-tactical-bg font-sans text-slate-100">
      <a
        href="#main"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main")?.focus();
        }}
        className="sr-only z-[4000] bg-tactical-panel px-3 py-2 text-xs text-tactical-cyan focus:not-sr-only focus:absolute focus:left-2 focus:top-2"
      >
        Aller au contenu
      </a>
      <header className="relative flex items-center justify-between gap-2 border-b border-tactical-line bg-tactical-panel px-2 sm:gap-3 sm:px-4">
        <div className="flex min-w-0 items-center gap-1 sm:gap-3">
          <button
            type="button"
            onClick={() => setNavOpen((v) => !v)}
            className="p-2 text-slate-300 hover:text-white md:hidden"
            aria-label={navOpen ? "Fermer le menu" : "Ouvrir le menu"}
            aria-expanded={navOpen}
            aria-controls="mobile-nav"
          >
            {navOpen ? <X size={18} /> : <Menu size={18} />}
          </button>
          <a href={hrefFor(nav[0] || { id: "" })} className="shrink-0 whitespace-nowrap py-2.5" title={`${PLATFORM.name} — ${PLATFORM.tagline}`}>
            <span className="block text-[10px] font-semibold uppercase tracking-[0.35em] text-tactical-cyan">{PLATFORM.name}</span>
            <span className="hidden text-xs text-slate-300 xl:block">{PLATFORM.tagline}</span>
          </a>
          <nav className="hidden min-w-0 overflow-x-auto md:flex" aria-label="Navigation principale">
            {nav.map((item) => (
              <NavLink key={item.id} item={item} href={hrefFor(item)} active={current === item.id} />
            ))}
            <a
              href={DOCS_URL}
              target="_blank"
              rel="noopener"
              title="Documentation — méthodes, sources, calculs, guides (nouvel onglet)"
              aria-label="Documentation"
              className="flex shrink-0 items-center gap-2 border-b-2 border-transparent px-3 py-4 text-xs uppercase tracking-wider text-slate-400 hover:text-white"
            >
              <BookOpen size={15} aria-hidden="true" />
              <span className="hidden min-[1760px]:inline">Documentation</span>
            </a>
          </nav>
        </div>
        <div className="flex min-w-0 items-center justify-end gap-2 sm:gap-3">
          {headerExtras}
          <div className="relative" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((v) => !v)}
              className="flex items-center gap-2 py-2 text-xs text-slate-300 hover:text-white"
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              aria-label={`Compte ${user.display_name || user.username}`}
            >
              <UserCircle2 size={18} className="text-tactical-cyan" />
              <span className="hidden max-w-[10rem] truncate text-left sm:block">
                <span className="block truncate">{user.display_name || user.username}</span>
                <span className="block text-[10px] uppercase tracking-wider text-slate-500">{user.role_label}</span>
              </span>
            </button>
            {menuOpen && (
              <div role="menu" className="absolute right-0 z-[3000] mt-2 w-60 max-w-[calc(100vw-1rem)] border border-tactical-line bg-tactical-panel shadow-2xl">
                <p className="border-b border-tactical-line px-4 py-3 text-[10px] uppercase tracking-wider text-slate-500 sm:hidden">
                  {user.display_name || user.username} · {user.role_label}
                </p>
                {menuExtras && menuExtras(closeMenu)}
                <button
                  type="button"
                  role="menuitem"
                  onClick={() => {
                    setPasswordOpen(true);
                    setMenuOpen(false);
                  }}
                  className="flex w-full items-center gap-2 px-4 py-3 text-left text-xs text-slate-300 hover:bg-white/5"
                >
                  <KeyRound size={14} /> Changer mon mot de passe
                </button>
                <button
                  type="button"
                  role="menuitem"
                  onClick={onLogout}
                  className="flex w-full items-center gap-2 border-t border-tactical-line px-4 py-3 text-left text-xs text-tactical-red hover:bg-white/5"
                >
                  <LogOut size={14} /> Se déconnecter
                </button>
              </div>
            )}
          </div>
        </div>
      </header>

      {navOpen && (
        <div className="fixed inset-0 z-[2500] md:hidden" onClick={closeNav}>
          <div className="absolute inset-0 bg-black/60" />
          <nav
            id="mobile-nav"
            aria-label="Navigation principale"
            className="absolute inset-y-0 left-0 flex w-72 max-w-[85vw] flex-col overflow-y-auto border-r border-tactical-line bg-tactical-panel shadow-2xl"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between border-b border-tactical-line px-4 py-3">
              <span>
                <span className="block text-[10px] font-semibold uppercase tracking-[0.35em] text-tactical-cyan">{PLATFORM.name}</span>
                <span className="block text-xs text-slate-300">{PLATFORM.tagline}</span>
              </span>
              <button type="button" onClick={closeNav} className="p-1 text-slate-400 hover:text-white" aria-label="Fermer le menu">
                <X size={18} />
              </button>
            </div>
            {nav.map((item) => (
              <NavLink key={item.id} item={item} href={hrefFor(item)} active={current === item.id} onClick={closeNav} variant="drawer" />
            ))}
            <a
              href={DOCS_URL}
              target="_blank"
              rel="noopener"
              className="flex items-center gap-3 border-l-2 border-transparent px-4 py-3 text-xs uppercase tracking-wider text-slate-300 hover:bg-white/5"
            >
              <BookOpen size={16} aria-hidden="true" /> Documentation
            </a>
            {drawerExtras && <div className="mt-auto border-t border-tactical-line p-4">{drawerExtras(closeNav)}</div>}
          </nav>
        </div>
      )}

      {passwordOpen && (
        <Modal title="Changer mon mot de passe" icon={KeyRound} onClose={() => setPasswordOpen(false)}>
          <ChangePasswordView
            user={user}
            onCancel={() => setPasswordOpen(false)}
            onChanged={(updated) => {
              onUserChange(updated);
              setPasswordOpen(false);
            }}
          />
        </Modal>
      )}

      <main id="main" tabIndex={-1} className="min-h-0 flex-1 focus:outline-none">
        {/* key: leaving a broken view resets the error boundary */}
        <ErrorBoundary key={viewKey ?? current}>{children}</ErrorBoundary>
      </main>
    </div>
  );
}
