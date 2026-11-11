import { useState } from "react";
import { KeyRound, Lock, LogOut, ShieldCheck } from "lucide-react";
import { Button, ErrorBanner, Field, inputClass } from "../components/ui.jsx";
import { PLATFORM } from "../platform.js";
import { changePassword, errorMessage, login } from "../services/api.js";

function Frame({ children, subtitle }) {
  return (
    <main className="flex min-h-full items-center justify-center bg-tactical-bg p-4 font-sans">
      <div className="w-full max-w-sm border border-tactical-line bg-tactical-panel shadow-2xl">
        <div className="border-b border-tactical-line px-6 py-5">
          <p className="text-[10px] font-semibold uppercase tracking-[0.35em] text-tactical-cyan">{PLATFORM.name}</p>
          <h1 className="mt-1 text-lg text-white">{PLATFORM.loginTitle}</h1>
          <p className="mt-1 text-xs text-slate-500">{subtitle}</p>
        </div>
        <div className="space-y-4 p-6">{children}</div>
        <p className="border-t border-tactical-line px-6 py-3 text-[10px] text-slate-600">{PLATFORM.loginNotice}</p>
      </div>
    </main>
  );
}

export function LoginView({ onLoggedIn, notice = "" }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const data = await login(username.trim(), password);
      setPassword("");
      onLoggedIn(data);
    } catch (err) {
      // Never keep a rejected password in memory / in the field.
      setPassword("");
      setError(errorMessage(err, "Connexion impossible"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Frame subtitle="Authentification">
      <form onSubmit={submit} className="space-y-4">
        {notice && <p className="border border-tactical-cyan/40 bg-tactical-cyan/10 px-3 py-2 text-xs text-tactical-cyan">{notice}</p>}
        <Field label="Identifiant">
          <input
            className={inputClass}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            autoFocus
            required
            maxLength={64}
          />
        </Field>
        <Field label="Mot de passe">
          <input
            className={inputClass}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete="current-password"
            required
            maxLength={256}
          />
        </Field>
        <ErrorBanner message={error} />
        <button
          type="submit"
          disabled={busy || !username || !password}
          className="flex w-full items-center justify-center gap-2 border border-tactical-cyan/50 bg-tactical-cyan/10 px-3 py-2.5 text-xs uppercase tracking-[0.2em] text-tactical-cyan hover:bg-tactical-cyan/20 disabled:opacity-40"
        >
          <Lock size={14} /> {busy ? "Vérification…" : "Se connecter"}
        </button>
      </form>
    </Frame>
  );
}

export function ChangePasswordView({ user, onChanged, onCancel, onLogout, forced = false }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const mismatch = confirm && next !== confirm;

  const submit = async (event) => {
    event.preventDefault();
    if (mismatch) return;
    setBusy(true);
    setError("");
    try {
      const data = await changePassword(current, next);
      onChanged(data.user);
    } catch (err) {
      setError(errorMessage(err, "Changement impossible"));
    } finally {
      setBusy(false);
    }
  };

  const form = (
    <form onSubmit={submit} className="space-y-4">
      {forced && (
        <p className="flex items-start gap-2 border border-tactical-amber/40 bg-tactical-amber/10 p-3 text-xs text-tactical-amber">
          <KeyRound size={14} className="mt-0.5 shrink-0" />
          Mot de passe temporaire : choisissez un mot de passe personnel avant d&apos;accéder à la plateforme.
        </p>
      )}
      {/* Lets password managers attach the new password to the right account. */}
      <input type="text" name="username" autoComplete="username" value={user?.username || ""} readOnly hidden />
      <Field label="Mot de passe actuel">
        <input className={inputClass} type="password" value={current} onChange={(e) => setCurrent(e.target.value)} autoComplete="current-password" required maxLength={256} />
      </Field>
      <Field label="Nouveau mot de passe" hint="12+ car., 3 types">
        <input className={inputClass} type="password" value={next} onChange={(e) => setNext(e.target.value)} autoComplete="new-password" required minLength={12} maxLength={256} />
      </Field>
      <Field label="Confirmation">
        <input className={inputClass} type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="new-password" required maxLength={256} />
      </Field>
      {mismatch && <p className="text-xs text-tactical-red">Les deux saisies diffèrent.</p>}
      <ErrorBanner message={error} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" busy={busy} icon={ShieldCheck} className="flex-1" disabled={mismatch || !next || !confirm}>
          Enregistrer
        </Button>
        {!forced && onCancel && <Button onClick={onCancel}>Annuler</Button>}
        {forced && onLogout && <Button icon={LogOut} onClick={onLogout}>Se déconnecter</Button>}
      </div>
      <p className="text-[10px] text-slate-600">Vos autres sessions seront fermées.</p>
    </form>
  );

  return forced ? <Frame subtitle={`Compte ${user?.username}`}>{form}</Frame> : form;
}
