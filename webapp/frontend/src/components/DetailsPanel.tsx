import { useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import type { ChatResult, FlowNode, FlowRun, GuardrailDecision, Guardrails } from "../lib/api";
import { formatAgentReply } from "../lib/jsonToMarkdown";
import { HitlPanel } from "./HitlPanel";
import { RunSummary } from "./RunSummary";

type HitlSubmit = (payload: {
  decision: string;
  notes: string;
  pathOverride?: string;
  restartFromNode?: string;
  input?: Record<string, string>;
}) => void;

function formatWhen(iso?: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString();
}

function computeDurationMs(node: FlowNode): number | null {
  if (typeof node.durationMs === "number") return node.durationMs;
  if (node.startedAt && node.endedAt) {
    const a = new Date(node.startedAt).getTime();
    const b = new Date(node.endedAt).getTime();
    if (!Number.isNaN(a) && !Number.isNaN(b) && b >= a) return b - a;
  }
  // Live elapsed while waiting / running (no endedAt yet)
  if (
    node.startedAt &&
    (node.status === "Waiting_HITL" || node.status === "Running" || node.status === "Paused")
  ) {
    const a = new Date(node.startedAt).getTime();
    if (!Number.isNaN(a)) return Math.max(0, Date.now() - a);
  }
  return null;
}

function formatDuration(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)} s`;
  return `${Math.floor(ms / 60_000)}m ${Math.round((ms % 60_000) / 1000)}s`;
}

function collectGuardrails(g?: Guardrails | null): GuardrailDecision[] {
  if (!g) return [];
  const out: GuardrailDecision[] = [];
  const push = (list?: GuardrailDecision[]) => {
    for (const d of list || []) if (d?.policy) out.push(d);
  };
  push(g.decisions);
  push(g.input?.decisions);
  push(g.output?.decisions);
  return out;
}

function extractText(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value !== "object") return String(value);
  const o = value as Record<string, unknown>;
  for (const key of ["output_markdown", "reply", "message", "content", "text", "reason", "error", "notes"]) {
    if (typeof o[key] === "string" && o[key]) return o[key] as string;
  }
  // HITL payloads: decision + notes (+ optional structured input)
  if (typeof o.decision === "string" && o.decision) {
    const notes = typeof o.notes === "string" ? o.notes : "";
    const extra =
      o.input && typeof o.input === "object" && Object.keys(o.input as object).length
        ? `\nExtra: ${JSON.stringify(o.input)}`
        : "";
    return `Decision: ${o.decision}${notes ? `\nNotes: ${notes}` : ""}${extra}`;
  }
  if (typeof o.body === "string") return o.body;
  return "";
}

function nodeFailReason(node: FlowNode): string | null {
  if (node.status !== "Failed") return null;
  const fromOut = extractText(node.output);
  if (fromOut) return fromOut.slice(0, 500);
  if (node.output) return JSON.stringify(node.output).slice(0, 400);
  return "Node failed";
}

function policyBlocked(node: FlowNode): { blocked: boolean; reason?: string } {
  const g = node.guardrails;
  if (g?.blocked) return { blocked: true, reason: "Guardrail blocked" };
  const decisions = collectGuardrails(g);
  const hit = decisions.find((d) =>
    ["blocked", "flagged", "deny", "denied"].includes(String(d.result || "").toLowerCase())
  );
  if (hit) return { blocked: true, reason: `${hit.policy}: ${hit.result}` };
  const pol = node.policy as Record<string, unknown> | null | undefined;
  if (pol && (pol.gateway === "blocked" || pol.blocked === true)) {
    return { blocked: true, reason: String(pol.reason || pol.error || "Policy blocked") };
  }
  return { blocked: false };
}

export function DetailsPanel({
  open,
  onClose,
  node,
  chat,
  run,
  hitl,
  runError,
  hitlDecision,
  embedded,
  loading,
  busy,
  onSelectHitl,
}: {
  open: boolean;
  onClose: () => void;
  node?: FlowNode | null;
  chat?: ChatResult | null;
  run?: FlowRun | null;
  hitl?: {
    show: boolean;
    canApprove: boolean;
    busy: boolean;
    flowId?: string;
    reviewers?: { oids?: string[]; roles?: string[] };
    onSubmit: HitlSubmit;
  };
  runError?: string | null;
  hitlDecision?: string | null;
  embedded?: boolean;
  loading?: boolean;
  busy?: boolean;
  onSelectHitl?: () => void;
}) {
  if (!open) return null;

  const showingRunSummary = !node && !chat && !!run;
  const title = node?.label || (chat ? "Chat details" : run ? "Run summary" : "Details");
  const subtitle = node
    ? `${node.id} · ${node.status}`
    : chat?.agent
      ? `agent:${chat.agent}`
      : run
        ? `${run.flowName || run.flowId} · ${run.status}`
        : "";
  const failReason = node ? nodeFailReason(node) : null;
  // Only the actively executing node should show a spinner — not Pending / Succeeded / etc.
  const waiting = !!node && (loading || node.status === "Running");
  const isHitlNode = !!node && node.status === "Waiting_HITL";

  const shell = embedded
    ? "w-[22rem] shrink-0 h-full border-l border-[var(--line)] bg-[var(--panel-solid)] backdrop-blur-md p-3 overflow-y-auto flex flex-col gap-2.5"
    : "w-[22rem] shrink-0 border-l border-[var(--line)] bg-[var(--panel-solid)] backdrop-blur-md p-4 overflow-y-auto flex flex-col gap-3 fade-in";

  return (
    <aside className={shell}>
      <div className="flex justify-between items-start gap-2">
        <div className="min-w-0">
          <h3 className="font-display text-lg tracking-tight truncate text-[var(--ink)]">{title}</h3>
          {subtitle && <p className="text-[11px] text-[var(--sea)] mt-0.5 truncate">{subtitle}</p>}
        </div>
        <button
          type="button"
          className="btn-ghost text-xs px-2 py-1"
          onClick={onClose}
          title={node ? "Back to run summary" : "Close"}
        >
          {node ? "Summary" : "Close"}
        </button>
      </div>

      {busy && !node && (
        <div className="rounded-lg px-3 py-2 text-xs border border-sea/25 bg-sea/10 text-sea font-semibold flex items-center gap-2">
          <span className="inline-block w-3 h-3 rounded-full border-2 border-sea border-t-transparent animate-spin" />
          Processing your action…
        </div>
      )}

      {waiting && (
        <div className="rounded-lg px-3 py-3 text-xs border border-[var(--line)] bg-[var(--mist)] flex items-center gap-2 text-[var(--sea)]">
          <span className="inline-block w-3.5 h-3.5 rounded-full border-2 border-sea border-t-transparent animate-spin" />
          Executing this node…
        </div>
      )}

      {isHitlNode && (
        <div className="rounded-lg px-3 py-2 text-xs border border-ember/25 bg-ember/10 text-ember font-semibold">
          Human review required on this node
        </div>
      )}

      {hitlDecision && isHitlNode && (
        <div
          className={`rounded-lg px-3 py-2 text-xs font-semibold border ${
            hitlDecision === "approve"
              ? "bg-moss/10 border-moss/25 text-moss"
              : hitlDecision === "reject"
                ? "bg-ember/10 border-ember/25 text-ember"
                : "bg-sea/10 border-sea/20 text-sea"
          }`}
        >
          Review decision: {hitlDecision}
        </div>
      )}

      {(failReason || (runError && node?.status === "Failed")) && (
        <div className="rounded-lg px-3 py-2 text-xs border border-red-200 bg-red-50 text-red-800 dark:bg-red-950/40 dark:border-red-800 dark:text-red-200">
          <div className="font-semibold mb-0.5">Failure reason</div>
          <div className="whitespace-pre-wrap leading-snug">{failReason || runError}</div>
        </div>
      )}

      {/* HITL form only when this specific node is waiting for review */}
      {hitl?.show && isHitlNode && (
        <HitlPanel
          canApprove={hitl.canApprove}
          busy={hitl.busy}
          flowId={hitl.flowId}
          reviewers={hitl.reviewers}
          onSubmit={hitl.onSubmit}
        />
      )}

      {node && <NodeDetails node={node} />}
      {chat && !node && <ChatDetails chat={chat} />}
      {showingRunSummary && run && (
        <RunSummary run={run} busy={busy} onSelectHitl={onSelectHitl} />
      )}
    </aside>
  );
}

function NodeDetails({ node }: { node: FlowNode }) {
  const [showExtras, setShowExtras] = useState(false);
  const decisions = collectGuardrails(node.guardrails);
  const blocked = policyBlocked(node);
  const duration = computeDurationMs(node);
  const inputText = useMemo(() => {
    const fromFields = extractText(node.input);
    if (fromFields) return fromFields;
    if (node.input && typeof node.input === "object") {
      const o = node.input as Record<string, unknown>;
      if (typeof o.message === "string" && o.message) return o.message;
      if (typeof o.decision === "string") {
        return extractText(o) || JSON.stringify(o, null, 2);
      }
      // Show structured HITL / agent input rather than blank
      try {
        return JSON.stringify(o, null, 2);
      } catch {
        return "";
      }
    }
    return "";
  }, [node.input]);

  const formattedOut = useMemo(() => {
    if (node.status === "Running" || node.status === "Pending" || node.status === "Skipped" || node.status === "Waiting_HITL") {
      return null;
    }
    const fromFields = extractText(node.output);
    if (fromFields) return formatAgentReply(fromFields);
    if (typeof node.output === "string") return formatAgentReply(node.output);
    if (node.output && typeof node.output === "object") {
      const o = node.output as Record<string, unknown>;
      const nested = o.output_markdown || o.reply || o.content || o.notes;
      if (typeof nested === "string") return formatAgentReply(nested);
      // Format the whole object via json2md (handles chat.completion shells)
      return formatAgentReply(JSON.stringify(node.output));
    }
    return null;
  }, [node.output, node.status]);

  return (
    <div className="space-y-2">
      {blocked.blocked && (
        <div className="rounded-lg px-3 py-2 text-xs border border-ember/30 bg-ember/10 text-ember font-semibold">
          Blocked by policy / guardrail
          {blocked.reason ? ` — ${blocked.reason}` : ""}
        </div>
      )}

      <Collapse title="Status & timing" defaultOpen>
        <div className="text-[11px] font-mono space-y-1 text-[var(--ink)]/80">
          <div>status: {node.status}</div>
          <div>started: {formatWhen(node.startedAt)}</div>
          <div>ended: {formatWhen(node.endedAt)}</div>
          <div>duration: {formatDuration(duration)}</div>
        </div>
      </Collapse>

      <Collapse title="Input" defaultOpen>
        {inputText ? (
          <div className="text-sm whitespace-pre-wrap leading-relaxed text-[var(--ink)]">{inputText}</div>
        ) : node.input ? (
          <pre className="text-[10px] whitespace-pre-wrap max-h-40 overflow-auto font-mono text-[var(--ink)]/75">
            {JSON.stringify(node.input, null, 2)}
          </pre>
        ) : (
          <div className="text-[11px] text-[var(--muted)]">No input recorded yet.</div>
        )}
      </Collapse>

      <Collapse title="Output / HITL result" defaultOpen>
        {node.status === "Running" ? (
          <div className="text-[11px] text-[var(--sea)] flex items-center gap-2">
            <span className="inline-block w-3 h-3 rounded-full border-2 border-sea border-t-transparent animate-spin" />
            Generating…
          </div>
        ) : node.status === "Waiting_HITL" ? (
          <div className="text-[11px] text-[var(--muted)]">Waiting for human decision…</div>
        ) : node.status === "Pending" ? (
          <div className="text-[11px] text-[var(--muted)]">Not started yet.</div>
        ) : node.status === "Skipped" ? (
          <div className="text-[11px] text-[var(--muted)]">
            {extractText(node.output) || "This path was not taken."}
          </div>
        ) : formattedOut?.markdown ? (
          <div className="chat-md text-[var(--ink)]">
            <ReactMarkdown>{formattedOut.markdown}</ReactMarkdown>
          </div>
        ) : (
          <div className="text-[11px] text-[var(--muted)]">No output yet.</div>
        )}
        {node.output != null && node.status !== "Pending" && node.status !== "Running" && (
          <button
            type="button"
            className="mt-2 text-[11px] text-[var(--sea)] underline-offset-2 hover:underline"
            onClick={() => setShowExtras((v) => !v)}
          >
            {showExtras ? "Hide raw details" : "Show raw details"}
          </button>
        )}
        {showExtras && (
          <pre className="mt-2 text-[10px] whitespace-pre-wrap max-h-48 overflow-auto bg-[var(--mist)] p-2 rounded-lg font-mono">
            {JSON.stringify(
              {
                usage: node.usage,
                tools: node.toolExecutions || node.tools,
                guardrails: node.guardrails,
                policy: node.policy,
                output: node.output,
              },
              null,
              2
            )}
          </pre>
        )}
      </Collapse>

      <Collapse
        title="Agent trace"
        defaultOpen={
          Array.isArray((node as { trace?: unknown }).trace) ||
          Array.isArray((node.output as { trace?: unknown } | null)?.trace)
        }
      >
        {(() => {
          const fromOutput = (node.output as { trace?: Array<Record<string, unknown>> } | null)?.trace;
          const steps: Array<Record<string, unknown>> =
            (node.trace as Array<Record<string, unknown>> | undefined) ||
            (Array.isArray(fromOutput) ? fromOutput : []);
          if (!steps.length) {
            return (
              <div className="text-[11px] text-[var(--muted)]">No agent trace recorded for this node yet.</div>
            );
          }
          return (
            <ol className="space-y-2 text-[11px]">
              {steps.map((step, i) => (
                <li key={i} className="rounded-lg border border-[var(--line)] bg-[var(--mist)] px-2.5 py-2">
                  <div className="flex flex-wrap gap-1.5 items-baseline">
                    <span className="font-semibold">{String(step.step || step.type || "step")}</span>
                    {step.name != null && <span className="text-[var(--sea)]">{String(step.name)}</span>}
                    {step.status != null && (
                      <span className="ml-auto text-[10px] uppercase text-[var(--muted)]">
                        {String(step.status)}
                      </span>
                    )}
                  </div>
                </li>
              ))}
            </ol>
          );
        })()}
      </Collapse>

      <Collapse title="Guardrails & policy" defaultOpen={blocked.blocked || decisions.length > 0}>
        <ul className="text-[11px] space-y-1.5">
          {decisions.map((d, i) => (
            <li key={i} className="flex flex-wrap gap-1.5 items-baseline">
              <span className="font-semibold text-[var(--ink)]">{d.policy}</span>
              <span
                className={
                  ["blocked", "flagged", "deny", "denied", "error"].includes(
                    String(d.result || "").toLowerCase()
                  )
                    ? "text-ember font-semibold"
                    : "text-[var(--sea)]"
                }
              >
                {d.result}
              </span>
            </li>
          ))}
          {node.policy != null && (
            <li className="pt-1">
              <div className="font-semibold text-[var(--ink)] mb-0.5">gateway policy</div>
              <pre className="text-[10px] whitespace-pre-wrap font-mono text-[var(--ink)]/70">
                {JSON.stringify(node.policy, null, 2)}
              </pre>
            </li>
          )}
          {!decisions.length && node.policy == null && (
            <li className="text-[var(--muted)]">No guardrail decisions recorded for this node.</li>
          )}
        </ul>
      </Collapse>
    </div>
  );
}

function ChatDetails({ chat }: { chat: ChatResult }) {
  const decisions = collectGuardrails(chat.guardrails);
  const trace = chat.trace || [];
  return (
    <div className="space-y-2">
      {chat.input && (
        <Collapse title="Input" defaultOpen>
          <div className="text-sm whitespace-pre-wrap">{chat.input}</div>
        </Collapse>
      )}
      <Collapse title="Output" defaultOpen>
        <div className="text-sm whitespace-pre-wrap">
          {chat.output_markdown || chat.reply || "—"}
        </div>
      </Collapse>
      <Collapse title="Agent trace" defaultOpen={trace.length > 0}>
        {trace.length ? (
          <ol className="space-y-2 text-[11px]">
            {trace.map((step, i) => (
              <li
                key={i}
                className="rounded-lg border border-[var(--line)] bg-[var(--mist)] px-2.5 py-2"
              >
                <div className="flex flex-wrap gap-1.5 items-baseline">
                  <span className="font-semibold text-[var(--ink)]">
                    {step.step || step.type || "step"}
                  </span>
                  {step.name && <span className="text-[var(--sea)]">{String(step.name)}</span>}
                  {step.status && (
                    <span className="ml-auto text-[10px] uppercase tracking-wide text-[var(--muted)]">
                      {String(step.status)}
                    </span>
                  )}
                </div>
                {step.summary && (
                  <div className="mt-1 text-[var(--ink)]/75 whitespace-pre-wrap">{String(step.summary)}</div>
                )}
                {step.note && <div className="mt-1 text-[var(--muted)]">{String(step.note)}</div>}
                {(step.arguments != null || step.output != null) && (
                  <pre className="mt-1.5 text-[10px] whitespace-pre-wrap font-mono text-[var(--ink)]/65 max-h-28 overflow-auto">
                    {JSON.stringify(
                      { arguments: step.arguments, output: step.output },
                      null,
                      2
                    )}
                  </pre>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <div className="text-[11px] text-[var(--muted)]">No trace steps for this turn.</div>
        )}
        {(chat.responseId || chat.previousResponseId || chat.sessionId) && (
          <div className="mt-2 text-[10px] font-mono text-[var(--muted)] space-y-0.5">
            {chat.sessionId && <div>session: {chat.sessionId}</div>}
            {chat.previousResponseId && <div>prev: {chat.previousResponseId}</div>}
            {chat.responseId && <div>response: {chat.responseId}</div>}
          </div>
        )}
      </Collapse>
      <Collapse title="Tokens" defaultOpen>
        <div className="text-[11px] font-mono leading-relaxed text-[var(--ink)]/80">
          model: {chat.model?.deployment || "—"}
          <br />
          prompt: {chat.usage?.promptTokens ?? "—"} · completion:{" "}
          {chat.usage?.completionTokens ?? "—"} · total: {chat.usage?.totalTokens ?? "—"}
        </div>
      </Collapse>
      <Collapse title="Tools / skills / connectors">
        <pre className="text-[11px] whitespace-pre-wrap font-mono text-[var(--ink)]/75">
          {JSON.stringify(
            {
              skills: chat.skills,
              mcps: chat.mcps,
              tools: chat.tools,
              toolExecutions: chat.toolExecutions,
            },
            null,
            2
          )}
        </pre>
      </Collapse>
      <Collapse title="Guardrails" defaultOpen={!!chat.guardrails?.blocked || decisions.length > 0}>
        <ul className="text-[11px] space-y-1">
          {decisions.map((d, i) => (
            <li key={i}>
              <span className="font-semibold">{d.policy}</span>: {d.result}
            </li>
          ))}
          {!decisions.length && <li className="text-[var(--muted)]">None recorded</li>}
        </ul>
      </Collapse>
    </div>
  );
}

function Collapse({
  title,
  children,
  defaultOpen = false,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <div className="border border-[var(--line)] rounded-xl overflow-hidden bg-[var(--panel)]">
      <button
        type="button"
        className="w-full text-left px-3 py-2 text-[10px] uppercase tracking-[0.12em] text-[var(--muted)] font-semibold hover:bg-[var(--mist)] flex justify-between items-center transition"
        onClick={() => setOpen((v) => !v)}
      >
        {title}
        <span className="text-[var(--sea)] text-xs">{open ? "▾" : "▸"}</span>
      </button>
      {open && (
        <div className="px-3 pb-3 pt-1 text-sm border-t border-[var(--line)]">{children}</div>
      )}
    </div>
  );
}
