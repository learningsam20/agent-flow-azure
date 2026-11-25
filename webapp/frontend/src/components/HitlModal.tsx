import { useState } from "react";

export function HitlModal({
  canApprove,
  onSubmit,
  busy,
  reviewers,
  flowId,
}: {
  canApprove: boolean;
  onSubmit: (payload: {
    decision: string;
    notes: string;
    pathOverride?: string;
    restartFromNode?: string;
    input?: Record<string, string>;
  }) => void;
  busy: boolean;
  reviewers?: { oids?: string[]; roles?: string[] };
  flowId?: string;
}) {
  const [notes, setNotes] = useState("");
  const [pathOverride, setPathOverride] = useState("A");
  const [budgetCap, setBudgetCap] = useState("50");
  const [severity, setSeverity] = useState("high");

  const extraInput: Record<string, string> = {};
  if (flowId === "finops-approval-flow") extraInput.budgetCap = budgetCap;
  if (flowId === "security-incident-flow") extraInput.severity = severity;

  return (
    <div className="fixed inset-0 z-50 bg-ink/40 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-sand w-full max-w-lg rounded-xl border border-ember/30 shadow-xl p-5">
        <h3 className="font-display text-xl text-ink">Human-in-the-Loop</h3>
        <p className="text-sm text-ink/70 mt-1">
          Provide input to continue the Logic App. Approve, reject, or re-route and re-execute.
        </p>
        {!!(reviewers?.roles?.length || reviewers?.oids?.length) && (
          <p className="mt-2 text-[11px] text-sea">
            Reviewers: {(reviewers?.roles || []).join(", ") || "—"}
            {reviewers?.oids?.length ? ` · ${reviewers.oids.length} oid(s)` : ""}
          </p>
        )}
        {!canApprove && (
          <p className="mt-3 text-sm text-ember font-semibold">
            Your role cannot approve this HITL (assigned reviewers / Agent.Admin / SecurityAuditor /
            App.Admin).
          </p>
        )}
        <textarea
          className="mt-3 w-full min-h-[90px] rounded-lg border border-ink/15 p-2 text-sm"
          placeholder="Reviewer notes / instructions for downstream agents"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
        />
        {flowId === "finops-approval-flow" && (
          <label className="mt-2 block text-xs">
            Budget cap (USD)
            <input
              className="mt-1 w-full rounded border border-ink/15 p-2 text-sm"
              value={budgetCap}
              onChange={(e) => setBudgetCap(e.target.value)}
            />
          </label>
        )}
        {flowId === "security-incident-flow" && (
          <label className="mt-2 block text-xs">
            Severity
            <select
              className="mt-1 w-full rounded border border-ink/15 p-2 text-sm"
              value={severity}
              onChange={(e) => setSeverity(e.target.value)}
            >
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </select>
          </label>
        )}
        <div className="mt-3 flex flex-wrap items-center gap-2 text-xs">
          <span className="text-ink/60">Re-route path:</span>
          <select
            className="rounded border border-ink/15 px-2 py-1"
            value={pathOverride}
            onChange={(e) => setPathOverride(e.target.value)}
          >
            <option value="A">A (parallel repo+sec)</option>
            <option value="B">B (FinOps)</option>
          </select>
        </div>
        <div className="mt-4 flex flex-wrap gap-2 justify-end">
          <button
            disabled={!canApprove || busy}
            onClick={() =>
              onSubmit({ decision: "reject", notes, input: extraInput })
            }
            className="px-3 py-1.5 rounded-md border border-ember text-ember text-sm disabled:opacity-40"
          >
            Reject
          </button>
          <button
            disabled={!canApprove || busy}
            onClick={() =>
              onSubmit({
                decision: "reroute",
                notes,
                pathOverride,
                restartFromNode: "Node1_Triage",
                input: extraInput,
              })
            }
            className="px-3 py-1.5 rounded-md border border-sea text-sea text-sm disabled:opacity-40"
          >
            Re-route &amp; re-run
          </button>
          <button
            disabled={!canApprove || busy}
            onClick={() =>
              onSubmit({ decision: "approve", notes, input: extraInput })
            }
            className="px-3 py-1.5 rounded-md bg-moss text-white text-sm disabled:opacity-40"
          >
            Approve &amp; resume
          </button>
        </div>
      </div>
    </div>
  );
}
