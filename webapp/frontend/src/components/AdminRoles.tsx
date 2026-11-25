import { useEffect, useState } from "react";
import { AdminUser, api } from "../lib/api";
import { AppPage } from "./AppPage";

export function AdminRoles({ onClose }: { onClose: () => void }) {
  const [users, setUsers] = useState<AdminUser[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const data = await api.adminUsers();
      setUsers(data.users);
      setRoles(data.assignableAgentRoles);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load().catch((e) => {
      setError(String(e.message || e));
      setLoading(false);
    });
  }, []);

  async function toggle(user: AdminUser, role: string) {
    setBusy(true);
    setError(null);
    try {
      const next = user.agentRoles.includes(role)
        ? user.agentRoles.filter((r) => r !== role)
        : [...user.agentRoles, role];
      await api.setUserRoles(user.oid, next, user.email, user.name);
      await load();
    } catch (e: unknown) {
      setError((e as Error).message || "Update failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppPage
      title="Users & roles"
      subtitle="App roles come from your identity provider. Agent access is assigned here."
      onBack={onClose}
      actions={
        <button type="button" className="btn-ghost text-xs" onClick={() => load()} disabled={loading}>
          Refresh
        </button>
      }
    >
      {error && (
        <div className="mb-4 rounded-xl border border-ember/25 bg-ember/10 text-ember px-4 py-3 text-sm">
          {error}
        </div>
      )}
      {loading && (
        <div className="panel p-8 grid place-items-center text-sm text-[var(--muted)]">
          <div className="flex items-center gap-2">
            <span className="inline-block w-4 h-4 rounded-full border-2 border-sea border-t-transparent animate-spin" />
            Loading users…
          </div>
        </div>
      )}
      {!loading && (
        <ul className="space-y-3">
          {users.map((u) => (
            <li key={u.oid} className="panel p-4">
              <div className="font-semibold text-sm text-[var(--ink)]">{u.name || u.email || "User"}</div>
              <div className="text-xs text-[var(--muted)]">{u.email}</div>
              <div className="text-[11px] text-[var(--sea)] mt-1">
                app: {(u.appRoles || []).join(", ") || "—"}
              </div>
              <div className="flex flex-wrap gap-2 mt-3">
                {roles.map((r) => {
                  const on = u.agentRoles.includes(r);
                  return (
                    <button
                      key={r}
                      type="button"
                      disabled={busy}
                      onClick={() => toggle(u, r)}
                      className={`text-xs px-2.5 py-1.5 rounded-lg border transition ${
                        on
                          ? "bg-moss text-white border-moss"
                          : "bg-[var(--panel-solid)] border-[var(--line)] text-[var(--ink)]"
                      }`}
                    >
                      {r}
                    </button>
                  );
                })}
              </div>
            </li>
          ))}
          {!users.length && (
            <li className="panel p-8 text-center text-sm text-[var(--muted)]">No users found.</li>
          )}
        </ul>
      )}
    </AppPage>
  );
}
