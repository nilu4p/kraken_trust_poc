import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, RotateCcw, UserPlus, Users } from "lucide-react";
import { Button, ErrorBanner, Field, Panel, Pill, Spinner, inputClass } from "../components/ui.jsx";
import { ROLES, formatRelative } from "../lib/format.js";
import { PLATFORM } from "../platform.js";
import { createUser, errorMessage, fetchUsers, resetUserPassword, updateUser } from "../services/api.js";

// Administration › Utilisateurs. Temporary passwords are shown
// once and must be changed at first login; every action is checked again by the API.

function SecretNotice({ title, secret, onClose }) {
  return (
    <div className="space-y-2 border border-tactical-amber/50 bg-tactical-amber/10 p-3 text-xs text-tactical-amber">
      <p>{title}</p>
      <p className="select-all break-all bg-tactical-bg px-2 py-1 font-mono text-sm text-white">{secret}</p>
      <p className="text-slate-400">Affiché une seule fois. Transmettez-le par un canal sûr : il devra être changé à la première connexion.</p>
      <Button onClick={onClose}>J&apos;ai transmis le mot de passe</Button>
    </div>
  );
}

function RoleMatrix({ roles }) {
  // Columns: the roles and their labels as the API names them; rows: platform.js capabilities.
  const columns = ROLES.map((id) => ({ id, label: roles?.[id] || id }));
  const rank = Object.fromEntries(columns.map((r, i) => [r.id, i]));
  let lastGroup = "";
  return (
    <Panel title="Matrice des droits par rôle" icon={Users}>
      <p className="mb-3 text-xs text-slate-500">
        Les rôles sont cumulatifs : chaque rôle hérite des droits des rôles à sa gauche. Les droits sont contrôlés côté serveur sur
        chaque requête ; l&apos;interface ne fait que les refléter.
      </p>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[34rem] text-left text-xs">
          <thead>
            <tr className="text-[10px] uppercase tracking-wider text-slate-500">
              <th className="py-2 pr-4">Capacité</th>
              {columns.map((r) => (
                <th key={r.id} className="w-28 py-2 text-center">
                  {r.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {PLATFORM.capabilities.map((cap) => {
              const header = cap.group !== lastGroup;
              lastGroup = cap.group;
              return [
                header && (
                  <tr key={`g-${cap.group}`}>
                    <td colSpan={columns.length + 1} className="pt-3 text-[10px] uppercase tracking-[0.2em] text-tactical-cyan">
                      {cap.group}
                    </td>
                  </tr>
                ),
                <tr key={cap.label} className="border-t border-tactical-line">
                  <td className="py-1.5 pr-4 text-slate-300">{cap.label}</td>
                  {columns.map((r) => {
                    const allowed = rank[r.id] >= rank[cap.min];
                    return (
                      <td key={r.id} className="py-1.5 text-center">
                        {allowed ? (
                          <CheckCircle2 size={14} className="inline text-tactical-green" aria-label="autorisé" />
                        ) : (
                          <span className="text-slate-700" aria-label="non autorisé">—</span>
                        )}
                      </td>
                    );
                  })}
                </tr>,
              ];
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

export default function UsersTab({ user }) {
  const [data, setData] = useState(null);
  const [form, setForm] = useState({ username: "", display_name: "", role: "analyst" });
  const [secret, setSecret] = useState(null);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(() => {
    fetchUsers().then(setData).catch((err) => setError(errorMessage(err)));
  }, []);
  useEffect(() => { load(); }, [load]);

  const run = async (label, action) => {
    setBusy(label);
    setError("");
    try {
      await action();
      load();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy("");
    }
  };

  if (!data) return error ? <ErrorBanner message={error} /> : <Spinner />;
  const changeRole = (u, role) => {
    if (role === "admin" && !window.confirm(`Donner le rôle Administrateur à ${u.username} ? Ce compte pourra gérer les comptes, les paramètres et les sources.`)) return;
    run(`role:${u.id}`, () => updateUser(u.id, { role }));
  };
  return (
    <div className="space-y-4">
      {secret && <SecretNotice title={secret.title} secret={secret.value} onClose={() => setSecret(null)} />}
      <Panel title="Nouveau compte" icon={UserPlus}>
        <form
          className="grid grid-cols-1 gap-3 md:grid-cols-4"
          onSubmit={(e) => {
            e.preventDefault();
            run("create", async () => {
              const created = await createUser(form);
              setSecret({ title: `Compte ${created.user.username} créé. Mot de passe temporaire :`, value: created.temporary_password });
              setForm({ username: "", display_name: "", role: "analyst" });
            });
          }}
        >
          <Field label="Identifiant">
            <input className={inputClass} value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required maxLength={64} pattern="[A-Za-z0-9._\-]+" />
          </Field>
          <Field label="Nom affiché">
            <input className={inputClass} value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} maxLength={120} />
          </Field>
          <Field label="Rôle">
            <select className={inputClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value })}>
              {Object.entries(data.roles).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex items-end">
            <Button type="submit" variant="primary" icon={UserPlus} busy={busy === "create"} disabled={!form.username}>
              Créer
            </Button>
          </div>
        </form>
      </Panel>
      <RoleMatrix roles={data.roles} />
      <ErrorBanner message={error} />
      <Panel title={`Comptes · ${data.users.length}`} icon={Users}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-left text-xs">
            <thead className="text-[10px] uppercase tracking-wider text-slate-500">
              <tr>
                <th className="py-2">Compte</th>
                <th className="py-2">Rôle</th>
                <th className="py-2">Dernière connexion</th>
                <th className="py-2">État</th>
                <th className="py-2 text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.users.map((u) => {
                const self = u.id === user.id;
                const locked = u.locked_until && new Date(u.locked_until) > new Date();
                return (
                  <tr key={u.id} className="border-t border-tactical-line">
                    <td className="py-2">
                      <p className="text-slate-100">{u.username}</p>
                      <p className="text-slate-500">{u.display_name}</p>
                    </td>
                    <td className="py-2">
                      <select
                        className="border border-tactical-line bg-tactical-bg px-2 py-1 text-slate-200"
                        value={u.role}
                        disabled={self}
                        onChange={(e) => changeRole(u, e.target.value)}
                      >
                        {Object.entries(data.roles).map(([key, label]) => (
                          <option key={key} value={key}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td className="py-2 text-slate-400">{u.last_login_at ? formatRelative(u.last_login_at) : "jamais"}</td>
                    <td className="space-x-1 py-2">
                      {u.disabled ? <Pill color="#64748b">Désactivé</Pill> : <Pill color="#3dff8a">Actif</Pill>}
                      {locked && <Pill color="#ff3b3b">Verrouillé</Pill>}
                      {u.must_change_password && <Pill color="#f5c542">MDP temporaire</Pill>}
                    </td>
                    <td className="space-x-2 py-2 text-right">
                      <Button
                        icon={RotateCcw}
                        busy={busy === `reset:${u.id}`}
                        onClick={() =>
                          window.confirm(
                            `Réinitialiser le mot de passe de ${u.username} ?\n\nSon mot de passe actuel ne fonctionnera plus et toutes ses sessions seront fermées.`,
                          ) &&
                          run(`reset:${u.id}`, async () => {
                            const { temporary_password: value } = await resetUserPassword(u.id);
                            setSecret({ title: `Nouveau mot de passe temporaire de ${u.username} :`, value });
                          })
                        }
                      >
                        Réinitialiser
                      </Button>
                      {!self && (
                        <Button
                          variant={u.disabled ? "ghost" : "danger"}
                          busy={busy === `toggle:${u.id}`}
                          onClick={() =>
                            (u.disabled || window.confirm(`Désactiver le compte ${u.username} ? Ses sessions seront fermées immédiatement.`)) &&
                            run(`toggle:${u.id}`, () => updateUser(u.id, { disabled: !u.disabled }))
                          }
                        >
                          {u.disabled ? "Réactiver" : "Désactiver"}
                        </Button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>
    </div>
  );
}
