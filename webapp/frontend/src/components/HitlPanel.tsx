import { useState } from "react";

export function HitlPanel({
  canApprove,
  onSubmit,
  busy,
  flowId,
  reviewers,
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
  flowId?: string;
  reviewers?: { oids?: string[]; roles?: string[] };
}) {
  const [notes, setNotes] = useState("");
  const [pathOverride, setPathOverride] = useState("A");
  const [budgetCap, setBudgetCap] = useState("50");
  const [severity, setSeverity] = useState("high");

  const extraInput: Record<string, string> = {};
  if (flowId === "finops-approval-flow") extraInput.budgetCap = budgetCap;
  if (flowId === "security-incident-flow") extraInput.severity = severity;

  return (
    <div className="rounded-xl border border-ember/30 bg-gradient-to-b from-ember/10 to-white p-3.5 space-y-2.5 shadow-sm">
      <div className="flex items-center gap-2">
        <span className="w-2 h-2 rounded-full bg-ember pulse-dot" />
        <div className="text-sm font-semibold text-ember">Human review required</div>
      </div>
      <p className="text-[11px] text-ink/65 leading-snug">
        Enter notes, then approve, reject, or re-route to continue.
      </p>
      {!!reviewers?.roles?.length && (
        <p className="text-[10px] text-sea">Reviewers: {reviewers.roles.join(", ")}</p>
      )}
      {!canApprove && (
        <p className="text-xs text-ember font-semibold">Your role cannot approve this HITL.</p>
      )}
      <textarea
        className="w-full min-h-[72px] rounded-lg border border-ink/10 p-2.5 text-sm bg-white focus:outline-none focus:ring-2 focus:ring-ember/25"
        placeholder="Reviewer notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
      />
      {flowId === "finops-approval-flow" && (
        <label className="block text-[11px] font-medium text-ink/60">
          Budget cap (USD)
          <input
            className="mt-1 w-full rounded-lg border border-ink/10 p-2 text-sm"
            value={budgetCap}
            onChange={(e) => setBudgetCap(e.target.value)}
          />
        </label>
      )}
      {flowId === "security-incident-flow" && (
        <label className="block text-[11px] font-medium text-ink/60">
          Severity
          <select
            className="mt-1 w-full rounded-lg border border-ink/10 p-2 text-sm"
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
      <label className="block text-[11px] font-medium text-ink/60">
        Re-route path
        <select
          className="mt-1 w-full rounded-lg border border-ink/10 p-2 text-sm"
          value={pathOverride}
          onChange={(e) => setPathOverride(e.target.value)}
        >
          <option value="A">A (parallel repo+sec)</option>
          <option value="B">B (FinOps)</option>
        </select>
      </label>
      <div className="flex flex-wrap gap-1.5 pt-1">
        <button
          type="button"
          disabled={!canApprove || busy}
          onClick={() => onSubmit({ decision: "reject", notes, input: extraInput })}
          className="btn-ghost text-xs text-ember border-ember/30 hover:bg-ember/5"
        >
          Reject
        </button>
        <button
          type="button"
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
          className="btn-ghost text-xs text-sea"
        >
          Re-route
        </button>
        <button
          type="button"
          disabled={!canApprove || busy}
          onClick={() => onSubmit({ decision: "approve", notes, input: extraInput })}
          className="btn-moss text-xs"
        >
          Approve
        </button>
      </div>
    </div>
  );
}
