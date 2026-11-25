import type { PermissionMatrix } from "../lib/api";
import { AppPage } from "./AppPage";

export function PermissionDrawer({
  matrix,
  roles,
  onClose,
}: {
  matrix: PermissionMatrix;
  roles: string[];
  onClose: () => void;
}) {
  return (
    <AppPage
      title="Permissions"
      subtitle={`Your roles: ${roles.join(", ") || "—"}`}
      onBack={onClose}
    >
      <div className="space-y-6">
        <section className="panel p-4">
          <h2 className="text-xs uppercase tracking-[0.12em] text-[var(--muted)] font-semibold mb-3">
            Agents
          </h2>
          <ul className="space-y-1">
            {Object.entries(matrix.agents).map(([agent, ok]) => (
              <li
                key={agent}
                className="flex justify-between text-sm border-b border-[var(--line)] py-2 last:border-0"
              >
                <span className="text-[var(--ink)]">{agent}</span>
                <span className={ok ? "text-moss font-semibold" : "text-ember font-semibold"}>
                  {ok ? "allowed" : "locked"}
                </span>
              </li>
            ))}
          </ul>
        </section>

        <section className="panel p-4">
          <h2 className="text-xs uppercase tracking-[0.12em] text-[var(--muted)] font-semibold mb-2">
            Skills
          </h2>
          <p className="text-sm text-[var(--ink)]">{matrix.skills.join(", ") || "—"}</p>
        </section>

        <section className="panel p-4">
          <h2 className="text-xs uppercase tracking-[0.12em] text-[var(--muted)] font-semibold mb-2">
            Connectors
          </h2>
          <p className="text-sm text-[var(--ink)]">{matrix.mcps.join(", ") || "—"}</p>
        </section>

        <section className="panel p-4">
          <h2 className="text-xs uppercase tracking-[0.12em] text-[var(--muted)] font-semibold mb-2">
            Human review
          </h2>
          <p className={`text-sm font-semibold ${matrix.hitl ? "text-moss" : "text-ember"}`}>
            {matrix.hitl ? "Can approve waiting reviews" : "Cannot approve reviews"}
          </p>
        </section>
      </div>
    </AppPage>
  );
}
