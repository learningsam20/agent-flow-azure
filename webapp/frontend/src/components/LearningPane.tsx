import type { LearningItem } from "../lib/api";
import { AppPage } from "./AppPage";

export function LearningPane({
  items,
  onClose,
  onRefresh,
}: {
  items: LearningItem[];
  onClose: () => void;
  onRefresh: () => void;
}) {
  return (
    <AppPage
      title="Learning"
      subtitle="Procedural memory captured from recent agent runs."
      onBack={onClose}
      actions={
        <button type="button" className="btn-ghost text-xs" onClick={onRefresh}>
          Refresh
        </button>
      }
    >
      <ul className="space-y-3">
        {items.map((item) => (
          <li key={item.id} className="panel p-4">
            <div className="text-sm font-semibold text-[var(--ink)]">{item.agent}</div>
            <div className="text-[11px] text-[var(--sea)] mt-0.5">{item.functionality}</div>
            <p className="text-sm mt-2.5 whitespace-pre-wrap leading-relaxed text-[var(--ink)]/85">
              {item.learning}
            </p>
            <div className="text-[10px] text-[var(--muted)] mt-2">
              {new Date(item.date).toLocaleString()}
            </div>
          </li>
        ))}
        {!items.length && (
          <li className="panel p-8 text-center text-sm text-[var(--muted)]">
            No learnings yet — run a workflow or chat to populate memory.
          </li>
        )}
      </ul>
    </AppPage>
  );
}
