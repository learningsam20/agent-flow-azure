import { memo, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  MiniMap,
  Position,
  ReactFlow,
  useReactFlow,
  ReactFlowProvider,
  type Edge,
  type Node,
  type NodeProps,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { api, type FlowGraph, type FlowRun } from "../lib/api";

const STATUS_COLOR: Record<string, string> = {
  Pending: "#94a3b8",
  Running: "#176a86",
  Waiting_HITL: "#b84f1f",
  Succeeded: "#2a6450",
  Failed: "#b91c1c",
  // Readable slate — was #cbd5e1 which vanished on light canvas / minimap
  Skipped: "#64748b",
  Cancelled: "#78716c",
  Paused: "#a16207",
};

/** Module cache so same workflow never re-fetches / re-lays out from scratch. */
const graphCache = new Map<string, FlowGraph>();

export function peekFlowGraph(flowId: string): FlowGraph | null {
  return graphCache.get(flowId) || null;
}

export function cacheFlowGraph(graph: FlowGraph): void {
  if (graph?.flowId) graphCache.set(graph.flowId, graph);
}

type AgentNodeData = {
  title: string;
  status: string;
  agent?: string | null;
  branch?: string | null;
  toolNames: string[];
  reason: string | null;
  policyHit: string | null;
  decisionOut: string | null;
  hitlDecision?: string | null;
  isActive: boolean;
  isFailed: boolean;
  onRestart?: (id: string) => void;
};

function failReason(n: FlowRun["nodes"][0]): string | null {
  if (n.status !== "Failed") return null;
  const out = n.output;
  if (!out) return "Failed";
  if (typeof out === "string") return out.slice(0, 120);
  if (typeof out === "object") {
    const o = out as Record<string, unknown>;
    const err = o.error || o.body || o.detail || o.message;
    if (typeof err === "string") return err.slice(0, 120);
    if (err) return JSON.stringify(err).slice(0, 120);
  }
  return "Failed";
}

function guardrailBlock(n: FlowRun["nodes"][0]): string | null {
  const g = n.guardrails as
    | {
        blocked?: boolean;
        decisions?: Array<{ policy?: string; result?: string }>;
        input?: { decisions?: Array<{ policy?: string; result?: string }> };
      }
    | null
    | undefined;
  if (g?.blocked) return "Guardrail blocked";
  const decisions = [...(g?.decisions || []), ...(g?.input?.decisions || [])];
  const hit = decisions.find((d) =>
    ["blocked", "flagged", "deny", "denied"].includes(String(d.result || "").toLowerCase())
  );
  if (hit) return `${hit.policy || "policy"}: ${hit.result}`;
  const pol = n.policy as Record<string, unknown> | null | undefined;
  if (pol && (pol.gateway === "blocked" || pol.blocked === true)) {
    return String(pol.reason || pol.error || "Policy blocked");
  }
  if (n.status === "Failed" && failReason(n)?.toLowerCase().includes("guardrail")) {
    return failReason(n);
  }
  return null;
}

const AgentFlowNode = memo(function AgentFlowNode({ id, data, selected }: NodeProps<Node<AgentNodeData>>) {
  const color = STATUS_COLOR[data.status] || "#64748b";
  const skipped = data.status === "Skipped";
  const bg =
    data.policyHit || data.isFailed
      ? "#fef2f2"
      : data.status === "Waiting_HITL"
        ? "#fff7ed"
        : data.status === "Running"
          ? "#eff8fc"
          : data.status === "Succeeded"
            ? "#f0fdf4"
            : skipped
              ? "#eef2f6"
              : "#fffef9";

  const statusLabel =
    skipped &&
    data.hitlDecision === "approve" &&
    (data.branch === "reject" ||
      id === "Final_Rejected" ||
      id === "Node_HITL_Reject_Notes" ||
      id === "Cancelled_Response")
      ? "Skipped · approve taken"
      : skipped &&
          data.hitlDecision === "reject" &&
          (data.branch === "approve" ||
            id === "Final_Approved" ||
            id === "Node5_Synthesize" ||
            id === "Final_Response")
        ? "Skipped · reject taken"
        : data.status;

  return (
    <div
      className="text-left w-full"
      style={{
        border: `${data.isActive || data.isFailed || selected ? 3 : 2}px ${skipped ? "dashed" : "solid"} ${color}`,
        borderRadius: 12,
        padding: 10,
        width: 188,
        background: bg,
        outline: data.policyHit ? "2px solid #b84f1f" : undefined,
        fontSize: 12,
        // Soft mute via color/border — not opacity (opacity hid labels + minimap chips)
        opacity: 1,
        filter: skipped ? "saturate(0.55)" : undefined,
        boxShadow: data.isActive
          ? `0 0 0 4px ${color}40, 0 8px 20px ${color}22`
          : data.isFailed
            ? "0 0 0 3px rgba(185,28,28,0.2)"
            : selected
              ? "0 0 0 2px rgba(23,106,134,0.25)"
              : skipped
                ? "none"
                : "0 1px 3px rgba(12,24,41,0.06)",
      }}
    >
      <Handle type="target" position={Position.Top} className="!bg-sea/40 !w-2 !h-2 !border-0" />
      <div
        className="font-semibold text-[12px] leading-tight flex items-center gap-1"
        style={{ color: skipped ? "#334155" : "inherit" }}
      >
        <span
          className={`inline-block w-2.5 h-2.5 rounded-full shrink-0 ${data.isActive ? "pulse-dot" : ""}`}
          style={{ background: color }}
        />
        <span className="truncate">{data.title}</span>
      </div>
      <div className="text-[11px] mt-0.5 font-semibold" style={{ color }}>
        {statusLabel}
        {data.isActive && data.status === "Running" ? " · executing" : ""}
        {data.isActive && data.status === "Waiting_HITL" ? " · needs review" : ""}
      </div>
      {data.decisionOut && (
        <div
          className={`text-[10px] mt-0.5 font-semibold ${
            data.decisionOut === "approve"
              ? "text-moss"
              : data.decisionOut === "reject"
                ? "text-ember"
                : "text-sea"
          }`}
        >
          HITL: {data.decisionOut}
        </div>
      )}
      {data.policyHit && (
        <div className="text-[10px] mt-1 text-ember font-semibold leading-snug line-clamp-2" title={data.policyHit}>
          Policy: {data.policyHit}
        </div>
      )}
      {data.reason && !data.policyHit && (
        <div className="text-[10px] mt-1 text-red-700 leading-snug line-clamp-2" title={data.reason}>
          {data.reason}
        </div>
      )}
      {data.agent && !data.reason && !data.policyHit && (
        <div className="text-[10px] opacity-60 truncate mt-0.5">{data.agent.replace("-agent", "")}</div>
      )}
      {!!data.toolNames.length && !data.reason && !data.policyHit && (
        <div className="mt-1 flex flex-wrap gap-0.5">
          {data.toolNames.map((t) => (
            <span key={t} className="text-[9px] px-1 py-0.5 rounded bg-ink/5 border border-ink/10">
              {t}
            </span>
          ))}
        </div>
      )}
      {data.status === "Failed" && data.agent && data.onRestart && (
        <button
          type="button"
          className="mt-1 underline text-[10px] text-red-700"
          onClick={(e) => {
            e.stopPropagation();
            data.onRestart?.(id);
          }}
        >
          Retry from here
        </button>
      )}
      <Handle type="source" position={Position.Bottom} className="!bg-sea/40 !w-2 !h-2 !border-0" />
    </div>
  );
});

const nodeTypes = { agent: AgentFlowNode };

function FitViewController({
  structureKey,
  redrawToken,
}: {
  structureKey: string;
  redrawToken: number;
}) {
  const { fitView } = useReactFlow();
  const lastKey = useRef<string>("");
  const lastRedraw = useRef(0);
  useEffect(() => {
    const structureChanged = structureKey && structureKey !== lastKey.current;
    const manualRedraw = redrawToken > 0 && redrawToken !== lastRedraw.current;
    if (!structureChanged && !manualRedraw) return;
    lastKey.current = structureKey;
    lastRedraw.current = redrawToken;
    const t = setTimeout(() => fitView({ padding: 0.18, duration: 220 }), 40);
    return () => clearTimeout(t);
  }, [structureKey, redrawToken, fitView]);
  return null;
}

function MiniMapNav() {
  const { setCenter, getZoom } = useReactFlow();
  return (
    <MiniMap
      pannable
      zoomable
      zoomStep={0.6}
      nodeStrokeWidth={2}
      nodeColor={(n) => {
        const status = (n.data as AgentNodeData | undefined)?.status;
        return STATUS_COLOR[status || ""] || "#64748b";
      }}
      nodeStrokeColor={(n) => {
        const status = (n.data as AgentNodeData | undefined)?.status;
        if (status === "Skipped") return "#475569";
        return STATUS_COLOR[status || ""] || "#0c1829";
      }}
      maskColor="rgba(12, 24, 41, 0.14)"
      style={{ bottom: 12, right: 12, width: 168, height: 118 }}
      ariaLabel="Flow overview — click to jump, drag to pan, scroll to zoom"
      onClick={(_event, position) => {
        setCenter(position.x, position.y, { zoom: Math.max(getZoom(), 0.85), duration: 220 });
      }}
      onNodeClick={(_event, node) => {
        const w = node.measured?.width ?? node.width ?? 180;
        const h = node.measured?.height ?? node.height ?? 70;
        setCenter(node.position.x + w / 2, node.position.y + h / 2, {
          zoom: Math.max(getZoom(), 1),
          duration: 220,
        });
      }}
    />
  );
}

function CanvasInner({
  run,
  selectedNodeId,
  onSelectNode,
  onRestart,
  busy,
  busyLabel,
}: {
  run: FlowRun;
  selectedNodeId?: string | null;
  onSelectNode: (id: string) => void;
  onRestart: (id: string) => void;
  busy?: boolean;
  busyLabel?: string | null;
}) {
  const cached = graphCache.get(run.flowId) || null;
  const [template, setTemplate] = useState<FlowGraph | null>(cached);
  const [redrawToken, setRedrawToken] = useState(0);
  const [redrawing, setRedrawing] = useState(false);
  const [layoutOverride, setLayoutOverride] = useState<Record<string, { x: number; y: number }> | null>(
    null
  );

  useEffect(() => {
    setLayoutOverride(null);
    setRedrawToken(0);
  }, [run.flowId]);

  async function redrawLayout() {
    if (redrawing) return;
    setRedrawing(true);
    try {
      const g = await api.flowGraph(run.flowId);
      graphCache.set(run.flowId, g);
      setTemplate(g);
      const next: Record<string, { x: number; y: number }> = {};
      for (const n of g.nodes) {
        if (n.position) next[n.id] = n.position;
      }
      setLayoutOverride(next);
      setRedrawToken((t) => t + 1);
    } catch {
      // Still re-fit current layout if fetch fails
      setRedrawToken((t) => t + 1);
    } finally {
      window.setTimeout(() => setRedrawing(false), 280);
    }
  }

  useEffect(() => {
    const hit = graphCache.get(run.flowId);
    if (hit) {
      setTemplate(hit);
      return;
    }
    // Seed from run positions if the live run already carries layout (prefetch path)
    const runHasLayout = run.nodes.some((n) => n.position);
    if (runHasLayout) {
      const seeded: FlowGraph = {
        flowId: run.flowId,
        name: run.flowName,
        source: run.graphSource || "run",
        nodes: run.nodes.map((n) => ({ ...n, position: n.position })),
        edges: run.edges || [],
      };
      graphCache.set(run.flowId, seeded);
      setTemplate(seeded);
    }
    let cancelled = false;
    api
      .flowGraph(run.flowId)
      .then((g) => {
        if (cancelled) return;
        graphCache.set(run.flowId, g);
        setTemplate(g);
      })
      .catch(() => {
        if (!cancelled && !runHasLayout) setTemplate(null);
      });
    return () => {
      cancelled = true;
    };
  }, [run.flowId]);

  // If run arrives with positions before template fetch completes, adopt them once
  useEffect(() => {
    if (template || !run.nodes.some((n) => n.position)) return;
    const seeded: FlowGraph = {
      flowId: run.flowId,
      name: run.flowName,
      source: run.graphSource || "run",
      nodes: run.nodes.map((n) => ({ ...n, position: n.position })),
      edges: run.edges || [],
    };
    graphCache.set(run.flowId, seeded);
    setTemplate(seeded);
  }, [run, template]);

  const activeId =
    run.nodes.find((n) => n.status === "Running" || n.status === "Waiting_HITL")?.id || null;
  const failedId = run.nodes.find((n) => n.status === "Failed")?.id || null;
  const hitlDecision =
    (run as FlowRun & { hitlDecision?: string }).hitlDecision ||
    (run.nodes.find((n) => n.id === "Wait_For_HITL_Callback")?.output as { decision?: string } | undefined)
      ?.decision;

  const structureKey = `${run.flowId}|${run.nodes.map((n) => n.id).join(",")}|${
    (run.edges || template?.edges || []).map((e) => e.id).join(",")
  }`;

  const positions = useMemo(() => {
    const map: Record<string, { x: number; y: number }> = {};
    // Template / redraw layout is canonical — run payloads often lack or carry a temp grid.
    for (const n of template?.nodes || []) {
      if (n.position) map[n.id] = n.position;
    }
    for (const n of run.nodes) {
      if (n.position && !map[n.id]) map[n.id] = n.position;
    }
    // Manual Redraw wins until the flow changes.
    if (layoutOverride) Object.assign(map, layoutOverride);
    return map;
  }, [template, layoutOverride, run.flowId, run.nodes]);

  const nodes: Node<AgentNodeData>[] = useMemo(() => {
    return run.nodes.map((n, idx) => {
      const pos = positions[n.id] || { x: (idx % 3) * 240, y: Math.floor(idx / 3) * 140 };
      const isActive = n.id === activeId;
      const isFailed = n.id === failedId;
      const toolNames = (n.tools || [])
        .map((t) => (typeof t === "string" ? t : t?.name))
        .filter(Boolean)
        .map(String)
        .slice(0, 2);
      const decisionOut =
        n.id.includes("HITL") || n.id.startsWith("Wait_For_HITL")
          ? (n.output as { decision?: string } | undefined)?.decision || hitlDecision || null
          : null;

      return {
        id: n.id,
        type: "agent",
        position: pos,
        zIndex: isActive || isFailed || n.id === selectedNodeId ? 10 : 1,
        selected: n.id === selectedNodeId,
        data: {
          title: n.label,
          status: n.status,
          agent: n.agent,
          branch: n.branch,
          toolNames,
          reason: failReason(n),
          policyHit: guardrailBlock(n),
          decisionOut,
          hitlDecision: hitlDecision || null,
          isActive,
          isFailed,
          onRestart,
        },
      };
    });
  }, [run.nodes, positions, activeId, failedId, selectedNodeId, hitlDecision, onRestart]);

  const edges: Edge[] = useMemo(() => {
    const edgeDefs = run.edges?.length ? run.edges : template?.edges || [];
    return edgeDefs.map((e) => {
      const toNode = run.nodes.find((n) => n.id === e.target);
      const isApproveEdge = e.label === "Approve" || toNode?.branch === "approve";
      const isRejectEdge = e.label === "Reject" || toNode?.branch === "reject";
      const untakenByHitl =
        (hitlDecision === "approve" && isRejectEdge) ||
        (hitlDecision === "reject" && isApproveEdge);
      const dimmed = toNode?.status === "Skipped" || untakenByHitl;
      const intoActive = e.target === activeId || e.source === activeId;
      const takenHitl =
        (hitlDecision === "approve" && isApproveEdge) ||
        (hitlDecision === "reject" && isRejectEdge);
      const stroke = dimmed
        ? "#94a3b8"
        : isApproveEdge
          ? "#2a6450"
          : isRejectEdge
            ? "#b91c1c"
            : "#176a86";
      return {
        id: e.id,
        source: e.source,
        target: e.target,
        // Keep branch labels visible on untaken paths (was stripped → “nothing there”)
        label: e.label || undefined,
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 16,
          height: 16,
          color: stroke,
        },
        animated: intoActive && !dimmed,
        style: {
          stroke,
          strokeWidth: takenHitl || intoActive ? 2.5 : dimmed ? 1.5 : 1.75,
          opacity: dimmed ? 0.72 : 1,
          strokeDasharray: dimmed ? "6 4" : undefined,
        },
        labelStyle: {
          fontSize: 10,
          fill: dimmed ? "#64748b" : "#334155",
          fontWeight: dimmed ? 500 : 600,
        },
        labelBgStyle: {
          fill: dimmed ? "#f1f5f9" : "#ffffff",
          fillOpacity: 0.92,
        },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
      };
    });
  }, [run.edges, run.nodes, template?.edges, activeId, hitlDecision]);

  return (
    <div className="h-full w-full relative">
      <div className="absolute z-20 left-3 top-3 right-3 flex flex-wrap items-start gap-2 pointer-events-none">
        <div className="pointer-events-auto text-[11px] bg-white/95 border border-sea/15 rounded-xl px-3 py-1.5 text-sea shadow-sm backdrop-blur-sm">
          <span className="font-semibold">{run.flowName || run.flowId}</span>
          <span className="mx-1.5 text-ink/25">·</span>
          <span className="font-semibold" style={{ color: STATUS_COLOR[run.status] || "#64748b" }}>
            {run.status}
          </span>
          {hitlDecision && (
            <>
              <span className="mx-1.5 text-ink/25">·</span>
              <span
                className={
                  hitlDecision === "approve"
                    ? "text-moss font-semibold"
                    : hitlDecision === "reject"
                      ? "text-ember font-semibold"
                      : "font-semibold"
                }
              >
                HITL {hitlDecision}
              </span>
            </>
          )}
        </div>
        {failedId && (
          <div className="pointer-events-auto text-[11px] bg-red-50 border border-red-200 text-red-800 rounded-xl px-3 py-1.5 shadow-sm max-w-md">
            <span className="font-semibold">Failed at {failedId}</span>
            <span className="opacity-80"> — click node for details</span>
          </div>
        )}
        {activeId && run.status === "Running" && (
          <div className="pointer-events-auto text-[11px] bg-sea/10 border border-sea/20 text-sea rounded-xl px-3 py-1.5 shadow-sm">
            Executing <span className="font-semibold">{activeId}</span>
          </div>
        )}
        <button
          type="button"
          title="Redraw layout"
          aria-label="Redraw layout"
          disabled={redrawing}
          onClick={() => void redrawLayout()}
          className="pointer-events-auto ml-auto text-[11px] inline-flex items-center gap-1.5 bg-white/95 border border-sea/15 rounded-xl px-2.5 py-1.5 text-sea shadow-sm backdrop-blur-sm hover:bg-sea/5 disabled:opacity-60"
        >
          <svg
            width="14"
            height="14"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className={redrawing ? "animate-spin" : undefined}
            aria-hidden
          >
            <path d="M21 12a9 9 0 1 1-2.6-6.3" />
            <polyline points="21 3 21 9 15 9" />
          </svg>
          {redrawing ? "Redrawing…" : "Redraw"}
        </button>
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        fitView
        onNodeClick={(_, n) => onSelectNode(n.id)}
        onPaneClick={() => {
          // Empty canvas click → clear selection / show run summary
          if (selectedNodeId) onSelectNode(selectedNodeId);
        }}
        proOptions={{ hideAttribution: true }}
        minZoom={0.25}
        maxZoom={1.6}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        defaultViewport={{ x: 0, y: 0, zoom: 0.85 }}
      >
        <FitViewController structureKey={structureKey} redrawToken={redrawToken} />
        <Background gap={18} color="#c5d6de" />
        <MiniMapNav />
        <Controls position="bottom-left" showInteractive={false} style={{ bottom: 12, left: 12, margin: 0 }} />
      </ReactFlow>
      {busy && (
        <div className="absolute inset-x-0 bottom-3 z-30 flex justify-center pointer-events-none px-3">
          <div className="pointer-events-none text-[12px] bg-sea text-white rounded-full px-3.5 py-1.5 shadow-lg flex items-center gap-2 font-semibold">
            <span className="inline-block w-3 h-3 rounded-full border-2 border-white border-t-transparent animate-spin" />
            {busyLabel || "Processing…"}
          </div>
        </div>
      )}
    </div>
  );
}

export function FlowCanvas(props: {
  run: FlowRun;
  selectedNodeId?: string | null;
  onSelectNode: (id: string) => void;
  onRestart: (id: string) => void;
  onRetryNode?: (id: string) => void;
  onStopNode?: (id: string) => void;
  busy?: boolean;
  busyLabel?: string | null;
}) {
  return (
    <ReactFlowProvider>
      <CanvasInner
        run={props.run}
        selectedNodeId={props.selectedNodeId}
        onSelectNode={props.onSelectNode}
        onRestart={props.onRestart}
        busy={props.busy}
        busyLabel={props.busyLabel}
      />
    </ReactFlowProvider>
  );
}
