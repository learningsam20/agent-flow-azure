import type { ReactNode } from "react";

export function AppPage({
  title,
  subtitle,
  onBack,
  actions,
  children,
}: {
  title: string;
  subtitle?: string;
  onBack: () => void;
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex-1 min-h-0 overflow-auto p-4 md:p-6 fade-in">
      <div className="max-w-4xl mx-auto">
        <div className="flex flex-wrap items-start justify-between gap-3 mb-6">
          <div className="min-w-0">
            <button type="button" className="btn-ghost text-xs mb-3" onClick={onBack}>
              ← Back to workspace
            </button>
            <h1 className="font-display text-2xl md:text-3xl tracking-tight text-[var(--ink)]">{title}</h1>
            {subtitle && <p className="text-sm text-[var(--muted)] mt-1.5 max-w-2xl">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
        </div>
        {children}
      </div>
    </div>
  );
}
