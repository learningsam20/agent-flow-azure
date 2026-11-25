import { ensureFreshToken, getAccessToken } from "./keycloak";

function errorMessage(data: unknown, fallback: string): string {
  const body = data as { detail?: { error?: string } | string; error?: string } | null;
  return (
    (typeof body?.detail === "object" && body?.detail?.error) ||
    body?.error ||
    (typeof body?.detail === "string" ? body.detail : undefined) ||
    fallback
  );
}

function isExpiredTokenError(status: number, data: unknown): boolean {
  if (status !== 401) return false;
  const msg = errorMessage(data, "").toLowerCase();
  return msg.includes("expired") || msg.includes("signature") || msg.includes("invalid keycloak token");
}

async function request<T>(path: string, init: RequestInit = {}, retried = false): Promise<T> {
  const headers = new Headers(init.headers || {});
  headers.set("Content-Type", "application/json");
  const token = (await ensureFreshToken()) || getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const correlationId = crypto.randomUUID();
  headers.set("X-Correlation-Id", correlationId);
  const res = await fetch(path, { ...init, headers });
  const text = await res.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = { detail: text };
    }
  }
  if (!res.ok) {
    // One refresh+retry when the access token expired mid-session.
    if (!retried && isExpiredTokenError(res.status, data)) {
      const fresh = await ensureFreshToken(0);
      if (fresh) return request<T>(path, init, true);
    }
    const err = new Error(
      errorMessage(data, res.status === 0 ? "Request aborted" : res.statusText)
    ) as Error & { status: number; body: unknown; correlationId: string; name: string };
    err.status = res.status;
    err.body = data;
    err.correlationId = correlationId;
    if (init.signal?.aborted || res.status === 0) err.name = "AbortError";
    throw err;
  }
  return data as T;
}

export const api = {
  me: () => request<MeResponse>("/api/me"),
  acl: () => request<{ acl_matrix: Record<string, unknown> }>("/api/acl"),
  listFlows: () => request<{ flows: FlowMeta[]; syncedAt?: string | null }>("/api/flows"),
  listAgents: () =>
    request<{
      agents: AgentMeta[];
      allowed: Record<string, boolean>;
      syncedAt?: string | null;
    }>("/api/agents"),
  syncAzure: () =>
    request<AzureSyncResult>("/api/sync/azure", { method: "POST", body: "{}" }),
  chat: (
    agentId: string,
    message: string,
    opts?: {
      sessionId?: string;
      previousResponseId?: string;
      history?: Array<{ role: string; content: string }>;
      signal?: AbortSignal;
    }
  ) =>
    request<ChatResult & { sessionId?: string; chatId?: string; responseId?: string }>(
      `/api/agents/${agentId}/chat`,
      {
        method: "POST",
        signal: opts?.signal,
        body: JSON.stringify({
          message,
          sessionId: opts?.sessionId,
          previousResponseId: opts?.previousResponseId,
          // Fallback bootstrap only; server prefers previous_response_id when present
          history: opts?.history || [],
        }),
      }
    ),
  runFlow: (message: string, flowId = "multi-agent-flow", path_hint?: string) =>
    request<FlowRun>(`/api/flows/${encodeURIComponent(flowId)}/run`, {
      method: "POST",
      body: JSON.stringify({ message, path_hint, flow_id: flowId }),
    }),
  listRuns: (allUsers = false) =>
    request<{ runs: FlowRun[]; chats: ChatSession[]; chatSessions?: ChatSession[] }>(
      `/api/flows/runs?all_users=${allUsers}`
    ),
  getRun: (id: string) => request<FlowRun>(`/api/flows/runs/${id}`),
  approve: (
    id: string,
    payload: {
      decision: string;
      notes: string;
      path_override?: string;
      restart_from_node?: string;
      input?: Record<string, unknown>;
    }
  ) =>
    request<FlowRun>(`/api/flows/${id}/approve`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  cancel: (id: string, nodeId?: string, reason = "") =>
    request<FlowRun>(`/api/flows/${id}/stop`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId, reason }),
    }),
  pause: (id: string, nodeId?: string) =>
    request<FlowRun>(`/api/flows/${id}/pause`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId }),
    }),
  resume: (id: string, nodeId?: string) =>
    request<FlowRun>(`/api/flows/${id}/resume`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId }),
    }),
  retry: (id: string, nodeId?: string) =>
    request<FlowRun>(`/api/flows/${id}/retry`, {
      method: "POST",
      body: JSON.stringify({ node_id: nodeId || "Init_State" }),
    }),
  restart: (id: string, nodeId: string) =>
    request<FlowRun>(`/api/flows/${id}/restart?node_id=${encodeURIComponent(nodeId)}`, {
      method: "POST",
    }),
  learning: (agentId?: string) =>
    request<{ learnings: LearningItem[] }>(
      `/api/learning${agentId ? `?agentId=${encodeURIComponent(agentId)}` : ""}`
    ),
  flowGraph: (flowId = "multi-agent-flow") =>
    request<FlowGraph>(`/api/flows/${encodeURIComponent(flowId)}/graph`),
  adminUsers: () => request<{ users: AdminUser[]; assignableAgentRoles: string[] }>("/api/admin/users"),
  setUserRoles: (oid: string, agentRoles: string[], email = "", name = "") =>
    request<AdminUser>(`/api/admin/users/${encodeURIComponent(oid)}/roles`, {
      method: "PUT",
      body: JSON.stringify({ agentRoles, email, name }),
    }),
};

export type FlowMeta = {
  id: string;
  name: string;
  description: string;
  demoPrompt: string;
  features: string[];
  hitlReviewerRoles?: string[];
  defaultPath?: string;
  source?: string;
  synced?: boolean;
  logicAppName?: string;
};

export type AgentMeta = {
  id: string;
  name: string;
  source?: string;
  kind?: string;
  state?: string | null;
};

export type AzureSyncResult = {
  ok: boolean;
  syncedAt?: string;
  agents: AgentMeta[];
  flows: FlowMeta[];
  agentCount: number;
  flowCount: number;
  errors: string[];
  resourceGroup?: string;
};

export type PermissionMatrix = {
  agents: Record<string, boolean>;
  skills: string[];
  mcps: string[];
  hitl: boolean;
  hitlRoles?: string[];
  appRoles?: string[];
  agentRoles?: string[];
  controls?: Record<string, boolean>;
};

export type MeResponse = {
  oid: string;
  name: string;
  email: string;
  roles: string[];
  appRoles: string[];
  agentRoles: string[];
  matrix: PermissionMatrix;
  isAdmin: boolean;
  flows?: FlowMeta[];
  agents?: string[];
  syncedAt?: string | null;
};

export type TokenUsage = {
  promptTokens?: number;
  completionTokens?: number;
  totalTokens?: number;
  reasoningTokens?: number;
  finishReason?: string;
  model?: string;
};

export type GuardrailDecision = {
  policy: string;
  result: string;
  details?: unknown;
};

export type Guardrails = {
  blocked?: boolean;
  decisions?: GuardrailDecision[];
  input?: { decisions?: GuardrailDecision[] };
  output?: { decisions?: GuardrailDecision[] };
  agentGuardrails?: Record<string, unknown>;
};

export type ToolChip = {
  name?: string;
  type?: string;
  status?: string;
  durationMs?: number;
  error?: string;
};

export type ToolExecution = ToolChip & {
  input?: unknown;
  output?: unknown;
  startedAt?: string;
  endedAt?: string;
};

export type TraceStep = {
  step?: string;
  status?: string;
  name?: string;
  type?: string;
  summary?: string;
  note?: string;
  callId?: string;
  arguments?: unknown;
  output?: unknown;
  sessionId?: string;
  previousResponseId?: string;
  responseId?: string;
  decisions?: unknown;
  agent?: string;
  model?: string;
};

export type ChatResult = {
  agent: string;
  reply: string;
  trace: TraceStep[];
  output_markdown: string;
  skills?: string[];
  mcps?: string[];
  tools?: Array<string | ToolChip>;
  toolExecutions?: ToolExecution[];
  secrets?: Array<Record<string, unknown>>;
  model?: { deployment?: string; harness?: string; source?: string };
  usage?: TokenUsage;
  guardrails?: Guardrails;
  correlationId?: string;
  createdAt?: string;
  input?: string;
  responseId?: string;
  previousResponseId?: string;
  sessionId?: string;
};

export type FlowNode = {
  id: string;
  label: string;
  agent: string | null;
  type?: string;
  system?: boolean;
  branch?: string | null;
  parallelGroup?: string | null;
  toolsHint?: string[];
  status: string;
  input?: unknown;
  output?: unknown;
  skills?: string[];
  mcps?: string[];
  tools?: Array<string | ToolChip>;
  toolExecutions?: ToolExecution[];
  policy?: unknown;
  usage?: TokenUsage | null;
  guardrails?: Guardrails | null;
  model?: { deployment?: string; harness?: string; source?: string } | null;
  startedAt?: string | null;
  endedAt?: string | null;
  durationMs?: number | null;
  correlationId?: string;
  trace?: TraceStep[];
  responseId?: string;
  position?: { x: number; y: number };
};

export type FlowEdge = {
  id: string;
  source: string;
  target: string;
  label?: string | null;
};

export type FlowGraph = {
  flowId: string;
  name?: string;
  description?: string;
  demoPrompt?: string;
  features?: string[];
  source: string;
  nodes: Array<FlowNode & { position?: { x: number; y: number } }>;
  edges: FlowEdge[];
};

export type FlowRun = {
  id: string;
  flowId: string;
  flowName?: string;
  userOid: string;
  userName: string;
  roles: string[];
  message: string;
  path: string;
  status: string;
  nodes: FlowNode[];
  edges?: FlowEdge[];
  graphSource?: string;
  features?: string[];
  createdAt: string;
  updatedAt?: string;
  paused?: boolean;
  correlationId?: string;
  hitlReviewerOids?: string[];
  hitlReviewerRoles?: string[];
  hitlDecision?: string;
  error?: string | Record<string, unknown>;
  viewerCanApproveHitl?: boolean;
  viewerCanControl?: boolean;
};

export type ChatTurnRecord = {
  id?: string;
  message: string;
  reply?: string;
  result?: ChatResult;
  createdAt?: string;
  agentId?: string;
};

/** One conversation thread (many turns) — what the history sidebar lists. */
export type ChatSession = {
  id: string;
  sessionId?: string;
  agentId: string;
  title?: string;
  preview?: string;
  message: string;
  result: ChatResult;
  createdAt: string;
  updatedAt?: string;
  messageCount?: number;
  turns?: ChatTurnRecord[];
  completedAt?: string;
  skills?: string[];
  mcps?: string[];
  tools?: unknown[];
  correlationId?: string;
};

/** @deprecated alias — sidebar now uses ChatSession */
export type ChatItem = ChatSession;

export type LearningItem = {
  id: string;
  agent: string;
  functionality: string;
  learning: string;
  date: string;
  correlationId?: string;
};

export type AdminUser = {
  oid: string;
  email: string;
  name: string;
  appRoles: string[];
  agentRoles: string[];
};
