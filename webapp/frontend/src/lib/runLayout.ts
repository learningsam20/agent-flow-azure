import type { FlowGraph, FlowNode, FlowRun } from "./api";

type Pos = { x: number; y: number };

/**
 * Keep canvas layout stable across poll/control updates.
 * Template (workflow graph) positions are canonical — cached runs often lack
 * positions or carry a temporary grid that caused a "jumbled then snap" flash.
 */
export function mergeRunPreserveLayout(
  prev: FlowRun | null | undefined,
  next: FlowRun,
  template?: FlowGraph | null
): FlowRun {
  const posById: Record<string, Pos> = {};

  for (const n of template?.nodes || []) {
    if (n.position) posById[n.id] = n.position;
  }
  // Fill gaps only — never let stale run positions override the template.
  if (prev && prev.flowId === next.flowId) {
    for (const n of prev.nodes) {
      if (n.position && !posById[n.id]) posById[n.id] = n.position;
    }
  }
  for (const n of next.nodes) {
    if (n.position && !posById[n.id]) posById[n.id] = n.position;
  }

  const nodes: FlowNode[] = next.nodes.map((n) => ({
    ...n,
    position: posById[n.id] || n.position,
  }));

  return {
    ...next,
    edges: next.edges?.length ? next.edges : prev?.edges?.length ? prev.edges : template?.edges,
    graphSource: next.graphSource || prev?.graphSource || template?.source,
    nodes,
  };
}
