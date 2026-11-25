import type { ReactNode } from "react";
import type { FlowNode } from "../lib/api";

export function InspectorDrawer({
  node,
  onClose,
}: {
  node: FlowNode | null;
  onClose: () => void;
}) {
  if (!node) return null;
  const decisions =
    node.guardrails?.decisions ||
    node.guardrails?.input?.decisions ||
    [];
  return (
    <aside className="w-96 shrink-0 border-l border-sea/15 bg-white/90 p-4 overflow-y-auto hidden lg:block">
      <div className="flex justify-between items-start mb-3">
        <div>
          <h3 className="font-semibold">{node.label}</h3>
          <p className="text-xs text-sea">{node.id} · {node.status}</p>
        </div>
        <button className="text-xs underline" onClick={onClose}>
          close
        </button>
      </div>
      <Section title="Timing">
        <div className="text-xs space-y-1 font-mono">
          <div>started: {node.startedAt ? new Date(node.startedAt).toLocaleString() : "—"}</div>
          <div>ended: {node.endedAt ? new Date(node.endedAt).toLocaleString() : "—"}</div>
          <div>duration: {node.durationMs != null ? `${node.durationMs} ms` : "—"}</div>
          <div className="truncate" title={node.correlationId}>
            correlation: {node.correlationId || "—"}
          </div>
        </div>
      </Section>
      <Section title="Model">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded">
          {JSON.stringify(node.model || { note: "no model (system node)" }, null, 2)}
        </pre>
      </Section>
      <Section title="Tokens">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded">
          {JSON.stringify(node.usage || { note: "no usage yet" }, null, 2)}
        </pre>
      </Section>
      <Section title="Guardrails">
        <ul className="space-y-1 text-xs">
          {decisions.map((d, i) => (
            <li key={i} className="border border-sea/10 rounded p-2 bg-sand/40">
              <div className="font-semibold">
                {d.policy}: <span className="text-sea">{d.result}</span>
              </div>
              <pre className="mt-1 text-[10px] whitespace-pre-wrap opacity-80">
                {JSON.stringify(d.details ?? d, null, 2)}
              </pre>
            </li>
          ))}
          {!decisions.length && <li className="text-ink/50">No guardrail decisions recorded</li>}
        </ul>
      </Section>
      <Section title="Agent">{node.agent || "— (system)"}</Section>
      <Section title="Skills">{(node.skills || []).join(", ") || "—"}</Section>
      <Section title="Connectors">{(Array.isArray(node.mcps) ? node.mcps.map((m) => (typeof m === "string" ? m : JSON.stringify(m))).join(", ") : "—")}</Section>
      <Section title="Tools (executed on this node)">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded max-h-40 overflow-auto">
          {JSON.stringify(node.toolExecutions || node.tools || [], null, 2)}
        </pre>
      </Section>
      {!!(node.toolsHint || []).length && (
        <Section title="Tools required (workflow)">{(node.toolsHint || []).join(", ")}</Section>
      )}
      <Section title="Secrets / credentials (refs only)">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded max-h-32 overflow-auto">
          {JSON.stringify((node.policy as { secrets?: unknown })?.secrets || [], null, 2)}
        </pre>
      </Section>
      <Section title="Gateway / policy">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded">
          {JSON.stringify(node.policy || { status: "n/a" }, null, 2)}
        </pre>
      </Section>
      <Section title="Input">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded max-h-40 overflow-auto">
          {JSON.stringify(node.input, null, 2)}
        </pre>
      </Section>
      <Section title="Output">
        <pre className="text-[11px] whitespace-pre-wrap bg-mist/80 p-2 rounded max-h-60 overflow-auto">
          {typeof node.output === "string" ? node.output : JSON.stringify(node.output, null, 2)}
        </pre>
      </Section>
    </aside>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mb-3">
      <div className="text-[11px] uppercase tracking-wide text-ink/50 mb-1">{title}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}
