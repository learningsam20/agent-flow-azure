import type { ChatSession, FlowRun } from "../lib/api";

const STATUS_DOT: Record<string, string> = {
  Running: "bg-sea pulse-dot",
  Waiting_HITL: "bg-ember pulse-dot",
  Succeeded: "bg-moss",
  Failed: "bg-red-600",
  Cancelled: "bg-stone-400",
  Paused: "bg-amber-600",
  Pending: "bg-slate-300",
};

export function HistorySidebar({
  open,
  onToggle,
  runs,
  chats,
  allUsers,
  canViewAll,
  selectedRunId,
  selectedChatId,
  onToggleAll,
  onSelectRun,
  onSelectChat,
  onNewChat,
}: {
  open: boolean;
  onToggle: () => void;
  runs: FlowRun[];
  chats: ChatSession[];
  allUsers: boolean;
  canViewAll: boolean;
  selectedRunId?: string | null;
  selectedChatId?: string | null;
  onToggleAll: (v: boolean) => void;
  onSelectRun: (id: string) => void;
  onSelectChat: (chat: ChatSession) => void;
  onNewChat?: () => void;
}) {
  if (!open) {
    return (
      <aside className="w-12 shrink-0 border-r border-sea/10 bg-white/40 backdrop-blur-sm flex flex-col items-center py-3 gap-3">
        <button
          type="button"
          title="Open history"
          onClick={onToggle}
          className="w-9 h-9 rounded-xl border border-sea/20 bg-white/80 text-sea text-sm hover:bg-sea hover:text-white transition shadow-sm"
          aria-label="Open history"
        >
          ☰
        </button>
        <span
          className="text-[10px] tracking-[0.2em] uppercase text-ink/35"
          style={{ writingMode: "vertical-rl", transform: "rotate(180deg)" }}
        >
          History
        </span>
        {(runs.length > 0 || chats.length > 0) && (
          <span className="mt-auto mb-2 text-[10px] font-semibold text-sea bg-sea/10 rounded-full px-1.5 py-0.5">
            {runs.length + chats.length}
          </span>
        )}
      </aside>
    );
  }

  return (
    <aside className="w-72 shrink-0 border-r border-sea/10 bg-white/55 backdrop-blur-md p-3 overflow-y-auto flex flex-col fade-in">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold tracking-tight">History</h2>
          <p className="text-[10px] text-ink/45 mt-0.5">
            {runs.length} flows · {chats.length} chats
          </p>
        </div>
        <div className="flex items-center gap-2">
          {canViewAll && (
            <label className="text-[11px] flex items-center gap-1 text-ink/60">
              <input
                type="checkbox"
                className="rounded border-sea/30"
                checked={allUsers}
                onChange={(e) => onToggleAll(e.target.checked)}
              />
              all
            </label>
          )}
          <button
            type="button"
            title="Collapse history"
            onClick={onToggle}
            className="w-7 h-7 rounded-lg border border-sea/20 text-sea text-xs hover:bg-sea/10 transition"
          >
            «
          </button>
        </div>
      </div>

      <h3 className="text-[10px] uppercase tracking-[0.14em] text-ink/40 mb-2 font-semibold">
        Flows
      </h3>
      <ul className="space-y-2 mb-5">
        {runs.map((r) => {
          const active = selectedRunId === r.id;
          return (
            <li key={r.id}>
              <button
                type="button"
                onClick={() => onSelectRun(r.id)}
                className={`w-full text-left rounded-xl border p-2.5 transition ${
                  active
                    ? "border-sea/40 bg-sea/10 shadow-sm"
                    : "border-transparent bg-sand/50 hover:bg-white hover:border-sea/15"
                }`}
              >
                <div className="flex items-center gap-1.5 text-xs font-semibold truncate">
                  <span
                    className={`inline-block w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[r.status] || "bg-slate-300"}`}
                  />
                  <span className="truncate">{r.status}</span>
                  {r.flowId && (
                    <span className="ml-auto text-[10px] font-medium text-ink/40 truncate max-w-[5.5rem]">
                      {r.flowId.replace(/-flow$/, "")}
                    </span>
                  )}
                </div>
                <div className="text-[11px] text-ink/65 line-clamp-2 mt-1 leading-snug">{r.message}</div>
                <div className="text-[10px] text-sea/80 mt-1.5">
                  {new Date(r.createdAt).toLocaleString()}
                </div>
              </button>
            </li>
          );
        })}
        {!runs.length && (
          <li className="text-xs text-ink/40 px-1 py-3">No flows yet — run a workflow to see it here.</li>
        )}
      </ul>

      <div className="flex items-center justify-between mb-2">
        <h3 className="text-[10px] uppercase tracking-[0.14em] text-ink/40 font-semibold">
          Chat sessions
        </h3>
        {onNewChat && (
          <button
            type="button"
            className="text-[10px] font-semibold text-sea hover:underline"
            onClick={onNewChat}
          >
            New
          </button>
        )}
      </div>
      <ul className="space-y-2">
        {chats.map((c) => {
          const sid = c.sessionId || c.id;
          const active = selectedChatId === sid || selectedChatId === c.id;
          const turns = c.messageCount || c.turns?.length || 1;
          return (
            <li key={sid}>
              <button
                type="button"
                onClick={() => onSelectChat(c)}
                className={`w-full text-left rounded-xl border p-2.5 transition ${
                  active
                    ? "border-sea/40 bg-sea/10 shadow-sm"
                    : "border-transparent bg-white/60 hover:bg-white hover:border-sea/15"
                }`}
              >
                <div className="flex items-center gap-1.5 text-xs font-semibold truncate">
                  <span className="truncate">{(c.agentId || "agent").replace("-agent", "")}</span>
                  <span className="ml-auto text-[10px] font-medium text-ink/40 shrink-0">
                    {turns} turn{turns === 1 ? "" : "s"}
                  </span>
                </div>
                <div className="text-[11px] text-ink/65 line-clamp-2 mt-1">
                  {c.title || c.preview || c.message}
                </div>
                <div className="text-[10px] text-sea/80 mt-1.5">
                  {new Date(c.updatedAt || c.createdAt).toLocaleString()}
                </div>
              </button>
            </li>
          );
        })}
        {!chats.length && <li className="text-xs text-ink/40 px-1 py-3">No chat sessions yet</li>}
      </ul>
    </aside>
  );
}
