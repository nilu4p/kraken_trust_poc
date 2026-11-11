import { Component } from "react";
import { PLATFORM } from "../platform.js";

/**
 * Keeps a rendering error in one view from blanking the whole console.
 * A failed lazy chunk usually means a new version was deployed while the page was open.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null };
  }

  static getDerivedStateFromError(error) {
    return { error, at: new Date().toISOString().slice(0, 19).replace("T", " ") };
  }

  componentDidCatch(error, info) {
    console.error(`${PLATFORM.name} view error`, error, info?.componentStack);
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    const newVersion = /dynamically imported module|Loading chunk|Importing a module script failed/i.test(String(error?.message));
    return (
      <div className="flex h-full items-center justify-center p-6">
        <div role="alert" className="max-w-md space-y-3 border border-tactical-line bg-tactical-panel p-6 text-sm text-slate-300">
          <p className="text-xs uppercase tracking-[0.2em] text-tactical-amber">
            {newVersion ? "Nouvelle version disponible" : "Cette vue a rencontré une erreur"}
          </p>
          <p>
            {newVersion
              ? "La plateforme a été mise à jour pendant que la page était ouverte. Rechargez pour obtenir la dernière version."
              : `Les autres pages restent utilisables. Si le problème persiste, signalez-le avec l'heure de l'incident : ${this.state.at} UTC.`}
          </p>
          {!newVersion && <p className="break-all font-mono text-[11px] text-slate-500">{String(error?.message || error)}</p>}
          <div className="flex gap-2">
            {!newVersion && (
              <button
                type="button"
                onClick={() => this.setState({ error: null })}
                className="border border-tactical-line px-3 py-1.5 text-xs text-slate-300 hover:text-white"
              >
                Réessayer
              </button>
            )}
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="border border-tactical-cyan/50 bg-tactical-cyan/10 px-3 py-1.5 text-xs text-tactical-cyan"
            >
              Recharger la page
            </button>
          </div>
        </div>
      </div>
    );
  }
}
