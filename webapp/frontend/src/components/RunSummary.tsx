import { useMemo } from "react";
import type { FlowRun } from "../lib/api";

function ms(n: number | null | undefined): string {
  if (n == null || Number.isNaN(n)) return "—";
  if (n < 1000) return `${n} ms`;
  if (n < 60_000) return `${(n / 1000).toFixed(1)} s`;
  const m = Math.floor(n / 60_000);
  const s = Math.round((n % 60_000) / 1000);
  return `${m}m ${s}s`;
}

function parseTs(iso?: string | null): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : t;
}

export type RunStats = {
  status: string;
  elapsedMs: number | null;
  agentCalls: number;
  toolCalls: number;
  totalTokens: number;
  promptTokens: number;
  completionTokens: number;
  hitlWaitMs: number | null;
  hitlDecision: string | null;
  succeeded: number;
  failed: number;
  skipped: number;
  pending: number;
  running: number;
  stages: number;
  retries: number;
};

export function computeRunStats(run: FlowRun): RunStats {
  const nodes = run.nodes || [];
  let agentCalls = 0;
  let toolCalls = 0;
  let totalTokens = 0;
  let promptTokens = 0;
  let completionTokens = 0;
  let hitlWaitMs: number | null = null;
  let succeeded = 0;
  let failed = 0;
  let skipped = 0;
  let pending = 0;
  let running = 0;
  let retries = 0;

  for (const n of nodes) {
    if (n.agent && ["Succeeded", "Failed", "Running", "Waiting_HITL"].includes(n.status)) {
      agentCalls += 1;
    }
    const tools = n.toolExecutions?.length
      ? n.toolExecutions.length
      : (n.tools || []).length;
    toolCalls += tools;
    const u = n.usage;
    if (u) {
      totalTokens += u.totalTokens || 0;
      promptTokens += u.promptTokens || 0;
      completionTokens += u.completionTokens || 0;
    }
    if (
      typeof n.durationMs === "number" &&
      (n.id.includes("HITL") || n.id.startsWith("Wait_For_HITL"))
    ) {
      hitlWaitMs = (hitlWaitMs || 0) + n.durationMs;
    }
    if (n.status === "Succeeded") succeeded += 1;
    else if (n.status === "Failed") failed += 1;
    else if (n.status === "Skipped") skipped += 1;
    else if (n.status === "Pending") pending += 1;
    else if (n.status === "Running" || n.status === "Waiting_HITL") running += 1;

    const out = n.output as { retry?: boolean; retries?: number } | undefined;
    if (out && typeof out === "object") {
      if (typeof out.retries === "number") retries += out.retries;
    }
  }

  // Prefer HITL node duration; else decision timestamp vs wait start
  if (hitlWaitMs == null) {
    const hitl = nodes.find(
      (n) => n.id.includes("HITL") || n.id.startsWith("Wait_For_HITL") || n.status === "Waiting_HITL"
    );
    if (hitl?.startedAt) {
      const end =
        parseTs(hitl.endedAt) ||
        parseTs((run as FlowRun & { hitl?: { at?: string } }).hitl?.at) ||
        (run.status === "Waiting_HITL" ? Date.now() : null);
      const start = parseTs(hitl.startedAt);
      if (start != null && end != null && end >= start) hitlWaitMs = end - start;
    }
  }

  const created = parseTs(run.createdAt);
  const updated =
    parseTs(run.updatedAt) ||
    Math.max(
      0,
      ...nodes.map((n) => parseTs(n.endedAt) || parseTs(n.startedAt) || 0)
    ) ||
    null;
  const elapsedMs =
    created != null
      ? Math.max(0, (updated && updated > 0 ? updated : Date.now()) - created)
      : null;

  const metaRetries = (run as FlowRun & { retryCount?: number }).retryCount;
  if (typeof metaRetries === "number") retries = Math.max(retries, metaRetries);

  return {
    status: run.status,
    elapsedMs,
    agentCalls,
    toolCalls,
    totalTokens,
    promptTokens,
    completionTokens,
    hitlWaitMs,
    hitlDecision: run.hitlDecision || null,
    succeeded,
    failed,
    skipped,
    pending,
    running,
    stages: nodes.filter((n) => !n.system).length || nodes.length,
    retries,
  };
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-[var(--line)] bg-[var(--mist)]/60 px-2.5 py-2">
      <div className="text-[10px] uppercase tracking-wide text-[var(--muted)]">{label}</div>
      <div className="text-sm font-semibold text-[var(--ink)] mt-0.5 tabular-nums">{value}</div>
    </div>
  );
}

export function RunSummary({
  run,
  busy,
  onSelectHitl,
}: {
  run: FlowRun;
  busy?: boolean;
  onSelectHitl?: () => void;
}) {
  const stats = useMemo(() => computeRunStats(run), [run]);
  const terminal = ["Succeeded", "Failed", "Cancelled"].includes(run.status);

  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-[var(--line)] px-3 py-2.5 bg-[var(--panel-solid)]">
        <div className="text-[11px] text-[var(--muted)]">Overall status</div>
        <div className="flex items-center gap-2 mt-0.5">
          {(busy || stats.running > 0) && !terminal && (
            <span className="inline-block w-3 h-3 rounded-full border-2 border-sea border-t-transparent animate-spin" />
          )}
          <span className="font-display text-lg text-[var(--ink)]">{stats.status}</span>
          {stats.hitlDecision && (
            <span className="text-[11px] font-semibold text-[var(--sea)]">· HITL {stats.hitlDecision}</span>
          )}
        </div>
        {run.message && (
          <p className="text-[12px] text-[var(--ink)]/75 mt-1.5 line-clamp-3 leading-snug">{run.message}</p>
        )}
      </div>

      {run.status === "Waiting_HITL" && onSelectHitl && (
        <button type="button" className="btn-ghost text-xs w-full border-ember/30 text-ember bg-ember/5" onClick={onSelectHitl}>
          Open HITL review
        </button>
      )}

      <div className="grid grid-cols-2 gap-1.5">
        <Stat label="Elapsed" value={ms(stats.elapsedMs)} />
        <Stat label="HITL wait" value={ms(stats.hitlWaitMs)} />
        <Stat label="Agent calls" value={stats.agentCalls} />
        <Stat label="Tool calls" value={stats.toolCalls} />
        <Stat label="Total tokens" value={stats.totalTokens || "—"} />
        <Stat
          label="Prompt / completion"
          value={
            stats.promptTokens || stats.completionTokens
              ? `${stats.promptTokens} / ${stats.completionTokens}`
              : "—"
          }
        />
        <Stat label="Succeeded" value={stats.succeeded} />
        <Stat label="Failed" value={stats.failed} />
        <Stat label="Skipped" value={stats.skipped} />
        <Stat label="Pending / active" value={`${stats.pending} / ${stats.running}`} />
        <Stat label="Stages" value={stats.stages} />
        <Stat label="Retries" value={stats.retries} />
      </div>

      <div className="text-[11px] text-[var(--muted)] leading-snug">
        Click a node on the canvas for stage input/output. Clear the selection (summary) anytime via Close
        on a node panel.
      </div>
    </div>
  );
}
