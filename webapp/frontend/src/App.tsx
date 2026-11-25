import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  api,
  ChatResult,
  ChatSession,
  FlowMeta,
  FlowRun,
  LearningItem,
  MeResponse,
} from "./lib/api";
import {
  bootKeycloak,
  getAccessToken,
  initKeycloak,
  isAuthCallback,
  login,
  logout,
  shouldWaitForSession,
} from "./lib/keycloak";
import { initTelemetry, trackEvent } from "./lib/telemetry";
import { TopBar } from "./components/TopBar";
import { HistorySidebar } from "./components/HistorySidebar";
import { FlowCanvas, cacheFlowGraph, peekFlowGraph } from "./components/FlowCanvas";
import { DetailsPanel } from "./components/DetailsPanel";
import { PermissionDrawer } from "./components/PermissionDrawer";
import { LearningPane } from "./components/LearningPane";
import { AdminRoles } from "./components/AdminRoles";
import { LandingPage } from "./components/LandingPage";
import { ChatConversation, type ChatTurn } from "./components/ChatConversation";
import { formatAgentReply } from "./lib/jsonToMarkdown";
import { applyTheme, getStoredTheme, toggleTheme, type ThemeMode } from "./lib/theme";
import { mergeRunPreserveLayout } from "./lib/runLayout";
import { loadCachedHistory, saveCachedHistory } from "./lib/historyCache";

const DEFAULT_AGENTS = [
  "triage-router-agent",
  "repo-ops-agent",
  "sec-audit-agent",
  "finops-analyst-agent",
  "exec-synthesizer-agent",
];

export default function App() {
  const [ready, setReady] = useState(false);
  const [authPending, setAuthPending] = useState(() => shouldWaitForSession());
  const [me, setMe] = useState<MeResponse | null>(null);
  const [flows, setFlows] = useState<FlowMeta[]>([]);
  const [agents, setAgents] = useState<string[]>(DEFAULT_AGENTS);
  const [flowId, setFlowId] = useState("multi-agent-flow");
  const [mode, setMode] = useState<"chat" | "flow">("flow");
  const [agent, setAgent] = useState(DEFAULT_AGENTS[0]);
  const [syncing, setSyncing] = useState(false);
  const [syncedAt, setSyncedAt] = useState<string | null>(null);
  const [syncFlash, setSyncFlash] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [chatOut, setChatOut] = useState<ChatResult | null>(null);
  const [chatTurns, setChatTurns] = useState<ChatTurn[]>([]);
  const [chatError, setChatError] = useState<string | null>(null);
  const [run, setRun] = useState<FlowRun | null>(null);
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [chats, setChats] = useState<ChatSession[]>([]);
  const [learnings, setLearnings] = useState<LearningItem[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedChatId, setSelectedChatId] = useState<string | null>(null);
  const [chatSessionId, setChatSessionId] = useState<string>(() => crypto.randomUUID());
  const [lastResponseId, setLastResponseId] = useState<string | null>(null);
  const [controlFlash, setControlFlash] = useState<string | null>(null);
  const lastFollowedStatusRef = useRef<Record<string, string>>({});
  const [page, setPage] = useState<"workspace" | "learning" | "permissions" | "admin">("workspace");
  const [historyOpen, setHistoryOpen] = useState(true);
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [chatBusy, setChatBusy] = useState(false);
  const [flowBusy, setFlowBusy] = useState(false);
  const [submitFlash, setSubmitFlash] = useState<string | null>(null);
  const [allUsers, setAllUsers] = useState(false);
  const [theme, setTheme] = useState<ThemeMode>(() => getStoredTheme());
  const pollRef = useRef<number | null>(null);
  const chatAbortRef = useRef<AbortController | null>(null);
  /** When true, polling must not steal the selected node (user is inspecting). */
  const userPinnedNodeRef = useRef(false);
  const detailsOpenRef = useRef(false);

  const selectedFlow = useMemo(
    () => flows.find((f) => f.id === flowId) || null,
    [flows, flowId]
  );

  const selectedNode = useMemo(
    () => run?.nodes.find((n) => n.id === selectedNodeId) || null,
    [run, selectedNodeId]
  );

  const hitlNode = useMemo(
    () => run?.nodes.find((n) => n.status === "Waiting_HITL") || null,
    [run]
  );

  const failedNode = useMemo(
    () => run?.nodes.find((n) => n.status === "Failed") || null,
    [run]
  );

  const runFailReason = useMemo(() => {
    if (!run) return null;
    if (typeof run.error === "string" && run.error) return run.error;
    if (run.error && typeof run.error === "object") {
      const o = run.error as Record<string, unknown>;
      const msg = o.error || o.message || o.body || o.detail;
      if (typeof msg === "string") return msg;
      return JSON.stringify(run.error).slice(0, 300);
    }
    if (!failedNode?.output) return null;
    if (typeof failedNode.output === "string") return failedNode.output;
    const o = failedNode.output as Record<string, unknown>;
    const msg = o.error || o.message || o.body || o.detail || o.reason;
    if (typeof msg === "string") return msg;
    if (msg) return JSON.stringify(msg).slice(0, 300);
    return null;
  }, [run, failedNode]);

  const canHitl =
    run?.viewerCanApproveHitl ??
    (!!me?.matrix.hitl &&
      (!!(run?.hitlReviewerRoles || []).some((r) => me?.roles.includes(r)) ||
        !!me?.isAdmin ||
        (run?.hitlReviewerOids || []).includes(me?.oid || "")));

  async function refreshMe() {
    if (!getAccessToken()) {
      setMe(null);
      return null;
    }
    const m = await api.me();
    setMe(m);
    if (m.flows?.length) setFlows(m.flows);
    if (m.agents?.length) {
      setAgents(m.agents);
      setAgent((prev) => (m.agents!.includes(prev) ? prev : m.agents![0]));
    }
    if (m.syncedAt) setSyncedAt(m.syncedAt);
    return m;
  }

  async function syncFromAzure() {
    if (syncing) return;
    setSyncing(true);
    setSyncFlash("Syncing agents & workflows from Azure…");
    try {
      const result = await api.syncAzure();
      if (result.flows?.length) setFlows(result.flows);
      const agentIds = (result.agents || []).map((a) => a.id).filter(Boolean);
      if (agentIds.length) {
        setAgents(agentIds);
        setAgent((prev) => (agentIds.includes(prev) ? prev : agentIds[0]));
      }
      if (result.syncedAt) setSyncedAt(result.syncedAt);
      await refreshMe().catch(() => null);
      const errN = result.errors?.length || 0;
      setSyncFlash(
        errN
          ? `Synced ${result.agentCount} agents, ${result.flowCount} Logic Apps (${errN} warning${errN === 1 ? "" : "s"})`
          : `Synced ${result.agentCount} agents · ${result.flows?.length ?? result.flowCount} flows`
      );
      trackEvent("azure_sync", {
        agents: result.agentCount,
        flows: result.flowCount,
        errors: errN,
      });
    } catch (e) {
      setSyncFlash(e instanceof Error ? e.message : "Azure sync failed");
    } finally {
      setSyncing(false);
      window.setTimeout(() => setSyncFlash(null), 6000);
    }
  }

  async function refreshHistory() {
    if (!getAccessToken()) return;
    const data = await api.listRuns(allUsers);
    setRuns(data.runs);
    setChats(data.chats || []);
    if (me?.oid) saveCachedHistory(me.oid, data.runs, data.chats || []);
    // Warm layout templates so history clicks don't flash a grid layout.
    const flowIds = [...new Set((data.runs || []).map((r) => r.flowId).filter(Boolean))];
    for (const fid of flowIds.slice(0, 8)) {
      if (peekFlowGraph(fid)) continue;
      void api
        .flowGraph(fid)
        .then((g) => cacheFlowGraph(g))
        .catch(() => undefined);
    }
    // Cosmos merge finishes in the background — refresh once more shortly after.
    if ((data as { cosmosPending?: boolean }).cosmosPending) {
      window.setTimeout(() => {
        api
          .listRuns(allUsers)
          .then((again) => {
            setRuns(again.runs);
            setChats(again.chats || []);
            if (me?.oid) saveCachedHistory(me.oid, again.runs, again.chats || []);
          })
          .catch(() => undefined);
      }, 2500);
    }
  }

  async function refreshLearning() {
    if (!getAccessToken()) return;
    const data = await api.learning();
    setLearnings(data.learnings || []);
  }

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const startPolling = useCallback(
    (runId: string) => {
      stopPolling();
      lastFollowedStatusRef.current = {};
      pollRef.current = window.setInterval(async () => {
        try {
          const latest = await api.getRun(runId);
          setRun((prev) => mergeRunPreserveLayout(prev, latest, peekFlowGraph(latest.flowId)));
          const failed = latest.nodes.find((n) => n.status === "Failed");
          const running = latest.nodes.find((n) => n.status === "Running");
          const hitl = latest.nodes.find((n) => n.status === "Waiting_HITL");
          const succeededWithOutput = [...latest.nodes]
            .reverse()
            .find(
              (n) =>
                n.status === "Succeeded" &&
                n.output != null &&
                lastFollowedStatusRef.current[n.id] !== "Succeeded"
            );

          // Detect newly finished nodes so the details panel shows their payload.
          for (const n of latest.nodes) {
            const prev = lastFollowedStatusRef.current[n.id];
            if (prev && prev !== n.status && (n.status === "Succeeded" || n.status === "Failed")) {
              if (!userPinnedNodeRef.current) {
                setSelectedNodeId(n.id);
                setDetailsOpen(true);
                detailsOpenRef.current = true;
              }
            }
            lastFollowedStatusRef.current[n.id] = n.status;
          }

          // Follow live execution only when the user hasn't pinned a node in the details panel.
          if (!userPinnedNodeRef.current) {
            const follow =
              running ||
              hitl ||
              (latest.status === "Failed" ? failed : null) ||
              (latest.status === "Succeeded"
                ? [...latest.nodes].reverse().find((n) => n.status === "Succeeded" && n.output != null)
                : null) ||
              succeededWithOutput;
            if (follow) {
              setSelectedNodeId(follow.id);
              if (follow.status === "Failed" || follow.status === "Succeeded" || follow.status === "Waiting_HITL") {
                setDetailsOpen(true);
                detailsOpenRef.current = true;
              }
            }
            if (failed && latest.status === "Failed") {
              setSelectedNodeId(failed.id);
              setDetailsOpen(true);
              detailsOpenRef.current = true;
            }
          } else if (failed && latest.status === "Failed" && !detailsOpenRef.current) {
            userPinnedNodeRef.current = false;
            setSelectedNodeId(failed.id);
            setDetailsOpen(true);
            detailsOpenRef.current = true;
          }
          if (latest.status !== "Running" && latest.status !== "Paused") {
            if (latest.status !== "Waiting_HITL") {
              stopPolling();
              // Finished runs land on the run summary (unless the user pinned a node).
              if (!userPinnedNodeRef.current) {
                if (latest.status === "Succeeded") {
                  setSelectedNodeId(null);
                  setDetailsOpen(true);
                } else if (latest.status === "Failed" && failed) {
                  setSelectedNodeId(failed.id);
                  setDetailsOpen(true);
                } else if (latest.status === "Cancelled") {
                  setSelectedNodeId(null);
                  setDetailsOpen(true);
                }
              }
              setControlFlash(
                latest.status === "Succeeded"
                  ? "Workflow succeeded"
                  : latest.status === "Failed"
                    ? "Workflow failed — open the failed node for details"
                    : `Workflow ${latest.status}`
              );
              window.setTimeout(() => setControlFlash(null), 4500);
              refreshHistory().catch(() => undefined);
            }
          }
        } catch {
          /* ignore transient poll errors */
        }
      }, 1200);
    },
    [stopPolling]
  );

  useEffect(() => () => stopPolling(), [stopPolling]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    let cancelled = false;
    const callback = isAuthCallback();
    const waitForSession = shouldWaitForSession();
    setAuthPending(callback || waitForSession);
    const failsafe = callback
      ? null
      : window.setTimeout(() => {
          if (!cancelled) {
            setAuthPending(false);
            setReady(true);
          }
        }, 4500);

    async function loadWorkspace() {
      const [meResult, fl, ag] = await Promise.all([
        refreshMe(),
        api.listFlows().catch(() => null),
        api.listAgents().catch(() => null),
      ]);
      if (cancelled) return;
      if (fl?.flows?.length) {
        setFlows(fl.flows);
        if (fl.syncedAt) setSyncedAt(fl.syncedAt);
        const first = fl.flows[0];
        if (first) {
          setFlowId(first.id);
          setInput((prev) => prev || first.demoPrompt || "");
        }
      }
      if (ag?.agents?.length) {
        const ids = ag.agents.map((a) => a.id);
        setAgents(ids);
        setAgent((prev) => (ids.includes(prev) ? prev : ids[0]));
        if (ag.syncedAt) setSyncedAt(ag.syncedAt);
      }
      return meResult;
    }

    (async () => {
      try {
        // Fast path: stored JWT — open the app via /api/me while Keycloak boots in background.
        if (!callback && getAccessToken()) {
          setAuthPending(true);
          try {
            await loadWorkspace();
            if (!cancelled) {
              setAuthPending(false);
              setReady(true);
            }
            void bootKeycloak().catch(() => undefined);
            void fetch("/api/config/public")
              .then((r) => (r.ok ? r.json() : null))
              .then((cfg) => cfg && initTelemetry(cfg.appInsightsConnectionString))
              .catch(() => undefined);
            return;
          } catch {
            localStorage.removeItem("afa_token");
            setMe(null);
          }
        }

        const kc = callback || waitForSession ? await bootKeycloak() : await initKeycloak();
        if (cancelled) return;

        void fetch("/api/config/public")
          .then((r) => (r.ok ? r.json() : null))
          .then((cfg) => cfg && initTelemetry(cfg.appInsightsConnectionString))
          .catch(() => undefined);

        let session = kc;
        if (!session.didInitialize || (callback && !session.authenticated)) {
          session = await bootKeycloak();
          if (cancelled) return;
        }

        if (session.authenticated) {
          setAuthPending(true);
          try {
            await loadWorkspace();
            if (window.location.hash || window.location.search.includes("code=")) {
              window.history.replaceState({}, document.title, window.location.pathname);
            }
          } catch (e) {
            console.error(e);
            localStorage.removeItem("afa_token");
            setMe(null);
          }
        } else {
          localStorage.removeItem("afa_token");
          if (!session.didInitialize) {
            void bootKeycloak()
              .then(async (late) => {
                if (cancelled || !late.authenticated) return;
                setAuthPending(true);
                try {
                  await loadWorkspace();
                } catch (e) {
                  console.error(e);
                } finally {
                  if (!cancelled) setAuthPending(false);
                }
              })
              .catch(() => undefined);
          }
        }
      } catch (e) {
        console.error(e);
      } finally {
        if (failsafe != null) window.clearTimeout(failsafe);
        if (!cancelled) {
          setAuthPending(false);
          setReady(true);
        }
      }
    })();
    return () => {
      cancelled = true;
      if (failsafe != null) window.clearTimeout(failsafe);
    };
  }, []);

  useEffect(() => {
    if (!me) return;
    // Paint cached history instantly on login/refresh, then reconcile with API.
    const cached = loadCachedHistory(me.oid);
    if (cached.runs.length || cached.chats.length) {
      setRuns(cached.runs);
      setChats(cached.chats);
    }
    refreshHistory().catch(() => undefined);
    refreshLearning().catch(() => undefined);
  }, [me, allUsers]);

  function startNewChatSession() {
    setChatSessionId(crypto.randomUUID());
    setLastResponseId(null);
    setChatTurns([]);
    setChatOut(null);
    setSelectedChatId(null);
    setChatError(null);
    setMode("chat");
    setPage("workspace");
    setDetailsOpen(false);
  }

  function stopChat() {
    chatAbortRef.current?.abort();
    chatAbortRef.current = null;
    setChatBusy(false);
    setChatError(null);
  }

  async function sendChat() {
    const message = input.trim();
    if (!message || chatBusy) return;
    chatAbortRef.current?.abort();
    const ac = new AbortController();
    chatAbortRef.current = ac;
    setChatBusy(true);
    setChatError(null);
    setMode("chat");
    setPage("workspace");
    const sessionId = chatSessionId || crypto.randomUUID();
    if (!chatSessionId) setChatSessionId(sessionId);
    // History is only a cold-start fallback; once we have a response id the server chains like playground.
    const history = lastResponseId
      ? []
      : chatTurns
          .filter((t) => !t.error && t.text.trim())
          .map((t) => ({ role: t.role, content: t.text }));
    const userTurn: ChatTurn = {
      id: `u-${crypto.randomUUID()}`,
      role: "user",
      text: message,
      at: new Date().toISOString(),
    };
    setChatTurns((prev) => [...prev, userTurn]);
    setInput("");
    trackEvent("chat_send", { agent, sessionId });
    try {
      const out = await api.chat(agent, message, {
        sessionId,
        previousResponseId: lastResponseId || undefined,
        history,
        signal: ac.signal,
      });
      if (ac.signal.aborted) return;
      const source = out.output_markdown || out.reply || "";
      const formatted = formatAgentReply(source);
      const text = formatted.markdown || out.reply || "";
      if (out.sessionId) setChatSessionId(out.sessionId);
      if (out.responseId) setLastResponseId(out.responseId);
      setSelectedChatId(out.sessionId || sessionId);
      setChatOut({ ...out, input: message });
      setChatTurns((prev) => [
        ...prev,
        {
          id: `a-${crypto.randomUUID()}`,
          role: "assistant",
          text,
          agent: out.agent || agent,
          at: out.createdAt || new Date().toISOString(),
          usage: out.usage,
          raw: out,
          fromJson: formatted.fromJson,
        },
      ]);
      // Keep details open on the latest turn so traces are visible without an extra click
      setDetailsOpen(true);
      refreshLearning();
    } catch (e: unknown) {
      if (ac.signal.aborted || (e as { name?: string }).name === "AbortError") {
        setChatTurns((prev) => [
          ...prev,
          {
            id: `e-${crypto.randomUUID()}`,
            role: "assistant",
            text: "Stopped.",
            agent,
            at: new Date().toISOString(),
            error: true,
          },
        ]);
        return;
      }
      const err = e as {
        status?: number;
        body?: { detail?: { error?: string }; error?: string };
        message?: string;
      };
      const msg =
        err.status === 403
          ? err.body?.detail?.error || err.body?.error || "403 Policy Violation"
          : err.message || "Chat failed";
      setChatError(msg);
      setChatTurns((prev) => [
        ...prev,
        {
          id: `e-${crypto.randomUUID()}`,
          role: "assistant",
          text: msg,
          agent,
          at: new Date().toISOString(),
          error: true,
        },
      ]);
    } finally {
      if (chatAbortRef.current === ac) chatAbortRef.current = null;
      setChatBusy(false);
      refreshHistory();
    }
  }

  async function runControl(
    action: "pause" | "resume" | "retry" | "cancel",
    opts?: { nodeId?: string }
  ) {
    if (!run || run.id === "pending" || flowBusy) return;
    setFlowBusy(true);
    setChatError(null);
    const labels = {
      pause: "Pausing…",
      resume: "Resuming…",
      retry: opts?.nodeId ? `Retrying from ${opts.nodeId}…` : "Retrying workflow…",
      cancel: "Stopping…",
    };
    setControlFlash(labels[action]);
    setSubmitFlash(labels[action]);
    userPinnedNodeRef.current = false;
    detailsOpenRef.current = false;
    try {
      let r: FlowRun;
      if (action === "pause") r = await api.pause(run.id, opts?.nodeId);
      else if (action === "resume") r = await api.resume(run.id, opts?.nodeId);
      else if (action === "retry") r = await api.retry(run.id, opts?.nodeId);
      else r = await api.cancel(run.id, opts?.nodeId);

      setRun((prev) => mergeRunPreserveLayout(prev, r, peekFlowGraph(r.flowId)));
      setFlowId(r.flowId);
      const focus =
        r.nodes.find((n) => n.status === "Running" || n.status === "Waiting_HITL") ||
        r.nodes.find((n) => n.status === "Failed") ||
        (opts?.nodeId ? r.nodes.find((n) => n.id === opts.nodeId) : null) ||
        r.nodes[0];
      if (focus) {
        setSelectedNodeId(focus.id);
        setDetailsOpen(true);
        detailsOpenRef.current = true;
      }
      if (r.status === "Running" || r.status === "Waiting_HITL" || r.status === "Paused") {
        startPolling(r.id);
        setControlFlash(
          action === "retry"
            ? `Retry started · ${r.status}`
            : action === "resume"
              ? `Resumed · ${r.status}`
              : `${action} · ${r.status}`
        );
      } else {
        stopPolling();
        setControlFlash(`${action} → ${r.status}`);
        if (r.status === "Failed") {
          const failed = r.nodes.find((n) => n.status === "Failed");
          if (failed) {
            setSelectedNodeId(failed.id);
            setDetailsOpen(true);
          }
        }
      }
      refreshHistory();
      refreshLearning();
    } catch (e: unknown) {
      const msg = (e as Error).message || `${action} failed`;
      setChatError(msg);
      setControlFlash(null);
    } finally {
      setFlowBusy(false);
      window.setTimeout(() => {
        setControlFlash(null);
        setSubmitFlash(null);
      }, 4000);
    }
  }

  async function startFlow() {
    if (flowBusy) return;
    setFlowBusy(true);
    setChatError(null);
    setMode("flow");
    setSubmitFlash("Submitted — running…");
    trackEvent("flow_start", { flowId });
    try {
      // Prefetch styled graph (with positions) so the canvas never flashes a grid layout
      let graphTemplate = peekFlowGraph(flowId);
      try {
        if (!graphTemplate) {
          graphTemplate = await api.flowGraph(flowId);
          cacheFlowGraph(graphTemplate);
        }
        setRun({
          id: "pending",
          flowId,
          flowName: graphTemplate.name || selectedFlow?.name,
          userOid: me?.oid || "",
          userName: me?.name || "",
          roles: me?.roles || [],
          message: input,
          path: selectedFlow?.defaultPath || "A",
          status: "Running",
          nodes: graphTemplate.nodes.map((n) => ({
            ...n,
            status: n.id === "Init_State" ? "Running" : "Pending",
          })),
          edges: graphTemplate.edges,
          graphSource: graphTemplate.source,
          createdAt: new Date().toISOString(),
        });
        userPinnedNodeRef.current = false;
        setSelectedNodeId(null);
        setDetailsOpen(true);
        detailsOpenRef.current = true;
      } catch {
        /* continue; API run still proceeds */
      }

      const r = await api.runFlow(input, flowId);
      setRun((prev) => mergeRunPreserveLayout(prev, r, graphTemplate));
      setSubmitFlash(`Running · ${r.flowName || r.flowId}`);
      const active =
        r.nodes.find((n) => n.status === "Running" || n.status === "Waiting_HITL") || null;
      userPinnedNodeRef.current = false;
      setSelectedNodeId(active?.id || null);
      setDetailsOpen(true);
      detailsOpenRef.current = true;
      startPolling(r.id);
      refreshLearning();
      refreshHistory();
    } catch (e: unknown) {
      setChatError((e as Error).message || "Flow failed to start");
      setSubmitFlash(null);
    } finally {
      setFlowBusy(false);
      window.setTimeout(() => setSubmitFlash(null), 4000);
    }
  }

  async function onHitl(payload: {
    decision: string;
    notes: string;
    pathOverride?: string;
    restartFromNode?: string;
    input?: Record<string, string>;
  }) {
    if (!run || run.id === "pending") return;
    setFlowBusy(true);
    setChatError(null);
    setControlFlash(
      payload.decision === "approve"
        ? "Submitting approval…"
        : payload.decision === "reject"
          ? "Submitting rejection…"
          : "Submitting re-route…"
    );
    setSubmitFlash("HITL decision submitted…");
    try {
      const r = await api.approve(run.id, {
        decision: payload.decision,
        notes: payload.notes,
        path_override: payload.pathOverride,
        restart_from_node: payload.restartFromNode,
        input: payload.input,
      });
      setRun((prev) => mergeRunPreserveLayout(prev, r, peekFlowGraph(r.flowId)));
      setControlFlash(
        payload.decision === "approve"
          ? "Approved — continuing…"
          : payload.decision === "reject"
            ? "Rejected"
            : "Re-routed — continuing…"
      );
      setSubmitFlash(
        payload.decision === "approve"
          ? "Approved — agents continuing"
          : payload.decision === "reject"
            ? "Rejected"
            : "Re-routed — continuing"
      );
      const focus =
        r.nodes.find((n) => n.status === "Failed") ||
        r.nodes.find((n) => n.status === "Running") ||
        (payload.decision === "approve"
          ? r.nodes.find((n) => n.id === "Node5_Synthesize" || n.id === "Final_Approved" || n.id === "Final_Response")
          : payload.decision === "reject"
            ? r.nodes.find((n) => n.id === "Final_Rejected" || n.id === "Cancelled_Response")
            : r.nodes.find((n) => n.status === "Running")) ||
        r.nodes.find((n) => n.id.includes("HITL") && n.status !== "Waiting_HITL");
      if (focus) setSelectedNodeId(focus.id);
      setDetailsOpen(true);
      // Approve/reroute finish synthesize in the background — keep polling.
      if (r.status === "Running" || r.status === "Waiting_HITL" || payload.decision !== "reject") {
        startPolling(r.id);
      } else {
        stopPolling();
      }
      refreshLearning();
      refreshHistory();
      window.setTimeout(() => setControlFlash(null), 4000);
    } catch (e: unknown) {
      setChatError((e as Error).message || "HITL failed");
    } finally {
      setFlowBusy(false);
    }
  }

  async function ensureFlowGraph(flowId: string) {
    const hit = peekFlowGraph(flowId);
    if (hit) return hit;
    try {
      const g = await api.flowGraph(flowId);
      cacheFlowGraph(g);
      return g;
    } catch {
      return null;
    }
  }

  async function loadRunFromHistory(id: string) {
    setChatError(null);
    setMode("flow");
    const cached = runs.find((r) => r.id === id);
    const flowIdHint = cached?.flowId;

    // Resolve layout template before the first paint — avoids the jumbled grid flash.
    let template = flowIdHint ? peekFlowGraph(flowIdHint) : null;
    if (flowIdHint && !template) {
      setSubmitFlash("Loading layout…");
      template = await ensureFlowGraph(flowIdHint);
    }

    if (cached) {
      setRun((prev) =>
        mergeRunPreserveLayout(
          prev?.flowId === cached.flowId ? prev : null,
          cached,
          template
        )
      );
      setFlowId(cached.flowId);
      setSelectedChatId(null);
      if (cached.status === "Running" || cached.status === "Waiting_HITL" || cached.status === "Paused") {
        const active =
          cached.nodes.find((n) => n.status === "Waiting_HITL" || n.status === "Running") || null;
        setSelectedNodeId(active?.id || null);
        if (cached.status === "Running" || cached.status === "Waiting_HITL") startPolling(cached.id);
      } else {
        setSelectedNodeId(null);
        stopPolling();
      }
      setDetailsOpen(true);
      setSubmitFlash(null);
    } else {
      setSubmitFlash("Loading run…");
      setFlowBusy(true);
    }

    try {
      const r = await api.getRun(id);
      if (!template || template.flowId !== r.flowId) {
        template = await ensureFlowGraph(r.flowId);
      }
      setRun((prev) => mergeRunPreserveLayout(prev?.flowId === r.flowId ? prev : null, r, template));
      setFlowId(r.flowId);
      setSelectedChatId(null);
      if (r.status === "Running" || r.status === "Waiting_HITL" || r.status === "Paused") {
        const active =
          r.nodes.find((n) => n.status === "Waiting_HITL" || n.status === "Running") || null;
        setSelectedNodeId(active?.id || null);
        if (r.status === "Running" || r.status === "Waiting_HITL") startPolling(r.id);
        else stopPolling();
      } else {
        setSelectedNodeId(null);
        stopPolling();
      }
      setDetailsOpen(true);
      setRuns((prev) => {
        const next = [r, ...prev.filter((x) => x.id !== r.id)];
        if (me?.oid) saveCachedHistory(me.oid, next, chats);
        return next;
      });
    } catch (e: unknown) {
      if (!cached) {
        setChatError((e as Error).message || "Could not load run");
      }
    } finally {
      setFlowBusy(false);
      setSubmitFlash(null);
    }
  }

  function selectFlowCard(f: FlowMeta) {
    setFlowId(f.id);
    setInput(f.demoPrompt);
    setMode("flow");
    // Keep current run visible unless starting a new one — don't wipe mid-review
  }

  function openNodeDetails(id: string) {
    // Clicking the already-selected node clears selection → run summary.
    if (selectedNodeId === id) {
      closeNodeDetails();
      return;
    }
    userPinnedNodeRef.current = true;
    detailsOpenRef.current = true;
    setSelectedNodeId(id);
    setDetailsOpen(true);
    setMode("flow");
  }

  function closeNodeDetails() {
    // With an active run, Close returns to the run summary (not an empty panel).
    userPinnedNodeRef.current = false;
    if (mode === "flow" && run) {
      setSelectedNodeId(null);
      setDetailsOpen(true);
      detailsOpenRef.current = true;
      return;
    }
    detailsOpenRef.current = false;
    setDetailsOpen(false);
  }

  const runTerminal = !!run && ["Succeeded", "Failed", "Cancelled"].includes(run.status);
  const canPause = !!run && !runTerminal && run.id !== "pending" && run.status !== "Paused";
  const canResume = !!run && run.status === "Paused" && run.id !== "pending";
  const canStop = !!run && !runTerminal && run.id !== "pending";
  const canRetry = !!run && run.id !== "pending";

  if (!ready || authPending) {
    return (
      <div className="min-h-screen grid place-items-center landing-root">
        <div className="text-center fade-in">
          <div className="w-10 h-10 mx-auto mb-3 rounded-2xl bg-gradient-to-br from-sea to-moss animate-pulse" />
          <p className="text-sand/80 font-medium">
            {authPending ? "Signing you in…" : "Loading identity…"}
          </p>
        </div>
      </div>
    );
  }

  if (!me) {
    return <LandingPage onSignIn={login} />;
  }

  const showHitlInPanel =
    !!run &&
    run.status === "Waiting_HITL" &&
    !!selectedNode &&
    selectedNode.status === "Waiting_HITL";

  const flowPanelOpen = mode === "flow" ? !!run : detailsOpen;
  const detailsProps = {
    open: mode === "chat" ? detailsOpen : flowPanelOpen,
    onClose: closeNodeDetails,
    node: mode === "flow" ? selectedNode : null,
    chat: mode === "chat" ? chatOut : null,
    run: mode === "flow" ? run : null,
    hitl: showHitlInPanel
      ? {
          show: true,
          canApprove: !!canHitl,
          busy: flowBusy,
          flowId: run?.flowId,
          reviewers: {
            oids: run?.hitlReviewerOids,
            roles: run?.hitlReviewerRoles,
          },
          onSubmit: onHitl,
        }
      : undefined,
    runError: mode === "flow" ? runFailReason : null,
    hitlDecision: mode === "flow" ? run?.hitlDecision || null : null,
    loading: false,
    busy: mode === "flow" ? flowBusy : false,
    onSelectHitl: hitlNode ? () => openNodeDetails(hitlNode.id) : undefined,
  };

  return (
    <div className="min-h-screen flex flex-col">
      <TopBar
        me={me}
        theme={theme}
        onToggleTheme={() => setTheme((t) => toggleTheme(t))}
        onLogout={() => logout()}
        onOpenLearning={() => {
          setPage("learning");
          refreshLearning().catch(() => undefined);
        }}
        onOpenPermissions={() => setPage("permissions")}
        onOpenAdmin={() => setPage("admin")}
        onSyncAzure={syncFromAzure}
        syncing={syncing}
        syncedAt={syncedAt}
      />

      {page === "learning" && (
        <LearningPane
          items={learnings}
          onClose={() => setPage("workspace")}
          onRefresh={() => refreshLearning()}
        />
      )}
      {page === "permissions" && (
        <PermissionDrawer
          matrix={me.matrix}
          roles={me.roles}
          onClose={() => setPage("workspace")}
        />
      )}
      {page === "admin" && me.isAdmin && (
        <AdminRoles onClose={() => setPage("workspace")} />
      )}

      {page === "workspace" && (
      <div className="flex flex-1 gap-0 min-h-0">
        <HistorySidebar
          open={historyOpen}
          onToggle={() => setHistoryOpen((v) => !v)}
          runs={runs}
          chats={chats}
          allUsers={allUsers}
          canViewAll={!!me?.isAdmin}
          selectedRunId={run?.id}
          selectedChatId={selectedChatId || chatSessionId}
          onToggleAll={setAllUsers}
          onNewChat={startNewChatSession}
          onSelectRun={(id) => {
            setPage("workspace");
            loadRunFromHistory(id);
          }}
          onSelectChat={(c) => {
            setPage("workspace");
            setMode("chat");
            const sid = c.sessionId || c.id;
            setSelectedChatId(sid);
            setChatSessionId(sid);
            // Resume Foundry chain from the last stored response in this session when available
            const lastTurn = [...(c.turns || [])].reverse().find((t) => t.result?.responseId);
            setLastResponseId(lastTurn?.result?.responseId || c.result?.responseId || null);
            setAgent(c.agentId);
            const turns: ChatTurn[] = [];
            if (c.turns?.length) {
              for (const t of c.turns) {
                turns.push({
                  id: `u-${t.id || crypto.randomUUID()}`,
                  role: "user",
                  text: t.message,
                  at: t.createdAt,
                });
                const result = t.result || c.result;
                const formatted = formatAgentReply(
                  result?.output_markdown || result?.reply || t.reply || ""
                );
                turns.push({
                  id: `a-${t.id || crypto.randomUUID()}`,
                  role: "assistant",
                  text: formatted.markdown || result?.reply || t.reply || "",
                  agent: t.agentId || c.agentId,
                  at: t.createdAt,
                  usage: result?.usage,
                  raw: result || null,
                  fromJson: formatted.fromJson,
                });
              }
            } else {
              const result = {
                ...c.result,
                input: c.message,
                createdAt: c.createdAt,
                skills: c.skills || c.result?.skills,
                mcps: c.mcps || c.result?.mcps,
                tools: (c.tools || c.result?.tools) as ChatResult["tools"],
              };
              const formatted = formatAgentReply(result.output_markdown || result.reply);
              turns.push(
                { id: `u-${c.id}`, role: "user", text: c.message, at: c.createdAt },
                {
                  id: `a-${c.id}`,
                  role: "assistant",
                  text: formatted.markdown || result.reply || "",
                  agent: c.agentId,
                  at: c.createdAt,
                  usage: result.usage,
                  raw: result,
                  fromJson: formatted.fromJson,
                }
              );
              setChatOut(result);
            }
            const lastAssistant = [...turns].reverse().find((t) => t.role === "assistant" && t.raw);
            if (lastAssistant?.raw) setChatOut(lastAssistant.raw);
            setChatTurns(turns);
            setInput("");
            setDetailsOpen(false);
            stopPolling();
          }}
        />

        <main className="flex-1 p-3 md:p-4 flex flex-col gap-3 min-w-0 overflow-auto">
          <header className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap gap-1.5 items-center p-1 rounded-xl bg-white/55 border border-sea/10">
              {!historyOpen && (
                <button type="button" className="btn-ghost text-xs" onClick={() => setHistoryOpen(true)}>
                  History
                </button>
              )}
              <button
                type="button"
                className={`btn text-sm px-3 ${mode === "chat" ? "btn-primary" : "btn-ghost border-0 bg-transparent"}`}
                onClick={() => setMode("chat")}
              >
                Chat
              </button>
              <button
                type="button"
                className={`btn text-sm px-3 ${mode === "flow" ? "btn-primary" : "btn-ghost border-0 bg-transparent"}`}
                onClick={() => setMode("flow")}
              >
                Workflow
              </button>
            </div>
            {(syncFlash || submitFlash || controlFlash || (run && mode === "flow")) && (
              <div
                className={`status-pill max-w-full ${
                  syncFlash
                    ? "bg-sea/10 border-sea/20 text-sea"
                    : run?.status === "Failed"
                    ? "bg-red-50 border-red-200 text-red-800"
                    : run?.status === "Succeeded"
                      ? "bg-moss/10 border-moss/25 text-moss"
                      : run?.status === "Waiting_HITL"
                        ? "bg-ember/10 border-ember/25 text-ember"
                        : run?.hitlDecision === "approve"
                          ? "bg-moss/10 border-moss/25 text-moss"
                          : "bg-sea/10 border-sea/20 text-sea"
                }`}
              >
                <span
                  className={`inline-block w-2 h-2 rounded-full ${
                    flowBusy || chatBusy || run?.status === "Running"
                      ? "bg-sea pulse-dot"
                      : run?.status === "Succeeded" || run?.hitlDecision === "approve"
                        ? "bg-moss"
                        : run?.status === "Failed"
                          ? "bg-red-600"
                          : run?.status === "Waiting_HITL"
                            ? "bg-ember pulse-dot"
                            : "bg-sea"
                  }`}
                />
                <span className="truncate">
                  {syncFlash ||
                    controlFlash ||
                    submitFlash ||
                    (run?.hitlDecision ? `${run.status} · HITL ${run.hitlDecision}` : run?.status)}
                </span>
              </div>
            )}
          </header>

          {mode === "flow" && (
            <section className="panel p-3 fade-in">
              <div className="flex flex-wrap gap-2 items-start">
                <label className="text-xs text-ink/55 shrink-0">
                  Flow
                  <select
                    className="mt-1 block min-w-[14rem] rounded-lg border border-sea/15 bg-white px-2.5 py-1.5 text-sm text-ink"
                    value={flowId}
                    onChange={(e) => {
                      const f = flows.find((x) => x.id === e.target.value);
                      if (f) selectFlowCard(f);
                    }}
                  >
                    {flows.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <textarea
                className="mt-2 w-full min-h-[64px] rounded-xl border border-sea/15 bg-white/90 p-3 text-sm outline-none focus:ring-2 focus:ring-sea/25 focus:border-sea/30 transition placeholder:text-ink/30"
                placeholder="Describe the task for this workflow…"
                value={input}
                onChange={(e) => setInput(e.target.value)}
              />
              <div className="mt-2.5 flex flex-wrap gap-2 items-center">
                <button type="button" disabled={!me || flowBusy} onClick={startFlow} className="btn-moss">
                  {flowBusy ? "Starting…" : "Run workflow"}
                </button>
                {selectedFlow && (
                  <span className="text-[11px] text-ink/45 line-clamp-1 flex-1">
                    {selectedFlow.description}
                  </span>
                )}
              </div>
            </section>
          )}

          {chatError && (
            <div className="rounded-xl bg-ember/10 border border-ember/25 text-ember px-4 py-3 text-sm font-semibold fade-in">
              {chatError}
            </div>
          )}

          {mode === "flow" && run?.status === "Failed" && runFailReason && (
            <div className="rounded-xl bg-red-50 border border-red-200 text-red-800 px-4 py-3 text-sm fade-in">
              <div className="font-semibold">
                Failed{failedNode ? ` at ${failedNode.label || failedNode.id}` : ""}
              </div>
              <div className="mt-1 text-xs leading-snug whitespace-pre-wrap opacity-90">{runFailReason}</div>
              {failedNode && (
                <button
                  type="button"
                  className="mt-2 text-xs underline font-semibold"
                  onClick={() => openNodeDetails(failedNode.id)}
                >
                  Open failed node
                </button>
              )}
            </div>
          )}

          {mode === "chat" && (
            <div className="flex flex-1 min-h-[360px] gap-0 items-stretch">
              <ChatConversation
                turns={chatTurns}
                agent={agent}
                agents={agents}
                lockedAgents={me.matrix.agents}
                input={input}
                busy={chatBusy}
                onInputChange={setInput}
                onAgentChange={(id) => {
                  if (id !== agent && chatTurns.length) {
                    // Switching agents starts a fresh session so context stays coherent.
                    setChatSessionId(crypto.randomUUID());
                    setLastResponseId(null);
                    setChatTurns([]);
                    setChatOut(null);
                    setSelectedChatId(null);
                  }
                  setAgent(id);
                }}
                onSend={sendChat}
                onStop={stopChat}
                onNewChat={startNewChatSession}
                onInspect={(raw) => {
                  setChatOut(raw);
                  setDetailsOpen(true);
                }}
              />
              <DetailsPanel {...detailsProps} embedded />
            </div>
          )}

          {mode === "flow" && (
            <div className="fade-in flex flex-col gap-2 flex-1 min-h-0">
              {run && (
                <div className="flex flex-wrap gap-1.5 items-center">
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    disabled={flowBusy || !canPause}
                    title={runTerminal ? "Run already finished" : run.status === "Paused" ? "Already paused" : "Pause execution"}
                    onClick={() => runControl("pause")}
                  >
                    {flowBusy && controlFlash?.startsWith("Paus") ? "Pausing…" : "Pause"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    disabled={flowBusy || !canResume}
                    title={
                      canResume
                        ? "Resume paused run"
                        : runTerminal
                          ? "Completed runs cannot be resumed — use Retry"
                          : "Resume is only available while paused"
                    }
                    onClick={() => runControl("resume")}
                  >
                    {flowBusy && controlFlash?.toLowerCase().includes("resum") ? "Resuming…" : "Resume"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost text-xs"
                    disabled={flowBusy || !canRetry}
                    onClick={() => runControl("retry")}
                  >
                    {flowBusy && controlFlash?.toLowerCase().includes("retr") ? "Retrying…" : "Retry all"}
                  </button>
                  <button
                    type="button"
                    className="btn-ghost text-xs text-ember border-ember/25"
                    disabled={flowBusy || !canStop}
                    title={runTerminal ? "Run already finished" : "Stop execution"}
                    onClick={() => runControl("cancel")}
                  >
                    {flowBusy && controlFlash?.startsWith("Stop") ? "Stopping…" : "Stop"}
                  </button>
                  {hitlNode && (
                    <button
                      type="button"
                      className="btn-ghost text-xs text-ember border-ember/30 bg-ember/5"
                      onClick={() => openNodeDetails(hitlNode.id)}
                    >
                      Open HITL node
                    </button>
                  )}
                  {selectedNodeId && (
                    <button
                      type="button"
                      className="btn-ghost text-xs text-sea"
                      onClick={() => {
                        setSelectedNodeId(null);
                        setDetailsOpen(true);
                      }}
                    >
                      Run summary
                    </button>
                  )}
                </div>
              )}
              <div className="flex flex-1 min-h-[min(520px,56vh)] items-stretch panel overflow-hidden">
                <div className="flex-1 min-w-0 min-h-[340px] relative">
                  {run ? (
                    <FlowCanvas
                      run={run}
                      selectedNodeId={selectedNodeId}
                      onSelectNode={openNodeDetails}
                      onRestart={(nodeId) => {
                        void runControl("retry", { nodeId });
                      }}
                      busy={flowBusy}
                      busyLabel={controlFlash || submitFlash}
                    />
                  ) : (
                    <div className="h-full grid place-items-center p-8 text-center">
                      <div>
                        <p className="font-display text-2xl text-ink/30">Workflow canvas</p>
                        <p className="text-sm text-ink/40 mt-2 max-w-sm mx-auto">
                          Choose a demo flow and click{" "}
                          <span className="font-semibold text-moss">Run workflow</span>. Nodes light
                          up as they execute; failures show the reason here.
                        </p>
                      </div>
                    </div>
                  )}
                </div>
                <DetailsPanel {...detailsProps} embedded />
              </div>
            </div>
          )}
        </main>
      </div>
      )}
    </div>
  );
}
